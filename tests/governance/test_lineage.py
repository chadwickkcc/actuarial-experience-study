"""Tests for the Phase-4 versioning & lineage module (Session 24).

Realises the §I.3 acceptance for FR-4-07 … FR-4-11 and NFR-G-05:
parent→child links, status transitions, supersession (≤1 APPROVED-current per
lineage), non-overlapping effective ranges, the live-set resolver, cross-version
comparison (changed cells + ΔTEV + rationale), and the reproducibility stamp.

Uses the shared ``gov_env`` fixture (a temp DB initialised via ``init_database``).
The fixture has no study/TEV data, so the few tests that need it seed minimal
rows directly via parameterized inserts (mirroring ``test_auth.py``).
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from src.governance.lineage import (
    OverlappingEffectiveRange,
    approve_and_supersede,
    compare_versions,
    create_version,
    lineage_root,
    reproducibility_stamp,
    resolve_live_set,
)
from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    load_assumption_set,
    save_assumption_set,
)
from src.utils.types import AssumptionSetStatus, Role, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user() -> User:
    return User(
        user_id="u-analyst",
        username="a.analyst",
        display_name="A. Analyst",
        role=Role.ANALYST,
        active=True,
    )


def _mult(multiplier: float = 1.0, rationale: str = "") -> DecrementMultiplier:
    return DecrementMultiplier(
        product="TERM",
        gender="M",
        risk_class="STD_NS",
        duration_band=[1, 5],
        multiplier=multiplier,
        credibility_z=0.8,
        credibility_lower=0.7,
        credibility_upper=1.1,
        override_rationale=rationale,
    )


def _lapse_mult(
    multiplier: float = 1.0,
    *,
    duration_band: list[int] | None = None,
    rationale: str = "",
) -> DecrementMultiplier:
    return DecrementMultiplier(
        product="TERM",
        gender="M",
        risk_class="STD_NS",
        duration_band=duration_band or [1, 5],
        multiplier=multiplier,
        credibility_z=0.6,
        credibility_lower=0.5,
        credibility_upper=1.2,
        override_rationale=rationale,
    )


def _seed_aset(
    db: str,
    *,
    set_id: str | None = None,
    version: int = 1,
    status: AssumptionSetStatus = AssumptionSetStatus.DRAFT,
    source_run: str = "run-1",
    mort: list[DecrementMultiplier] | None = None,
    lapse: list[DecrementMultiplier] | None = None,
) -> str:
    """Seed a minimal assumption set (YAML + metadata row); return its id."""
    set_id = set_id or str(uuid.uuid4())
    yaml_dir = Path(db).parent / "assumption_sets"
    aset = AssumptionSet(
        id=set_id,
        version=version,
        status=status,
        effective_date=date.today().isoformat(),
        author_id="a.analyst",
        basis="best-estimate",
        source_study_run_id=source_run,
        rdr=0.09,
        earned_rate_ga=0.05,
        earned_rate_sa=0.06,
        tax_rate=0.21,
        expense_inflation=0.025,
        rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0,
        maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=mort if mort is not None else [_mult()],
        lapse_multipliers=lapse if lapse is not None else [],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={},
        yaml_file_path=str(yaml_dir / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _seed_tev_run(db: str, assumption_set_id: str, total_tev: float) -> None:
    """Seed a baseline (sensitivity_id NULL) gold_tev_run_log row."""
    con = duckdb.connect(db)
    try:
        con.execute(
            "INSERT INTO gold_tev_run_log ("
            "tev_run_id, assumption_set_id, sensitivity_id, run_ts, "
            "model_point_hash, config_hash, code_version, projection_years, "
            "status, total_tev) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                str(uuid.uuid4()), assumption_set_id, None, datetime.utcnow(),
                "mp-hash", "cfg-hash", "2.0", 60, "COMPLETE", total_tev,
            ],
        )
    finally:
        con.close()


def _seed_study_run(db: str, run_id: str, data_snapshot_hash: str) -> None:
    con = duckdb.connect(db)
    try:
        con.execute(
            "INSERT INTO gold_study_runs ("
            "run_id, run_ts, product_codes, study_start_date, study_end_date, "
            "exposure_method, mortality_table, credibility_method, "
            "data_snapshot_hash, config_hash, code_version, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id, datetime.utcnow(), '["TERM"]', date(2016, 1, 1),
                date(2023, 12, 31), "ANNUAL", "2015_VBT", "LF",
                data_snapshot_hash, "cfg-hash", "1.0", "COMPLETE",
            ],
        )
    finally:
        con.close()


def _write_min_tev_config(tmp_path) -> str:
    p = tmp_path / "tev_config.yaml"
    p.write_text("rdr: 0.09\nearned_rate_ga: 0.05\n", encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Status enum (FR-4-07)
# ---------------------------------------------------------------------------

def test_draft_status_member_exists():
    assert AssumptionSetStatus.DRAFT.value == "DRAFT"


# ---------------------------------------------------------------------------
# create_version + lineage_root (FR-4-07)
# ---------------------------------------------------------------------------

def test_create_version_records_parent_link(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    child = create_version(root, "run-1", _user(), db_path=db)
    assert child != root
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT parent_set_id, status, version FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [child],
        ).fetchone()
    finally:
        con.close()
    assert row[0] == root            # parent link recorded
    assert row[1] == "DRAFT"         # new version starts DRAFT
    assert row[2] == 2               # version incremented from parent


def test_create_version_clones_parent_multipliers(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, mort=[_mult(0.92)])
    child = create_version(root, "run-1", _user(), db_path=db)
    child_set = load_assumption_set(child, Path(db))
    assert len(child_set.mortality_multipliers) == 1
    assert child_set.mortality_multipliers[0].multiplier == 0.92


def test_lineage_root_walks_to_root(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    child = create_version(root, "run-1", _user(), db_path=db)
    grandchild = create_version(child, "run-1", _user(), db_path=db)
    assert lineage_root(grandchild, db_path=db) == root
    assert lineage_root(child, db_path=db) == root
    assert lineage_root(root, db_path=db) == root


def test_create_root_version_seeds_draft_with_no_parent(gov_env, tmp_path):
    db = gov_env["db"]
    _seed_study_run(db, "run-root", data_snapshot_hash="snap")
    tev_cfg = _write_min_tev_config(tmp_path)
    new_id = create_version(
        None, "run-root", _user(),
        db_path=db, tev_config_path=tev_cfg,
        output_yaml_dir=str(tmp_path / "asets"),
    )
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT parent_set_id, status, source_study_run_id "
            "FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [new_id],
        ).fetchone()
    finally:
        con.close()
    assert row[0] is None            # root: no parent
    assert row[1] == "DRAFT"
    assert row[2] == "run-root"


# ---------------------------------------------------------------------------
# approve_and_supersede (FR-4-08; NFR-G-05)
# ---------------------------------------------------------------------------

def test_approve_sets_approved_status_and_range(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT status, effective_from, effective_to, approved_ts "
            "FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [root],
        ).fetchone()
    finally:
        con.close()
    assert row[0] == "APPROVED"
    assert row[1] == date(2024, 1, 1)
    assert row[2] == date(2024, 12, 31)
    assert row[3] is not None


def test_second_approval_supersedes_first(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    child = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(child, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    con = duckdb.connect(db, read_only=True)
    try:
        r = con.execute(
            "SELECT status, superseded_by FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?", [root]).fetchone()
        c = con.execute(
            "SELECT status FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?", [child]).fetchone()
        approved = con.execute(
            "SELECT COUNT(*) FROM gold_assumption_sets WHERE status = 'APPROVED'"
        ).fetchone()[0]
    finally:
        con.close()
    assert r[0] == "SUPERSEDED"
    assert r[1] == child             # superseded_by points to the new version
    assert c[0] == "APPROVED"
    assert approved == 1             # ≤1 APPROVED-current per lineage


def test_overlapping_effective_range_rejected(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    child = create_version(root, "run-1", _user(), db_path=db)
    with pytest.raises(OverlappingEffectiveRange):
        approve_and_supersede(child, date(2024, 6, 1), date(2025, 6, 1), db_path=db)
    # No partial mutation: child stays DRAFT, root stays APPROVED.
    con = duckdb.connect(db, read_only=True)
    try:
        c_status = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [child]).fetchone()[0]
        r_status = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [root]).fetchone()[0]
    finally:
        con.close()
    assert c_status == "DRAFT"
    assert r_status == "APPROVED"


# ---------------------------------------------------------------------------
# resolve_live_set (FR-4-09)
# ---------------------------------------------------------------------------

def test_resolve_live_set_returns_set_in_range(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    assert resolve_live_set(root, date(2024, 6, 15), db_path=db) == root
    assert resolve_live_set(root, date(2023, 1, 1), db_path=db) is None


def test_resolve_live_set_ignores_superseded(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    child = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(child, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    # root is now SUPERSEDED → not live for its old window
    assert resolve_live_set(root, date(2024, 6, 15), db_path=db) is None
    # child is the live set for its window
    assert resolve_live_set(root, date(2025, 6, 15), db_path=db) == child


# ---------------------------------------------------------------------------
# compare_versions (FR-4-10)
# ---------------------------------------------------------------------------

def test_compare_versions_reports_changed_cells_delta_tev_rationale(gov_env):
    db = gov_env["db"]
    a = _seed_aset(db, mort=[_mult(0.90)])
    b = _seed_aset(db, mort=[_mult(1.05, rationale="strengthened for adverse trend")])
    _seed_tev_run(db, a, total_tev=100.0)
    _seed_tev_run(db, b, total_tev=130.0)

    diff = compare_versions(a, b, db_path=db)

    assert diff.delta_tev == pytest.approx(30.0)
    assert len(diff.changed_cells) == 1
    cell = diff.changed_cells[0]
    assert cell["old"] == 0.90
    assert cell["new"] == 1.05
    assert "adverse trend" in cell["rationale"]
    assert any("adverse trend" in r for r in diff.rationale_by_cell.values())


def test_compare_versions_no_change_empty_diff(gov_env):
    db = gov_env["db"]
    a = _seed_aset(db, mort=[_mult(0.90)])
    b = _seed_aset(db, mort=[_mult(0.90)])
    _seed_tev_run(db, a, total_tev=100.0)
    _seed_tev_run(db, b, total_tev=100.0)
    diff = compare_versions(a, b, db_path=db)
    assert diff.changed_cells == []
    assert diff.delta_tev == pytest.approx(0.0)


def test_compare_versions_delta_tev_nan_when_tev_missing(gov_env):
    db = gov_env["db"]
    a = _seed_aset(db, mort=[_mult(0.90)])
    b = _seed_aset(db, mort=[_mult(0.90)])
    diff = compare_versions(a, b, db_path=db)   # no TEV runs seeded
    assert math.isnan(diff.delta_tev)


# ---------------------------------------------------------------------------
# reproducibility_stamp (FR-4-11)
# ---------------------------------------------------------------------------

def test_reproducibility_stamp_traces_to_study_run(gov_env):
    db = gov_env["db"]
    _seed_study_run(db, "run-xyz", data_snapshot_hash="snap-abc")
    aset = _seed_aset(db, source_run="run-xyz")
    stamp = reproducibility_stamp(aset, db_path=db)
    assert stamp["source_study_run_id"] == "run-xyz"
    assert stamp["data_snapshot_hash"] == "snap-abc"
    assert stamp["assumption_set_id"] == aset


# ---------------------------------------------------------------------------
# Persistence guard: a plain re-save must not wipe lineage columns
# ---------------------------------------------------------------------------

def test_plain_resave_preserves_parent_and_effective_dates(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    child = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(child, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    # Simulate a later plain save (e.g. a Stage-2 editor re-save).
    cset = load_assumption_set(child, Path(db))
    save_assumption_set(cset, Path(db))
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT parent_set_id, effective_from, effective_to "
            "FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [child],
        ).fetchone()
    finally:
        con.close()
    assert row[0] == root
    assert row[1] == date(2025, 1, 1)
    assert row[2] == date(2025, 12, 31)


def test_draft_resave_preserves_parent_link(gov_env):
    """The realistic Stage-2 case: a DRAFT child edited and re-saved keeps its
    parent_set_id (the metadata INSERT omits it)."""
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    child = create_version(root, "run-1", _user(), db_path=db)
    cset = load_assumption_set(child, Path(db))
    cset.mortality_multipliers[0].multiplier = 0.99   # an edit
    save_assumption_set(cset, Path(db))
    con = duckdb.connect(db, read_only=True)
    try:
        parent = con.execute(
            "SELECT parent_set_id FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?", [child]).fetchone()[0]
    finally:
        con.close()
    assert parent == root


# ---------------------------------------------------------------------------
# Strengthening: boundary / robustness / multi-version / multi-decrement
# ---------------------------------------------------------------------------

def test_resolve_live_set_boundary_dates_inclusive(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    # Both endpoints are inclusive.
    assert resolve_live_set(root, date(2024, 1, 1), db_path=db) == root
    assert resolve_live_set(root, date(2024, 12, 31), db_path=db) == root
    # Just outside the range → no live set.
    assert resolve_live_set(root, date(2023, 12, 31), db_path=db) is None
    assert resolve_live_set(root, date(2025, 1, 1), db_path=db) is None


def test_resolve_live_set_accepts_any_member_id(gov_env):
    """Passing any lineage member (not just the root) resolves the live set."""
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    child = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(child, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    # Query via the child id; it is normalised to the lineage root.
    assert resolve_live_set(child, date(2025, 6, 1), db_path=db) == child
    assert resolve_live_set(child, date(2024, 6, 1), db_path=db) is None
    # An unknown id returns None, never raises.
    assert resolve_live_set("does-not-exist", date(2025, 6, 1), db_path=db) is None


def test_overlap_check_includes_superseded_ranges(gov_env):
    """Non-overlap is enforced across the whole lineage history, including a
    now-SUPERSEDED set's window."""
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    v2 = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(v2, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    # root is now SUPERSEDED with window 2024; a new version overlapping it is rejected.
    v3 = create_version(v2, "run-1", _user(), db_path=db)
    with pytest.raises(OverlappingEffectiveRange):
        approve_and_supersede(v3, date(2024, 6, 1), date(2024, 9, 30), db_path=db)


def test_approve_rejects_inverted_range(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    with pytest.raises(ValueError):
        approve_and_supersede(root, date(2024, 12, 31), date(2024, 1, 1), db_path=db)


def test_three_version_lineage_single_approved(gov_env):
    db = gov_env["db"]
    root = _seed_aset(db, source_run="run-1")
    approve_and_supersede(root, date(2023, 1, 1), date(2023, 12, 31), db_path=db)
    v2 = create_version(root, "run-1", _user(), db_path=db)
    approve_and_supersede(v2, date(2024, 1, 1), date(2024, 12, 31), db_path=db)
    v3 = create_version(v2, "run-1", _user(), db_path=db)
    approve_and_supersede(v3, date(2025, 1, 1), date(2025, 12, 31), db_path=db)
    con = duckdb.connect(db, read_only=True)
    try:
        approved = con.execute(
            "SELECT COUNT(*) FROM gold_assumption_sets WHERE status = 'APPROVED'"
        ).fetchone()[0]
        statuses = {
            r[0]: r[1] for r in con.execute(
                "SELECT assumption_set_id, status FROM gold_assumption_sets"
            ).fetchall()
        }
    finally:
        con.close()
    assert approved == 1
    assert statuses[root] == "SUPERSEDED"
    assert statuses[v2] == "SUPERSEDED"
    assert statuses[v3] == "APPROVED"
    # The live set is the only APPROVED one and resolves only in its window.
    assert resolve_live_set(root, date(2025, 6, 1), db_path=db) == v3
    assert resolve_live_set(root, date(2024, 6, 1), db_path=db) is None


def test_lineage_root_unknown_id_raises(gov_env):
    with pytest.raises(ValueError):
        lineage_root("nope", db_path=gov_env["db"])


def test_reproducibility_stamp_unknown_id_raises(gov_env):
    with pytest.raises(ValueError):
        reproducibility_stamp("nope", db_path=gov_env["db"])


def test_compare_versions_detects_added_and_removed_cells(gov_env):
    db = gov_env["db"]
    # a has a lapse cell at duration [1,5]; b drops it and adds one at [6,10].
    a = _seed_aset(db, mort=[_mult(0.90)], lapse=[_lapse_mult(1.0, duration_band=[1, 5])])
    b = _seed_aset(db, mort=[_mult(0.90)], lapse=[_lapse_mult(1.1, duration_band=[6, 10])])
    diff = compare_versions(a, b, db_path=db)
    changes = {(c["decrement"], tuple(c["dimension"]["duration_band"])): (c["old"], c["new"])
               for c in diff.changed_cells}
    assert ("lapse", (1, 5)) in changes
    assert changes[("lapse", (1, 5))] == (1.0, None)     # removed
    assert ("lapse", (6, 10)) in changes
    assert changes[("lapse", (6, 10))] == (None, 1.1)    # added


def test_compare_versions_spans_multiple_decrements(gov_env):
    db = gov_env["db"]
    a = _seed_aset(db, mort=[_mult(0.90)], lapse=[_lapse_mult(1.0)])
    b = _seed_aset(
        db,
        mort=[_mult(1.05, rationale="mortality strengthened")],
        lapse=[_lapse_mult(0.85, rationale="lapse softened")],
    )
    diff = compare_versions(a, b, db_path=db)
    decrements = {c["decrement"] for c in diff.changed_cells}
    assert decrements == {"mortality", "lapse"}
    assert any("mortality strengthened" in r for r in diff.rationale_by_cell.values())
    assert any("lapse softened" in r for r in diff.rationale_by_cell.values())


def test_create_version_clone_isolated_from_parent(gov_env):
    """Editing+saving a cloned child must not mutate the parent's stored values."""
    db = gov_env["db"]
    root = _seed_aset(db, mort=[_mult(0.92)])
    child = create_version(root, "run-1", _user(), db_path=db)
    cset = load_assumption_set(child, Path(db))
    cset.mortality_multipliers[0].multiplier = 1.5
    save_assumption_set(cset, Path(db))
    parent_set = load_assumption_set(root, Path(db))
    assert parent_set.mortality_multipliers[0].multiplier == 0.92
