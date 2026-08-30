"""Step 3 — Governance Sign-Off & Lock.

Presents the submitted assumption set to the configured multi-level approval
chain, captures each governance decision (APPROVE or RETURN), and locks the
set on the completing approval. The signing actor is the authenticated user;
proposer ≠ approver is enforced at every level (FR-4-05). The materiality
metric (max |Δ multiplier| vs the prior approved version) drives the required
final sign-off level (FR-4-16).
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from ui import skills_logic as skills
from src.ai.llm.base import LLMProviderError
from src.ai.llm.client import load_llm_config
from src.ai.skills.memo import interpret_ae_and_draft_memo
import yaml

from src.utils.types import ArtifactType, Decision, DecrementType
from src.assumptions.assumption_set import load_assumption_set
from src.assumptions.workflow import get_workflow_iterations

# Phase 4 — configurable approval chain (FR-4-12..18).
from src.governance.auth import current_user
from src.governance.rbac import PermissionDenied, may_sign_off_at
from src.governance.workflow import (
    SegregationViolation,
    check_segregation,
    load_chain,
    materiality_vs_prior_approved,
    next_required_level,
    pending_approvals,
    record_signoff,
)

from ui.theme import page_setup
page_setup("Step 3 — Sign Off & Lock")

from ui.config import require_auth
require_auth()
st.title("Step 3 — Governance Sign-Off & Lock")
st.markdown(
    "This step accepts assumption sets that have been **submitted for sign-off** "
    "in Step 2. The reviewer must be a different actuary from the proposer. "
    "The completing **APPROVE** locks the assumption set permanently. "
    "A **RETURN** decision re-opens it for editing in Step 2."
)

# ---------------------------------------------------------------------------
# Workflow progress indicator
# ---------------------------------------------------------------------------
cols_prog = st.columns(3)
cols_prog[0].success("Step 1 — Select Study Basis ✓")
cols_prog[1].success("Step 2 — Edit & Submit ✓")
cols_prog[2].info("**Step 3** — Sign Off & Lock")

st.divider()

# ---------------------------------------------------------------------------
# Assumption-set selection: session first, else pick a submitted set
# ---------------------------------------------------------------------------
@st.cache_data(ttl=0)
def _load_aset(aset_id: str):
    return load_assumption_set(aset_id, DB_PATH)


@st.cache_data(ttl=0)
def _load_run_meta(study_run_id: str):
    """Fetch a human-readable label for the source study run."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute(
            "SELECT run_ts, product_codes FROM gold_study_runs WHERE run_id = ?",
            [study_run_id],
        ).fetchone()
    finally:
        con.close()


con = duckdb.connect(str(DB_PATH), read_only=True)
try:
    _submitted = con.execute("""
        SELECT assumption_set_id, version, status, author_id, effective_date
        FROM gold_assumption_sets
        WHERE status IN ('STAGE3_APPROVED', 'APPROVED')
        ORDER BY created_ts DESC LIMIT 20
    """).df()
finally:
    con.close()

_session_id = st.session_state.get("active_assumption_set_id")
_options: dict[str, str] = {}
if _session_id:
    _options[f"(from session) {_session_id[:8]}…"] = _session_id
for _, r in _submitted.iterrows():
    lbl = (f"{r['author_id']} | v{r['version']} | eff. {r['effective_date']} "
           f"({r['status']}) — {r['assumption_set_id'][:8]}…")
    _options.setdefault(lbl, r["assumption_set_id"])

if not _options:
    st.warning(
        "No assumption set is awaiting sign-off. Submit one from "
        "**Step 2 — Edit & Submit** first."
    )
    st.stop()

_sel = st.selectbox("Assumption set", list(_options.keys()), key="s3_set_selector")
aset_id = _options[_sel]
st.session_state["active_assumption_set_id"] = aset_id

aset = _load_aset(aset_id)

# Only sets submitted for sign-off (or already locked) belong on this page.
if aset.status not in ("STAGE3_APPROVED", "APPROVED"):
    st.warning(
        f"Assumption set status is **{aset.status}** — expected a set submitted "
        "for sign-off. Return to Step 2 and submit it first."
    )
    st.stop()

workflow_session_id = st.session_state.get("workflow_session_id", str(uuid.uuid4()))
# The proposer is the set's recorded author (FR-4-03) — never the current signer.
proposer_id = (
    st.session_state.get("workflow_author_id")
    or getattr(aset, "author_id", None)
    or "UNKNOWN"
)
source_study_run_id = (
    st.session_state.get("source_study_run_id") or aset.source_study_run_id or ""
)

if aset.status == "APPROVED":
    st.success("✅ This assumption set is already **APPROVED**. No further action required.")
    st.divider()

# ---------------------------------------------------------------------------
# Summary panel
# ---------------------------------------------------------------------------
st.subheader("Assumption Set Summary")

_materiality = materiality_vs_prior_approved(aset_id, db_path=str(DB_PATH))

info_col1, info_col2, info_col3, info_col4 = st.columns(4)
aset_label = f"v{aset.version} ({str(aset.effective_date)[:10]}, {proposer_id})"
info_col1.metric("Assumption Set", aset_label)
info_col2.metric("Proposer", proposer_id)
info_col3.metric("Status", str(aset.status.value if hasattr(aset.status, "value") else aset.status))
if _materiality is not None:
    info_col4.metric("Materiality (max |Δ mult|)", f"{_materiality:.4f}")
else:
    info_col4.metric("Materiality", "First approval")

sr_meta = _load_run_meta(source_study_run_id) if source_study_run_id else None
if sr_meta:
    sr_label = f"{sr_meta[1]} @ {str(sr_meta[0])[:10]}"
elif source_study_run_id:
    sr_label = f"(metadata not found — id `{source_study_run_id[:8]}…`)"
else:
    sr_label = "—"

st.caption(f"**Source study:** {sr_label}")
with st.expander("Audit trail IDs (raw UUIDs)", expanded=False):
    st.code(
        f"assumption_set_id   = {aset_id}\n"
        f"source_study_run_id = {source_study_run_id or '—'}\n"
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
        "iteration_number", "action", "actuary_id",
        "actuary_comment", "iteration_ts",
    ]
    hist_df = hist_df[[c for c in display_cols if c in hist_df.columns]]
    st.dataframe(hist_df, hide_index=True, use_container_width=True)
    st.caption(f"Total Step 2 iterations this session: **{total_iterations}**")
else:
    st.caption("No iteration records found for this workflow session.")

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
    "Approvals run through the configured multi-level chain. The signing actor is "
    "your authenticated identity; proposer ≠ approver is enforced at every level "
    "— the proposer can never approve their own set."
)

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

_mat_threshold = float(
    ((_gov_cfg.get("materiality") or {}).get("max_multiplier_delta_threshold", 0.05))
)
st.caption(
    f"Next required level: **{next_level.level} — {next_level.required_role.value}**. "
    + (
        f"Materiality: max |Δ multiplier| = {_materiality:.4f} vs prior approved "
        f"version (threshold {_mat_threshold:.2f})."
        if _materiality is not None
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
    options=["APPROVE", "RETURN TO STEP 2"],
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
        with st.spinner("Recording sign-off…"):
            try:
                rec = record_signoff(
                    me, ArtifactType.ASSUMPTION_SET, aset_id, aset.version, dec,
                    signoff_comment.strip(), db_path=DB, config_path=GOV_CONFIG,
                )
            except (PermissionDenied, SegregationViolation, ValueError) as exc:
                st.error(str(exc))
                st.stop()

        _load_aset.clear()
        if dec == Decision.RETURN:
            st.warning(
                f"**Returned to Step 2** by {me.display_name}. The set is back to PROPOSED. "
                f"Comment: _{signoff_comment.strip()}_  \n"
                f"Navigate to **Step 2** to make the requested changes."
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
# View existing sign-offs
# ---------------------------------------------------------------------------
with st.expander("All sign-offs for this assumption set", expanded=False):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        signoffs = con.execute("""
            SELECT chain_level, required_role, actor_role, decision,
                   comment, materiality_value, signoff_ts
            FROM gold_governance_signoffs
            WHERE artifact_type = 'ASSUMPTION_SET' AND artifact_id = ?
            ORDER BY seq DESC
        """, [aset_id]).df()
    finally:
        con.close()

    if signoffs.empty:
        st.caption("No sign-off records yet.")
    else:
        st.dataframe(signoffs, hide_index=True, use_container_width=True)
