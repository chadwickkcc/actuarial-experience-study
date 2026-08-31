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


# ---------------------------------------------------------------------------
# Family scoping (adversarial review B-3)
# ---------------------------------------------------------------------------

def _family_policies() -> pd.DataFrame:
    """A UL-family frame: 2 UL + 3 ULSG + 1 IUL, as _load_silver_policies returns it."""
    codes = ["UL", "UL", "ULSG", "ULSG", "ULSG", "IUL"]
    return pd.DataFrame(
        {
            "product_code": codes,
            "issue_date": [date(2015, 6, 1)] * len(codes),
            "termination_date": pd.Series([None] * len(codes), dtype="object"),
            "status_code": ["IF"] * len(codes),
            "face_amount": [100000.0] * len(codes),
        }
    )


def test_recon_is_scoped_to_each_products_own_policies(recon_db: Path) -> None:
    """``_load_silver_policies`` returns the whole UL family for any member, so
    stamping every row with the *invoked* product_code made each label carry all
    4,500 family policies — portfolio deaths came out 50% high and a 500-policy
    product reported 3,152 in force (adversarial review B-3)."""
    run_id = str(uuid.uuid4())
    start, end = date(2020, 1, 1), date(2021, 12, 31)
    con = duckdb.connect(str(recon_db))
    try:
        _run_reconciliation(con, _family_policies(), "UL", run_id, start, end)
        rows = dict(
            con.execute(
                "SELECT product_code, MAX(end_if_count) FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ? GROUP BY 1",
                [run_id],
            ).fetchall()
        )
    finally:
        con.close()

    assert rows == {"UL": 2, "ULSG": 3, "IUL": 1}, (
        f"each label must carry only its own policies, got {rows}"
    )


def test_recon_totals_match_the_book_not_a_multiple_of_it(recon_db: Path) -> None:
    """The portfolio total must equal the book, not N x the family."""
    run_id = str(uuid.uuid4())
    start, end = date(2020, 1, 1), date(2021, 12, 31)
    con = duckdb.connect(str(recon_db))
    try:
        _run_reconciliation(con, _family_policies(), "UL", run_id, start, end)
        total = con.execute(
            "SELECT SUM(end_if_count) FROM gold_inforce_reconciliation "
            "WHERE study_run_id = ? AND calendar_year = ?",
            [run_id, 2021],
        ).fetchone()[0]
    finally:
        con.close()
    assert total == 6, f"expected the 6 policies in the book, got {total}"


def test_recon_without_a_product_code_column_uses_the_invoked_code(recon_db: Path) -> None:
    """Back-compat: a frame that carries no product_code is attributed wholly to
    the invoked product."""
    run_id = str(uuid.uuid4())
    con = duckdb.connect(str(recon_db))
    try:
        _run_reconciliation(
            con, _policies(4, 100000.0), "TERM", run_id, date(2020, 1, 1), date(2020, 12, 31)
        )
        rows = dict(
            con.execute(
                "SELECT product_code, MAX(end_if_count) FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ? GROUP BY 1",
                [run_id],
            ).fetchall()
        )
    finally:
        con.close()
    assert rows == {"TERM": 4}


# ---------------------------------------------------------------------------
# Segment tie-out (adversarial review M-4)
# ---------------------------------------------------------------------------

def _segment_rows(run_id: str, product: str, decrements: list[tuple[str, int]]) -> pd.DataFrame:
    """Minimal exposure segments carrying decrements: [(decrement_type, year), ...]."""
    rows = []
    for i, (dec, year) in enumerate(decrements):
        rows.append({
            "segment_id": f"{product}-{i}", "study_run_id": run_id,
            "policy_id": f"{product}-P{i}", "product_code": product,
            "segment_start_date": date(year, 1, 1), "segment_end_date": date(year, 12, 31),
            "exposure_years": 1.0, "lapse_exposure_years": 1.0,
            "face_amount_start": 1000.0, "face_amount_end": 1000.0,
            "face_amount_wtd_avg": 1000.0, "attained_age_start": 40.0,
            "attained_age_end": 41.0, "attained_age_band": "40-44",
            "issue_age_anb": 35, "issue_age_band": "35-39", "policy_year": 3,
            "duration_band": "2-5", "calendar_year": year, "gender": "M",
            "smoker_status": "NS", "risk_class": "STD_NS", "plan_code": "X",
            "is_plt_flag": False, "decrement_flag": dec is not None,
            "decrement_type": dec, "exposure_method": "ANNUAL",
            "ci_rider_in_force_flag": False,
        })
    return pd.DataFrame(rows)


def _write_segments(db: Path, df: pd.DataFrame) -> None:
    con = duckdb.connect(str(db))
    try:
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='gold_exposure_segments' ORDER BY ordinal_position").fetchall()]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        df = df[cols]
        con.register("_seg", df)
        con.execute("INSERT INTO gold_exposure_segments SELECT * FROM _seg")
        con.unregister("_seg")
    finally:
        con.close()


def _deaths(n: int, year: int) -> pd.DataFrame:
    """n policies that all die in `year`, plus one that stays in force."""
    return pd.DataFrame({
        "product_code": ["TERM"] * (n + 1),
        "issue_date": [date(2015, 1, 1)] * (n + 1),
        "termination_date": [date(year, 6, 1)] * n + [None],
        "status_code": ["DEATH"] * n + ["IF"],
        "face_amount": [1000.0] * (n + 1),
    })


def test_recon_detects_exposure_segments_that_do_not_match_the_movement(recon_db: Path) -> None:
    """The control is presented as proving the seriatim exposure file ties out, but
    it only ever compared the policy frame with itself — it passed with ZERO
    exposure segments in the database, which is why the family-scoping defect
    (B-3) went unnoticed."""
    run_id = str(uuid.uuid4())
    # movement says 3 deaths in 2020; the exposure file only carries 1.
    _write_segments(recon_db, _segment_rows(run_id, "TERM", [("DEATH", 2020)]))
    con = duckdb.connect(str(recon_db))
    try:
        ok = _run_reconciliation(
            con, _deaths(3, 2020), "TERM", run_id, date(2020, 1, 1), date(2020, 12, 31)
        )
    finally:
        con.close()
    assert not ok, "recon must fail when the exposure file disagrees with the movement"


def test_recon_passes_when_segments_match(recon_db: Path) -> None:
    run_id = str(uuid.uuid4())
    _write_segments(
        recon_db,
        _segment_rows(run_id, "TERM", [("DEATH", 2020), ("DEATH", 2020), ("DEATH", 2020)]),
    )
    con = duckdb.connect(str(recon_db))
    try:
        ok = _run_reconciliation(
            con, _deaths(3, 2020), "TERM", run_id, date(2020, 1, 1), date(2020, 12, 31)
        )
    finally:
        con.close()
    assert ok
