"""Phase 4 UAT — Section 2 (Versioning & Lineage, FR-4-07..11) — engine harness.

Versioning & lineage effective-dating/supersession/compare has an engine
(src/governance/lineage.py) plus, since 2026-07-04, the Versioning & Lineage UI page
(ui/views/29_assumption_lineage.py). This harness drives the engine directly to assert
the six Section-2 behaviours (2.1–2.6) end-to-end on real data.

NON-DESTRUCTIVE: it copies the live data/experience_study.duckdb into a temp dir and runs
every engine call against the COPY (``db_path=`` override); the live DB is never opened
writable. To be deterministic regardless of prior publishes/re-opens on the live DB, it
clears effective ranges across the copied lineage before the effective-dating checks and
writes any child YAML into the temp dir (auto-cleaned).

Usage (from the project root, venv active):
    .venv/bin/python scripts/uat_section2.py

Exit code 0 iff all checks PASS.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb  # noqa: E402

from src.governance import lineage  # noqa: E402
from src.governance.users import get_user_by_username  # noqa: E402
from src.utils.types import VersionDiff  # noqa: E402

LIVE_DB = ROOT_DIR / "data" / "experience_study.duckdb"

_results: list[tuple[str, bool, str]] = []


def record(row: str, ok: bool, detail: str) -> None:
    _results.append((row, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {row}: {detail}")


def _row(db: str, sid: str) -> tuple:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT status, parent_set_id, version, effective_from, effective_to, "
            "superseded_by FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [sid],
        ).fetchone()
    finally:
        con.close()


def _pick_root(db: str) -> tuple[str, str]:
    """A lineage-root set (parent IS NULL) + its source study run, for cloning."""
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT assumption_set_id, source_study_run_id FROM gold_assumption_sets "
            "WHERE parent_set_id IS NULL ORDER BY created_ts LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit("No root assumption set in DB — create one in TEV Stage 1 first.")
    return row[0], row[1]


def _clear_ranges(db: str) -> None:
    """Clear effective ranges/supersession on the COPY for a deterministic start."""
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets "
            "SET effective_from = NULL, effective_to = NULL, superseded_by = NULL"
        )
    finally:
        con.close()


def run() -> int:
    if not LIVE_DB.exists():
        raise SystemExit(f"Live DB not found at {LIVE_DB}")
    with tempfile.TemporaryDirectory(prefix="uat_s2_") as tmp:
        work = Path(tmp)
        db = str(work / "copy.duckdb")
        shutil.copy2(LIVE_DB, db)
        print(f"Working on an isolated copy under {work} (live DB never written)")

        analyst = get_user_by_username("a.analyst", db_path=db)
        assert analyst is not None, "seed user a.analyst missing"
        _clear_ranges(db)
        root_set, study_run = _pick_root(db)
        if not study_run:
            study_run = lineage.reproducibility_stamp(root_set, db_path=db)["source_study_run_id"]
        root_before = _row(db, root_set)
        print(f"root set {root_set} v{root_before[2]}; source run {study_run}")

        # --- 2.1: create a DRAFT child version --------------------------------
        child = lineage.create_version(
            root_set, study_run, analyst, db_path=db, output_yaml_dir=str(work)
        )
        crow = _row(db, child)
        ok_21 = crow[0] == "DRAFT" and crow[1] == root_set and crow[2] == root_before[2] + 1
        record("2.1 (DRAFT child, parent link, version+1)", ok_21,
               f"child={child} status={crow[0]} parent={crow[1]} version={crow[2]}")

        # --- 2.2 + 2.3: supersession + effective dating + live-set resolve ----
        lineage.approve_and_supersede(root_set, dt.date(2026, 1, 1), dt.date(2026, 6, 30), db_path=db)
        lineage.approve_and_supersede(child, dt.date(2026, 7, 1), dt.date(2026, 12, 31), db_path=db)
        root_after = _row(db, root_set)
        child_after = _row(db, child)
        root_id = lineage.lineage_root(child, db_path=db)
        live = lineage.resolve_live_set(root_id, dt.date(2026, 7, 3), db_path=db)
        ok_22 = (
            root_after[0] == "SUPERSEDED" and root_after[5] == child
            and child_after[0] == "APPROVED"
        )
        record("2.2 (prior APPROVED superseded; child APPROVED)", ok_22,
               f"root={root_after[0]} superseded_by={root_after[5]} child={child_after[0]}")
        record("2.3 (live-set resolves to the in-range version)", live == child,
               f"resolve_live_set(2026-07-03)={live} (expected {child})")

        # --- 2.4: overlapping range rejected, no write -----------------------
        grandchild = lineage.create_version(
            child, study_run, analyst, db_path=db, output_yaml_dir=str(work)
        )
        try:
            lineage.approve_and_supersede(
                grandchild, dt.date(2026, 10, 1), dt.date(2027, 3, 31), db_path=db
            )
            record("2.4 (overlapping range rejected, no write)", False, "overlap was NOT rejected")
        except lineage.OverlappingEffectiveRange as e:
            gc = _row(db, grandchild)
            ch = _row(db, child)
            ok_24 = gc[0] == "DRAFT" and ch[0] == "APPROVED"
            record("2.4 (overlapping range rejected, no write)", ok_24,
                   f"raised OverlappingEffectiveRange; grandchild={gc[0]} child={ch[0]}")

        # --- 2.5: cross-version comparison -----------------------------------
        diff = lineage.compare_versions(root_set, child, db_path=db)
        ok_25 = (
            isinstance(diff, VersionDiff)
            and isinstance(diff.changed_cells, list)
            and isinstance(diff.rationale_by_cell, dict)
        )
        record("2.5 (VersionDiff: changed cells + ΔTEV + rationale)", ok_25,
               f"changed_cells={len(diff.changed_cells)} ΔTEV={diff.delta_tev} "
               f"rationale_keys={len(diff.rationale_by_cell)} (empty for a bare clone)")

        # --- 2.6: reproducibility --------------------------------------------
        stamp = lineage.reproducibility_stamp(child, db_path=db)
        ok_26 = (
            stamp.get("source_study_run_id") == study_run
            and "data_snapshot_hash" in stamp and "ai_model_id" in stamp
        )
        record("2.6 (reproducibility traces to source run)", ok_26,
               f"source_study_run_id={stamp.get('source_study_run_id')} "
               f"snapshot={stamp.get('data_snapshot_hash')}")

        return _summary()


def _summary() -> int:
    print("\n================ Section 2 summary ================")
    for row, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {row}")
    n_pass = sum(1 for _, ok, _ in _results if ok)
    print("  ------------------------------------------------")
    print(f"  {n_pass}/{len(_results)} checks PASS")
    return 0 if n_pass == len(_results) else 1


if __name__ == "__main__":
    sys.exit(run())
