"""Governance-output audit remediation (2026-07-04) — APPROVED-set immutability.

The first-round fix guarded `transition_assumption_set_status`, but an adversarial
review found the SAME "silently unlock an APPROVED set" defect reachable through a
DIFFERENT door: `save_assumption_set` writes `status` unconditionally, and the Stage-2
editor forces `status=PROPOSED` before saving. These tests lock BOTH doors and prove
the legitimate flows (chain completion, mid-chain RETURN) still work.

Reuses the real build-and-approve helpers from tests.governance.test_reporting.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.governance.workflow import record_signoff
from src.tev.assumption_set import load_assumption_set, save_assumption_set
from src.tev.workflow import (
    LockedStatusTransition,
    transition_assumption_set_status,
)
from src.utils.types import ArtifactType, AssumptionSetStatus, Decision

from tests.governance.test_reporting import (
    _approve_assumption_set,
    _seed_set,
    _status,
    _u,
    _write_chain_config,
)


# ---------------------------------------------------------------------------
# The save-path door (the hole the transition guard alone did NOT close)
# ---------------------------------------------------------------------------

def test_save_cannot_unlock_approved_set(gov_env, tmp_path):
    """Re-saving an APPROVED set as PROPOSED (the Stage-2 downgrade) is refused."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db)
    _approve_assumption_set(db, cfg, set_id, version=1)
    assert _status(db, set_id) == "APPROVED"

    aset = load_assumption_set(set_id, Path(db))
    aset.status = AssumptionSetStatus.PROPOSED   # mimic ui/views/21_tev_stage2.py:589
    with pytest.raises(LockedStatusTransition):
        save_assumption_set(aset, Path(db))

    # The set is still locked, and its approval metadata was not disturbed.
    assert _status(db, set_id) == "APPROVED"


def test_idempotent_resave_of_approved_set_is_allowed(gov_env, tmp_path):
    """Re-saving an APPROVED set that is STILL APPROVED (no downgrade) is permitted."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db)
    _approve_assumption_set(db, cfg, set_id, version=1)

    aset = load_assumption_set(set_id, Path(db))   # load reads status=APPROVED from DB
    assert aset.status == AssumptionSetStatus.APPROVED
    save_assumption_set(aset, Path(db))            # must not raise
    assert _status(db, set_id) == "APPROVED"


# ---------------------------------------------------------------------------
# The transition-guard door, and legitimate flows around it
# ---------------------------------------------------------------------------

def test_full_happy_path_reaches_approved_with_guard(gov_env, tmp_path):
    """junior→senior→chief still drives a set all the way to APPROVED (guard intact)."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db)
    _approve_assumption_set(db, cfg, set_id, version=1)
    assert _status(db, set_id) == "APPROVED"


def test_mid_chain_return_reopens_to_proposed(gov_env, tmp_path):
    """A RETURN mid-chain (STAGE3_APPROVED→PROPOSED) is NOT blocked by the lock guard."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db)
    transition_assumption_set_status(Path(db), set_id, "STAGE3_APPROVED")
    record_signoff(
        _u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
        Decision.RETURN, "needs rework", db_path=db, config_path=cfg, delta_tev=0.05,
    )
    assert _status(db, set_id) == "PROPOSED"


def test_return_after_completion_is_refused_not_guard_blocked(gov_env, tmp_path):
    """Once APPROVED, a further RETURN is refused by the chain-complete check, not the guard."""
    db = gov_env["db"]
    cfg = _write_chain_config(tmp_path / "gov.yaml")
    set_id = _seed_set(db)
    _approve_assumption_set(db, cfg, set_id, version=1)
    with pytest.raises(ValueError):   # "sign-off chain is already complete"
        record_signoff(
            _u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
            Decision.RETURN, "too late", db_path=db, config_path=cfg, delta_tev=0.05,
        )
    assert _status(db, set_id) == "APPROVED"
