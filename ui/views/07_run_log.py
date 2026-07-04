"""Study Run Log — history of all past study runs with re-run capability."""
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import (
    DA_MAPPING_YAML,
    DA_SOURCE_CSV,
    DB_PATH,
    DEFAULT_CI_TABLE,
    DEFAULT_LAPSE_TABLE,
    DEFAULT_MORTALITY_TABLE,
    REFERENCE_TABLES_DIR,
    TERM_MAPPING_YAML,
    TERM_SOURCE_CSV,
    UL_MAPPING_YAML,
    UL_SOURCE_CSV,
    VUL_MAPPING_YAML,
    VUL_SOURCE_CSV,
    WL_MAPPING_YAML,
    WL_SOURCE_CSV,
)
from src.calculation.ae_engine import calculate_ae
from src.data_quality.runner import DQCriticalFailure, run_dq_checks
from src.exposure.engine import build_exposure_file
from src.ingestion.pipeline import run_etl_pipeline
from src.utils.db_init import init_database
from src.utils.types import CredibilityMethod, ExposureMethod, StudyConfig

st.set_page_config(page_title="Run Log", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Study Run Log")

# Product → (source_csv, mapping_yaml) — mirrors study_setup.py
_PRODUCT_ETL_CONFIG: dict[str, tuple[str, str]] = {
    "TERM": (TERM_SOURCE_CSV, TERM_MAPPING_YAML),
    "WL":   (WL_SOURCE_CSV,   WL_MAPPING_YAML),
    "UL":   (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "ULSG": (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "VUL":  (VUL_SOURCE_CSV,  VUL_MAPPING_YAML),
    "DA":   (DA_SOURCE_CSV,   DA_MAPPING_YAML),
}


def _register_study_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    product_codes: list[str],
    start_date: date,
    end_date: date,
    exposure_method: str,
    mortality_table: str,
    lapse_table: str,
    ci_table: str,
    credibility_method: str,
) -> None:
    """Insert an initial RUNNING row into gold_study_runs."""
    conn.execute(
        """
        INSERT INTO gold_study_runs (
            run_id, run_ts, product_codes, study_start_date, study_end_date,
            exposure_method, mortality_table, lapse_table, ci_table,
            credibility_method, data_snapshot_hash, config_hash, code_version,
            run_duration_sec, status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'RUNNING', NULL)
        """,
        [
            run_id,
            datetime.utcnow(),
            json.dumps(product_codes),
            str(start_date),
            str(end_date),
            exposure_method,
            Path(mortality_table).name,
            Path(lapse_table).name,
            Path(ci_table).name,
            credibility_method,
            "synthetic-seed42",
            "phase1a-v1",
            "1.0.0",
        ],
    )


def _finalise_study_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    duration_sec: float,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Update the study run row to COMPLETE or FAILED."""
    conn.execute(
        """
        UPDATE gold_study_runs
        SET status = ?, run_duration_sec = ?, error_message = ?
        WHERE run_id = ?
        """,
        [status, duration_sec, error_message, run_id],
    )


def _load_run_log() -> pd.DataFrame:
    """Load run log from gold_study_runs; fall back to gold_ae_results if empty."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        runs = conn.execute(
            """
            SELECT run_id, run_ts, product_codes, study_start_date, study_end_date,
                   exposure_method, credibility_method,
                   mortality_table, lapse_table, ci_table,
                   run_duration_sec, status
            FROM gold_study_runs
            ORDER BY run_ts DESC
            """
        ).df()

        if runs.empty:
            ae_runs = conn.execute(
                """
                SELECT DISTINCT
                    study_run_id AS run_id,
                    MIN(_created_ts) AS run_ts,
                    'TERM' AS product_codes,
                    'ANNUAL' AS exposure_method,
                    'LF' AS credibility_method,
                    'COMPLETE' AS status
                FROM gold_ae_results
                GROUP BY study_run_id
                ORDER BY run_ts DESC
                """
            ).df()
            return ae_runs

        return runs

    finally:
        conn.close()


def _run_headline(run_id: str) -> dict:
    """Load headline A/E metrics for a run."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = conn.execute(
            """
            SELECT SUM(actual_deaths_count) AS deaths,
                   SUM(expected_deaths_count) AS exp_deaths,
                   CASE WHEN SUM(expected_deaths_count)>0
                        THEN SUM(actual_deaths_count)/SUM(expected_deaths_count)
                        ELSE NULL END AS ae_count,
                   SUM(actual_lapses) AS lapses,
                   SUM(expected_lapses) AS exp_lapses,
                   SUM(actual_ci_claims) AS ci_claims
            FROM gold_ae_results
            WHERE study_run_id = ? AND illness_code IS NULL
            """,
            [run_id],
        ).fetchone()
        return {
            "deaths": int(row[0] or 0),
            "exp_deaths": float(row[1] or 0),
            "ae_count": float(row[2]) if row[2] is not None else None,
            "lapses": int(row[3] or 0),
            "ci_claims": int(row[5] or 0),
        }
    finally:
        conn.close()


def _run_data_summary(run_id: str) -> dict:
    """Load per-product exposure and DQ summary for a run."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        exposure_df = conn.execute(
            """
            SELECT product_code,
                   SUM(exposure_years)                                                     AS exposure_years,
                   COUNT(*)                                                                AS segments,
                   SUM(CASE WHEN decrement_type = 'DEATH'                THEN 1 ELSE 0 END) AS deaths,
                   SUM(CASE WHEN decrement_type IN ('LAPSE','PLT_LAPSE') THEN 1 ELSE 0 END) AS lapses,
                   SUM(CASE WHEN decrement_type = 'CI_CLAIM'             THEN 1 ELSE 0 END) AS ci_claims
            FROM gold_exposure_segments
            WHERE study_run_id = ?
            GROUP BY product_code
            ORDER BY product_code
            """,
            [run_id],
        ).df()

        dq_df = conn.execute(
            """
            SELECT product_code, dq_score_pct, records_quarantined, total_records
            FROM gold_dq_run_summary
            WHERE study_run_id = ?
            ORDER BY product_code
            """,
            [run_id],
        ).df()

        return {"exposure": exposure_df, "dq": dq_df}
    finally:
        conn.close()


# ── Load run log ──────────────────────────────────────────────────────────────

log_df = _load_run_log()

if log_df.empty:
    st.info("No study runs found. Run your first study from the Study Setup page.")
    st.stop()

st.subheader(f"All Study Runs ({len(log_df)})")

# Display log table — main columns only (reference tables shown in run detail)
display_df = log_df.copy()
if "run_ts" in display_df.columns:
    display_df["run_ts"] = pd.to_datetime(display_df["run_ts"]).dt.strftime("%Y-%m-%d %H:%M")
if "run_duration_sec" in display_df.columns:
    display_df["run_duration_sec"] = display_df["run_duration_sec"].map(
        lambda v: f"{v:.1f}s" if pd.notna(v) else "—"
    )
display_df["run_id_short"] = display_df["run_id"].str[:8].str.upper()

rename_map = {
    "run_id_short": "Run ID",
    "run_ts": "Timestamp",
    "product_codes": "Products",
    "study_start_date": "Study Start",
    "study_end_date": "Study End",
    "exposure_method": "Exposure Method",
    "credibility_method": "Credibility",
    "run_duration_sec": "Duration",
    "status": "Status",
}
cols_to_show = [c for c in rename_map if c in display_df.columns]
st.dataframe(
    display_df[cols_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True,
)

# ── Run detail / re-run ───────────────────────────────────────────────────────

st.divider()
st.subheader("Run Detail")

run_options = log_df["run_id"].tolist()


def _run_label(row: pd.Series) -> str:
    """Build a human-readable label from run metadata."""
    ts = str(row.get("run_ts", ""))[:16]
    try:
        prods = ", ".join(json.loads(row["product_codes"])) if row.get("product_codes") else "?"
    except Exception:
        prods = str(row.get("product_codes", "?"))
    return f"{ts} — {prods}" if ts else row["run_id"]


run_labels  = {row["run_id"]: _run_label(row) for _, row in log_df.iterrows()}
default_run = st.session_state.get("active_run_id", run_options[0])
if default_run not in run_options:
    default_run = run_options[0]

selected_run = st.selectbox(
    "Select run to inspect or re-run",
    options=run_options,
    index=run_options.index(default_run),
    format_func=lambda r: run_labels.get(r, r),
)

# Headline metrics
headline = _run_headline(selected_run)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Actual Deaths", f"{headline['deaths']:,}")
col2.metric("Expected Deaths", f"{headline['exp_deaths']:,.1f}")
col3.metric(
    "Mortality A/E",
    f"{headline['ae_count']:.3f}" if headline["ae_count"] is not None else "—",
)
col4.metric("CI Claims", f"{headline['ci_claims']:,}")

# Run parameters expander (reference tables + full run config)
selected_row = log_df[log_df["run_id"] == selected_run].iloc[0]
with st.expander("Run Parameters", expanded=False):
    params_data = {}
    for col, label in [
        ("study_start_date", "Study Start"),
        ("study_end_date", "Study End"),
        ("exposure_method", "Exposure Method"),
        ("credibility_method", "Credibility Method"),
        ("mortality_table", "Mortality Table"),
        ("lapse_table", "Lapse Table"),
        ("ci_table", "CI Incidence Table"),
    ]:
        if col in selected_row.index:
            params_data[label] = str(selected_row[col]) if pd.notna(selected_row[col]) else "—"
    params_df = pd.DataFrame({"Parameter": list(params_data.keys()), "Value": list(params_data.values())})
    st.dataframe(params_df, use_container_width=True, hide_index=True)

# Per-run data summary expander
with st.expander("Run Data Summary", expanded=False):
    summary = _run_data_summary(selected_run)
    exp_df = summary["exposure"]
    dq_df  = summary["dq"]

    if not exp_df.empty:
        st.markdown("**Exposure by Product**")
        exp_display = exp_df.rename(columns={
            "product_code":   "Product",
            "exposure_years": "Exposure Years",
            "segments":       "Segments",
            "deaths":         "Deaths",
            "lapses":         "Lapses",
            "ci_claims":      "CI Claims",
        })
        exp_disp = exp_display.copy()
        exp_disp["Exposure Years"] = exp_disp["Exposure Years"].apply(lambda v: f"{v:,.1f}" if pd.notna(v) else "")
        exp_disp["Segments"] = exp_disp["Segments"].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "")
        st.dataframe(exp_disp, use_container_width=True, hide_index=True)
    else:
        st.caption("No exposure data for this run.")

    if not dq_df.empty:
        st.markdown("**Data Quality by Product**")
        dq_display = dq_df.rename(columns={
            "product_code":        "Product",
            "dq_score_pct":        "DQ Score %",
            "records_quarantined": "Quarantined",
            "total_records":       "Total Records",
        })
        dq_display["DQ Score %"] = dq_display["DQ Score %"].map(lambda v: f"{v:.1f}%")
        st.dataframe(dq_display, use_container_width=True, hide_index=True)
    else:
        st.caption("No DQ summary data for this run.")

# Re-run button
st.caption(
    "Click Re-Run to replay the full pipeline with the same parameters "
    "as this run (useful for reproducing results after code changes)."
)
rerun_btn = st.button("Re-Run This Study", type="secondary")

if rerun_btn:
    # Load original run parameters from gold_study_runs
    try:
        conn_ro = duckdb.connect(str(DB_PATH), read_only=True)
        orig = conn_ro.execute(
            """
            SELECT product_codes, study_start_date, study_end_date,
                   exposure_method, credibility_method,
                   mortality_table, lapse_table, ci_table
            FROM gold_study_runs
            WHERE run_id = ?
            """,
            [selected_run],
        ).fetchone()
        conn_ro.close()
    except Exception as exc:
        st.error(f"Could not load original run parameters: {exc}")
        st.stop()

    if orig is None:
        st.error("Selected run not found in gold_study_runs — cannot re-run.")
        st.stop()

    orig_products_raw, orig_start, orig_end, orig_exp_method, orig_cred_method, \
        orig_mort, orig_lapse, orig_ci = orig

    # Parse product codes (stored as JSON array)
    try:
        orig_products: list[str] = json.loads(orig_products_raw)
    except Exception:
        orig_products = [str(orig_products_raw)]

    # Reconstruct full reference table paths from stored filenames
    def _resolve_table(filename: str, default: str) -> str:
        if not filename:
            return default
        candidate = REFERENCE_TABLES_DIR / filename
        return str(candidate) if candidate.exists() else default

    mort_path  = _resolve_table(orig_mort,  DEFAULT_MORTALITY_TABLE)
    lapse_path = _resolve_table(orig_lapse, DEFAULT_LAPSE_TABLE)
    ci_path    = _resolve_table(orig_ci,    DEFAULT_CI_TABLE)

    orig_start_date = date.fromisoformat(str(orig_start)[:10])
    orig_end_date   = date.fromisoformat(str(orig_end)[:10])

    new_run_id = str(uuid.uuid4())
    st.info(
        f"Re-running as new run `{new_run_id[:8]}...` — "
        f"products: {', '.join(orig_products)}"
    )

    n_products   = len(orig_products)
    total_steps  = n_products * 3 + 2  # ETL+DQ+Exposure per product, AE, finalise
    step         = 0
    prog         = st.progress(0, text="Initialising...")

    def _pct(s: int) -> int:
        return min(99, int(s / total_steps * 95))

    t0 = datetime.utcnow()

    try:
        init_database(DB_PATH)
        conn_rw = duckdb.connect(str(DB_PATH))
        _register_study_run(
            conn_rw, new_run_id, orig_products,
            orig_start_date, orig_end_date,
            orig_exp_method, mort_path, lapse_path, ci_path,
            orig_cred_method,
        )
        conn_rw.close()
        step += 1
        prog.progress(_pct(step), text="Database initialised.")

        study_config = StudyConfig(
            study_start_date=orig_start_date,
            study_end_date=orig_end_date,
            product_codes=orig_products,
            exposure_method=ExposureMethod(orig_exp_method),
            mortality_table_path=mort_path,
            lapse_table_path=lapse_path,
            ci_table_path=ci_path,
            credibility_method=CredibilityMethod(orig_cred_method),
        )

        status_box = st.empty()

        for product in orig_products:
            src_csv, mapping_yaml = _PRODUCT_ETL_CONFIG.get(product, (None, None))
            if src_csv is None:
                st.warning(f"No ETL config for {product}, skipping.")
                step += 3
                continue

            # ETL
            prog.progress(_pct(step), text=f"ETL — {product}...")
            etl_res = run_etl_pipeline(
                product_code=product,
                source_path=Path(src_csv),
                mapping_config_path=Path(mapping_yaml),
                db_path=DB_PATH,
                run_id=new_run_id,
            )
            if not etl_res.success:
                raise RuntimeError(f"ETL failed for {product}: {etl_res.warnings}")
            status_box.success(f"[{product}] ETL — {etl_res.records_ingested:,} ingested ({etl_res.duration_sec:.1f}s)")
            step += 1

            # DQ
            prog.progress(_pct(step), text=f"DQ checks — {product}...")
            try:
                run_dq_checks(product, DB_PATH, new_run_id, halt_on_critical=False)
            except DQCriticalFailure as exc:
                raise RuntimeError(f"[{product}] DQ critical failure: {exc}") from exc
            status_box.success(f"[{product}] DQ complete.")
            step += 1

            # Exposure
            prog.progress(_pct(step), text=f"Exposure — {product}...")
            exp_res = build_exposure_file(product, DB_PATH, study_config, new_run_id)
            status_box.success(
                f"[{product}] Exposure — {exp_res.total_segments:,} segments, "
                f"{exp_res.total_exposure_years:,.1f} exposure-years, "
                f"recon {'PASS' if exp_res.recon_passes else 'FAIL'} ({exp_res.duration_sec:.1f}s)"
            )
            step += 1

        # A/E (all products together)
        prog.progress(_pct(step), text="Calculating A/E ratios...")
        ae_res = calculate_ae(orig_products, DB_PATH, study_config, new_run_id)
        status_box.success(
            f"A/E complete — {ae_res.total_deaths} deaths, "
            f"A/E count {ae_res.total_ae_count:.3f} ({ae_res.duration_sec:.1f}s)"
        )
        step += 1

        duration = (datetime.utcnow() - t0).total_seconds()
        conn_rw = duckdb.connect(str(DB_PATH))
        _finalise_study_run(conn_rw, new_run_id, duration, "COMPLETE")
        conn_rw.close()

        prog.progress(100, text="Done!")
        st.success(
            f"Re-run complete in {duration:.1f}s. "
            f"New run ID: `{new_run_id[:8]}...` "
            f"Mortality A/E: {ae_res.total_ae_count:.3f}"
        )
        st.session_state["active_run_id"] = new_run_id
        st.rerun()

    except Exception as exc:
        duration = (datetime.utcnow() - t0).total_seconds()
        try:
            conn_rw = duckdb.connect(str(DB_PATH))
            _finalise_study_run(conn_rw, new_run_id, duration, "FAILED", str(exc))
            conn_rw.close()
        except Exception:
            pass
        prog.progress(100, text="Failed.")
        st.error(f"Re-run failed: {exc}")

# ── Report generation links ───────────────────────────────────────────────────

st.divider()
st.subheader("Reports")
st.markdown(
    "Use the buttons below to generate HTML reports for the selected run. "
    "Reports are written to the `reports/` directory."
)

col_r1, col_r2 = st.columns(2)
if col_r1.button("Generate Working Actuary Report"):
    try:
        from src.reporting.generator import generate_working_actuary_report
        from ui.config import REPORTS_DIR
        out_path = REPORTS_DIR / f"working_actuary_{selected_run[:8]}.html"
        generate_working_actuary_report(selected_run, DB_PATH, out_path)
        st.success(f"Report written to `{out_path}`")
    except Exception as exc:
        st.error(f"Report generation failed: {exc}")

if col_r2.button("Generate Chief Actuary Summary"):
    try:
        from src.reporting.generator import generate_chief_actuary_summary
        from ui.config import REPORTS_DIR
        out_path = REPORTS_DIR / f"chief_actuary_{selected_run[:8]}.html"
        generate_chief_actuary_summary(selected_run, DB_PATH, out_path)
        st.success(f"Report written to `{out_path}`")
    except Exception as exc:
        st.error(f"Report generation failed: {exc}")


# ---------------------------------------------------------------------------
# AI activity log (NFR-A-07): the gold_ai_audit_log written by the AI Analyst
# chatbot and the Skills, queryable here alongside the study run log.
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("AI Activity Log (chatbot & Skills)")
with st.expander("Recent AI turns (gold_ai_audit_log)", expanded=False):
    try:
        _con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            _audit_df = _con.execute(
                "SELECT entry_ts, source, session_id, turn_index, intent, "
                "model_string, blocked, block_reason, faithfulness_score, "
                "result_row_count, input_tokens, output_tokens, est_cost_usd "
                "FROM gold_ai_audit_log ORDER BY entry_ts DESC LIMIT 200"
            ).fetchdf()
        finally:
            _con.close()
        if _audit_df.empty:
            st.info("No AI activity recorded yet. Use the AI Analyst page.")
        else:
            st.caption(
                "Append-only per-turn audit (FR-3B-47). Every figure shown to a "
                "user was traceable to the data; blocked turns are recorded too."
            )
            st.dataframe(_audit_df, use_container_width=True, hide_index=True)
    except Exception:
        st.info(
            "AI activity log is unavailable for this database "
            "(no gold_ai_audit_log table yet — run an AI Analyst turn first)."
        )
