"""Stress Test 2: Force a Reconciliation Failure (DQ-TL-14).

Corrupts issue_date > termination_date for 50 terminated policies, which breaks
the in-force identity:
    BEG_IF + NEW_ISSUES - DECREMENTS = END_IF

A policy with issue_date after termination_date appears as a decrement in year Y
but is absent from BEG_IF in year Y, making the left side smaller than the right.
This triggers DQ-TL-01 (date-order check) and DQ-TL-14 (reconciliation check).

Run directly (silver_term_policies must have exactly 3,200 records):
    python tests/stress_test_recon_failure.py

Cleanup is automatic — original issue_dates are restored at the end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_quality.runner import DQCriticalFailure, run_dq_checks

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "experience_study.duckdb"
CORRUPT_COUNT = 50
EXPECTED_COUNT = 3200


def get_active_run_id(con: duckdb.DuckDBPyConnection) -> str | None:
    """Return the _etl_run_id with the most records in silver_term_policies."""
    rows = con.execute("""
        SELECT _etl_run_id, COUNT(*) AS n
        FROM silver_term_policies
        GROUP BY _etl_run_id
        ORDER BY n DESC
        LIMIT 1
    """).fetchall()
    return rows[0][0] if rows else None


def count_records(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    """Count records in silver_term_policies for a given run."""
    return con.execute(
        "SELECT COUNT(*) FROM silver_term_policies WHERE _etl_run_id = ?",
        [run_id],
    ).fetchone()[0]


def main() -> None:
    """Execute the reconciliation-failure stress test."""
    print("=" * 60)
    print("Stress Test 2: Force Reconciliation Failure (DQ-TL-14)")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"\n[ERROR] Database not found at {DB_PATH}")
        sys.exit(1)

    con = duckdb.connect(str(DB_PATH))

    # ── Step 1: Identify active ETL run ──────────────────────────────────────
    run_id = get_active_run_id(con)
    if run_id is None:
        print("\n[ERROR] silver_term_policies is empty.")
        print("Run a Term Life study from the Streamlit UI (or CLI) first.")
        con.close()
        sys.exit(1)

    print(f"\nActive _etl_run_id : {run_id}")
    current_count = count_records(con, run_id)
    print(f"Current record count : {current_count}")

    if current_count < EXPECTED_COUNT:
        print(f"\n[ERROR] Expected {EXPECTED_COUNT} records, found {current_count}.")
        print("Re-run the Term Life ETL pipeline to restore silver_term_policies.")
        con.close()
        sys.exit(1)

    # ── Step 2: Find 50 terminated policies to corrupt ────────────────────────
    # Pick terminated policies whose issue_date is safely before termination_date
    targets = con.execute("""
        SELECT policy_id, issue_date, termination_date
        FROM silver_term_policies
        WHERE _etl_run_id = ?
          AND termination_date IS NOT NULL
          AND issue_date < termination_date
        LIMIT ?
    """, [run_id, CORRUPT_COUNT]).fetchall()

    if len(targets) < CORRUPT_COUNT:
        print(f"\n[ERROR] Only {len(targets)} corruptible records found (need {CORRUPT_COUNT}).")
        con.close()
        sys.exit(1)

    target_ids = [row[0] for row in targets]
    print(f"\nSelected {len(target_ids)} terminated policies for date corruption.")
    print(f"  Example: policy_id={targets[0][0]}")
    print(f"    issue_date={targets[0][1]}  termination_date={targets[0][2]}")

    # ── Step 3: Corrupt issue_date → after termination_date ──────────────────
    # Set issue_date = termination_date + 365 days (clearly after termination)
    id_placeholders = ", ".join("?" for _ in target_ids)
    con.execute(f"""
        UPDATE silver_term_policies
        SET issue_date = termination_date + INTERVAL 365 DAYS
        WHERE policy_id IN ({id_placeholders})
          AND _etl_run_id = ?
    """, target_ids + [run_id])

    print(f"\n[CORRUPTED] Set issue_date = termination_date + 365 days for {CORRUPT_COUNT} policies.")
    print(f"  Example after: issue_date would be {targets[0][2]} + 365 days")

    # ── Step 4: Run DQ checks (non-halting) ───────────────────────────────────
    print("\nRunning DQ checks (halt_on_critical=False) ...")
    result = run_dq_checks("TERM", DB_PATH, run_id, halt_on_critical=False)

    print(f"\n  critical_failure : {result.critical_failure}")
    print(f"  dq_score_pct     : {result.dq_score_pct:.2f}%")
    print(f"  total_records    : {result.total_records}")

    for check_id in ("DQ-TL-01", "DQ-TL-14"):
        chk = next((c for c in result.check_results if c.check_id == check_id), None)
        if chk is not None:
            status = "PASS" if chk.passed else "FAIL"
            print(f"  {check_id} : {status}  (fail_count={chk.fail_count})")
        else:
            print(f"  {check_id} : not found in results")

    # ── Step 5: Confirm halt path (DQ-TL-01 fires first) ─────────────────────
    print("\nRe-running with halt_on_critical=True to confirm pipeline-halt ...")
    try:
        run_dq_checks("TERM", DB_PATH, run_id, halt_on_critical=True)
        print("  [UNEXPECTED] No exception raised.")
    except DQCriticalFailure as exc:
        print(f"  [OK] DQCriticalFailure caught: {exc}")

    # ── Step 6: Restore original issue_dates ─────────────────────────────────
    print("\nRestoring original issue_dates ...")
    for policy_id, original_issue_date, _ in targets:
        con.execute(
            "UPDATE silver_term_policies SET issue_date = ? WHERE policy_id = ? AND _etl_run_id = ?",
            [original_issue_date, policy_id, run_id],
        )

    restored_count = count_records(con, run_id)
    print(f"[RESTORED] silver_term_policies count : {restored_count}")

    # Sanity-check a restored record
    restored = con.execute(
        "SELECT issue_date, termination_date FROM silver_term_policies WHERE policy_id = ? AND _etl_run_id = ?",
        [target_ids[0], run_id],
    ).fetchone()
    if restored:
        print(f"  Example: policy_id={target_ids[0]}  issue_date={restored[0]}  termination_date={restored[1]}")

    con.close()

    print("\n" + "=" * 60)
    print("STRESS TEST COMPLETE")
    print("  All date corruptions have been reversed.")
    print("  silver_term_policies is clean.")
    print("=" * 60)


if __name__ == "__main__":
    main()
