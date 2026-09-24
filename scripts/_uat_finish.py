"""Finish a headless demo-DB rebuild: fraud scan + the two A/E reports.

Both are buttons in the app (Fraud Monitor; Study Run Log), so a scripted rebuild
otherwise ships with no fraud results and no reports for the Run Log downloads and
the compliance pack's report links.

Last step of the rebuild sequence (reset → _uat_rerun → _uat_ai_fit →
_uat_seed_workflow → _uat_seed_ai_activity → THIS). Re-running is harmless: it
replaces the reports and adds one more fraud-scan run.

Usage:  .venv/bin/python scripts/_uat_finish.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

from ui.config import DB_PATH, REPORTS_DIR
from src.fraud import run_fraud_scan
from src.governance.users import get_user_by_username
from src.reporting.generator import (
    generate_chief_actuary_summary,
    generate_working_actuary_report,
)


def main() -> None:
    """Scan the latest COMPLETE study run for fraud and write its two reports."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    row = con.execute(
        "SELECT run_id FROM gold_study_runs WHERE status = 'COMPLETE' "
        "ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit("No COMPLETE study run — run scripts/_uat_rerun.py first.")
    run_id = row[0]

    analyst = get_user_by_username("a.analyst", db_path=str(DB_PATH))
    if analyst is None:
        raise SystemExit("User a.analyst not found — run scripts/_uat_rerun.py first.")
    res = run_fraud_scan(DB_PATH, run_id, user=analyst)
    print(f"fraud scan: {res.n_claims_scored} scored, {res.n_claims_flagged} flagged")

    generate_working_actuary_report(
        run_id, DB_PATH, REPORTS_DIR / f"working_actuary_{run_id[:8]}.html"
    )
    generate_chief_actuary_summary(
        run_id, DB_PATH, REPORTS_DIR / f"chief_actuary_{run_id[:8]}.html"
    )
    print(f"reports written to {REPORTS_DIR} for run {run_id[:8]}")


if __name__ == "__main__":
    main()
