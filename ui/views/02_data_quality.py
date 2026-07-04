"""Data Quality Dashboard — DQ score, check grid, quarantine browser."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH
from src.data_quality.runner import override_quarantine_record
from src.governance.audit import record_ae_event
from src.governance.auth import current_user

st.set_page_config(page_title="Data Quality", layout="wide")

from ui.config import require_auth
_user = require_auth()
st.title("Data Quality Dashboard")


def _record_dq_override_event(quarantine_id: str, actuary_id: str, justification: str) -> None:
    """Record a DQ_OVERRIDE A/E governance event after a successful override (FR-4-19).

    Best-effort and fully guarded — the quarantine override itself has already
    committed, so a failure to log the governance event must never surface to the
    user or undo the override. The actor is the authenticated governance user when
    available, else the free-text actuary id from the form.
    """
    try:
        user = current_user()
        actor = user.user_id if user is not None else (actuary_id or "unknown")
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            row = conn.execute(
                "SELECT study_run_id, check_id FROM gold_dq_quarantine WHERE quarantine_id = ?",
                [quarantine_id],
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return
        run_id, check_id = row
        detail = f"quarantine {quarantine_id} (check {check_id}): {justification.strip()}"
        record_ae_event("DQ_OVERRIDE", run_id, actor, detail, db_path=DB_PATH)
    except Exception:
        pass


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for available study runs with DQ results.

    Label format: "YYYY-MM-DD HH:MM — <products>" derived from gold_study_runs.
    Falls back to the run_id string if the run row is not found.
    """
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT d.study_run_id,
                   r.run_ts,
                   r.product_codes
            FROM (SELECT DISTINCT study_run_id FROM gold_dq_run_summary) d
            LEFT JOIN gold_study_runs r ON r.run_id = d.study_run_id
            ORDER BY r.run_ts DESC NULLS LAST
            """
        ).fetchall()
        result = []
        for run_id, run_ts, product_codes in rows:
            if run_ts is not None:
                try:
                    import json as _json
                    products = ", ".join(_json.loads(product_codes)) if product_codes else "?"
                except Exception:
                    products = str(product_codes)
                ts_str = str(run_ts)[:16]  # "YYYY-MM-DD HH:MM"
                label = f"{ts_str} — {products}"
            else:
                label = run_id
            result.append((run_id, label))
        return result
    finally:
        conn.close()


def _load_dq_summary(run_id: str) -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT dq_run_id, product_code, total_records, records_passed,
                   records_quarantined, records_halted, dq_score_pct,
                   critical_failure, check_results
            FROM gold_dq_run_summary
            WHERE study_run_id = ?
            ORDER BY run_ts DESC
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


def _parse_check_results(check_results_json: str) -> pd.DataFrame:
    """Parse the JSON check_results field into a DataFrame."""
    try:
        checks = json.loads(check_results_json)
        return pd.DataFrame(checks)
    except Exception:
        return pd.DataFrame()


def _load_quarantine(run_id: str) -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT quarantine_id, policy_id, check_id, check_description,
                   failing_field, failing_value, quarantine_ts,
                   actuary_override_flag, override_ts, override_justification,
                   override_actuary_id
            FROM gold_dq_quarantine
            WHERE study_run_id = ?
            ORDER BY check_id, policy_id
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


# ── Run selector ─────────────────────────────────────────────────────────────

run_pairs = _load_run_ids()
if not run_pairs:
    st.info("No DQ results found. Run a study from the Study Setup page.")
    st.stop()

run_ids = [r[0] for r in run_pairs]
run_labels = {r[0]: r[1] for r in run_pairs}

default_run = st.session_state.get("active_run_id", run_ids[0])
if default_run not in run_ids:
    default_run = run_ids[0]

selected_run = st.selectbox(
    "Study run",
    options=run_ids,
    index=run_ids.index(default_run),
    format_func=lambda r: run_labels.get(r, r),
)

# ── DQ Summary ───────────────────────────────────────────────────────────────

summary_df = _load_dq_summary(selected_run)
if summary_df.empty:
    st.warning("No DQ summary found for this run.")
    st.stop()

# Aggregate across all products in the run
total_records = int(summary_df["total_records"].sum())
total_passed = int(summary_df["records_passed"].sum())
total_quarantined = int(summary_df["records_quarantined"].sum())
critical = bool(summary_df["critical_failure"].any())
dq_score = (total_passed / total_records * 100.0) if total_records > 0 else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("DQ Score", f"{dq_score:.2f}%", delta=None)
col2.metric("Total Records", f"{total_records:,}")
col3.metric("Passed", f"{total_passed:,}")
col4.metric(
    "Quarantined",
    f"{total_quarantined:,}",
    delta=None,
    delta_color="inverse",
)

if critical:
    st.error("Critical DQ failure detected — pipeline was halted.")
elif total_quarantined == 0 and not critical:
    st.success(f"DQ score: {dq_score:.2f}% — no quarantined records.")
else:
    st.warning(f"DQ score: {dq_score:.2f}% — {total_quarantined:,} records quarantined.")

# ── DQ gauge chart ───────────────────────────────────────────────────────────

import plotly.graph_objects as go

fig_gauge = go.Figure(
    go.Indicator(
        mode="gauge+number",
        value=dq_score,
        number={"suffix": "%", "font": {"size": 28}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "red" if critical else ("#2ecc71" if dq_score >= 99 else "#e67e22")},
            "steps": [
                {"range": [0, 90], "color": "#fdecea"},
                {"range": [90, 99], "color": "#fef9e7"},
                {"range": [99, 100], "color": "#eafaf1"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 4},
                "thickness": 0.75,
                "value": 99,
            },
        },
        title={"text": "DQ Score"},
    )
)
fig_gauge.update_layout(height=250, margin=dict(t=40, b=10))
st.plotly_chart(fig_gauge, use_container_width=False)

# ── Check-by-check results ────────────────────────────────────────────────────

_SEVERITY_LABELS = {
    "ERROR_HALT": "🔴 Critical (Halt)",
    "ERROR":      "🟠 Error",
    "WARN":       "🟡 Warning",
}

st.subheader("Check Results")
for _, prod_row in summary_df.iterrows():
    product = prod_row["product_code"]
    prod_score = float(prod_row["dq_score_pct"])
    prod_quarantined = int(prod_row["records_quarantined"])
    prod_critical = bool(prod_row.get("critical_failure", False))
    if prod_critical:
        status_icon = "🛑"
    elif prod_quarantined > 0:
        status_icon = "⚠️"
    else:
        status_icon = "✅"
    label = f"{status_icon} {product} — {int(prod_row['total_records']):,} records, score {prod_score:.1f}%"

    with st.expander(label, expanded=(prod_quarantined > 0 or prod_critical)):
        checks_df = _parse_check_results(str(prod_row["check_results"]))
        if not checks_df.empty:
            checks_df["Status"] = checks_df["status"].map(
                {"PASS": "✅ Pass", "FAIL": "❌ Fail"}
            ).fillna("—")
            checks_df["Severity"] = checks_df["severity"].map(_SEVERITY_LABELS).fillna(
                checks_df["severity"]
            )
            checks_df["Fail Count"] = checks_df.apply(
                lambda r: "—" if r["status"] == "PASS" else str(int(r["fail_count"])), axis=1
            )
            display_cols = ["check_id", "description", "Severity", "Status", "Fail Count"]
            available_cols = [c for c in display_cols if c in checks_df.columns]
            st.dataframe(
                checks_df[available_cols].rename(
                    columns={"check_id": "Check ID", "description": "Description"}
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No check-level detail available.")

# ── Quarantine browser ────────────────────────────────────────────────────────

st.subheader("Quarantine Record Browser")
quarantine_df = _load_quarantine(selected_run)

if quarantine_df.empty:
    st.success("No quarantined records for this run.")
else:
    st.info(f"{len(quarantine_df):,} quarantined records.")

    filter_check = st.selectbox(
        "Filter by check ID",
        options=["All"] + sorted(quarantine_df["check_id"].unique().tolist()),
    )
    show_df = (
        quarantine_df
        if filter_check == "All"
        else quarantine_df[quarantine_df["check_id"] == filter_check]
    )

    st.dataframe(
        show_df[
            ["quarantine_id", "policy_id", "check_id", "check_description",
             "failing_field", "failing_value", "actuary_override_flag",
             "override_justification"]
        ],
        use_container_width=True,
        hide_index=True,
    )

    # Override action
    st.subheader("Override and Include Record")
    with st.form("override_form"):
        quarantine_id = st.text_input("Quarantine ID (from table above)")
        # Override actor is the authenticated user (FR-4-03), not free text — the
        # override is a governance action written to gold_dq_quarantine.override_actuary_id.
        actuary_id = _user.username
        st.text_input(
            "Actuary ID", value=_user.username, disabled=True,
            help="Captured from your signed-in account.",
        )
        justification = st.text_area(
            "Justification (required)",
            placeholder="Explain why this record should be included despite the DQ failure.",
        )
        submitted = st.form_submit_button("Apply Override")
        if submitted:
            if not quarantine_id or not justification:
                st.error("Quarantine ID and justification are required.")
            else:
                ok = override_quarantine_record(
                    quarantine_id=quarantine_id,
                    actuary_id=actuary_id,
                    justification=justification,
                    db_path=DB_PATH,
                )
                if ok:
                    st.success("Override recorded. Re-run the study to include this record.")
                    _record_dq_override_event(quarantine_id, actuary_id, justification)
                else:
                    st.error("Quarantine ID not found.")
