"""Segregation-of-duties tests for the Phase-4 approval chain (Session 25).

Realises the §I.3 acceptance for FR-4-05 / NFR-G-03: proposer ≠ approver is
absolute (a user may never sign off on an artifact they authored, at any level),
and unless ``segregation.allow_multi_level_signoff`` is true a single user may not
sign two levels of the same chain. Also covers the role-for-level guard
(``may_sign_off_at``) that prevents a wrong-role / out-of-order sign-off.
"""

from __future__ import annotations

import pytest

from src.governance.audit import submit_study_run
from src.governance.rbac import PermissionDenied
from src.governance.workflow import (
    SegregationViolation,
    next_required_level,
    record_signoff,
)
from src.utils.types import ArtifactType, Decision

from tests.governance.test_workflow import _seed_set, _u, _write_chain_config


def test_author_cannot_sign_off_even_with_matching_role(gov_env, tmp_path):
    """The proposer cannot sign off, even when their role occupies a chain level (FR-4-05)."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    # The junior actuary authored this set, then tries to sign level 1 (junior).
    set_id = _seed_set(db, author="j.junior")
    with pytest.raises(SegregationViolation):
        record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "self-approve attempt",
                       db_path=db, config_path=cfg, delta_tev=0.05)


def test_distinct_signer_per_level_blocks_same_user(gov_env, tmp_path):
    """With allow_multi_level_signoff=false, one user cannot sign two levels (FR-4-05)."""
    db = gov_env["db"]
    # A chain with two junior levels so the same junior could (role-wise) sign both.
    cfg = _write_chain_config(
        tmp_path / "gov.yaml",
        ["junior_actuary", "junior_actuary", "chief_actuary"],
        allow_multi=False,
    )
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "level 1", db_path=db, config_path=cfg, delta_tev=0.05)
    with pytest.raises(SegregationViolation):
        record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "level 2 by same user",
                       db_path=db, config_path=cfg, delta_tev=0.05)


def test_allow_multi_level_signoff_permits_same_user(gov_env, tmp_path):
    """With allow_multi_level_signoff=true, the same user may sign consecutive same-role levels."""
    db = gov_env["db"]
    cfg = _write_chain_config(
        tmp_path / "gov.yaml",
        ["junior_actuary", "junior_actuary", "chief_actuary"],
        allow_multi=True,
    )
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "level 1", db_path=db, config_path=cfg, delta_tev=0.05)
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "level 2", db_path=db, config_path=cfg, delta_tev=0.05)
    nxt = next_required_level(ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg)
    assert nxt is not None and nxt.required_role.value == "chief_actuary"


def test_wrong_role_for_level_blocked(gov_env, tmp_path):
    """A user whose role does not match the current required level cannot sign (FR-4-06)."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db, author="a.analyst")
    # Chief tries to sign level 1 (junior_actuary) first — wrong role / out of order.
    with pytest.raises(PermissionDenied):
        record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "wrong level", db_path=db, config_path=cfg, delta_tev=0.05)


def test_study_run_submitter_cannot_sign_off(gov_env, tmp_path):
    """A study run's submitter (proposer) cannot also sign its chain (FR-4-05).

    The submitter is captured by the STUDY_RUN_SUBMITTED event; proposer ≠ approver
    is absolute for study runs, exactly as for assumption sets.
    """
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    run_id = "studyrun-seg"
    submit_study_run(run_id, _u(db, "j.junior").user_id, db_path=db)
    with pytest.raises(SegregationViolation):
        record_signoff(_u(db, "j.junior"), ArtifactType.STUDY_RUN, run_id, None,
                       Decision.APPROVE, "self-approve attempt", db_path=db, config_path=cfg)


def test_study_run_non_submitter_may_sign(gov_env, tmp_path):
    """A non-submitter whose role occupies the level signs normally (no over-block)."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    run_id = "studyrun-ok"
    submit_study_run(run_id, _u(db, "a.analyst").user_id, db_path=db)  # analyst submits
    rec = record_signoff(_u(db, "j.junior"), ArtifactType.STUDY_RUN, run_id, None,
                         Decision.APPROVE, "level 1", db_path=db, config_path=cfg)
    assert rec.decision == Decision.APPROVE


def test_real_username_author_is_blocked_but_placeholder_author_fails_open(gov_env, tmp_path):
    """Characterisation of the original bug + the fix's dependency (governance audit 2026-07-04).

    The proposer≠approver rule reads gold_assumption_sets.author_id and compares it to
    the signer's real identity. So it ONLY fires when the author was captured as a real
    username (the fix). A set authored with the old free-text placeholder ("ACTUARY_1")
    matches no real identity and therefore FAILS OPEN — its own proposer could sign it.
    This is exactly why the UI must capture the authenticated user, and why any legacy
    placeholder-authored set is a migration concern (it must be remapped or re-proposed).
    """
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")

    # (fix) real-username author → the author is correctly blocked from self-signing.
    real = _seed_set(db, author="j.junior")
    with pytest.raises(SegregationViolation):
        record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, real, 1,
                       Decision.APPROVE, "self-approve", db_path=db, config_path=cfg, delta_tev=0.05)

    # (legacy) placeholder author → NOT blocked; the same human can sign it. Documents
    # the residual exposure for pre-fix data (no assertion of desirability — a red flag).
    legacy = _seed_set(db, author="ACTUARY_1")
    rec = record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, legacy, 1,
                         Decision.APPROVE, "signs legacy", db_path=db, config_path=cfg, delta_tev=0.05)
    assert rec.decision == Decision.APPROVE
