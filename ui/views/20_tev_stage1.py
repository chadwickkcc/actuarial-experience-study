"""Stage 1 — Experience Study Summary.

Read-only view of A/E results from the most recent study run.
The actuary selects a study run as the basis for a new assumption set,
then clicks "Create Proposed Assumption Set" to proceed to Stage 2.

Implements FR-2-34 (Stage 1) and feeds into FR-2-35 (Stage 2).
"""
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from ui.stats_helpers import (
    credibility_z,
    poisson_ci,
    credibility_weighted_ae,
    get_run_method,
)
from src.tev.assumption_set import create_assumption_set_from_ae_run

st.set_page_config(page_title="TEV Stage 1 — Experience Study", layout="wide")

from ui.config import require_auth, user_can
from src.governance.rbac import Action, PermissionDenied, require
_user = require_auth()
_can_propose = user_can(_user, Action.PROPOSE)
st.title("Stage 1 — Experience Study")
st.markdown(
    "**Read-only.** Select a completed study run as the basis for a new assumption set. "
    "Review credibility-weighted A/E ratios and 95% confidence intervals, then click "
    "**Create Proposed Assumption Set** to proceed to Stage 2."
)

# ---------------------------------------------------------------------------
# Workflow progress indicator
# ---------------------------------------------------------------------------
cols_prog = st.columns(4)
cols_prog[0].success("**Stage 1** — Experience Study ✓")
cols_prog[1].info("Stage 2 — Propose Assumptions")
cols_prog[2].info("Stage 3 — TEV Impact Analysis")
cols_prog[3].info("Stage 4 — Governance Sign-Off")

st.divider()

# ---------------------------------------------------------------------------
# Load available study runs
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _load_study_runs() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute("""
            SELECT run_id, run_ts, product_codes, study_start_date, study_end_date,
                   status, run_duration_sec
            FROM gold_study_runs
            WHERE status = 'COMPLETE'
            ORDER BY run_ts DESC
        """).df()
    finally:
        con.close()


@st.cache_data(ttl=30)
def _load_ae_summary(run_id: str) -> pd.DataFrame:
    """Load aggregate A/E by product and decrement type for a study run."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = con.execute("""
            SELECT
                COALESCE(product_code, 'ALL') AS product,
                SUM(actual_deaths_count)         AS actual_deaths,
                SUM(expected_deaths_count)        AS expected_deaths,
                CASE WHEN SUM(expected_deaths_count) > 0
                     THEN SUM(actual_deaths_count)::DOUBLE / SUM(expected_deaths_count)
                     ELSE NULL END                AS ae_mortality,
                SUM(actual_lapses)                AS actual_lapses,
                SUM(expected_lapses)              AS expected_lapses,
                CASE WHEN SUM(expected_lapses) > 0
                     THEN SUM(actual_lapses)::DOUBLE / SUM(expected_lapses)
                     ELSE NULL END                AS ae_lapse,
                SUM(actual_ci_claims)             AS actual_ci,
                SUM(expected_ci_claims)           AS expected_ci,
                CASE WHEN SUM(expected_ci_claims) > 0
                     THEN SUM(actual_ci_claims)::DOUBLE / SUM(expected_ci_claims)
                     ELSE NULL END                AS ae_ci
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND illness_code IS NULL
              AND product_code IS NOT NULL
            GROUP BY product_code
            ORDER BY product_code
        """, [run_id]).df()
        # Credibility Z and 95% Poisson CI must be recomputed from the *aggregate*
        # death count, not averaged from per-cell values (FR-1A-24, FR-1A-25).
        method = get_run_method(con, run_id)
        df["avg_cred_z"] = credibility_z(df["actual_deaths"], method=method)
        ci_lo, ci_hi = poisson_ci(df["ae_mortality"], df["actual_deaths"])
        df["ci_lower"] = ci_lo
        df["ci_upper"] = ci_hi
        df["cred_wtd_ae"] = credibility_weighted_ae(df["ae_mortality"], df["avg_cred_z"])
        return df
    finally:
        con.close()


@st.cache_data(ttl=30)
def _run_credibility_method(run_id: str) -> str:
    """Return the credibility method ('LF' or 'BUHLMANN') selected for a run."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return get_run_method(con, run_id)
    finally:
        con.close()


@st.cache_data(ttl=30)
def _load_prior_assumption_set(run_id: str) -> Optional[dict]:
    """Find the most recently approved assumption set linked to a prior study run."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute("""
            SELECT assumption_set_id, version, status, effective_date, author_id
            FROM gold_assumption_sets
            WHERE status IN ('APPROVED', 'PROPOSED')
            ORDER BY created_ts DESC
            LIMIT 1
        """).fetchone()
        if row:
            return dict(zip(["id", "version", "status", "effective_date", "author_id"], row))
        return None
    finally:
        con.close()


runs_df = _load_study_runs()
if runs_df.empty:
    st.warning("No completed study runs found. Run a study first from **Study Setup**.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar — run selection
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Study Run Selection")
    run_options = {
        f"{r['run_id'][:8]}… ({str(r['run_ts'])[:16]})": r["run_id"]
        for _, r in runs_df.iterrows()
    }
    selected_label = st.selectbox("Select study run", list(run_options.keys()))
    selected_run_id = run_options[selected_label]

    st.markdown("---")
    # Proposer identity is the authenticated user, NOT a free-text field (FR-4-03).
    # This is what gold_assumption_sets.author_id records, and what check_segregation
    # compares against the signer's username to enforce proposer != approver (FR-4-05).
    author_id = _user.username
    st.text_input(
        "Your actuary ID",
        value=_user.username,
        disabled=True,
        key="s1_author_id",
        help="Captured from your signed-in account; used for segregation of duties.",
    )

    st.markdown("---")
    st.caption(f"Run ID: `{selected_run_id}`")

# ---------------------------------------------------------------------------
# A/E Summary Table
# ---------------------------------------------------------------------------
st.subheader("Credibility-Weighted A/E Ratios")

# Study metadata line
selected_run_row = runs_df[runs_df["run_id"] == selected_run_id].iloc[0]
_start = str(selected_run_row.get("study_start_date", ""))[:10]
_end = str(selected_run_row.get("study_end_date", ""))[:10]
_products = selected_run_row.get("product_codes", "")
_run_ts = str(selected_run_row.get("run_ts", ""))[:16]
st.markdown(
    f"**Study period:** {_start} → {_end} &nbsp;|&nbsp; "
    f"**Products:** {_products} &nbsp;|&nbsp; "
    f"**Run:** {_run_ts}"
)

ae_df = _load_ae_summary(selected_run_id)

if ae_df.empty:
    st.warning("No A/E results for this run. The study may not have produced results.")
    st.stop()

# Format for display
display_df = ae_df.copy()
for col in ["ae_mortality", "ae_lapse", "ae_ci", "cred_wtd_ae"]:
    if col in display_df.columns:
        display_df[col] = display_df[col].map(lambda x: f"{x:.4f}" if x is not None and pd.notna(x) else "—")
for col in ["avg_cred_z"]:
    if col in display_df.columns:
        display_df[col] = display_df[col].map(lambda x: f"{x:.3f}" if x is not None and pd.notna(x) else "—")
for col in ["ci_lower", "ci_upper"]:
    if col in display_df.columns:
        display_df[col] = display_df[col].map(lambda x: f"{x:.4f}" if x is not None and pd.notna(x) else "—")

rename_map = {
    "product": "Product",
    "actual_deaths": "Actual Deaths",
    "expected_deaths": "Expected Deaths",
    "ae_mortality": "A/E Mortality",
    "avg_cred_z": "Credibility Z",
    "ci_lower": "95% CI Lower",
    "ci_upper": "95% CI Upper",
    "actual_lapses": "Actual Lapses",
    "expected_lapses": "Expected Lapses",
    "ae_lapse": "A/E Lapse",
    "actual_ci": "Actual CI Claims",
    "expected_ci": "Expected CI Claims",
    "ae_ci": "A/E CI Incidence",
    "cred_wtd_ae": "Cred-Wtd A/E",
}
display_df = display_df.rename(columns=rename_map)

# Flag low-credibility cells visually
st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
)

_cred_method = _run_credibility_method(selected_run_id)
_cred_label = "Bühlmann" if _cred_method.strip().upper() == "BUHLMANN" else "Limited Fluctuation"
st.caption(
    "⚠️ Low credibility (Z < 0.5) cells are indicated in the Credibility Z column. "
    f"All A/E ratios use {_cred_label} credibility. 95% CI uses Poisson formula."
)

# ---------------------------------------------------------------------------
# Comparison vs prior assumption set
# ---------------------------------------------------------------------------
prior_as = _load_prior_assumption_set(selected_run_id)
with st.expander("Compare with prior assumption set", expanded=False):
    if prior_as:
        st.write(f"**Prior assumption set:** `{prior_as['id'][:8]}…`  "
                 f"v{prior_as['version']} — {prior_as['status']} — "
                 f"Author: {prior_as['author_id']} — Effective: {prior_as['effective_date']}")
        st.info(
            "The multipliers below will be pre-populated from the A/E study. "
            "You can compare them with the prior set in Stage 2."
        )
    else:
        st.info(
            "No prior approved assumption set found. This is the first run — "
            "the comparison column will be available once an assumption set has "
            "been approved through Stage 4."
        )

# ---------------------------------------------------------------------------
# Create Proposed Assumption Set
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Create Proposed Assumption Set")
st.markdown(
    "Click the button below to create a new **PROPOSED** assumption set "
    "pre-populated with the credibility-weighted A/E ratios from this study run. "
    "You will be taken to Stage 2 to review and edit before running the TEV."
)

col_btn, col_status = st.columns([2, 3])
with col_btn:
    create_clicked = st.button(
        "Create Proposed Assumption Set →",
        type="primary",
        use_container_width=True,
        disabled=not _can_propose,
    )
    if not _can_propose:
        st.caption(
            f"Your role ({_user.role.value}) cannot propose — "
            "sign in as an analyst to create assumption sets."
        )

if create_clicked:
    try:
        require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
    except PermissionDenied as exc:
        st.error(str(exc))
        st.stop()
    tev_config_path = CONFIG_DIR / "tev_config.yaml"
    if not tev_config_path.exists():
        st.error(f"TEV config not found: {tev_config_path}")
        st.stop()

    output_yaml_dir = DB_PATH.parent / "assumption_sets"
    output_yaml_dir.mkdir(parents=True, exist_ok=True)

    with st.spinner("Building assumption set from A/E results…"):
        try:
            aset = create_assumption_set_from_ae_run(
                study_run_id=selected_run_id,
                author_id=author_id,
                db_path=DB_PATH,
                tev_config_path=tev_config_path,
                output_yaml_dir=output_yaml_dir,
            )
        except Exception as exc:
            st.error(f"Failed to create assumption set: {exc}")
            st.stop()

    # Store in session state for Stage 2
    st.session_state["active_assumption_set_id"] = aset.id
    st.session_state["source_study_run_id"] = selected_run_id
    st.session_state["workflow_session_id"] = str(uuid.uuid4())
    st.session_state["workflow_author_id"] = author_id
    st.session_state["workflow_iteration"] = 0
    st.session_state["stage3_approved"] = False
    st.session_state["s3_envelope_run"] = False
    st.session_state["s3_envelope_tev_min"] = None
    st.session_state["s3_envelope_tev_max"] = None
    st.session_state["s3_envelope_percentile"] = None

    readable_label = f"{aset.author_id} | v{aset.version} | eff. {aset.effective_date}"
    st.success(
        f"✅ Assumption set created: **{readable_label}**\n\n"
        f"Mortality multipliers: {len(aset.mortality_multipliers)} cells  |  "
        f"Lapse multipliers: {len(aset.lapse_multipliers)} cells  |  "
        f"CI multipliers: {len(aset.ci_incidence_multipliers)} cells"
    )
    st.caption(f"UUID: `{aset.id}`")
    st.info("Proceed to **Stage 2 — Propose Assumptions** in the sidebar to review and edit.")

# ---------------------------------------------------------------------------
# Existing assumption sets (resume workflow)
# ---------------------------------------------------------------------------
with st.expander("Resume an existing workflow (select assumption set)", expanded=False):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        existing_sets = con.execute("""
            SELECT assumption_set_id, version, status, author_id, effective_date, created_ts
            FROM gold_assumption_sets
            ORDER BY created_ts DESC
            LIMIT 20
        """).df()
    finally:
        con.close()

    if existing_sets.empty:
        st.caption("No assumption sets found.")
    else:
        st.dataframe(existing_sets, hide_index=True, use_container_width=True)
        sel_id = st.text_input("Paste assumption set ID to resume:")
        if st.button("Resume this assumption set") and sel_id.strip():
            _resume_id = sel_id.strip()
            # Preserve the resumed set's ORIGINAL author for proposer attribution
            # (Stage-4 "Proposer" + the legacy approval summary) — do NOT overwrite it
            # with the current user, who may merely be continuing someone else's work.
            # Segregation is unaffected (it reads gold_assumption_sets.author_id directly).
            _match = existing_sets[existing_sets["assumption_set_id"] == _resume_id]
            _resumed_author = (
                str(_match.iloc[0]["author_id"]) if not _match.empty else author_id
            )
            st.session_state["active_assumption_set_id"] = _resume_id
            st.session_state["workflow_session_id"] = str(uuid.uuid4())
            st.session_state["workflow_author_id"] = _resumed_author
            st.session_state["workflow_iteration"] = 0
            st.session_state["stage3_approved"] = False
            st.session_state["s3_envelope_run"] = False
            st.session_state["s3_envelope_tev_min"] = None
            st.session_state["s3_envelope_tev_max"] = None
            st.session_state["s3_envelope_percentile"] = None
            st.success(f"Resumed. Proceed to Stage 2.")
