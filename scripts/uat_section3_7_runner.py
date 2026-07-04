"""Phase 4 UAT — Test 3.7 (Governed re-open, FR-4-18) — engine harness.

Governed re-open has no dedicated UI in this build; workflow.reopen() is engine-only.
This drives it exactly as the governed flow would: an APPROVED assumption set is
re-opened with a mandatory justification, producing a NEW DRAFT child version while the
original stays immutable (still APPROVED).

NON-DESTRUCTIVE: it copies the live data/experience_study.duckdb into a temp dir and runs
every engine call against the COPY (db_path= override). The live DB is never opened
writable. reopen() writes one child assumption-set YAML via create_version() to the default
location; the harness captures that path and deletes the stray file on exit.

Usage (from the project root, venv active):
    .venv/bin/python scripts/uat_section3_7_runner.py

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

from src.governance.lineage import approve_and_supersede  # noqa: E402
from src.governance.users import get_user_by_username  # noqa: E402
from src.governance.workflow import reopen  # noqa: E402

LIVE_DB = ROOT_DIR / "data" / "experience_study.duckdb"

_results: list[tuple[str, bool, str]] = []
_stray_yamls: list[Path] = []


def record(row: str, ok: bool, detail: str) -> None:
    _results.append((row, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {row}: {detail}")


def _row(db: str, set_id: str) -> tuple:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT status, parent_set_id, version, description, yaml_file_path "
            "FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()
    finally:
        con.close()


def _note_stray_yaml(db: str, child_id: str, work: Path) -> None:
    """Record the child YAML reopen() wrote outside the temp workdir, for cleanup."""
    r = _row(db, child_id)
    if r and r[4]:
        p = Path(r[4])
        if p.exists() and work not in p.resolve().parents:
            _stray_yamls.append(p)


def run() -> int:
    if not LIVE_DB.exists():
        raise SystemExit(f"Live DB not found at {LIVE_DB}")
    with tempfile.TemporaryDirectory(prefix="uat_s37_") as tmp:
        work = Path(tmp)
        db = str(work / "copy.duckdb")
        shutil.copy2(LIVE_DB, db)
        print(f"Working on an isolated copy under {work} (live DB never written)")

        analyst = get_user_by_username("a.analyst", db_path=db)
        assert analyst is not None, "seed user a.analyst missing"

        # --- Precondition: get an APPROVED set to re-open ---------------------
        con = duckdb.connect(db, read_only=True)
        set_id = con.execute(
            "SELECT assumption_set_id FROM gold_assumption_sets ORDER BY created_ts LIMIT 1"
        ).fetchone()[0]
        con.close()
        before = _row(db, set_id)
        if before[0] != "APPROVED":
            # Approve via the lineage-approve path purely to set up the precondition.
            approve_and_supersede(set_id, dt.date(2026, 1, 1), dt.date(2026, 12, 31), db_path=db)
        approved = _row(db, set_id)
        print(f"target set {set_id} status={approved[0]} version={approved[2]}")
        assert approved[0] == "APPROVED", "precondition setup failed (set not APPROVED)"

        # --- 3.7-A: re-open the APPROVED set → new DRAFT child ----------------
        child_id = reopen(set_id, analyst, "macro update warrants a revision", db_path=db)
        _note_stray_yaml(db, child_id, work)
        crow = _row(db, child_id)
        ok_a = (
            child_id != set_id
            and crow[0] == "DRAFT"
            and crow[1] == set_id
            and crow[2] == approved[2] + 1
        )
        record("3.7-A (new DRAFT child created)", ok_a,
               f"child={child_id} status={crow[0]} parent={crow[1]} version={crow[2]} "
               f"(parent v{approved[2]})")

        # --- 3.7-B: original immutable (unchanged, still APPROVED) ------------
        after = _row(db, set_id)
        ok_b = (
            after[0] == "APPROVED"
            and after[2] == approved[2]        # version unchanged
            and after[1] == approved[1]        # parent link unchanged
        )
        record("3.7-B (original immutable)", ok_b,
               f"original status={after[0]} version={after[2]} (was {approved[0]}/v{approved[2]})")

        # --- 3.7-C: justification recorded durably on the child --------------
        ok_c = bool(crow[3]) and "macro update warrants a revision" in crow[3]
        record("3.7-C (justification recorded on child)", ok_c, f"description={crow[3]!r}")

        # --- 3.7-D: blank justification rejected -----------------------------
        try:
            reopen(set_id, analyst, "   ", db_path=db)
            record("3.7-D (blank justification rejected)", False, "blank justification was NOT rejected")
        except ValueError as e:
            record("3.7-D (blank justification rejected)", True, f"ValueError: {e}")

        # --- 3.7-E: re-open of a non-APPROVED set rejected -------------------
        # The child is DRAFT, so re-opening it must be refused.
        try:
            reopen(child_id, analyst, "reopen a draft", db_path=db)
            record("3.7-E (non-APPROVED reopen rejected)", False, "DRAFT set was NOT rejected")
        except ValueError as e:
            record("3.7-E (non-APPROVED reopen rejected)", True, f"ValueError: {e}")

        return _summary()


def _summary() -> int:
    print("\n================ Test 3.7 summary ================")
    for row, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {row}")
    n_pass = sum(1 for _, ok, _ in _results if ok)
    print("  ------------------------------------------------")
    print(f"  {n_pass}/{len(_results)} checks PASS")
    return 0 if n_pass == len(_results) else 1


if __name__ == "__main__":
    try:
        code = run()
    finally:
        for p in _stray_yamls:
            try:
                p.unlink()
                print(f"cleaned stray child YAML: {p}")
            except OSError:
                pass
    sys.exit(code)
