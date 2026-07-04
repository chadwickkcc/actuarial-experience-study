"""Governance-output audit remediation (2026-07-04) — actor-identity fixes.

Locks the fixes for the output-review findings:

- Fix 1 (root cause of findings 1 & 5): the TEV four-stage workflow captures actor
  identity from the authenticated user (FR-4-03), never the free-text ``"ACTUARY_1"``
  placeholder — so ``gold_assumption_sets.author_id`` is a real username and the
  proposer≠approver segregation check (FR-4-05) can actually fire.
- Fix 2 / 6: the Stage-3 "Submit for sign-off" button is disabled once the set is
  already submitted / locked (no re-submission noise, and no unlock attempt).
- Fix 5: the unified audit reader resolves a username-keyed actor (legacy APPROVAL
  ``reviewer_id`` and, post-fix, WORKFLOW ``actuary_id``) to the same display name the
  sign-off log shows, so one person reads as one identity across all sources.
"""
from __future__ import annotations

import pathlib
import uuid

import duckdb
import pytest

from src.governance.audit import unified_audit_query
from src.tev.workflow import log_workflow_iteration, record_governance_approval


_VIEWS = pathlib.Path("ui/views")


def _src(name: str) -> str:
    return (_VIEWS / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fix 1 — authenticated author capture (source guards)
# ---------------------------------------------------------------------------

def test_stage1_binds_author_to_authenticated_user():
    src = _src("20_tev_stage1.py")
    assert 'value="ACTUARY_1"' not in src, "Stage 1 still uses the free-text ACTUARY_1 default"
    assert "author_id = _user.username" in src, "Stage 1 must bind author_id to the authenticated user"
    assert "disabled=True" in src, "the actuary-ID field should be read-only"


def test_stage2_actor_is_authenticated_user():
    src = _src("21_tev_stage2.py")
    assert 'value=st.session_state.get("workflow_author_id", "ACTUARY_1")' not in src
    assert "actuary_id = _user.username" in src


def test_stage3_and_stage4_do_not_default_to_placeholder():
    s3 = _src("22_tev_stage3.py")
    s4 = _src("23_tev_stage4.py")
    # The "ACTUARY_1" literal must not survive as a fallback default in code.
    assert 'workflow_author_id", "ACTUARY_1"' not in s3
    assert 'workflow_author_id", "ACTUARY_1"' not in s4
    assert "or _user.username" in s3  # Stage 3 falls back to the authenticated user
    # Stage 4 falls back to the persisted proposer, never the current signer.
    assert 'getattr(aset, "author_id", None)' in s4


# ---------------------------------------------------------------------------
# Fix 2 / 6 — Stage-3 submit button guard (source guard)
# ---------------------------------------------------------------------------

def test_stage3_submit_disabled_once_submitted():
    src = _src("22_tev_stage3.py")
    assert '_already_submitted = aset.status in ("STAGE3_APPROVED", "APPROVED")' in src
    assert "disabled=not _can_propose or _already_submitted" in src


def test_stage1_resume_preserves_original_author():
    """Resuming an existing set must keep the set's stored author, not the current user."""
    src = _src("20_tev_stage1.py")
    assert 'st.session_state["workflow_author_id"] = _resumed_author' in src
    assert '_match.iloc[0]["author_id"]' in src  # author read from the resumed set's row


def test_dq_override_actor_is_authenticated_user():
    """The DQ quarantine-override actor is the authenticated user, not a free-text field."""
    src = _src("02_data_quality.py")
    assert 'value="actuary-1"' not in src, "DQ override still uses a free-text actuary id"
    assert "actuary_id = _user.username" in src


# ---------------------------------------------------------------------------
# Fix (round 2) — Stage-2 editor cannot unlock an APPROVED set (source guard)
# ---------------------------------------------------------------------------

def test_stage2_locks_approved_set():
    src = _src("21_tev_stage2.py")
    assert '_is_locked = aset.status.value == "APPROVED"' in src
    # save button disabled + server-side re-check both present
    assert "or _is_locked" in src
    assert "if _is_locked:" in src


# ---------------------------------------------------------------------------
# Fix 5 — unified audit reader resolves usernames to display names
# ---------------------------------------------------------------------------

def _seed_min_set(db: str, set_id: str, author: str) -> None:
    con = duckdb.connect(db)
    try:
        con.execute(
            """
            INSERT INTO gold_assumption_sets (
                assumption_set_id, version, status, effective_date, author_id, basis,
                source_study_run_id, yaml_file_path, rdr, earned_rate_ga, earned_rate_sa,
                tax_rate, expense_inflation, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """,
            [set_id, 1, "APPROVED", "2024-01-01", author, "best-estimate",
             str(uuid.uuid4()), "", 0.09, 0.05, 0.06, 0.21, 0.025],
        )
    finally:
        con.close()


def test_workflow_and_approval_actors_resolve_to_display_names(gov_env):
    """A username-keyed WORKFLOW / APPROVAL row reads as the same display name as SIGNOFF."""
    db = gov_env["db"]
    set_id = str(uuid.uuid4())
    _seed_min_set(db, set_id, author="a.analyst")

    # WORKFLOW row keyed on the authenticated username (post-Fix-1).
    log_workflow_iteration(
        db_path=db, workflow_session_id=str(uuid.uuid4()), iteration_number=1,
        assumption_set_id=set_id, stage=2, action="SAVED", actuary_id="a.analyst",
    )
    # Legacy APPROVAL row keyed on the chief's username.
    record_governance_approval(
        db_path=db, assumption_set_id=set_id, workflow_session_id=str(uuid.uuid4()),
        source_study_run_id=str(uuid.uuid4()), tev_baseline_run_id=str(uuid.uuid4()),
        proposer_id="a.analyst", reviewer_id="c.chief", reviewer_decision="APPROVE",
        reviewer_comment="signed", total_iterations=1, envelope_run_flag=False,
        baseline_tev=1.0, delta_tev_vs_prior=None, max_sensitivity_delta=None,
        iteration_history=[],
    )

    events = unified_audit_query(db_path=db)
    wf = [e for e in events if e["source"] == "WORKFLOW"]
    ap = [e for e in events if e["source"] == "APPROVAL"]
    assert wf and ap

    # Resolved to display names, and now joinable to a real user_id + role.
    assert wf[0]["actor"] == "A. Analyst"
    assert wf[0]["actor_user_id"] is not None
    assert wf[0]["role"] == "analyst"
    assert ap[0]["actor"] == "C. Chief"
    assert ap[0]["actor_user_id"] is not None
    assert ap[0]["role"] == "chief_actuary"


def test_unknown_actor_falls_through_raw(gov_env):
    """An actor id not in gold_users is passed through unchanged (no crash)."""
    db = gov_env["db"]
    set_id = str(uuid.uuid4())
    _seed_min_set(db, set_id, author="ACTUARY_1")
    log_workflow_iteration(
        db_path=db, workflow_session_id=str(uuid.uuid4()), iteration_number=1,
        assumption_set_id=set_id, stage=2, action="SAVED", actuary_id="ACTUARY_1",
    )
    events = unified_audit_query(db_path=db)
    wf = [e for e in events if e["source"] == "WORKFLOW"]
    assert wf and wf[0]["actor"] == "ACTUARY_1"
    assert wf[0]["actor_user_id"] is None
