"""Locked-set write ORDER and content guard (adversarial review B-2).

Complements ``test_locked_set_immutability.py`` (the 2026-07-04 status-transition
guard) with the three holes the 2026-08-31 review found.

The 2026-08-31 adversarial review found three ways a locked set could still be
mutated:

1. ``save_assumption_set`` wrote the YAML *before* the lock guard ran, so a save
   that was correctly REJECTED had already overwritten the approved artifact on
   disk. Since multipliers load from YAML and status from the DB, the set then
   read as APPROVED with attacker-supplied assumptions.
2. ``_PRESERVED_COLS`` omitted ``approved_by``/``approved_ts``/``superseded_by``,
   so a permitted re-save silently erased who approved it and when.
3. An "idempotent" APPROVED re-save was permitted without comparing content.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pytest

from src.assumptions.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    load_assumption_set,
    save_assumption_set,
)
from src.assumptions.workflow import LockedStatusTransition
from src.utils.types import AssumptionSetStatus


def _mult(m: float = 1.0) -> DecrementMultiplier:
    return DecrementMultiplier(
        product="TERM", gender="M", risk_class="STD_NS", duration_band=[1, 5],
        multiplier=m, credibility_z=0.5, credibility_lower=0.8, credibility_upper=1.2,
    )


def _seed(db: str, status: AssumptionSetStatus, mult: float = 1.0) -> str:
    set_id = str(uuid.uuid4())
    aset = AssumptionSet(
        id=set_id, version=1, status=status,
        effective_date=date.today().isoformat(), author_id="a.analyst",
        basis="best-estimate", source_study_run_id="run-1",
        mortality_multipliers=[_mult(mult)], lapse_multipliers=[],
        surrender_multipliers=[], ci_incidence_multipliers=[],
        premium_persistency=[], shock_lapse_plt={},
        yaml_file_path=str(Path(db).parent / "assumption_sets" / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _approve(db: str, set_id: str, by: str = "c.chief") -> None:
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET status='APPROVED', approved_by=?, "
            "approved_ts=CURRENT_TIMESTAMP WHERE assumption_set_id=?",
            [by, set_id],
        )
    finally:
        con.close()


def test_rejected_save_does_not_touch_the_yaml(gov_env):
    """A save the guard rejects must leave the approved artifact byte-identical."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED, mult=1.0)
    _approve(db, set_id)

    yaml_path = Path(load_assumption_set(set_id, Path(db)).yaml_file_path)
    before = yaml_path.read_bytes()

    tampered = load_assumption_set(set_id, Path(db))
    tampered.status = AssumptionSetStatus.PROPOSED      # the unlock attempt
    for m in tampered.mortality_multipliers:
        m.multiplier = 0.0001                          # the payload

    with pytest.raises(LockedStatusTransition):
        save_assumption_set(tampered, Path(db))

    assert yaml_path.read_bytes() == before, (
        "a REJECTED save still overwrote the locked artifact on disk"
    )
    assert load_assumption_set(set_id, Path(db)).mortality_multipliers[0].multiplier == 1.0


def test_approved_resave_with_changed_content_is_rejected(gov_env):
    """The 'idempotent' APPROVED re-save must not be a content-rewrite door."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED, mult=1.0)
    _approve(db, set_id)

    aset = load_assumption_set(set_id, Path(db))
    aset.status = AssumptionSetStatus.APPROVED          # "idempotent" re-save
    for m in aset.mortality_multipliers:
        m.multiplier = 9.9999                          # but the content changed

    with pytest.raises(LockedStatusTransition):
        save_assumption_set(aset, Path(db))

    assert load_assumption_set(set_id, Path(db)).mortality_multipliers[0].multiplier == 1.0


def test_approval_attribution_survives_a_permitted_resave(gov_env):
    """A genuinely idempotent re-save must not erase who approved it."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED)
    _approve(db, set_id, by="c.chief")

    aset = load_assumption_set(set_id, Path(db))
    aset.status = AssumptionSetStatus.APPROVED         # unchanged content
    save_assumption_set(aset, Path(db))

    con = duckdb.connect(db, read_only=True)
    try:
        approved_by, approved_ts = con.execute(
            "SELECT approved_by, approved_ts FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?", [set_id],
        ).fetchone()
    finally:
        con.close()
    assert approved_by == "c.chief", "re-save erased the approver"
    assert approved_ts is not None, "re-save erased the approval timestamp"


def test_supersession_pointer_survives_a_resave(gov_env):
    """``superseded_by`` must not be orphaned by a re-save."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED)
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET status='SUPERSEDED', superseded_by=? "
            "WHERE assumption_set_id=?", ["newer-set-id", set_id],
        )
    finally:
        con.close()

    aset = load_assumption_set(set_id, Path(db))
    aset.status = AssumptionSetStatus.SUPERSEDED
    save_assumption_set(aset, Path(db))

    con = duckdb.connect(db, read_only=True)
    try:
        superseded_by = con.execute(
            "SELECT superseded_by FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()
    assert superseded_by == "newer-set-id"
