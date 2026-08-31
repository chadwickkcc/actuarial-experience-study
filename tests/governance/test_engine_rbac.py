"""Engine-level RBAC on governance mutators (adversarial review B-1, M-10).

FR-4-04 / NFR-G-02 require authorisation to be enforced *server-side*, not by UI
hiding. The 2026-08-31 adversarial review demonstrated a complete bypass: an
``analyst`` (no ``sign_off`` right) published an assumption set live with zero
sign-offs, demoted the chief-approved set to SUPERSEDED, and left no audit trail
— because ``lineage.approve_and_supersede`` took no user and checked nothing.

These tests lock the engine-side gate for both the publish path (B-1) and the
fraud-scan path (M-10).
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pytest

from src.assumptions.assumption_set import AssumptionSet, DecrementMultiplier, save_assumption_set
from src.governance.lineage import approve_and_supersede
from src.governance.rbac import PermissionDenied
from src.utils.types import AssumptionSetStatus, Role, User


def _mk_user(role: Role, username: str) -> User:
    return User(
        user_id=f"u-{username}",
        username=username,
        display_name=username,
        role=role,
        active=True,
    )


ANALYST = _mk_user(Role.ANALYST, "a.analyst")
CHIEF = _mk_user(Role.CHIEF_ACTUARY, "c.chief")


def _seed(db: str, *, status: AssumptionSetStatus) -> str:
    set_id = str(uuid.uuid4())
    aset = AssumptionSet(
        id=set_id,
        version=1,
        status=status,
        effective_date=date.today().isoformat(),
        author_id="a.analyst",
        basis="best-estimate",
        source_study_run_id="run-1",
        mortality_multipliers=[
            DecrementMultiplier(
                product="TERM", gender="M", risk_class="STD_NS", duration_band=[1, 5],
                multiplier=1.0, credibility_z=0.5,
                credibility_lower=0.8, credibility_upper=1.2,
            )
        ],
        lapse_multipliers=[],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={},
        yaml_file_path=str(Path(db).parent / "assumption_sets" / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _status(db: str, set_id: str) -> str:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# B-1 — publish requires sign_off, server-side
# ---------------------------------------------------------------------------

def test_analyst_cannot_publish_a_version(gov_env):
    """The demonstrated bypass: an analyst has no sign_off right and must be
    refused by the ENGINE, not merely by a disabled button."""
    db = gov_env["db"]
    set_id = _seed(db, status=AssumptionSetStatus.STAGE3_APPROVED)

    with pytest.raises(PermissionDenied):
        approve_and_supersede(
            set_id, date(2024, 1, 1), date(2024, 12, 31), user=ANALYST, db_path=db,
        )

    # And nothing was written.
    assert _status(db, set_id) == "STAGE3_APPROVED"


def test_publish_requires_a_user_argument():
    """``user`` is mandatory — a caller cannot omit authorisation."""
    import inspect

    sig = inspect.signature(approve_and_supersede)
    assert "user" in sig.parameters, "approve_and_supersede must take a user"
    assert sig.parameters["user"].default is inspect.Parameter.empty, (
        "user must be REQUIRED, not optional — an optional user is not a gate"
    )


def test_draft_set_cannot_be_published_even_by_chief(gov_env):
    """A DRAFT has not been through the sign-off chain; publishing it would make
    unreviewed assumptions live (the second half of the demonstrated bypass)."""
    db = gov_env["db"]
    set_id = _seed(db, status=AssumptionSetStatus.DRAFT)

    with pytest.raises(ValueError, match="DRAFT|not been|status"):
        approve_and_supersede(
            set_id, date(2024, 1, 1), date(2024, 12, 31), user=CHIEF, db_path=db,
        )
    assert _status(db, set_id) == "DRAFT"


def test_chief_can_publish_a_submitted_set(gov_env):
    """The legitimate path still works."""
    db = gov_env["db"]
    set_id = _seed(db, status=AssumptionSetStatus.STAGE3_APPROVED)

    approve_and_supersede(
        set_id, date(2024, 1, 1), date(2024, 12, 31), user=CHIEF, db_path=db,
    )
    assert _status(db, set_id) == "APPROVED"


def test_publish_is_audited(gov_env):
    """A governance action that changes the live basis must leave a trail."""
    db = gov_env["db"]
    set_id = _seed(db, status=AssumptionSetStatus.STAGE3_APPROVED)

    approve_and_supersede(
        set_id, date(2024, 1, 1), date(2024, 12, 31), user=CHIEF, db_path=db,
    )
    con = duckdb.connect(db, read_only=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM gold_workflow_iterations "
            "WHERE assumption_set_id = ? AND action = 'PUBLISHED'",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1, "publishing must be recorded in the governance trail"


# ---------------------------------------------------------------------------
# M-10 — the fraud scan is a governed write and needs the same server-side gate
# ---------------------------------------------------------------------------

def test_fraud_scan_requires_propose_right(gov_env):
    """``run_fraud_scan`` writes three Gold tables. Before this fix it was guarded
    only by a disabled Streamlit button — the engine took no user at all."""
    from src.fraud.runner import run_fraud_scan

    db = Path(gov_env["db"])
    junior = _mk_user(Role.JUNIOR_ACTUARY, "j.junior")   # sign_off/view/export, NOT propose

    with pytest.raises(PermissionDenied):
        run_fraud_scan(db, "any-run", user=junior, persist=False)


def test_fraud_scan_requires_a_user_argument():
    import inspect
    from src.fraud.runner import run_fraud_scan

    sig = inspect.signature(run_fraud_scan)
    assert "user" in sig.parameters, "run_fraud_scan must take a user"
    assert sig.parameters["user"].default is inspect.Parameter.empty, (
        "user must be REQUIRED — an optional user is not a gate"
    )


def test_analyst_may_run_a_fraud_scan(gov_env):
    """The legitimate path still works (empty DB → zero claims, no crash)."""
    from src.fraud.runner import run_fraud_scan

    res = run_fraud_scan(Path(gov_env["db"]), "any-run", user=ANALYST, persist=False)
    assert res.n_claims_scored == 0
    assert res.n_claims_flagged == 0
