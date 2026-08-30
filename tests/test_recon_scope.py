"""Per-product scoping of gold_inforce_reconciliation writes (demo refresh P1).

Locks the fix for the delete-scope bug where ``_run_reconciliation`` deleted by
``study_run_id`` alone, so each product's write wiped every other product's
rows and only the last product survived.
"""
import uuid
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.exposure.engine import _run_reconciliation
from src.utils.db_init import init_database


def _policies(n: int, face: float) -> pd.DataFrame:
    """Minimal policies frame with the columns reconciliation reads."""
    return pd.DataFrame(
        {
            "issue_date": [date(2015, 6, 1)] * n,
            "termination_date": pd.Series([None] * n, dtype="object"),
            "status_code": ["IF"] * n,
            "face_amount": [face] * n,
        }
    )


@pytest.fixture()
def recon_db(tmp_path: Path) -> Path:
    db = tmp_path / "data" / "recon.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    init_database(db)
    return db


def test_recon_rows_survive_per_product(recon_db: Path) -> None:
    run_id = str(uuid.uuid4())
    start, end = date(2020, 1, 1), date(2021, 12, 31)
    con = duckdb.connect(str(recon_db))
    try:
        _run_reconciliation(con, _policies(5, 100000.0), "TERM", run_id, start, end)
        _run_reconciliation(con, _policies(3, 50000.0), "WL", run_id, start, end)
        products = dict(
            con.execute(
                "SELECT product_code, COUNT(*) FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ? GROUP BY 1",
                [run_id],
            ).fetchall()
        )
        # 2 study years -> 2 rows per product; both products present
        assert products == {"TERM": 2, "WL": 2}

        # Re-running one product replaces only that product's rows
        _run_reconciliation(con, _policies(5, 100000.0), "TERM", run_id, start, end)
        products = dict(
            con.execute(
                "SELECT product_code, COUNT(*) FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ? GROUP BY 1",
                [run_id],
            ).fetchall()
        )
        assert products == {"TERM": 2, "WL": 2}
    finally:
        con.close()
