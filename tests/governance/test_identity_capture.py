"""Governance-output audit remediation (2026-07-04) — actor-identity fixes.

Locks the fixes for the output-review findings (paths updated for the demo-refresh
three-step assumption workflow):

- Fix 1 (root cause of findings 1 & 5): the assumption workflow captures actor
  identity from the authenticated user (FR-4-03), never the free-text ``"ACTUARY_1"``
  placeholder — so ``gold_assumption_sets.author_id`` is a real username and the
  proposer≠approver segregation check (FR-4-05) can actually fire.
- Fix 2 / 6: the Step-2 "Submit for sign-off" affordance is suppressed once the set
  is already submitted / locked (no re-submission noise, and no unlock attempt).
- Fix 5: the unified audit reader resolves a username-keyed actor (WORKFLOW
  ``actuary_id``) to the same display name the sign-off log shows, so one person
  reads as one identity across all sources.
"""
from __future__ import annotations

import pathlib
import uuid

import duckdb
import pytest

from src.governance.audit import unified_audit_query
from src.assumptions.workflow import log_workflow_iteration


_VIEWS = pathlib.Path("ui/views")


def _src(name: str) -> str:
    return (_VIEWS / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fix 1 — authenticated author capture (source guards)
# ---------------------------------------------------------------------------

def test_step1_binds_author_to_authenticated_user():
    src = _src("20_assumption_step1.py")
    assert 'value="ACTUARY_1"' not in src, "Step 1 still uses the free-text ACTUARY_1 default"
    assert "author_id = _user.username" in src, "Step 1 must bind author_id to the authenticated user"
    assert "disabled=True" in src, "the actuary-ID field should be read-only"


def test_step2_actor_is_authenticated_user():
    src = _src("21_assumption_step2.py")
    assert 'value=st.session_state.get("workflow_author_id", "ACTUARY_1")' not in src
    assert "actuary_id = _user.username" in src


def test_step3_does_not_default_to_placeholder():
    s3 = _src("22_assumption_step3.py")
    # The "ACTUARY_1" literal must not survive as a fallback default in code.
    assert 'workflow_author_id", "ACTUARY_1"' not in s3
    # Step 3 falls back to the persisted proposer, never the current signer.
    assert 'getattr(aset, "author_id", None)' in s3


# ---------------------------------------------------------------------------
# Fix 2 / 6 — Step-2 submit guard (source guard)
# ---------------------------------------------------------------------------

def test_step2_submit_guarded_once_submitted():
    src = _src("21_assumption_step2.py")
    assert '_already_submitted = _status in ("STAGE3_APPROVED", "APPROVED")' in src
    # the submit button only renders on the not-yet-submitted branch
    assert "if _already_submitted:" in src


def test_step1_resume_preserves_original_author():
    """Resuming an existing set must keep the set's stored author, not the current user."""
    src = _src("20_assumption_step1.py")
    assert 'st.session_state["workflow_author_id"] = _resumed_author' in src
    assert '_match.iloc[0]["author_id"]' in src  # author read from the resumed set's row


def test_dq_override_actor_is_authenticated_user():
    """The DQ quarantine-override actor is the authenticated user, not a free-text field."""
    src = _src("02_data_quality.py")
    assert 'value="actuary-1"' not in src, "DQ override still uses a free-text actuary id"
    assert "actuary_id = _user.username" in src


# ---------------------------------------------------------------------------
# Fix (round 2) — Step-2 editor cannot unlock an APPROVED set (source guard)
# ---------------------------------------------------------------------------

def test_step2_locks_approved_set():
    src = _src("21_assumption_step2.py")
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
                source_study_run_id, yaml_file_path, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """,
            [set_id, 1, "APPROVED", "2024-01-01", author, "best-estimate",
             str(uuid.uuid4()), ""],
        )
    finally:
        con.close()


def test_workflow_actor_resolves_to_display_name(gov_env):
    """A username-keyed WORKFLOW row reads as the same display name as SIGNOFF."""
    db = gov_env["db"]
    set_id = str(uuid.uuid4())
    _seed_min_set(db, set_id, author="a.analyst")

    # WORKFLOW row keyed on the authenticated username (post-Fix-1).
    log_workflow_iteration(
        db_path=db, workflow_session_id=str(uuid.uuid4()), iteration_number=1,
        assumption_set_id=set_id, stage=2, action="SAVED", actuary_id="a.analyst",
    )

    events = unified_audit_query(db_path=db)
    wf = [e for e in events if e["source"] == "WORKFLOW"]
    assert wf

    # Resolved to display names, and now joinable to a real user_id + role.
    assert wf[0]["actor"] == "A. Analyst"
    assert wf[0]["actor_user_id"] is not None
    assert wf[0]["role"] == "analyst"


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
