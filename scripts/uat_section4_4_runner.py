"""Phase 4 UAT — Test 4.4 (Audit tamper-evidence, FR-4-20/21) — engine harness.

Tamper-detection has no destructive UI path (the Governance & Audit page only *reads*
and verifies), so this drives the engine directly, exactly as a forensic check would:
it confirms ``verify_chain`` reports an untouched hash-chained log as intact, then
corrupts it two ways and confirms each is caught at the correct ``first_divergence_seq``:

  4.4-A  clean chain            -> ok=True, rows_checked = N (the live sign-offs)
  4.4-B  business-column tamper -> ok=False, divergence at the tampered row's seq
  4.4-C  deleted row (linkage)  -> ok=False, divergence at the row after the gap

NON-DESTRUCTIVE: it copies the live data/experience_study.duckdb into a temp dir and runs
every check against a fresh COPY (one per scenario). The corrupting UPDATE/DELETE only
ever touch the copy; the live DB is never opened writable (``verify_chain`` itself opens
read-only).

Usage (from the project root, venv active):
    .venv/bin/python scripts/uat_section4_4_runner.py

Exit code 0 iff all checks PASS.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb  # noqa: E402

from src.governance.audit import verify_chain  # noqa: E402

LIVE_DB = ROOT_DIR / "data" / "experience_study.duckdb"
TABLE = "gold_governance_signoffs"

_results: list[tuple[str, bool, str]] = []


def record(row: str, ok: bool, detail: str) -> None:
    _results.append((row, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {row}: {detail}")


def _copy_db(work: Path, tag: str) -> str:
    dst = work / f"copy_{tag}.duckdb"
    shutil.copy2(LIVE_DB, dst)
    return str(dst)


def _hashed_seqs(db: str) -> list[int]:
    """Ordered seq values of the hash-chained (entry_hash IS NOT NULL) rows."""
    con = duckdb.connect(db, read_only=True)
    try:
        return [
            int(r[0])
            for r in con.execute(
                f"SELECT seq FROM {TABLE} WHERE entry_hash IS NOT NULL ORDER BY seq"
            ).fetchall()
        ]
    finally:
        con.close()


def _exec_on_copy(db: str, sql: str, params: list) -> None:
    """Run one write against the COPY, then close the writable handle."""
    con = duckdb.connect(db, read_only=False)
    try:
        con.execute(sql, params)
    finally:
        con.close()


def run() -> int:
    if not LIVE_DB.exists():
        raise SystemExit(f"Live DB not found at {LIVE_DB}")

    with tempfile.TemporaryDirectory(prefix="uat_s44_") as tmp:
        work = Path(tmp)
        print(f"Working on isolated copies under {work} (live DB never written)")

        seqs = _hashed_seqs(_copy_db(work, "probe"))
        n = len(seqs)
        print(f"{TABLE} carries {n} hash-chained row(s): seq {seqs}")
        if n < 2:
            record("4.4 (precondition)", False,
                   f"need >=2 hashed sign-off rows to demonstrate tamper detection; found {n}. "
                   "Run the Stage-4 sign-off chain on an assumption set first.")
            return _summary()

        # --- 4.4-A: an untouched chain verifies intact ----------------------
        db_a = _copy_db(work, "clean")
        res_a = verify_chain(TABLE, db_path=db_a)
        ok_a = res_a.ok and res_a.rows_checked == n
        record("4.4-A (clean chain intact)", ok_a,
               f"ok={res_a.ok} rows_checked={res_a.rows_checked} (expected {n})")

        # --- 4.4-B: corrupt a business column on the 2nd row ----------------
        # The preceding row still verifies, so divergence is reported AT the
        # tampered row (recomputed entry_hash no longer matches the stored one).
        db_b = _copy_db(work, "tamper")
        target = seqs[1]
        _exec_on_copy(db_b, f"UPDATE {TABLE} SET comment = ? WHERE seq = ?",
                      ["TAMPERED — forged after sign-off", target])
        res_b = verify_chain(TABLE, db_path=db_b)
        ok_b = (not res_b.ok) and res_b.first_divergence_seq == target and res_b.rows_checked == 2
        record("4.4-B (business-column tamper detected)", ok_b,
               f"ok={res_b.ok} first_divergence_seq={res_b.first_divergence_seq} "
               f"(expected {target}) rows_checked={res_b.rows_checked}")

        # --- 4.4-C: delete a row (append-only violation) breaks linkage -----
        # Delete a non-last row; the following row's prev_hash no longer links,
        # so divergence is reported at that following row's seq.
        db_c = _copy_db(work, "delete")
        if n >= 3:
            deleted, expect_div = seqs[1], seqs[2]     # delete the middle row
        else:
            deleted, expect_div = seqs[0], seqs[1]     # 2 rows: delete the first
        _exec_on_copy(db_c, f"DELETE FROM {TABLE} WHERE seq = ?", [deleted])
        res_c = verify_chain(TABLE, db_path=db_c)
        ok_c = (not res_c.ok) and res_c.first_divergence_seq == expect_div
        record("4.4-C (deleted-row linkage break detected)", ok_c,
               f"deleted seq={deleted} -> ok={res_c.ok} "
               f"first_divergence_seq={res_c.first_divergence_seq} (expected {expect_div})")

        # --- Live DB untouched (sanity) ------------------------------------
        live_res = verify_chain(TABLE, db_path=str(LIVE_DB))
        record("4.4-D (live DB still intact after harness)", live_res.ok,
               f"ok={live_res.ok} rows_checked={live_res.rows_checked}")

        return _summary()


def _summary() -> int:
    print("\n================ Test 4.4 summary ================")
    for row, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {row}")
    n_pass = sum(1 for _, ok, _ in _results if ok)
    print("  ------------------------------------------------")
    print(f"  {n_pass}/{len(_results)} checks PASS")
    return 0 if n_pass == len(_results) else 1


if __name__ == "__main__":
    sys.exit(run())
