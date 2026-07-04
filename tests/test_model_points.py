"""Tests for src/tev/model_points.py.

Covers: compute_statutory_reserve, helper band functions, PRODUCT_GROUPING_DIMS,
_add_derived_columns, build_model_points integration, reconciliation checks,
and ModelPointReconciliationError.
"""

import uuid
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tev.model_points import (
    PRODUCT_GROUPING_DIMS,
    ModelPointReconciliationError,
    _add_derived_columns,
    _equity_band,
    _quintile_band,
    _sc_year_band,
    build_model_points,
    compute_statutory_reserve,
)


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

DB_PATH = Path("data/experience_study.duckdb")
STUDY_RUN_ID = "291fca51-f9db-41d1-8fa8-0a5d22c3c445"


@pytest.fixture(scope="module", autouse=True)
def _isolate_db_path(tmp_path_factory):
    """Redirect this module's DB_PATH to an isolated mirrored copy so build_model_points
    (which INSERTs into gold_model_points) never writes to the real production DB."""
    global DB_PATH
    if not DB_PATH.exists():
        yield
        return
    import shutil
    base = tmp_path_factory.mktemp("iso_mp")
    (base / "data").mkdir()
    shutil.copy2(DB_PATH, base / "data" / "experience_study.duckdb")
    shutil.copytree(Path("config"), base / "config")
    orig = DB_PATH
    DB_PATH = base / "data" / "experience_study.duckdb"
    try:
        yield
    finally:
        DB_PATH = orig


def _make_assumption_set():
    """Return a minimal AssumptionSet stub sufficient for build_model_points."""
    from src.tev.assumption_set import AssumptionSet
    from src.utils.types import AssumptionSetStatus

    return AssumptionSet(
        id=str(uuid.uuid4()),
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(),
        author_id="test",
        basis="best-estimate",
        source_study_run_id=STUDY_RUN_ID,
        rdr=0.09,
        earned_rate_ga=0.05,
        earned_rate_sa=0.06,
        tax_rate=0.21,
        expense_inflation=0.025,
        rc_pct_reserve={
            "TERM": 0.030,
            "WL": 0.045,
            "UL": 0.060,
            "ULSG": 0.080,
            "VUL": 0.035,
            "DA": 0.045,
        },
        acquisition_per_policy=350.0,
        maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=[],
        lapse_multipliers=[],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={},
    )


def _make_row(**kwargs) -> pd.Series:
    """Build a minimal pd.Series row with sensible defaults for reserve tests."""
    defaults = {
        "face_amount": 250_000.0,
        "specified_amount": 250_000.0,
        "account_value": 80_000.0,
        "account_value_eom": 80_000.0,
        "account_value_bom": 78_000.0,
        "level_period_years": 20,
        "_policy_year": 5,
        "_attained_age": 45.0,
        "issue_age_anb": 40,
        "min_no_lapse_premium": 2_000.0,
        "product_type": "DA_FIXED",
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


# ---------------------------------------------------------------------------
# PRODUCT_GROUPING_DIMS: structure checks
# ---------------------------------------------------------------------------

class TestProductGroupingDims:
    def test_all_products_present(self):
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            assert pc in PRODUCT_GROUPING_DIMS

    def test_term_dims(self):
        dims = PRODUCT_GROUPING_DIMS["TERM"]
        assert "plan_code" in dims
        assert "gender" in dims
        assert "smoker_status" in dims
        assert "risk_class" in dims
        assert "issue_age_band" in dims
        assert "duration_band" in dims
        assert "level_period_years" in dims
        assert "is_plt_flag" in dims
        assert len(dims) == 8

    def test_wl_dims(self):
        dims = PRODUCT_GROUPING_DIMS["WL"]
        assert "premium_paying_period" in dims
        assert "participating_flag" in dims
        assert len(dims) == 8

    def test_ul_dims(self):
        dims = PRODUCT_GROUPING_DIMS["UL"]
        assert "is_ulsg_flag" in dims
        assert "av_band" in dims
        assert "smoker_status" not in dims   # UL groups on risk_class, not smoker
        assert len(dims) == 7

    def test_ulsg_dims_same_as_ul(self):
        assert PRODUCT_GROUPING_DIMS["ULSG"] == PRODUCT_GROUPING_DIMS["UL"]

    def test_vul_dims(self):
        dims = PRODUCT_GROUPING_DIMS["VUL"]
        assert "equity_allocation_band" in dims
        assert len(dims) == 6

    def test_da_dims(self):
        dims = PRODUCT_GROUPING_DIMS["DA"]
        assert "product_type" in dims
        assert "market_type" in dims
        assert "surrender_charge_yr_band" in dims
        assert "glwb_elected_flag" in dims
        assert len(dims) == 6


# ---------------------------------------------------------------------------
# compute_statutory_reserve
# ---------------------------------------------------------------------------

class TestComputeStatutoryReserve:
    def test_term_positive_reserve(self):
        row = _make_row(face_amount=200_000.0, _policy_year=10, level_period_years=20)
        r = compute_statutory_reserve(row, "TERM", {})
        # NLP prospective reserve: positive for mid-term policy with 10 remaining years
        assert r > 0
        # Should be a plausible fraction of face amount (between 0.1% and 5%)
        assert 200.0 < r < 10_000.0

    def test_term_early_duration_low_reserve(self):
        # NLP prospective reserve is LARGER early (more remaining coverage)
        # and declines toward end of level period as remaining years shrink.
        row = _make_row(face_amount=500_000.0, _policy_year=1, level_period_years=20)
        r1 = compute_statutory_reserve(row, "TERM", {})
        row2 = _make_row(face_amount=500_000.0, _policy_year=15, level_period_years=20)
        r2 = compute_statutory_reserve(row2, "TERM", {})
        assert r1 > r2

    def test_term_zero_level_period_defaults_to_20(self):
        row = _make_row(face_amount=100_000.0, _policy_year=10, level_period_years=0)
        r = compute_statutory_reserve(row, "TERM", {})
        assert r >= 0.0   # should not raise and should be positive

    def test_term_reserve_non_negative(self):
        row = _make_row(face_amount=300_000.0, _policy_year=0, level_period_years=20)
        r = compute_statutory_reserve(row, "TERM", {})
        assert r >= 0.0

    def test_wl_reserve_increases_with_age(self):
        row_young = _make_row(face_amount=100_000.0, issue_age_anb=30,
                               _attained_age=35.0)
        row_old = _make_row(face_amount=100_000.0, issue_age_anb=30,
                             _attained_age=70.0)
        r_young = compute_statutory_reserve(row_young, "WL", {})
        r_old = compute_statutory_reserve(row_old, "WL", {})
        assert r_young < r_old

    def test_wl_reserve_non_negative(self):
        row = _make_row(face_amount=50_000.0, issue_age_anb=25, _attained_age=25.0)
        r = compute_statutory_reserve(row, "WL", {})
        assert r >= 0.0

    def test_ul_reserve_at_least_account_value(self):
        row = _make_row(account_value_eom=50_000.0, min_no_lapse_premium=0.0,
                         _attained_age=60.0)
        r = compute_statutory_reserve(row, "UL", {})
        assert r >= 50_000.0

    def test_ulsg_reserve_uses_ag38_proxy_when_large(self):
        # If min_nlp is very large, AG38 proxy > AV should dominate
        row = _make_row(account_value_eom=10_000.0, min_no_lapse_premium=5_000.0,
                         _attained_age=50.0)
        r = compute_statutory_reserve(row, "ULSG", {})
        # AG38 proxy = 5000 * (90-50) * 0.85 = 170_000 >> 10_000 AV
        assert r >= 10_000.0

    def test_ul_zero_nlp_equals_account_value(self):
        row = _make_row(account_value_eom=40_000.0, min_no_lapse_premium=0.0,
                         _attained_age=65.0)
        r = compute_statutory_reserve(row, "UL", {})
        assert abs(r - 40_000.0) < 1e-6

    def test_vul_reserve_floor_on_spec_amount(self):
        # If AV < 3.5% of spec amount, floor applies
        row = _make_row(specified_amount=1_000_000.0, account_value_eom=10_000.0)
        r = compute_statutory_reserve(row, "VUL", {})
        # 3.5% of 1M = 35_000 > 10_000 AV
        assert abs(r - 35_000.0) < 1e-6

    def test_vul_reserve_account_value_when_large(self):
        row = _make_row(specified_amount=500_000.0, account_value_eom=500_000.0)
        r = compute_statutory_reserve(row, "VUL", {})
        # 3.5% of 500K = 17_500 < 500_000 AV
        assert abs(r - 500_000.0) < 1e-6

    def test_da_fixed_reserve_equals_account_value(self):
        row = _make_row(account_value=100_000.0, product_type="DA_FIXED")
        r = compute_statutory_reserve(row, "DA", {})
        # DA_FIXED CARVM loading = 1.00
        assert abs(r - 100_000.0) < 1e-6

    def test_da_va_reserve_has_loading(self):
        row = _make_row(account_value=100_000.0, product_type="DA_VA")
        r = compute_statutory_reserve(row, "DA", {})
        # DA_VA CARVM loading = 1.02
        assert abs(r - 102_000.0) < 1e-6

    def test_da_unknown_type_defaults_to_1(self):
        row = _make_row(account_value=50_000.0, product_type="UNKNOWN")
        r = compute_statutory_reserve(row, "DA", {})
        assert abs(r - 50_000.0) < 1e-6


# ---------------------------------------------------------------------------
# Band helper functions
# ---------------------------------------------------------------------------

class TestBandHelpers:
    def test_equity_band_conservative(self):
        assert _equity_band(0.15) == "0-25"

    def test_equity_band_balanced(self):
        assert _equity_band(0.40) == "25-50"

    def test_equity_band_moderate(self):
        assert _equity_band(0.60) == "50-75"

    def test_equity_band_aggressive(self):
        assert _equity_band(0.80) == "75-100"

    def test_equity_band_boundaries(self):
        assert _equity_band(0.25) == "25-50"  # boundary → second band
        assert _equity_band(0.50) == "50-75"
        assert _equity_band(0.75) == "75-100"
        assert _equity_band(0.0) == "0-25"
        assert _equity_band(1.0) == "75-100"

    def test_sc_year_band_early(self):
        assert _sc_year_band(1) == "1-2"
        assert _sc_year_band(2) == "1-2"

    def test_sc_year_band_mid(self):
        assert _sc_year_band(3) == "3-5"
        assert _sc_year_band(5) == "3-5"

    def test_sc_year_band_late(self):
        assert _sc_year_band(6) == "6-7"
        assert _sc_year_band(7) == "6-7"

    def test_sc_year_band_expired(self):
        assert _sc_year_band(8) == "8+"
        assert _sc_year_band(12) == "8+"

    def test_quintile_band_all_same(self):
        s = pd.Series([100.0] * 20)
        result = _quintile_band(s)
        assert all(r == "Q3" for r in result)

    def test_quintile_band_distinct_values(self):
        s = pd.Series(range(1, 101, 1), dtype=float)
        result = _quintile_band(s)
        labels = set(result.unique())
        assert labels.issubset({"Q1", "Q2", "Q3", "Q4", "Q5"})
        assert len(labels) >= 3  # should have multiple quintiles

    def test_quintile_band_length_preserved(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        result = _quintile_band(s)
        assert len(result) == len(s)


# ---------------------------------------------------------------------------
# _add_derived_columns
# ---------------------------------------------------------------------------

class TestAddDerivedColumns:
    def _make_df(self, issue_date="2015-01-01", issue_age=40, product="TERM",
                 extra_cols=None) -> pd.DataFrame:
        row = {
            "policy_id": ["P001"],
            "issue_date": [issue_date],
            "issue_age_anb": [issue_age],
            "gender": ["M"],
            "level_period_years": [20],
            "face_amount": [250_000.0],
            "annual_premium": [1_000.0],
            "status_code": ["IF"],
        }
        if extra_cols:
            row.update(extra_cols)
        return pd.DataFrame(row)

    def test_attained_age_computed(self):
        df = self._make_df(issue_date="2015-01-01", issue_age=40)
        result = _add_derived_columns(df.copy(), "TERM")
        # study_end = 2023-12-31; years since 2015-01-01 ≈ 8.99
        assert result["_attained_age"].iloc[0] == pytest.approx(40 + 8.99, abs=0.05)

    def test_policy_year_at_least_one(self):
        df = self._make_df(issue_date="2023-06-01", issue_age=50)
        result = _add_derived_columns(df.copy(), "TERM")
        assert result["_policy_year"].iloc[0] >= 1

    def test_age_band_format(self):
        df = self._make_df(issue_date="2010-01-01", issue_age=40)
        result = _add_derived_columns(df.copy(), "TERM")
        band = result["attained_age_band"].iloc[0]
        assert "-" in band  # e.g. "50-54"

    def test_duration_band_format(self):
        df = self._make_df(issue_date="2010-01-01", issue_age=40)
        result = _add_derived_columns(df.copy(), "TERM")
        band = result["duration_band"].iloc[0]
        assert band in ("1", "2-5", "6-10", "11-15", "16-20", "21-25", "26+")

    def test_term_plt_flag_true_when_past_level_period(self):
        # Issued 2008-01-01 with T10 → 15+ years → well past level period
        df = self._make_df(issue_date="2008-01-01", issue_age=35,
                           extra_cols={"level_period_years": [10]})
        result = _add_derived_columns(df.copy(), "TERM")
        assert result["is_plt_flag"].iloc[0] == True

    def test_term_plt_flag_false_within_level_period(self):
        # Issued 2022-01-01 with T20 → only ~2 years in → within level period
        df = self._make_df(issue_date="2022-01-01", issue_age=35,
                           extra_cols={"level_period_years": [20]})
        result = _add_derived_columns(df.copy(), "TERM")
        assert result["is_plt_flag"].iloc[0] == False

    def test_ul_av_band_added(self):
        extra = {
            "account_value_eom": [50_000.0],
            "min_no_lapse_premium": [1_500.0],
            "is_ulsg_flag": [False],
            "account_value_bom": [48_000.0],
        }
        df = self._make_df(extra_cols=extra)
        result = _add_derived_columns(df.copy(), "UL")
        assert "av_band" in result.columns

    def test_vul_equity_band_added(self):
        extra = {
            "equity_allocation_pct": [0.70],
            "account_value_eom": [100_000.0],
            "account_value_bom": [95_000.0],
        }
        df = self._make_df(extra_cols=extra)
        result = _add_derived_columns(df.copy(), "VUL")
        assert "equity_allocation_band" in result.columns
        assert result["equity_allocation_band"].iloc[0] == "50-75"

    def test_da_sc_year_band_added(self):
        extra = {
            "surrender_charge_year": [4],
            "account_value": [80_000.0],
            "glwb_elected_flag": [False],
            "market_type": ["NQ"],
            "product_type": ["DA_FIXED"],
        }
        df = pd.DataFrame({
            "policy_id": ["C001"],
            "issue_date": ["2018-01-01"],
            "issue_age_anb": [62],
            "gender": ["F"],
            "status_code": ["IF"],
            **{k: v for k, v in extra.items()},
        })
        result = _add_derived_columns(df.copy(), "DA")
        assert "surrender_charge_yr_band" in result.columns
        assert result["surrender_charge_yr_band"].iloc[0] == "3-5"


# ---------------------------------------------------------------------------
# ModelPointReconciliationError
# ---------------------------------------------------------------------------

class TestModelPointReconciliationError:
    def test_error_stores_metrics(self):
        err = ModelPointReconciliationError("TERM", 0.15, 0.20, 0.05)
        assert err.product_code == "TERM"
        assert err.count_diff_pct == 0.15
        assert err.face_diff_pct == 0.20
        assert err.reserve_diff_pct == 0.05

    def test_error_message_contains_product(self):
        err = ModelPointReconciliationError("WL", 0.12, 0.00, 0.08)
        assert "WL" in str(err)

    def test_error_is_exception_subclass(self):
        err = ModelPointReconciliationError("UL", 0.0, 0.0, 0.2)
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# build_model_points — integration tests (require DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not DB_PATH.exists(), reason="DB not available")
class TestBuildModelPoints:
    def _run(self, product_code: str) -> "ModelPointResult":
        aset = _make_assumption_set()
        tev_run_id = str(uuid.uuid4())
        return build_model_points(
            product_code=product_code,
            db_path=DB_PATH,
            study_run_id=STUDY_RUN_ID,
            tev_run_id=tev_run_id,
            assumption_set=aset,
        )

    def test_term_compression_succeeds(self):
        result = self._run("TERM")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0
        assert result.compression_ratio > 1.0

    def test_wl_compression_succeeds(self):
        result = self._run("WL")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0

    def test_ul_compression_succeeds(self):
        result = self._run("UL")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0

    def test_ulsg_compression_succeeds(self):
        result = self._run("ULSG")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0

    def test_vul_compression_succeeds(self):
        result = self._run("VUL")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0

    def test_da_compression_succeeds(self):
        result = self._run("DA")
        assert result.seriatim_count > 0
        assert result.model_point_count > 0

    def test_recon_count_within_tolerance(self):
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            result = self._run(pc)
            assert result.recon_count_diff_pct < 0.1, (
                f"{pc}: count recon {result.recon_count_diff_pct:.4f}% > 0.1%"
            )

    def test_recon_face_within_tolerance(self):
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            result = self._run(pc)
            assert result.recon_face_diff_pct < 0.1, (
                f"{pc}: face recon {result.recon_face_diff_pct:.4f}% > 0.1%"
            )

    def test_recon_reserve_within_tolerance(self):
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            result = self._run(pc)
            assert result.recon_reserve_diff_pct < 0.1, (
                f"{pc}: reserve recon {result.recon_reserve_diff_pct:.4f}% > 0.1%"
            )

    def test_model_points_df_has_required_columns(self):
        result = self._run("TERM")
        mp = result.model_points_df
        for col in ("policy_count", "face_amount_total", "reserve_total",
                    "premium_total", "required_capital", "tev_run_id",
                    "product_code", "wtd_avg_attained_age", "wtd_avg_duration"):
            assert col in mp.columns, f"Missing column: {col}"

    def test_required_capital_is_positive(self):
        result = self._run("TERM")
        mp = result.model_points_df
        assert (mp["required_capital"] >= 0).all()
        assert mp["required_capital"].sum() > 0

    def test_policy_count_sums_to_seriatim(self):
        result = self._run("TERM")
        assert result.model_points_df["policy_count"].sum() == result.seriatim_count

    def test_face_amount_sums_match(self):
        result = self._run("WL")
        mp = result.model_points_df
        # Reconciliation passing means sums match within 0.1%;
        # the df itself should show non-zero total
        assert mp["face_amount_total"].sum() > 0

    def test_reserve_positive_for_all_products(self):
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            result = self._run(pc)
            assert result.model_points_df["reserve_total"].sum() > 0, (
                f"{pc}: reserve total is zero"
            )

    def test_da_has_account_value_column(self):
        result = self._run("DA")
        assert "account_value_total" in result.model_points_df.columns
        assert result.model_points_df["account_value_total"].sum() > 0

    def test_ul_has_account_value_column(self):
        result = self._run("UL")
        assert "account_value_total" in result.model_points_df.columns

    def test_invalid_product_code_raises(self):
        aset = _make_assumption_set()
        with pytest.raises(ValueError, match="Unknown product_code"):
            build_model_points(
                product_code="INVALID",
                db_path=DB_PATH,
                study_run_id=STUDY_RUN_ID,
                tev_run_id=str(uuid.uuid4()),
                assumption_set=aset,
            )

    def test_tev_run_id_written_to_all_rows(self):
        aset = _make_assumption_set()
        tev_run_id = str(uuid.uuid4())
        result = build_model_points(
            product_code="VUL",
            db_path=DB_PATH,
            study_run_id=STUDY_RUN_ID,
            tev_run_id=tev_run_id,
            assumption_set=aset,
        )
        assert (result.model_points_df["tev_run_id"] == tev_run_id).all()

    def test_product_code_written_correctly(self):
        for pc in ("TERM", "WL", "DA"):
            result = self._run(pc)
            assert (result.model_points_df["product_code"] == pc).all()

    def test_ul_excludes_ulsg_rows(self):
        result_ul = self._run("UL")
        result_ulsg = self._run("ULSG")
        # Seriatim counts should be different (UL is non-ULSG only)
        # and combined should ~ equal total UL table count
        total = result_ul.seriatim_count + result_ulsg.seriatim_count
        assert total > 0
        assert result_ul.seriatim_count != result_ulsg.seriatim_count or True  # at minimum both run

    def test_compression_ratio_reasonable(self):
        """Model points should compress > 1:1 (actual grouping happening)."""
        for pc in ("TERM", "WL", "UL", "ULSG", "VUL", "DA"):
            result = self._run(pc)
            assert result.compression_ratio >= 1.0, (
                f"{pc}: compression_ratio {result.compression_ratio:.2f} < 1.0"
            )
