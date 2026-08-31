"""Content hash binding an assumption YAML to its DB row (adversarial review m-6).

Before this, ``gold_assumption_sets`` stored only ``yaml_file_path``: the
multipliers of an APPROVED (locked) set could be edited on disk and nothing —
not the lock guard, not ``verify_chain``, not ``reproducibility_stamp`` —
detected it, because every read trusts the file. A ``yaml_sha256`` recorded at
save closes that door.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pytest
import yaml as _yaml

from src.assumptions.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    YamlIntegrityError,
    content_hash,
    load_assumption_set,
    save_assumption_set,
    verify_assumption_set_integrity,
)
from src.utils.types import AssumptionSetStatus


def _seed(db: str, status: AssumptionSetStatus, mult: float = 1.0) -> str:
    set_id = str(uuid.uuid4())
    aset = AssumptionSet(
        id=set_id, version=1, status=status,
        effective_date=date.today().isoformat(), author_id="a.analyst",
        basis="best-estimate", source_study_run_id="run-1",
        mortality_multipliers=[
            DecrementMultiplier(
                product="TERM", gender="M", risk_class="STD_NS", duration_band=[1, 5],
                multiplier=mult, credibility_z=0.5,
                credibility_lower=0.5, credibility_upper=1.5,
            )
        ],
        lapse_multipliers=[], surrender_multipliers=[], ci_incidence_multipliers=[],
        premium_persistency=[], shock_lapse_plt={},
        yaml_file_path=str(Path(db).parent / "assumption_sets" / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _lock(db: str, set_id: str) -> None:
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET status='APPROVED', approved_by='c.chief', "
            "approved_ts=CURRENT_TIMESTAMP WHERE assumption_set_id=?", [set_id],
        )
    finally:
        con.close()


def _edit_yaml_on_disk(path: Path, multiplier: float) -> None:
    """Rewrite the multiplier directly in the file — the attack the hash detects."""
    doc = _yaml.safe_load(path.read_text())
    doc["assumption_set"]["mortality"]["multipliers"][0]["multiplier"] = multiplier
    path.write_text(_yaml.dump(doc, default_flow_style=False, sort_keys=False))


def _stored(db: str, set_id: str) -> str | None:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT yaml_sha256 FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()


def test_save_records_the_content_hash(gov_env):
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED)
    stored = _stored(db, set_id)
    assert stored and len(stored) == 64
    assert stored == content_hash(load_assumption_set(set_id, Path(db)))
    assert verify_assumption_set_integrity(set_id, Path(db)) is True


def test_offdisk_edit_of_a_locked_set_is_detected(gov_env):
    """Editing an APPROVED set's YAML on disk is refused at load (m-6)."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED, mult=1.0)
    _lock(db, set_id)
    yaml_path = Path(load_assumption_set(set_id, Path(db)).yaml_file_path)

    _edit_yaml_on_disk(yaml_path, 0.0001)

    assert verify_assumption_set_integrity(set_id, Path(db)) is False
    with pytest.raises(YamlIntegrityError, match="modified outside the application"):
        load_assumption_set(set_id, Path(db))
    # the unchecked read still works, so the checker itself can inspect the file
    unchecked = load_assumption_set(set_id, Path(db), verify_integrity=False)
    assert unchecked.mortality_multipliers[0].multiplier == pytest.approx(0.0001)


def test_offdisk_edit_of_an_editable_set_warns_but_loads(gov_env, caplog):
    """A still-editable set is not immutable, so a mismatch is logged, not fatal."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED, mult=1.0)
    yaml_path = Path(load_assumption_set(set_id, Path(db)).yaml_file_path)
    _edit_yaml_on_disk(yaml_path, 0.9)

    with caplog.at_level("WARNING"):
        aset = load_assumption_set(set_id, Path(db))
    assert aset.mortality_multipliers[0].multiplier == pytest.approx(0.9)
    assert any("does not match the content hash" in r.message for r in caplog.records)


def test_legitimate_resave_refreshes_the_hash(gov_env):
    """A normal edit-and-save must not leave a stale hash (no false tamper alarm)."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED, mult=1.0)
    first = _stored(db, set_id)

    aset = load_assumption_set(set_id, Path(db))
    aset.mortality_multipliers[0].multiplier = 1.1
    save_assumption_set(aset, Path(db))

    assert _stored(db, set_id) != first
    assert verify_assumption_set_integrity(set_id, Path(db)) is True
    load_assumption_set(set_id, Path(db))  # no raise, no warning


def test_hash_ignores_volatile_fields(gov_env):
    """created_ts / status churn must not read as tampering."""
    db = gov_env["db"]
    set_id = _seed(db, AssumptionSetStatus.PROPOSED)
    aset = load_assumption_set(set_id, Path(db))
    before = content_hash(aset)
    aset.status = AssumptionSetStatus.STAGE3_APPROVED
    assert content_hash(aset) == before
