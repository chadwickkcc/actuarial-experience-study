"""Envelope schema migration — drops and recreates gold_workflow_iterations
and gold_assumption_approvals with the updated v1.1 column layout.

WARNING: This is a DESTRUCTIVE migration. Both tables are dropped and
recreated. All existing workflow iteration and approval records will be
permanently lost.

This is acceptable in UAT where no production approval records exist.

Usage:
    python scripts/migrate_envelope_schema.py --confirm

The --confirm flag is required. Without it the script exits without
making any changes.
"""
import argparse
import sys
from pathlib import Path

# Allow import of project modules regardless of working directory
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import duckdb
from src.utils.db_init import init_database

_DB_PATH = _ROOT / "data" / "experience_study.duckdb"

_WARNING = """
============================================================
  DESTRUCTIVE MIGRATION — READ BEFORE PROCEEDING
============================================================

This script will DROP the following tables:
  • gold_workflow_iterations
  • gold_assumption_approvals

All workflow audit records and governance approvals stored
in these tables will be permanently deleted.

This is intentional for the v2.1 → schema-v1.1 migration
(optimiser_* columns → envelope_* columns) and is only safe
to run in UAT environments with NO production approval data.

If this database contains production approval records, stop
now and perform a manual column-level migration instead.

Confirm you have verified no production data exists before
proceeding. Pass --confirm to execute.
============================================================
"""


def migrate(db_path: Path) -> None:
    """Drop and recreate the two affected gold tables."""
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))
    try:
        print("Dropping gold_workflow_iterations …")
        con.execute("DROP TABLE IF EXISTS gold_workflow_iterations")
        print("Dropping gold_assumption_approvals …")
        con.execute("DROP TABLE IF EXISTS gold_assumption_approvals")
    finally:
        con.close()

    print("Recreating schema via init_database() …")
    init_database(str(db_path))
    print("Migration complete. New schema applied.")

    con2 = duckdb.connect(str(db_path), read_only=True)
    try:
        cols_iter = [
            r[0] for r in con2.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'gold_workflow_iterations' ORDER BY ordinal_position"
            ).fetchall()
        ]
        cols_appr = [
            r[0] for r in con2.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'gold_assumption_approvals' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        con2.close()

    print(f"\ngold_workflow_iterations columns ({len(cols_iter)}):")
    for c in cols_iter:
        print(f"  {c}")
    print(f"\ngold_assumption_approvals columns ({len(cols_appr)}):")
    for c in cols_appr:
        print(f"  {c}")

    # Verify key columns present and old columns absent
    assert "envelope_run_flag" in cols_iter, "FAIL: envelope_run_flag missing from iterations"
    assert "optimiser_run_flag" not in cols_iter, "FAIL: old optimiser_run_flag still present"
    assert "optimiser_suggestion_adopted" not in cols_iter, "FAIL: old column still present"
    assert "envelope_run_flag" in cols_appr, "FAIL: envelope_run_flag missing from approvals"
    assert "envelope_tev_min" in cols_appr, "FAIL: envelope_tev_min missing"
    assert "envelope_tev_max" in cols_appr, "FAIL: envelope_tev_max missing"
    assert "proposed_envelope_percentile" in cols_appr, "FAIL: percentile column missing"
    assert "optimiser_used_flag" not in cols_appr, "FAIL: old optimiser_used_flag still present"
    assert "optimiser_adopted_flag" not in cols_appr, "FAIL: old column still present"

    print("\nAll schema assertions passed. Migration verified.")


def main() -> None:
    """Entry point — requires --confirm flag."""
    print(_WARNING)

    parser = argparse.ArgumentParser(
        description="Migrate gold_workflow_iterations and gold_assumption_approvals to envelope schema."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm that no production data exists and authorise the destructive DROP.",
    )
    parser.add_argument(
        "--db",
        default=str(_DB_PATH),
        help=f"Path to DuckDB file (default: {_DB_PATH})",
    )
    args = parser.parse_args()

    if not args.confirm:
        print("Aborted — pass --confirm to authorise the migration.")
        sys.exit(1)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found at {db_path}. Run init_database first.")
        sys.exit(2)

    migrate(db_path)


if __name__ == "__main__":
    main()
