"""Stage 2 — Proposed Assumption Set Editor.

Editable assumption set pre-populated from the A/E study (FR-2-35).
Credibility bounds shown alongside each multiplier as guardrails.
Live ΔTEV preview panel in sidebar using pre-computed sensitivity approximation (FR-2-36).
"Restore from A/E" resets cells to credibility-weighted A/E values (FR-2-37).
Every save is logged to gold_workflow_iterations.
"""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import copy

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    load_assumption_set,
    save_assumption_set,
    create_assumption_set_from_ae_run,
    find_ai_proposal_for_set,
    record_ai_provenance,
)
from src.utils.types import AssumptionSetStatus
from src.tev.workflow import (
    log_workflow_iteration,
    get_next_iteration_number,
)

st.set_page_config(page_title="TEV Stage 2 — Propose Assumptions", layout="wide")

from ui.config import require_auth, user_can
from src.governance.rbac import Action, PermissionDenied, require
_user = require_auth()
_can_propose = user_can(_user, Action.PROPOSE)
st.title("Stage 2 — Proposed Assumption Set")

# ---------------------------------------------------------------------------
# Workflow progress indicator
# ---------------------------------------------------------------------------
cols_prog = st.columns(4)
cols_prog[0].success("Stage 1 — Experience Study ✓")
cols_prog[1].success("**Stage 2** — Assumptions ✓")  # shortened to prevent wrapping
cols_prog[2].info("Stage 3 — TEV Impact Analysis")
cols_prog[3].info("Stage 4 — Governance Sign-Off")

st.divider()

# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _require_assumption_set() -> Optional[str]:
    aset_id = st.session_state.get("active_assumption_set_id")
    if not aset_id:
        st.warning(
            "No active assumption set. Go to **Stage 1 — Experience Study** "
            "to create one, or resume an existing set."
        )
        return None
    return aset_id


def _load_aset(aset_id: str) -> Optional[AssumptionSet]:
    try:
        return load_assumption_set(aset_id, DB_PATH)
    except (ValueError, FileNotFoundError) as exc:
        st.error(f"Could not load assumption set: {exc}")
        return None


# ---------------------------------------------------------------------------
# Sidebar — selection and navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Assumption Set")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        all_sets = con.execute("""
            SELECT assumption_set_id, version, status, author_id, effective_date,
                   author_id || ' | v' || CAST(version AS VARCHAR)
                   || ' | eff. ' || CAST(effective_date AS VARCHAR)
                   || ' (' || status || ')' AS label
            FROM gold_assumption_sets
            ORDER BY created_ts DESC LIMIT 20
        """).df()
    finally:
        con.close()

    set_labels = ["(from session)"] + all_sets["label"].tolist()
    set_ids_map = {"(from session)": None}
    for _, row in all_sets.iterrows():
        set_ids_map[row["label"]] = row["assumption_set_id"]

    chosen_label = st.selectbox("Select assumption set", set_labels, key="s2_set_selector")
    chosen_id = set_ids_map.get(chosen_label)
    if chosen_id:
        st.session_state["active_assumption_set_id"] = chosen_id

    aset_id = st.session_state.get("active_assumption_set_id")
    if aset_id:
        match = all_sets[all_sets["assumption_set_id"] == aset_id]
        if not match.empty:
            r = match.iloc[0]
            st.caption(f"Active: {r['author_id']} | v{r['version']} | eff. {r['effective_date']}")
        else:
            st.caption(f"Active: `{aset_id[:12]}…`")

    st.divider()
    # Actor identity is the authenticated user (FR-4-03) — read-only, never free text,
    # so the workflow-iteration audit trail carries a real, referenceable account.
    actuary_id = _user.username
    st.text_input(
        "Your actuary ID",
        value=_user.username,
        disabled=True,
        key="s2_actuary_id",
        help="Captured from your signed-in account.",
    )

# ---------------------------------------------------------------------------
# Load assumption set
# ---------------------------------------------------------------------------
aset_id = _require_assumption_set()
if not aset_id:
    st.stop()

aset = _load_aset(aset_id)
if aset is None:
    st.stop()

# Restore version counter — incrementing forces data_editor widgets to fully reinitialise
_rv = st.session_state.get("restore_version", 0)

# Cache the original A/E-derived values for "Restore from A/E"
cache_key = f"aset_orig_{aset_id}"
if cache_key not in st.session_state:
    st.session_state[cache_key] = {
        "mortality_multipliers":    [m.to_dict() for m in aset.mortality_multipliers],
        "lapse_multipliers":        [m.to_dict() for m in aset.lapse_multipliers],
        "surrender_multipliers":    [m.to_dict() for m in aset.surrender_multipliers],
        "ci_incidence_multipliers": [m.to_dict() for m in aset.ci_incidence_multipliers],
    }

# Human-readable header — author, version, effective date
st.markdown(
    f"**Assumption set:** {aset.author_id} | v{aset.version} | "
    f"eff. {aset.effective_date} | Status: **{aset.status.value}**"
)
st.caption(f"UUID: `{aset_id}`")

# A completed (APPROVED) set is immutable — editing/saving it would silently unlock it
# (governance audit 2026-07-04). Block edits here; the governed way to change an approved
# set is to re-open a new DRAFT version on the Assumption Lineage page.
_is_locked = aset.status.value == "APPROVED"
if _is_locked:
    st.warning(
        "🔒 This assumption set is **APPROVED and locked**. It cannot be edited or "
        "re-saved. To make changes, re-open a new version on the **Assumption Lineage** "
        "page (which creates a DRAFT child); the approved version stays immutable."
    )

# ---------------------------------------------------------------------------
# Multiplier tables (FR-2-35)
# ---------------------------------------------------------------------------

def _fmt_band(b) -> str:
    """Format a duration_band list [lo, hi] as a readable string."""
    if isinstance(b, list) and len(b) == 2:
        return str(b[0]) if b[0] == b[1] else f"{b[0]}–{b[1]}"
    return str(b)


def _mults_to_df(mults: list[DecrementMultiplier]) -> pd.DataFrame:
    """Convert multipliers to an editable DataFrame.

    Adds a read-only 'ae_from_study' column by inverting the credibility blend:
        ae = (cw_ae - (1 - z)) / z   (exact when z > 0)
    Duration band is formatted as a plain string (e.g. "1", "2–5", "6–10").
    """
    if not mults:
        return pd.DataFrame(columns=[
            "product", "gender", "risk_class", "duration_band",
            "ae_from_study", "credibility_z", "credibility_lower", "credibility_upper",
            "multiplier", "override_rationale",
        ])
    rows = [m.to_dict() for m in mults]
    df = pd.DataFrame(rows)
    df["duration_band"] = df["duration_band"].apply(_fmt_band)

    def _ae_raw(r: dict) -> float:
        z, m = r["credibility_z"], r["multiplier"]
        return round((m - (1.0 - z)) / z, 6) if z > 0 else m

    df.insert(4, "ae_from_study", [_ae_raw(r) for r in rows])
    return df


def _df_to_mults(df: pd.DataFrame) -> list[DecrementMultiplier]:
    """Convert an edited DataFrame back to DecrementMultiplier list."""
    mults = []
    for _, row in df.iterrows():
        band = row["duration_band"]
        if isinstance(band, str):
            s = band.strip()
            if "–" in s:
                lo, hi = s.split("–")
                band = [int(lo), int(hi)]
            elif "[" in s:
                band = [int(x) for x in s.strip("[]").split(",")]
            else:
                v = int(s)
                band = [v, v]
        elif not isinstance(band, list):
            band = [int(band), int(band)]
        mults.append(DecrementMultiplier(
            product=str(row["product"]),
            gender=str(row["gender"]),
            risk_class=str(row["risk_class"]),
            duration_band=band,
            multiplier=float(row["multiplier"]),
            credibility_z=float(row["credibility_z"]),
            credibility_lower=float(row["credibility_lower"]),
            credibility_upper=float(row["credibility_upper"]),
            override_rationale=str(row.get("override_rationale", "")),
        ))
    return mults


_MULT_COLUMN_CONFIG = {
    "product":           st.column_config.TextColumn("Product", disabled=True),
    "gender":            st.column_config.TextColumn("Gender", disabled=True),
    "risk_class":        st.column_config.TextColumn("Risk Class", disabled=True),
    "duration_band":     st.column_config.TextColumn("Duration Band", disabled=True),
    "ae_from_study":     st.column_config.NumberColumn("A/E (from study)", format="%.4f", disabled=True),
    "credibility_z":     st.column_config.NumberColumn("Cred. Z", format="%.3f", disabled=True),
    "credibility_lower": st.column_config.NumberColumn("CI Lower", format="%.4f", disabled=True),
    "credibility_upper": st.column_config.NumberColumn("CI Upper", format="%.4f", disabled=True),
    "multiplier":        st.column_config.NumberColumn("Proposed Multiplier", format="%.4f", step=0.001),
    "override_rationale": st.column_config.TextColumn("Override Rationale"),
}

tab_mort, tab_lapse, tab_ci, tab_econ = st.tabs([
    "Mortality Multipliers",
    "Lapse Multipliers",
    "CI Incidence Multipliers",
    "Economic Parameters",
])

# --- Mortality ---
with tab_mort:
    st.markdown(
        "Edit **Proposed Multiplier** values. "
        "**A/E (from study)** is the raw experience ratio; "
        "the **CI bounds** are the 95% confidence interval — keep multipliers within these guardrails."
    )
    mort_df = _mults_to_df(aset.mortality_multipliers)
    if mort_df.empty:
        st.info("No mortality multiplier cells (no A/E data by segment).")
        edited_mort_df = mort_df
    else:
        edited_mort_df = st.data_editor(
            mort_df,
            key=f"edit_mort_{_rv}",
            use_container_width=True,
            num_rows="fixed",
            column_config=_MULT_COLUMN_CONFIG,
        )

# --- Lapse ---
with tab_lapse:
    st.markdown("Edit **lapse multipliers**. Bounds shown are from lapse A/E study.")
    lapse_df = _mults_to_df(aset.lapse_multipliers)
    if lapse_df.empty:
        st.info("No lapse multiplier cells.")
        edited_lapse_df = lapse_df
    else:
        edited_lapse_df = st.data_editor(
            lapse_df,
            key=f"edit_lapse_{_rv}",
            use_container_width=True,
            num_rows="fixed",
            column_config=_MULT_COLUMN_CONFIG,
        )

# --- CI Incidence ---
with tab_ci:
    st.markdown("Edit **CI incidence multipliers**. The `product` column holds the illness code.")
    ci_df = _mults_to_df(aset.ci_incidence_multipliers)
    if ci_df.empty:
        st.info("No CI incidence multiplier cells.")
        edited_ci_df = ci_df
    else:
        edited_ci_df = st.data_editor(
            ci_df,
            key=f"edit_ci_{_rv}",
            use_container_width=True,
            num_rows="fixed",
            column_config=_MULT_COLUMN_CONFIG,
        )

# --- Economic Parameters ---
with tab_econ:
    st.markdown("**Economic parameters** affect PVFP discounting and PVCoC calculation.")
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        new_rdr = st.number_input(
            "Risk Discount Rate (RDR)", value=aset.rdr,
            min_value=0.01, max_value=0.30, step=0.001, format="%.3f", key="s2_rdr"
        )
        new_earned_ga = st.number_input(
            "Earned Rate (General Account)", value=aset.earned_rate_ga,
            min_value=0.01, max_value=0.20, step=0.001, format="%.3f", key="s2_earned_ga"
        )
        new_earned_sa = st.number_input(
            "Earned Rate (Separate Account)", value=aset.earned_rate_sa,
            min_value=0.01, max_value=0.25, step=0.001, format="%.3f", key="s2_earned_sa"
        )
    with col_e2:
        new_tax = st.number_input(
            "Tax Rate", value=aset.tax_rate,
            min_value=0.0, max_value=0.50, step=0.005, format="%.3f", key="s2_tax"
        )
        new_exp_infl = st.number_input(
            "Expense Inflation", value=aset.expense_inflation,
            min_value=0.0, max_value=0.20, step=0.001, format="%.3f", key="s2_exp_infl"
        )
        new_maint_pp = st.number_input(
            "Maintenance per Policy ($)", value=aset.maintenance_per_policy,
            min_value=0.0, max_value=5000.0, step=1.0, format="%.0f", key="s2_maint_pp"
        )

    st.subheader("Required Capital (% of Reserve)")
    rc_cols = st.columns(3)
    rc_products = ["TERM", "WL", "UL", "ULSG", "VUL", "DA"]
    rc_vals: dict[str, float] = {}
    for i, prod in enumerate(rc_products):
        with rc_cols[i % 3]:
            rc_vals[prod] = st.number_input(
                f"RC % — {prod}",
                value=aset.rc_pct_reserve.get(prod, 0.04),
                min_value=0.0, max_value=0.30, step=0.005, format="%.3f",
                key=f"s2_rc_{prod}",
            )

# ---------------------------------------------------------------------------
# Live ΔTEV preview — sidebar (FR-2-36)
# Placed after tabs so edited_*_df variables are in scope.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120)
def _load_sensitivity_results(as_id: str) -> Optional[pd.DataFrame]:
    """Load most recent sensitivity results for this assumption set from DB."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = con.execute("""
            SELECT r.sensitivity_id, r.product_code, r.tev, l.total_tev,
                   r.tev_run_id
            FROM gold_tev_results r
            JOIN gold_tev_run_log l USING (tev_run_id)
            WHERE r.assumption_set_id = ?
              AND r.sensitivity_id IS NOT NULL
            ORDER BY l.run_ts DESC
            LIMIT 66
        """, [as_id]).df()
        return df if not df.empty else None
    finally:
        con.close()


@st.cache_data(ttl=120)
def _load_baseline_tev(as_id: str) -> Optional[float]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute("""
            SELECT total_tev FROM gold_tev_run_log
            WHERE assumption_set_id = ? AND sensitivity_id IS NULL
            ORDER BY run_ts DESC LIMIT 1
        """, [as_id]).fetchone()
        return float(row[0]) if row else None
    finally:
        con.close()


with st.sidebar:
    st.divider()
    st.markdown("**📊 ΔTEV Preview** _(approx.)_")

    sens_df = _load_sensitivity_results(aset_id)
    baseline_tev = _load_baseline_tev(aset_id)

    if baseline_tev is None:
        st.info("Available after first Stage 3 TEV run.")
    elif sens_df is None:
        st.info("Run sensitivity grid in Stage 3 to enable.")
    else:
        SENS_SHOCKS = {
            "SENS-01": ("lapse",          0.90),
            "SENS-02": ("lapse",          1.10),
            "SENS-03": ("mortality_life", 0.95),
            "SENS-04": ("mortality_life", 1.05),
            "SENS-06": ("ci_incidence",   0.90),
            "SENS-07": ("ci_incidence",   1.10),
        }
        total_row = sens_df.groupby("sensitivity_id")["total_tev"].first()

        orig = st.session_state.get(cache_key, {})

        def _mean_mult(mults_dicts: list) -> float:
            if not mults_dicts:
                return 1.0
            return sum(m["multiplier"] for m in mults_dicts) / len(mults_dicts)

        orig_mort  = _mean_mult(orig.get("mortality_multipliers", []))
        orig_lapse = _mean_mult(orig.get("lapse_multipliers", []))
        orig_ci    = _mean_mult(orig.get("ci_incidence_multipliers", []))

        cur_mort  = float(edited_mort_df["multiplier"].mean())  if not edited_mort_df.empty  else orig_mort
        cur_lapse = float(edited_lapse_df["multiplier"].mean()) if not edited_lapse_df.empty else orig_lapse
        cur_ci    = float(edited_ci_df["multiplier"].mean())    if not edited_ci_df.empty    else orig_ci

        approx_delta = 0.0
        for sens_id, (dec_type, shock) in SENS_SHOCKS.items():
            if sens_id not in total_row.index:
                continue
            delta_per_shock = float(total_row[sens_id]) - baseline_tev
            if dec_type == "lapse" and orig_lapse > 0:
                ratio_change = (cur_lapse - orig_lapse) / orig_lapse
            elif dec_type == "mortality_life" and orig_mort > 0:
                ratio_change = (cur_mort - orig_mort) / orig_mort
            elif dec_type == "ci_incidence" and orig_ci > 0:
                ratio_change = (cur_ci - orig_ci) / orig_ci
            else:
                ratio_change = 0.0
            if abs(shock - 1.0) > 0:
                approx_delta += delta_per_shock * (ratio_change / (shock - 1.0))

        rdr_change = new_rdr - aset.rdr
        for s_id in ["SENS-10", "SENS-11"]:
            if s_id in total_row.index:
                d = float(total_row[s_id]) - baseline_tev
                shock_sign = +1 if s_id == "SENS-10" else -1
                if abs(d) > 0:
                    approx_delta += d * (rdr_change / (shock_sign * 0.01))
                break

        st.metric("Baseline TEV", f"${baseline_tev:,.0f}")
        st.metric(
            "Approx ΔTEV",
            f"${approx_delta:+,.0f}",
            delta=f"${approx_delta:+,.0f}",
            delta_color="normal",
        )
        st.metric("Approx new TEV", f"${baseline_tev + approx_delta:,.0f}")
        st.caption("First-order approx. Run Stage 3 for exact result.")

# ---------------------------------------------------------------------------
# Action buttons (FR-2-35)
# ---------------------------------------------------------------------------
st.divider()

col_save, col_restore, col_space = st.columns([2, 2, 3])

with col_restore:
    if st.button("↺ Restore from A/E", use_container_width=True):
        # Increment restore_version to force data_editor widget reinstantiation
        st.session_state["restore_version"] = _rv + 1
        st.success("Multipliers restored from A/E values. Scroll up to review.")
        st.rerun()

with col_save:
    save_comment = st.text_input(
        "Save comment (required)",
        placeholder="Briefly describe changes from A/E values…",
        key="s2_save_comment",
    )
    save_clicked = st.button(
        "💾 Save as PROPOSED",
        type="primary",
        use_container_width=True,
        disabled=not save_comment.strip() or not _can_propose or _is_locked,
    )
    if _is_locked:
        st.caption("APPROVED sets are locked — re-open a new version to edit.")
    elif not _can_propose:
        st.caption(
            f"Your role ({_user.role.value}) cannot propose — "
            "sign in as an analyst to edit and save assumptions."
        )

# ---------------------------------------------------------------------------
# Adopt AI proposal — records set-level AI provenance (FR-3A-30 / §D.4)
# ---------------------------------------------------------------------------
# If a GLM proposal was fitted for this assumption set's source study run (on the
# Assumption Comparison page), the actuary may record that this edit adopts it.
# Adoption happens here — never on the read-only comparison page (FR-3A-44).
_ai_proposal = find_ai_proposal_for_set(DB_PATH, aset.source_study_run_id)
_adopt_ai = False
_adopted_value = None
if _ai_proposal is not None:
    with st.expander("🤖 Adopt AI proposal (records provenance)"):
        st.caption(
            f"A GLM proposal exists for this source study run "
            f"(model `{_ai_proposal['model_id'][:8]}…`, {_ai_proposal['decrement']} / "
            f"{_ai_proposal['product_code']}). Tick below and enter the adopted "
            "factor; on save, the AI-proposed value and model id are stamped onto "
            "this assumption set. The save comment above is the required justification. "
            "This **updates the assumption set you are editing in place** — it does "
            "not create a new set (new sets are minted only in Stage 1)."
        )
        _adopt_ai = st.checkbox("This save adopts the AI proposal", key="s2_adopt_ai")
        _adopted_value = st.number_input(
            "AI-proposed factor adopted", value=1.0, step=0.01, format="%.4f",
            key="s2_adopted_value", disabled=not _adopt_ai,
        )

def _validate_bounds(df: pd.DataFrame, label: str) -> list[str]:
    """Return error strings for any rows whose multiplier violates credibility bounds."""
    errors = []
    if df.empty:
        return errors
    for _, row in df.iterrows():
        z = float(row.get("credibility_z", 0))
        if z <= 0:
            continue
        mult = float(row["multiplier"])
        lo   = float(row["credibility_lower"])
        hi   = float(row["credibility_upper"])
        seg  = f"{row['product']} | {row['gender']} | {row['risk_class']} | {row['duration_band']}"
        if mult < lo:
            errors.append(
                f"**{label}** — {seg}: multiplier **{mult:.4f}** is below the "
                f"credibility lower bound of **{lo:.4f}**."
            )
        elif mult > hi:
            errors.append(
                f"**{label}** — {seg}: multiplier **{mult:.4f}** exceeds the "
                f"credibility upper bound of **{hi:.4f}**."
            )
    return errors


if save_clicked and save_comment.strip():
    if _is_locked:  # server-side re-check: never unlock an APPROVED set via save
        st.error(
            "This assumption set is APPROVED and locked; re-open a new version to edit it."
        )
        st.stop()
    try:
        require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
    except PermissionDenied as exc:
        st.error(str(exc))
        st.stop()
    try:
        new_mort_mults  = _df_to_mults(edited_mort_df)  if not edited_mort_df.empty  else aset.mortality_multipliers
        new_lapse_mults = _df_to_mults(edited_lapse_df) if not edited_lapse_df.empty else aset.lapse_multipliers
        new_ci_mults    = _df_to_mults(edited_ci_df)    if not edited_ci_df.empty    else aset.ci_incidence_multipliers
    except Exception as exc:
        st.error(f"Could not parse edited multipliers: {exc}")
        st.stop()

    bound_errors = (
        _validate_bounds(edited_mort_df,  "Mortality")
        + _validate_bounds(edited_lapse_df, "Lapse")
        + _validate_bounds(edited_ci_df,    "CI Incidence")
    )
    if bound_errors:
        st.error(
            "**Save blocked — credibility bound violations detected.** "
            "Correct the highlighted values before saving, or provide a rationale "
            "and contact your peer reviewer if an override is warranted.\n\n"
            + "\n\n".join(f"- {e}" for e in bound_errors)
        )
        st.stop()

    aset.mortality_multipliers    = new_mort_mults
    aset.lapse_multipliers        = new_lapse_mults
    aset.ci_incidence_multipliers = new_ci_mults
    aset.rdr                      = new_rdr
    aset.earned_rate_ga           = new_earned_ga
    aset.earned_rate_sa           = new_earned_sa
    aset.tax_rate                 = new_tax
    aset.expense_inflation        = new_exp_infl
    aset.maintenance_per_policy   = new_maint_pp
    aset.rc_pct_reserve           = rc_vals
    aset.status                   = AssumptionSetStatus.PROPOSED

    with st.spinner("Saving assumption set…"):
        try:
            save_assumption_set(aset, DB_PATH)
        except Exception as exc:
            st.error(f"Save failed: {exc}")
            st.stop()

    # Record AI provenance when this save adopts an AI proposal (FR-3A-30 / §D.4).
    if _adopt_ai and _ai_proposal is not None:
        try:
            record_ai_provenance(
                DB_PATH, aset.id, float(_adopted_value), _ai_proposal["model_id"]
            )
            st.caption(
                f"AI provenance recorded: proposed value {float(_adopted_value):.4f} "
                f"from model {_ai_proposal['model_id'][:8]}… — stamped onto the "
                "**existing** assumption set in place; no new set is created."
            )
        except Exception as exc:  # noqa: BLE001 — provenance is non-blocking
            st.warning(f"Could not record AI provenance: {exc}")

    wf_session = st.session_state.get("workflow_session_id", "UNKNOWN")
    iter_num   = get_next_iteration_number(DB_PATH, wf_session)
    log_workflow_iteration(
        db_path=DB_PATH,
        workflow_session_id=wf_session,
        iteration_number=iter_num,
        assumption_set_id=aset_id,
        stage=2,
        action="SAVED",
        actuary_id=actuary_id,
        actuary_comment=save_comment.strip(),
    )
    st.session_state["workflow_iteration"] = iter_num
    st.success(
        f"✅ Assumption set saved as PROPOSED (iteration {iter_num}). "
        "Proceed to **Stage 3 — TEV Impact Analysis**."
    )
    st.cache_data.clear()
