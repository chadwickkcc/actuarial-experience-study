"""CI rows must carry calendar_year so CI year-on-year analytics work (review M-5).

Every CI row in ``gold_ae_results`` had ``calendar_year IS NULL`` while
``compute_yoy_movement`` filters ``calendar_year IS NOT NULL``. Consequence: CI
had no YoY, no trend and no driver waterfall anywhere, the fact pack carried no
CI entry (so the AI commentary skill could never mention CI), and the Management
Commentary page reported "No experience for this decrement in the selected run"
for a headline demo story — 589 claims at A/E 1.2325 — while the same page's
justification expander showed TERM CI A/E 1.3897.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.calculation.ae_engine import _build_ci_illness_rows
from src.utils.types import CredibilityMethod, ExposureMethod, StudyConfig


def _cfg() -> StudyConfig:
    from datetime import date

    return StudyConfig(
        study_start_date=date(2020, 1, 1),
        study_end_date=date(2021, 12, 31),
        product_codes=["TERM"],
        exposure_method=ExposureMethod.ANNUAL,
        mortality_table_path="", lapse_table_path="", ci_table_path="",
        credibility_method=CredibilityMethod.LIMITED_FLUCTUATION,
    )


def _exp_df() -> pd.DataFrame:
    """CI-rider exposure segments spanning two calendar years."""
    return pd.DataFrame({
        "ci_rider_in_force_flag": [True] * 4,
        "gender": ["M"] * 4,
        "attained_age_band": ["45-49"] * 4,
        "product_code": ["TERM"] * 4,
        "plan_code": ["T20"] * 4,
        "exposure_years": [1.0, 1.0, 1.0, 1.0],
        "calendar_year": [2020, 2020, 2021, 2021],
    })


def _ci_table() -> pd.DataFrame:
    return pd.DataFrame({
        "gender": ["M", "M"],
        "attained_age_band": ["45-49", "45-49"],
        "illness_code": ["CI-001", "CI-002"],
        "incidence_rate_per_1000": [2.0, 1.0],
    })


def _events() -> pd.DataFrame:
    """One CI-001 claim in each year."""
    return pd.DataFrame({
        "product_code": ["TERM", "TERM"],
        "illness_code": ["CI-001", "CI-001"],
        "gender": ["M", "M"],
        "attained_age_band": ["45-49", "45-49"],
        "calendar_year": [2020, 2021],
        "cnt": [1, 1],
    })


def test_ci_rows_carry_calendar_year():
    rows = _build_ci_illness_rows(
        _exp_df(), _ci_table(), _events(), "run-1", _cfg(), datetime(2026, 1, 1)
    )
    assert not rows.empty
    assert "calendar_year" in rows.columns
    assert rows["calendar_year"].notna().all(), (
        "CI rows must carry the calendar year — without it CI has no YoY, no trend "
        "and no driver attribution"
    )
    assert set(rows["calendar_year"].unique()) == {2020, 2021}


def test_ci_actuals_are_attributed_to_the_right_year():
    rows = _build_ci_illness_rows(
        _exp_df(), _ci_table(), _events(), "run-1", _cfg(), datetime(2026, 1, 1)
    )
    ci001 = rows[rows["illness_code"] == "CI-001"]
    by_year = ci001.set_index("calendar_year")["actual_ci_claims"].to_dict()
    assert by_year == {2020: 1, 2021: 1}


def test_ci_totals_are_unchanged_by_the_finer_grain():
    """Splitting by year must not create or lose exposure or claims."""
    rows = _build_ci_illness_rows(
        _exp_df(), _ci_table(), _events(), "run-1", _cfg(), datetime(2026, 1, 1)
    )
    assert rows["actual_ci_claims"].sum() == 2
    # 4 segment-years x 1.0 exposure, expanded over 2 illness codes
    assert rows["ci_exposure_count"].sum() == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Confidence bounds must be floored at zero (adversarial review m-3)
# ---------------------------------------------------------------------------

def test_confidence_lower_bound_is_floored_at_zero():
    """``ui/stats_helpers.poisson_ci`` floors the lower bound at 0; the engine did
    not, so 1,199 mortality rows (and 5,018 lapse rows) stored a NEGATIVE lower
    confidence bound — e.g. n=1 gave lo = -129.59. A/E cannot be negative, and
    these columns are on the AI allowlist and in CSV exports."""
    from src.calculation.ae_engine import _add_stat_columns

    df = pd.DataFrame({
        "ae_count": [134.9945, 0.5, 1.0],
        "actual_deaths_count": [1, 400, 0],
    })
    out = _add_stat_columns(df, "ae_count", "actual_deaths_count", "count", 1082.0, "LF")

    lo = out["ci_lower_count"]
    assert (lo.dropna() >= 0).all(), f"negative lower bound(s): {lo.tolist()}"
    # The sparse cell must still be wide on the upside, not silently clamped.
    assert out["ci_upper_count"].iloc[0] > out["ae_count"].iloc[0]
    # A zero-claim cell has no interval at all.
    assert pd.isna(lo.iloc[2])


# ---------------------------------------------------------------------------
# anti_selection_flag must actually reach the table (adversarial review M-7)
# ---------------------------------------------------------------------------

def test_anti_selection_flag_is_persisted(tmp_path):
    """FR-1B-10 was non-functional: the flag was computed then dropped by the
    insert (a stale comment claimed the column was not in the DDL — it is), so
    all 159,568 Gold rows read FALSE while 809 cells qualified. It is on the AI
    allowlist, so the analyst could read a false negative."""
    import uuid as _uuid
    from datetime import datetime as _dt

    import duckdb
    from src.calculation.ae_engine import _insert_ae_results
    from src.utils.db_init import init_database

    db = tmp_path / "ae.duckdb"
    init_database(db)

    df = pd.DataFrame({
        "result_id": [str(_uuid.uuid4()), str(_uuid.uuid4())],
        "study_run_id": ["run-1", "run-1"],
        "product_code": ["UL", "TERM"],
        "ae_lapse": [1.9, 0.8],
        "anti_selection_flag": [True, False],
        "_created_ts": [_dt(2026, 1, 1), _dt(2026, 1, 1)],
    })
    con = duckdb.connect(str(db))
    try:
        _insert_ae_results(con, df)
        rows = dict(
            con.execute(
                "SELECT product_code, anti_selection_flag FROM gold_ae_results"
            ).fetchall()
        )
    finally:
        con.close()
    assert rows == {"UL": True, "TERM": False}, (
        f"the flag must survive the insert, got {rows}"
    )


def test_amount_basis_lower_bound_is_also_floored():
    """The amount-basis CI is computed outside ``_add_stat_columns``; it needs the
    same floor (1,199 rows still stored a negative ci_lower_amount after the
    count-basis fix)."""
    import re

    src = (
        __import__("pathlib").Path("src/calculation/ae_engine.py").read_text()
    )
    m = re.search(r'agg\["ci_lower_amount"\]\s*=\s*(.+?)\n', src, re.DOTALL)
    assert m and "np.maximum(" in m.group(1), (
        "ci_lower_amount must be floored at zero like the count basis"
    )
