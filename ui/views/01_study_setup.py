"""Study Setup page — configure and run the full Phase 1A–1C pipeline (all five products)."""
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import streamlit as st

from ui.config import (
    DA_MAPPING_YAML,
    DA_SOURCE_CSV,
    DB_PATH,
    DEFAULT_CI_TABLE,
    DEFAULT_LAPSE_TABLE,
    DEFAULT_MORTALITY_TABLE,
    DEFAULT_STUDY_END,
    DEFAULT_STUDY_START,
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

# Mapping: product_code → (source_csv, mapping_yaml)
_PRODUCT_ETL_CONFIG: dict[str, tuple[str, str]] = {
    "TERM": (TERM_SOURCE_CSV, TERM_MAPPING_YAML),
    "WL":   (WL_SOURCE_CSV,   WL_MAPPING_YAML),
    "UL":   (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "ULSG": (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "VUL":  (VUL_SOURCE_CSV,  VUL_MAPPING_YAML),
    "DA":   (DA_SOURCE_CSV,   DA_MAPPING_YAML),
}

_ALL_PRODUCTS = ["TERM", "WL", "UL", "ULSG", "VUL", "DA"]
from src.calculation.ae_engine import calculate_ae
from src.data_quality.runner import DQCriticalFailure, run_dq_checks
from src.exposure.engine import build_exposure_file
from src.ingestion.pipeline import run_etl_pipeline
from src.utils.db_init import init_database
from src.utils.types import (
    CredibilityMethod,
    ExposureMethod,
    StudyConfig,
)

st.set_page_config(page_title="Study Setup", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Study Setup")
st.caption("Configure study parameters and run the full pipeline.")


def _available_ref_tables() -> list[str]:
    """Return list of Parquet files in the reference tables directory."""
    return sorted(str(p) for p in REFERENCE_TABLES_DIR.glob("*.parquet"))


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


# ── Study Parameters (main panel) ───────────────────────────────────────────

st.subheader("Study Parameters")

_col_dates, _col_products = st.columns([2, 3])
with _col_dates:
    st.markdown("**Study Period**")
    study_start = st.date_input(
        "Start date",
        value=date(2016, 1, 1),
        min_value=date(2016, 1, 1),
        max_value=date(2023, 1, 1),
    )
    study_end = st.date_input(
        "End date",
        value=date(2023, 12, 31),
        min_value=date(2016, 1, 1),
        max_value=date(2023, 12, 31),
    )

with _col_products:
    st.markdown("**Product Scope**")
    products = st.multiselect(
        "Products to include",
        options=_ALL_PRODUCTS,
        default=["TERM"],
        help="Select one or more products. Run Term Life first for a quick smoke-test.",
    )

_col_exp, _col_cred = st.columns(2)
with _col_exp:
    st.markdown("**Exposure Method**")
    exposure_method = st.radio(
        "Method",
        options=["ANNUAL", "DISTRIBUTED"],
        index=0,
        help="ANNUAL = Balducci (actuarial standard for mortality). DISTRIBUTED = UDD.",
        horizontal=True,
    )
with _col_cred:
    st.markdown("**Credibility Method**")
    credibility_method = st.radio(
        "Method",
        options=["LF", "BUHLMANN"],
        index=0,
        format_func=lambda x: "Limited Fluctuation (LF)" if x == "LF" else "Bühlmann",
        horizontal=True,
    )

with st.expander("Reference Tables", expanded=False):
    ref_tables = _available_ref_tables()
    _rc1, _rc2, _rc3 = st.columns(3)
    with _rc1:
        mortality_table = st.selectbox(
            "Mortality table",
            options=ref_tables,
            index=next(
                (i for i, p in enumerate(ref_tables) if "2015vbt" in p),
                next((i for i, p in enumerate(ref_tables) if "mortality" in p and "iar" not in p), 0),
            ),
            format_func=lambda p: Path(p).name,
            help="Use mortality_2015vbt.parquet for life products.",
        )
    with _rc2:
        lapse_table = st.selectbox(
            "Lapse table",
            options=ref_tables,
            index=next((i for i, p in enumerate(ref_tables) if "lapse" in p), 0),
            format_func=lambda p: Path(p).name,
        )
    with _rc3:
        ci_table = st.selectbox(
            "CI incidence table",
            options=ref_tables,
            index=next((i for i, p in enumerate(ref_tables) if "ci" in p), 0),
            format_func=lambda p: Path(p).name,
        )

st.divider()
st.subheader("Pipeline Steps (per product)")
st.markdown(
    "Each selected product runs: **ETL** (CSV → Bronze → Silver) → "
    "**DQ** (data quality checks) → **Exposure** (seriatim segments) → "
    "**A/E** (mortality, lapse, CI incidence ratios)."
)

run_btn = st.button("Run Study", type="primary", use_container_width=False)

if run_btn:
    if not products:
        st.error("Select at least one product.")
        st.stop()
    if study_start >= study_end:
        st.error("Study start must be before study end.")
        st.stop()

    run_id = str(uuid.uuid4())
    st.info(f"Starting study run `{run_id[:8]}...` — products: {', '.join(products)}")

    progress = st.progress(0, text="Initialising database...")
    status_box = st.empty()
    t0 = datetime.utcnow()

    n_products = len(products)
    # Progress bands: init=5%, per-product: ETL 20%, DQ 15%, Exposure 25%, then AE together 25%, finalise 10%
    steps_per_product = 3  # ETL, DQ, Exposure
    total_steps = n_products * steps_per_product + 2  # +2: init and AE
    step = 0

    def _pct(s: int) -> int:
        return min(99, int(s / total_steps * 95))

    try:
        # ── Init DB ──────────────────────────────────────────────────────
        init_database(DB_PATH)
        conn_rw = duckdb.connect(str(DB_PATH))
        _register_study_run(
            conn_rw,
            run_id,
            products,
            study_start,
            study_end,
            exposure_method,
            mortality_table,
            lapse_table,
            ci_table,
            credibility_method,
        )
        conn_rw.close()
        step += 1
        progress.progress(_pct(step), text="Database initialised.")

        study_config = StudyConfig(
            study_start_date=study_start,
            study_end_date=study_end,
            product_codes=products,
            exposure_method=ExposureMethod(exposure_method),
            mortality_table_path=mortality_table,
            lapse_table_path=lapse_table,
            ci_table_path=ci_table,
            credibility_method=CredibilityMethod(credibility_method),
        )

        # ── Per-product ETL → DQ → Exposure ──────────────────────────────
        for product in products:
            src_csv, mapping_yaml = _PRODUCT_ETL_CONFIG.get(product, (None, None))
            if src_csv is None:
                st.warning(f"No ETL config for product {product}, skipping.")
                continue

            # ETL
            progress.progress(_pct(step), text=f"ETL — {product}...")
            etl_result = run_etl_pipeline(
                product_code=product,
                source_path=Path(src_csv),
                mapping_config_path=Path(mapping_yaml),
                db_path=DB_PATH,
                run_id=run_id,
            )
            if not etl_result.success:
                raise RuntimeError(f"ETL failed for {product}: {etl_result.warnings}")
            status_box.success(
                f"[{product}] ETL — {etl_result.records_ingested:,} ingested, "
                f"{etl_result.records_conformed:,} conformed ({etl_result.duration_sec:.1f}s)"
            )
            step += 1

            # DQ
            progress.progress(_pct(step), text=f"DQ checks — {product}...")
            try:
                dq_result = run_dq_checks(
                    product_code=product,
                    db_path=DB_PATH,
                    study_run_id=run_id,
                    halt_on_critical=True,
                )
            except DQCriticalFailure as exc:
                raise RuntimeError(f"[{product}] DQ critical failure: {exc}") from exc
            icon = "⚠️" if dq_result.critical_failure else "✅"
            status_box.info(
                f"{icon} [{product}] DQ — score {dq_result.dq_score_pct:.1f}%, "
                f"{dq_result.records_quarantined} quarantined "
                f"({sum(1 for c in dq_result.check_results if c.passed)}/{len(dq_result.check_results)} checks passed)"
            )
            step += 1

            # Exposure
            progress.progress(_pct(step), text=f"Exposure — {product}...")
            exp_result = build_exposure_file(
                product_code=product,
                db_path=DB_PATH,
                study_config=study_config,
                study_run_id=run_id,
            )
            status_box.success(
                f"[{product}] Exposure — {exp_result.total_segments:,} segments, "
                f"{exp_result.total_exposure_years:,.1f} exposure-years, "
                f"recon {'PASS' if exp_result.recon_passes else 'FAIL'} "
                f"({exp_result.duration_sec:.1f}s)"
            )
            step += 1

        # ── A/E (all products together) ───────────────────────────────────
        progress.progress(_pct(step), text="Calculating A/E ratios (all products)...")
        ae_result = calculate_ae(
            product_codes=products,
            db_path=DB_PATH,
            study_config=study_config,
            study_run_id=run_id,
        )
        status_box.success(
            f"A/E complete — {ae_result.total_deaths} deaths, "
            f"A/E count {ae_result.total_ae_count:.3f}, "
            f"A/E amount {ae_result.total_ae_amount:.3f} "
            f"({ae_result.duration_sec:.1f}s)"
        )
        step += 1

        # ── Finalise ──────────────────────────────────────────────────────
        duration = (datetime.utcnow() - t0).total_seconds()
        conn_rw = duckdb.connect(str(DB_PATH))
        _finalise_study_run(conn_rw, run_id, duration, "COMPLETE")
        conn_rw.close()

        progress.progress(100, text="Done!")
        st.success(
            f"Study run `{run_id[:8]}...` completed in {duration:.1f}s. "
            f"Navigate to the A/E Explorer pages to review results."
        )
        st.session_state["active_run_id"] = run_id

    except Exception as exc:
        duration = (datetime.utcnow() - t0).total_seconds()
        try:
            conn_rw = duckdb.connect(str(DB_PATH))
            _finalise_study_run(conn_rw, run_id, duration, "FAILED", str(exc))
            conn_rw.close()
        except Exception:
            pass
        progress.progress(100, text="Failed.")
        st.error(f"Study run failed: {exc}")

# ── Current data summary ─────────────────────────────────────────────────────

st.divider()
st.subheader("Current Data Summary")

_SILVER_TABLES = {
    "TERM": "silver_term_policies",
    "WL": "silver_wl_policies",
    "UL": "silver_ul_policies",
    "ULSG": "silver_ul_policies",  # shares table with UL; filtered by product_code below
    "VUL": "silver_vul_policies",
    "DA": "silver_annuity_contracts",
}
_PK_COL = {
    "TERM": "policy_id", "WL": "policy_id", "UL": "policy_id",
    "ULSG": "policy_id", "VUL": "policy_id", "DA": "contract_id",
}
# Products whose silver table is shared — must filter by product_code when counting
_PRODUCT_CODE_FILTER = {"UL", "ULSG"}

try:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    run_ids_rows = conn.execute(
        "SELECT DISTINCT study_run_id FROM gold_ae_results"
    ).fetchall()

    counts: dict[str, int] = {}
    for prod, tbl in _SILVER_TABLES.items():
        pk = _PK_COL[prod]
        try:
            if prod in _PRODUCT_CODE_FILTER:
                n = conn.execute(
                    f"SELECT COUNT(DISTINCT {pk}) FROM {tbl} "
                    f"WHERE product_code = ? "
                    f"  AND _etl_run_id = ("
                    f"    SELECT _etl_run_id FROM {tbl} WHERE product_code = ? "
                    f"    GROUP BY _etl_run_id ORDER BY COUNT(*) DESC LIMIT 1"
                    f"  )",
                    [prod, prod],
                ).fetchone()[0]
            else:
                n = conn.execute(
                    f"SELECT COUNT(DISTINCT {pk}) FROM {tbl} "
                    f"WHERE _etl_run_id = ("
                    f"  SELECT _etl_run_id FROM {tbl} "
                    f"  GROUP BY _etl_run_id ORDER BY COUNT(*) DESC LIMIT 1"
                    f")"
                ).fetchone()[0]
            counts[prod] = n
        except Exception:
            counts[prod] = 0

    # Exposure years per product from the most recent completed run
    exposure_years: dict[str, float] = {}
    try:
        latest_run = conn.execute(
            """
            SELECT run_id FROM gold_study_runs
            WHERE status = 'COMPLETE'
            ORDER BY run_ts DESC LIMIT 1
            """
        ).fetchone()
        if latest_run:
            rows = conn.execute(
                """
                SELECT product_code, SUM(exposure_years)
                FROM gold_exposure_segments
                WHERE study_run_id = ?
                GROUP BY product_code
                """,
                [latest_run[0]],
            ).fetchall()
            exposure_years = {r[0]: r[1] for r in rows}
    except Exception:
        pass

    conn.close()

    cols = st.columns(len(_SILVER_TABLES) + 1)
    for i, (prod, cnt) in enumerate(counts.items()):
        exp_yrs = exposure_years.get(prod)
        exp_label = f"{exp_yrs:,.0f} exp-yrs" if exp_yrs else "—"
        cols[i].metric(f"{prod} records", f"{cnt:,}", delta=exp_label, delta_color="off")
    cols[-1].metric("Study runs", len(run_ids_rows))
except Exception as exc:
    st.warning(f"Could not read current data: {exc}")
