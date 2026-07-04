"""Cleanup for Stress Test 3 — removes TRM-WARNTEST from all layers.

Run after the stress test is complete:
    python tests/cleanup_stress_test_3.py
"""

import duckdb
from pathlib import Path

DB_PATH = Path("data/experience_study.duckdb")
CSV_PATH = Path("synthetic_data/output/term_policies.csv")

POLICY_ID = "TRM-WARNTEST"


def cleanup_database() -> None:
    """Remove TRM-WARNTEST from all database tables."""
    con = duckdb.connect(str(DB_PATH))
    try:
        # Each table may use a different primary key column name
        table_pk = {
            "silver_term_policies": "policy_id",
            "bronze_term_policies": "raw_policy_id",
            "gold_dq_quarantine":   "policy_id",
            "gold_exposure_segments": "policy_id",
        }
        for table, pk in table_pk.items():
            result = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {pk} = ?", [POLICY_ID]
            ).fetchone()[0]
            if result:
                con.execute(
                    f"DELETE FROM {table} WHERE {pk} = ?", [POLICY_ID]
                )
                print(f"  Removed {result} row(s) from {table}")
            else:
                print(f"  {table}: nothing to remove")
    finally:
        con.close()


def cleanup_csv() -> None:
    """Remove TRM-WARNTEST line from source CSV."""
    lines = CSV_PATH.read_text().splitlines(keepends=True)
    original_count = len(lines)
    lines = [l for l in lines if not l.startswith(f"{POLICY_ID},")]
    if len(lines) < original_count:
        CSV_PATH.write_text("".join(lines))
        print(f"  Removed {POLICY_ID} from {CSV_PATH}")
    else:
        print(f"  {CSV_PATH}: nothing to remove")


if __name__ == "__main__":
    print("=== Stress Test 3 Cleanup ===")
    print("Database:")
    cleanup_database()
    print("CSV:")
    cleanup_csv()
    print("Done.")
