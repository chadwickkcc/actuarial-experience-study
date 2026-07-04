"""Stage 4 — Governance Sign-Off.

Presents the TEV Impact Report to a reviewer (different from the proposer),
captures the governance decision (APPROVE or RETURN TO STAGE 2), and records
the immutable approval record.

Implements FR-2-42 to FR-2-46.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR, REPORTS_DIR
from ui import skills_logic as skills
from src.ai.llm.base import LLMProviderError
from src.ai.llm.client import load_llm_config
from src.ai.skills.memo import interpret_ae_and_draft_memo
import yaml

from src.utils.types import ArtifactType, Decision, DecrementType
from src.tev.assumption_set import load_assumption_set
from src.tev.workflow import (
    get_workflow_iterations,
)
from src.reporting.generator import generate_tev_impact_report

# Phase 4 — configurable approval chain (Session 25, FR-4-12..18).
from src.governance.auth import current_user
from src.governance.rbac import PermissionDenied, may_sign_off_at
from src.governance.workflow import (
    SegregationViolation,
    check_segregation,
    load_chain,
    next_required_level,
    pending_approvals,
    record_signoff,
)

st.set_page_config(page_title="TEV Stage 4 — Governance Sign-Off", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Stage 4 — Governance Sign-Off")
st.markdown(
    "This stage is **locked until Stage 3 is approved**. "
    "The reviewer must be a different actuary from the proposer. "
    "An **APPROVE** decision locks the assumption set permanently. "
    "A **RETURN** decision sends the workflow back to Stage 2."
)

# ---------------------------------------------------------------------------
# Workflow progress indicator
# ---------------------------------------------------------------------------
cols_prog = st.columns(4)
cols_prog[0].success("Stage 1 — Experience Study ✓")
cols_prog[1].success("Stage 2 — Propose Assumptions ✓")
cols_prog[2].success("Stage 3 — TEV Impact Analysis ✓")
cols_prog[3].info("**Stage 4** — Governance Sign-Off")

st.divider()

# ---------------------------------------------------------------------------
# Session state guard + DB status check
# ---------------------------------------------------------------------------
aset_id = st.session_state.get("active_assumption_set_id")
if not aset_id:
    st.warning(
        "No active assumption set found. "
        "Start from **Stage 1** to create or resume a workflow."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Load assumption set for display
# ---------------------------------------------------------------------------
@st.cache_data(ttl=0)
def _load_aset(aset_id: str):
    return load_assumption_set(aset_id, DB_PATH)


@st.cache_data(ttl=0)
def _load_run_meta(study_run_id: str, tev_run_id_: str):
    """Fetch human-readable labels for study run and TEV run."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        sr = con.execute(
            "SELECT run_ts, product_codes FROM gold_study_runs WHERE run_id = ?",
            [study_run_id],
        ).fetchone()
        tr = con.execute(
            "SELECT run_ts FROM gold_tev_run_log WHERE tev_run_id = ?",
            [tev_run_id_],
        ).fetchone()
    finally:
        con.close()
    return sr, tr


aset = _load_aset(aset_id)

# Reject sets that are not yet at Stage 3 approval (or beyond).
if aset.status not in ("STAGE3_APPROVED", "APPROVED"):
    st.warning(
        f"Assumption set status is **{aset.status}** — expected STAGE3_APPROVED. "
        "Return to Stage 3 and approve before proceeding."
    )
    st.stop()

# For sets that are not yet fully APPROVED, require the session-state flag
# set by Stage 3's approve button. This prevents direct URL navigation to
# Stage 4 mid-workflow. Already-APPROVED sets bypass this gate because
# session state is cleared after approval and the set is permanently locked.
if aset.status != "APPROVED":
    stage3_approved = st.session_state.get("stage3_approved", False)
    if not stage3_approved:
        st.warning(
            "Stage 3 has not been approved yet for this session. "
            "Complete Stage 3 and click **Approve and proceed to Stage 4** before continuing."
        )
        st.stop()

workflow_session_id = st.session_state.get("workflow_session_id", str(uuid.uuid4()))
# The proposer is the set's recorded author (FR-4-03) — never the current signer and
# never the "ACTUARY_1" placeholder. Prefer the session value, else the persisted
# author_id so segregation and the legacy summary attribute the real proposer.
proposer_id = (
    st.session_state.get("workflow_author_id")
    or getattr(aset, "author_id", None)
    or "UNKNOWN"
)
tev_run_id = st.session_state.get("s3_tev_run_id", "")
total_tev = st.session_state.get("s3_total_tev", 0.0)
delta_tev = st.session_state.get("s3_delta_tev")
max_sensitivity_delta = st.session_state.get("s3_max_sensitivity_delta")
source_study_run_id = st.session_state.get("source_study_run_id", "")
envelope_run = st.session_state.get("s3_envelope_run", False)
envelope_tev_min = st.session_state.get("s3_envelope_tev_min")
envelope_tev_max = st.session_state.get("s3_envelope_tev_max")
envelope_percentile = st.session_state.get("s3_envelope_percentile")
env_res_obj = st.session_state.get("s3_env_result")

if aset.status == "APPROVED":
    st.success(
        f"✅ This assumption set is already **APPROVED**. "
        f"No further action required."
    )
    st.divider()

# ---------------------------------------------------------------------------
# Summary panel
# ---------------------------------------------------------------------------
st.subheader("Assumption Set Summary")
info_col1, info_col2, info_col3, info_col4 = st.columns(4)
aset_label = f"v{aset.version} ({str(aset.effective_date)[:10]}, {proposer_id})"
info_col1.metric("Assumption Set", aset_label)
info_col2.metric("Proposer", proposer_id)
info_col3.metric("Baseline TEV", f"${total_tev:,.0f}")
if delta_tev is not None:
    info_col4.metric("ΔTEV vs prior", f"${delta_tev:+,.0f}")
else:
    info_col4.metric("ΔTEV vs prior", "N/A (first run)")

sr_meta, tr_meta = _load_run_meta(source_study_run_id, tev_run_id)
if sr_meta:
    sr_label = f"{sr_meta[1]} @ {str(sr_meta[0])[:10]}"
elif source_study_run_id:
    sr_label = f"(metadata not found — id `{source_study_run_id[:8]}…`)"
else:
    sr_label = "—"

if tr_meta:
    tr_label = str(tr_meta[0])[:16]
elif tev_run_id:
    tr_label = f"(metadata not found — id `{tev_run_id[:8]}…`)"
else:
    tr_label = "—"

cols_detail = st.columns(3)
cols_detail[0].caption(f"**Source study:** {sr_label}")
cols_detail[1].caption(f"**TEV run:** {tr_label}")
cols_detail[2].caption(f"**Envelope analysis run:** {'Yes' if envelope_run else 'No'}")
with st.expander("Audit trail IDs (raw UUIDs)", expanded=False):
    st.code(
        f"assumption_set_id   = {aset_id}\n"
        f"source_study_run_id = {source_study_run_id or '—'}\n"
        f"tev_run_id          = {tev_run_id or '—'}\n"
        f"workflow_session_id = {workflow_session_id or '—'}",
        language="text",
    )

st.divider()

# ---------------------------------------------------------------------------
# Iteration history for audit trail
# ---------------------------------------------------------------------------
st.subheader("Iteration History")
history = get_workflow_iterations(DB_PATH, workflow_session_id)
total_iterations = len([h for h in history if h.get("stage") in (2, 3)])

if history:
    hist_df = pd.DataFrame(history)
    display_cols = [
        "iteration_number", "stage", "action", "actuary_id",
        "total_tev", "delta_tev_vs_prior",
        "envelope_run_flag",
        "actuary_comment", "iteration_ts",
    ]
    hist_df = hist_df[[c for c in display_cols if c in hist_df.columns]]
    hist_disp = hist_df.copy()
    if "total_tev" in hist_disp.columns:
        hist_disp["total_tev"] = hist_disp["total_tev"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
    if "delta_tev_vs_prior" in hist_disp.columns:
        hist_disp["delta_tev_vs_prior"] = hist_disp["delta_tev_vs_prior"].apply(lambda v: f"${v:+,.0f}" if pd.notna(v) else "")
    st.dataframe(hist_disp, hide_index=True, use_container_width=True)
    st.caption(f"Total Stage 2/3 iterations: **{total_iterations}**")
else:
    st.caption("No iteration records found for this workflow session.")

st.divider()

# ---------------------------------------------------------------------------
# TEV Impact Report
# ---------------------------------------------------------------------------
st.subheader("TEV Impact Report")
st.markdown(
    "Generate the TEV Impact Report for governance review. "
    "This is the document the reviewer uses as the basis for sign-off."
)

gen_col, _ = st.columns([2, 3])
with gen_col:
    if st.button("Generate TEV Impact Report", use_container_width=True):
        with st.spinner("Generating report…"):
            try:
                report_path = generate_tev_impact_report(
                    db_path=DB_PATH,
                    assumption_set_id=aset_id,
                    tev_run_id=tev_run_id,
                    workflow_session_id=workflow_session_id,
                    output_dir=REPORTS_DIR,
                    envelope_run=envelope_run,
                    envelope_tev_min=envelope_tev_min,
                    envelope_tev_max=envelope_tev_max,
                    envelope_percentile=envelope_percentile,
                    envelope_width_abs=env_res_obj.envelope_width_abs if env_res_obj else None,
                    envelope_width_pct=env_res_obj.envelope_width_pct if env_res_obj else None,
                    top5_decrements=env_res_obj.top5_decrements if env_res_obj else None,
                    theta_proposed=env_res_obj.theta_proposed if env_res_obj else None,
                    theta_min=env_res_obj.theta_min if env_res_obj else None,
                    theta_max=env_res_obj.theta_max if env_res_obj else None,
                    credibility_bounds=env_res_obj.credibility_bounds if env_res_obj else None,
                )
                with open(report_path, "rb") as f:
                    report_bytes = f.read()
                st.session_state["s4_report_path"] = str(report_path)
                st.success(f"Report generated: `{Path(report_path).name}`")
                st.download_button(
                    "Download TEV Impact Report (HTML)",
                    data=report_bytes,
                    file_name=Path(report_path).name,
                    mime="text/html",
                )
            except Exception as exc:
                st.error(f"Report generation failed: {exc}")

st.divider()

# ---------------------------------------------------------------------------
# AI-drafted A/E memo (Phase 3b, Session 19) — FR-3B-20
# ---------------------------------------------------------------------------
st.subheader("AI-drafted A/E memo")
st.markdown(
    "Draft a governance memo for one product/decrement with the AI memo Skill. "
    "The output is an **AI draft** — every number is checked against the study "
    "data and an untraceable number blocks the draft (never repaired). "
    "Review and sign off below; the draft adopts nothing."
)
_memo_models = skills.available_skill_models(CONFIG_DIR)
if not source_study_run_id:
    st.caption("No source study run on this workflow — memo unavailable.")
elif not _memo_models:
    st.caption("No models configured in llm_config.yaml.")
else:
    mc1, mc2, mc3 = st.columns(3)
    memo_product = mc1.selectbox(
        "Product", ["TERM", "WL", "UL", "ULSG", "IUL", "VUL", "DA_FIXED", "DA_FIA", "DA_VA"],
        key="s4_memo_product",
    )
    memo_decrement = mc2.selectbox(
        "Decrement", list(DecrementType),
        format_func=lambda d: d.value, key="s4_memo_decrement",
    )
    memo_model = mc3.selectbox(
        "Model", [m["model_id"] for m in _memo_models],
        format_func=lambda mid: next(
            (f"{m['display_name']}" + ("" if m["enabled"] else f" — {m['disabled_reason']}")
             for m in _memo_models if m["model_id"] == mid), mid),
        key="s4_memo_model",
    )
    if st.button("Draft A/E memo (AI)"):
        with st.spinner("Drafting memo…"):
            try:
                memo_input = skills.assemble_memo_input(
                    DB_PATH, source_study_run_id, memo_decrement, memo_product,
                    whatif_delta_tev=delta_tev,
                )
                st.session_state["s4_memo_out"] = interpret_ae_and_draft_memo(
                    memo_input, load_llm_config(CONFIG_DIR / "llm_config.yaml"), memo_model
                )
            except LLMProviderError as exc:
                st.session_state["s4_memo_out"] = {"_provider_error": str(exc)}
    _memo = st.session_state.get("s4_memo_out")
    if _memo is not None:
        if "_provider_error" in _memo:
            st.error(_memo["_provider_error"])
        elif _memo.get("blocked"):
            _msg = _memo.get("reason") or "Draft blocked (not repaired)."
            _nums = _memo.get("untraceable_nums") or []
            if _nums:
                _msg += f" Untraceable: {', '.join(_nums)}"
            st.error(_msg)
        else:
            st.markdown(_memo["markdown"])
            st.download_button(
                "Download memo (.md)", data=_memo["markdown"].encode("utf-8"),
                file_name=f"ae_memo_{aset_id[:8]}.md", mime="text/markdown",
            )
            if _memo.get("hashes"):
                st.caption("Prompt template hashes: "
                           + ", ".join(f"`{k}`={v[:12]}…" for k, v in _memo["hashes"].items()))

st.divider()

# ---------------------------------------------------------------------------
# Governance Sign-Off — configurable multi-level chain (FR-4-12..18)
# ---------------------------------------------------------------------------
if aset.status == "APPROVED":
    st.success("This assumption set is already APPROVED and locked. No further action required.")
    st.stop()

GOV_CONFIG = str(CONFIG_DIR / "governance_config.yaml")
DB = str(DB_PATH)

me = current_user()
if me is None:
    st.warning("You must be signed in to record a governance sign-off.")
    st.stop()

try:
    with open(GOV_CONFIG, "r", encoding="utf-8") as _fh:
        _gov_cfg = yaml.safe_load(_fh) or {}
    chain = load_chain(_gov_cfg)
except Exception as exc:
    st.error(f"Could not load the approval chain from governance_config.yaml: {exc}")
    st.stop()

st.subheader("Governance Sign-Off")
st.caption(
    "The legacy single Stage-4 reviewer is generalised into the configured "
    "multi-level approval chain. The signing actor is your authenticated identity; "
    "proposer ≠ approver is enforced at every level (FR-4-05)."
)

# ΔTEV fraction vs the prior approved set drives the materiality-required final
# level (FR-4-16); None when there is no prior (first approval → full chain).
delta_frac = None
if delta_tev is not None and total_tev:
    prior_tev = total_tev - delta_tev
    if prior_tev:
        delta_frac = abs(delta_tev) / abs(prior_tev)

# Current chain state (current round = sign-offs since the last RETURN).
_con = duckdb.connect(DB, read_only=True)
try:
    _rows = _con.execute(
        "SELECT chain_level, actor_role, decision, comment, seq "
        "FROM gold_governance_signoffs "
        "WHERE artifact_type = 'ASSUMPTION_SET' AND artifact_id = ? ORDER BY seq",
        [aset_id],
    ).fetchall()
finally:
    _con.close()
_last_return = max([i for i, r in enumerate(_rows) if r[2] == "RETURN"], default=-1)
round_rows = _rows[_last_return + 1:]
approved_levels = {r[0] for r in round_rows if r[2] == "APPROVE"}

prog = []
for lvl in chain:
    actor = next((r for r in round_rows if r[0] == lvl.level and r[2] == "APPROVE"), None)
    prog.append({
        "Level": lvl.level,
        "Required role": lvl.required_role.value,
        "Status": "✅ signed" if lvl.level in approved_levels else "—",
        "Signed by role": actor[1] if actor else "",
        "Comment": actor[3] if actor else "",
    })
st.dataframe(pd.DataFrame(prog), hide_index=True, use_container_width=True)

next_level = next_required_level(
    ArtifactType.ASSUMPTION_SET, aset_id, db_path=DB, config_path=GOV_CONFIG
)
if next_level is None:
    st.success("✅ The approval chain is complete; the assumption set is locked (APPROVED).")
    st.stop()

st.caption(
    f"Next required level: **{next_level.level} — {next_level.required_role.value}**. "
    + (
        f"Materiality: |ΔTEV| ≈ {delta_frac:.2%} vs prior approved set."
        if delta_frac is not None
        else "Materiality: first approval — the full chain is required."
    )
)

with st.expander("My pending approvals", expanded=False):
    _pend = pending_approvals(me, db_path=DB, config_path=GOV_CONFIG)
    if _pend:
        st.dataframe(pd.DataFrame(_pend), hide_index=True, use_container_width=True)
    else:
        st.caption("Nothing is awaiting your sign-off.")

# Only the role occupying the next level may act (role-for-level + chain order).
if not may_sign_off_at(me, next_level):
    st.info(
        f"It is not your turn to sign. You are **{me.display_name}** "
        f"({me.role.value}); the next required level is "
        f"**{next_level.required_role.value}**."
    )
    st.stop()

# Segregation pre-check (proposer ≠ approver; distinct signer per level).
try:
    check_segregation(me, ArtifactType.ASSUMPTION_SET, aset_id, db_path=DB, config_path=GOV_CONFIG)
except SegregationViolation as exc:
    st.error(f"Segregation of duties: {exc}")
    st.stop()

# Attestation + decision (FR-4-15).
attest_text = str(_gov_cfg.get("attestation_text") or "")
st.markdown(f"> {attest_text}")
attested = st.checkbox("I attest to the statement above.", key="s4_attest")
signoff_comment = st.text_area(
    "Sign-off comment (mandatory)",
    height=120,
    placeholder="Record your review findings, conditions, or the reason for returning…",
    key="s4_signoff_comment",
)
decision = st.radio(
    "Decision",
    options=["APPROVE", "RETURN TO STAGE 2"],
    index=0,
    horizontal=True,
    key="s4_chain_decision",
)
submit_btn = st.button(
    f"Record sign-off (level {next_level.level} — {next_level.required_role.value})",
    type="primary",
)

if submit_btn:
    errors = []
    if not attested:
        errors.append("You must attest to the statement before signing off.")
    if not signoff_comment.strip():
        errors.append("A sign-off comment is mandatory.")
    if errors:
        for e in errors:
            st.error(e)
    else:
        dec = Decision.APPROVE if decision == "APPROVE" else Decision.RETURN
        legacy_ctx = {
            "workflow_session_id": workflow_session_id,
            "source_study_run_id": source_study_run_id,
            "tev_baseline_run_id": tev_run_id,
            "proposer_id": proposer_id,
            "baseline_tev": total_tev,
            "delta_tev_vs_prior": delta_tev,
            "max_sensitivity_delta": max_sensitivity_delta,
            "total_iterations": total_iterations,
            "envelope_run_flag": envelope_run,
            "envelope_tev_min": envelope_tev_min,
            "envelope_tev_max": envelope_tev_max,
            "proposed_envelope_percentile": envelope_percentile,
            "iteration_history": [{k: str(v) for k, v in h.items()} for h in history],
        }
        with st.spinner("Recording sign-off…"):
            try:
                rec = record_signoff(
                    me, ArtifactType.ASSUMPTION_SET, aset_id, aset.version, dec,
                    signoff_comment.strip(), db_path=DB, config_path=GOV_CONFIG,
                    delta_tev=delta_frac, legacy_context=legacy_ctx,
                )
            except (PermissionDenied, SegregationViolation, ValueError) as exc:
                st.error(str(exc))
                st.stop()

        _load_aset.clear()
        st.session_state["stage3_approved"] = False
        if dec == Decision.RETURN:
            st.warning(
                f"**Returned to Stage 2** by {me.display_name}. The set is back to PROPOSED. "
                f"Comment: _{signoff_comment.strip()}_  \n"
                f"Navigate to **Stage 2** to make the requested changes."
            )
        else:
            complete = next_required_level(
                ArtifactType.ASSUMPTION_SET, aset_id, db_path=DB, config_path=GOV_CONFIG
            ) is None
            if complete:
                st.balloons()
                st.success(
                    f"✅ Final level signed by {me.display_name}. The assumption set "
                    f"`{aset_id[:8]}…` is now **APPROVED** and permanently locked."
                )
            else:
                nl = next_required_level(
                    ArtifactType.ASSUMPTION_SET, aset_id, db_path=DB, config_path=GOV_CONFIG
                )
                st.success(
                    f"Level {rec.chain_level} signed by {me.display_name}. "
                    f"Next required level: **{nl.required_role.value}**."
                )
        st.rerun()

# ---------------------------------------------------------------------------
# View existing approvals
# ---------------------------------------------------------------------------
with st.expander("All approvals for this assumption set", expanded=False):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        approvals = con.execute("""
            SELECT approval_id, reviewer_id, reviewer_decision,
                   reviewer_comment, proposed_ts, approved_ts,
                   total_iterations, baseline_tev, delta_tev_vs_prior
            FROM gold_assumption_approvals
            WHERE assumption_set_id = ?
            ORDER BY proposed_ts DESC
        """, [aset_id]).df()
    finally:
        con.close()

    if approvals.empty:
        st.caption("No approval records yet.")
    else:
        approvals_disp = approvals.copy()
        approvals_disp["baseline_tev"] = approvals_disp["baseline_tev"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
        approvals_disp["delta_tev_vs_prior"] = approvals_disp["delta_tev_vs_prior"].apply(lambda v: f"${v:+,.0f}" if pd.notna(v) else "")
        st.dataframe(approvals_disp, hide_index=True, use_container_width=True)
