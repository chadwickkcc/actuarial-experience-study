"""Study Run Sign-Off — governance approval of an A/E study run (FR-4-14).

A study run must be approved through the configured multi-level sign-off chain
(junior → senior → chief) before it is "fit for assumption-setting". This page is
the UI for that flow: a proposer submits a COMPLETE run into governance, the
approvers sign it off level by level (proposer ≠ approver enforced), and once fit
the compliance pack can be exported.

It mirrors the Stage-4 sign-off core (ui/views/23_tev_stage4.py) with
ArtifactType.STUDY_RUN — study runs carry no version / ΔTEV, so the full chain to
chief always applies. The governance engine is unchanged; this is UI wiring.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st
import yaml

from ui.config import DB_PATH, CONFIG_DIR, require_auth, user_can
from ui import governance_logic as gov
from src.governance.auth import current_user
from src.governance.audit import submit_study_run
from src.governance.rbac import Action, PermissionDenied, may_sign_off_at, require
from src.governance.reporting import export_compliance_pack
from src.governance.workflow import (
    SegregationViolation,
    check_segregation,
    is_study_run_fit,
    load_chain,
    next_required_level,
    pending_approvals,
    record_signoff,
)
from src.utils.types import ArtifactType, Decision

st.set_page_config(page_title="Study Run Sign-Off", layout="wide")

_user = require_auth()
st.title("Study Run Sign-Off")
st.markdown(
    "Approve an A/E **study run** through the configured governance chain "
    "(junior → senior → chief). A run must be submitted by a proposer and then "
    "signed off at every level before it is **fit for assumption-setting** "
    "(FR-4-14). Proposer ≠ approver is enforced (FR-4-05)."
)

GOV_CONFIG = str(CONFIG_DIR / "governance_config.yaml")
DB = str(DB_PATH)
me = current_user()  # authenticated actor (require_auth already guaranteed non-None)
_can_propose = user_can(_user, Action.PROPOSE)

# ---------------------------------------------------------------------------
# Run selector
# ---------------------------------------------------------------------------
runs = gov.list_complete_study_runs()
if not runs:
    st.warning("No COMPLETE study runs found. Run a study first (see **Run Study**).")
    st.stop()
labels = {r["label"]: r["run_id"] for r in runs}
selected_label = st.selectbox("Study run", list(labels.keys()))
run_id = labels[selected_label]
st.caption(f"Run ID: `{run_id}`")

st.divider()

# ---------------------------------------------------------------------------
# Submit step (proposer) — a run must be submitted before it can be signed off
# ---------------------------------------------------------------------------
if not gov.study_run_submitted(run_id):
    st.subheader("1. Submit for governance approval")
    st.info("This run has not been submitted for approval yet.")
    submit_btn = st.button(
        "Submit for governance approval", type="primary", disabled=not _can_propose
    )
    if not _can_propose:
        st.caption(
            f"Your role ({_user.role.value}) cannot submit — a run is submitted by a "
            "proposer (analyst)."
        )
    if submit_btn:
        try:
            require(_user, Action.PROPOSE)  # server-side re-check
        except PermissionDenied as exc:
            st.error(str(exc))
            st.stop()
        try:
            submit_study_run(
                run_id, me.user_id,
                detail=f"Submitted for governance approval by {me.username}", db_path=DB,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Submit failed: {exc}")
            st.stop()
        st.success("Submitted for governance approval.")
        st.rerun()
    st.stop()

# ---------------------------------------------------------------------------
# Chain state + progress
# ---------------------------------------------------------------------------
try:
    with open(GOV_CONFIG, "r", encoding="utf-8") as _fh:
        _gov_cfg = yaml.safe_load(_fh) or {}
    chain = load_chain(_gov_cfg)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load the approval chain from governance_config.yaml: {exc}")
    st.stop()

st.subheader("2. Approval chain")

_con = duckdb.connect(DB, read_only=True)
try:
    _rows = _con.execute(
        "SELECT chain_level, actor_role, decision, comment, seq "
        "FROM gold_governance_signoffs "
        "WHERE artifact_type = 'STUDY_RUN' AND artifact_id = ? ORDER BY seq",
        [run_id],
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
    ArtifactType.STUDY_RUN, run_id, db_path=DB, config_path=GOV_CONFIG
)

# ---------------------------------------------------------------------------
# Chain complete → fit + compliance-pack export
# ---------------------------------------------------------------------------
if next_level is None:
    st.success(
        "✅ The approval chain is complete — this study run is **fit for "
        "assumption-setting**."
    )
    st.subheader("3. Compliance pack")
    _can_export = user_can(_user, Action.EXPORT)
    export_btn = st.button(
        "Export compliance pack (HTML)", type="primary", disabled=not _can_export
    )
    if not _can_export:
        st.caption(f"Your role ({_user.role.value}) does not have the `export` permission.")
    if export_btn:
        try:
            path = export_compliance_pack(
                ArtifactType.STUDY_RUN, run_id, "html", db_path=DB, config_path=GOV_CONFIG
            )
            html_bytes = Path(path).read_bytes()
            st.success(f"Compliance pack written: {Path(path).name}")
            st.download_button(
                "Download compliance pack", data=html_bytes,
                file_name=Path(path).name, mime="text/html",
            )
        except (ValueError, NotImplementedError) as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001 - defensive UI guard
            st.error(f"Export failed: {exc}")
    st.stop()

st.caption(
    f"Next required level: **{next_level.level} — {next_level.required_role.value}**. "
    "Study runs always require the full chain (no ΔTEV shortcut)."
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
    check_segregation(me, ArtifactType.STUDY_RUN, run_id, db_path=DB, config_path=GOV_CONFIG)
except SegregationViolation as exc:
    st.error(f"Segregation of duties: {exc}")
    st.stop()

# ---------------------------------------------------------------------------
# Attestation + decision
# ---------------------------------------------------------------------------
st.subheader("3. Record your sign-off")
attest_text = str(_gov_cfg.get("attestation_text") or "")
st.markdown(f"> {attest_text}")
attested = st.checkbox("I attest to the statement above.", key="sr_attest")
signoff_comment = st.text_area(
    "Sign-off comment (mandatory)",
    height=120,
    placeholder="Record your review findings, conditions, or the reason for returning…",
    key="sr_signoff_comment",
)
decision = st.radio(
    "Decision", options=["APPROVE", "RETURN"], index=0, horizontal=True,
    key="sr_chain_decision",
)
submit_signoff = st.button(
    f"Record sign-off (level {next_level.level} — {next_level.required_role.value})",
    type="primary",
)

if submit_signoff:
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
                    me, ArtifactType.STUDY_RUN, run_id, None, dec,
                    signoff_comment.strip(), db_path=DB, config_path=GOV_CONFIG,
                    delta_tev=None, legacy_context=None,
                )
            except (PermissionDenied, SegregationViolation, ValueError) as exc:
                st.error(str(exc))
                st.stop()
        if dec == Decision.RETURN:
            st.warning(
                f"**Returned** by {me.display_name}. The chain resets; the run must be "
                f"re-approved from level 1. Comment: _{signoff_comment.strip()}_"
            )
        elif is_study_run_fit(run_id, db_path=DB, config_path=GOV_CONFIG):
            st.balloons()
            st.success(
                f"✅ Final level signed by {me.display_name}. Study run "
                f"`{run_id[:8]}…` is now **fit for assumption-setting**."
            )
        else:
            nl = next_required_level(
                ArtifactType.STUDY_RUN, run_id, db_path=DB, config_path=GOV_CONFIG
            )
            st.success(
                f"Level {rec.chain_level} signed by {me.display_name}. "
                f"Next required level: **{nl.required_role.value}**."
            )
        st.rerun()
