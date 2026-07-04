"""Unit tests for the exposure engine (src/exposure/engine.py).

Run with:  pytest tests/test_exposure.py -v
"""

import uuid
from datetime import date

import pandas as pd
import pytest

from src.exposure.engine import (
    ReconciliationFailure,
    _build_segments_for_policy,
    compute_age_band,
    compute_duration_band,
    compute_premium_jump_band,
)
from src.utils.types import ExposureMethod


# ---------------------------------------------------------------------------
# Fixtures — minimal policy Series objects
# ---------------------------------------------------------------------------

def _make_policy(
    policy_id: str = "TRM-TEST001",
    issue_date: date = date(2010, 1, 1),
    dob: date = date(1972, 1, 1),
    issue_age_anb: int = 38,
    status_code: str = "IF",
    termination_date=None,
    termination_cause_code=None,
    level_period_years: int = 20,
    gender: str = "M",
    smoker_status: str = "NS",
    risk_class: str = "STD_NS",
    plan_code: str = "T20",
    face_amount: float = 500_000.0,
    annual_premium: float = 1_250.0,
    premium_mode: str = "ANNUAL",
    ci_rider_flag: bool = False,
    ci_rider_sum_assured=None,
    ci_rider_premium=None,
    plt_structure_code=None,
    premium_jump_ratio=None,
    distribution_channel: str = "INDEPENDENT",
    issue_state: str = "CA",
) -> pd.Series:
    """Return a minimal policy Series matching silver_term_policies columns."""
    return pd.Series({
        "policy_id":             policy_id,
        "product_code":          "TERM",
        "plan_code":             plan_code,
        "issue_date":            issue_date,
        "date_of_birth":         dob,
        "issue_age_anb":         issue_age_anb,
        "gender":                gender,
        "smoker_status":         smoker_status,
        "risk_class":            risk_class,
        "face_amount":           face_amount,
        "annual_premium":        annual_premium,
        "premium_mode":          premium_mode,
        "status_code":           status_code,
        "termination_date":      termination_date,
        "termination_cause_code": termination_cause_code,
        "level_period_years":    level_period_years,
        "plt_premium_year_1":    None,
        "plt_structure_code":    plt_structure_code,
        "premium_jump_ratio":    premium_jump_ratio,
        "ci_rider_flag":         ci_rider_flag,
        "ci_rider_sum_assured":  ci_rider_sum_assured,
        "ci_rider_premium":      ci_rider_premium,
        "distribution_channel":  distribution_channel,
        "issue_state":           issue_state,
    })


STUDY_START = date(2016, 1, 1)
STUDY_END   = date(2023, 12, 31)
RUN_ID      = str(uuid.uuid4())


def _segs(policy, status_code="IF", termination_date=None, **kwargs):
    p = _make_policy(status_code=status_code, termination_date=termination_date, **kwargs)
    return _build_segments_for_policy(p, STUDY_START, STUDY_END, ExposureMethod.ANNUAL, RUN_ID, {})


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestComputeAgeBand:
    def test_typical_age(self):
        assert compute_age_band(52.3) == "50-54"

    def test_exact_boundary(self):
        assert compute_age_band(50.0) == "50-54"

    def test_band_size_10(self):
        assert compute_age_band(52.3, 10) == "50-59"

    def test_young_age(self):
        assert compute_age_band(18.0) == "15-19"


class TestComputeDurationBand:
    def test_year_1(self):
        assert compute_duration_band(1) == "1"

    def test_year_3(self):
        assert compute_duration_band(3) == "2-5"

    def test_year_5(self):
        assert compute_duration_band(5) == "2-5"

    def test_year_6(self):
        assert compute_duration_band(6) == "6-10"

    def test_year_30(self):
        assert compute_duration_band(30) == "26+"


class TestComputePremiumJumpBand:
    def test_le_2x(self):
        assert compute_premium_jump_band(1.5) == "<=2x"

    def test_2_to_3(self):
        assert compute_premium_jump_band(2.5) == "2-3x"

    def test_3_to_5(self):
        assert compute_premium_jump_band(4.0) == "3-5x"

    def test_5_to_8(self):
        assert compute_premium_jump_band(6.0) == "5-8x"

    def test_gt_12(self):
        assert compute_premium_jump_band(15.0) == ">12x"


# ---------------------------------------------------------------------------
# Segment count tests
# ---------------------------------------------------------------------------

class TestSegmentCount:
    def test_policy_spanning_full_study_produces_many_segments(self):
        """A T20 policy issued 2010-01-01, still IF through 2023-12-31.

        Split points: policy anniversaries (2016-01-01 to 2023-01-01 = 8)
        + Jan-1 calendar boundaries (same as anniversaries for this policy)
        + study start (2016-01-01) + study end (2023-12-31).

        For this policy, anniversaries coincide with Jan-1 boundaries.
        Expected: one segment per year from 2016 to 2023 = 8 segments,
        plus a trailing segment from 2023-01-01 to 2023-12-31.
        Total: 9 segments (one per anniversary-to-anniversary gap
        within study, including the stub at the end).
        """
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        # 8 full-year segments (2016→2017, ..., 2022→2023) + 1 stub (2023-01-01 to 2023-12-31)
        assert len(segs) >= 8
        assert all(0 < s["exposure_years"] <= 1.0001 for s in segs)

    def test_policy_issued_during_study_starts_at_issue_date(self):
        """Policy issued 2020-06-15 has no exposure before issue date."""
        segs = _segs(None, issue_date=date(2020, 6, 15), level_period_years=20)
        assert segs[0]["segment_start_date"] == date(2020, 6, 15)

    def test_policy_with_no_study_overlap_produces_no_segments(self):
        """Policy issued 2024-01-01 is entirely outside study window."""
        p = _make_policy(issue_date=date(2024, 1, 1))
        segs = _build_segments_for_policy(p, STUDY_START, STUDY_END, ExposureMethod.ANNUAL, RUN_ID, {})
        assert segs == []


# ---------------------------------------------------------------------------
# Balducci (Annual) exposure method tests
# ---------------------------------------------------------------------------

class TestBalduccDeathExposure:
    def test_death_month6_gets_full_year_exposure(self):
        """A death in month 6 of a policy year gets exposure_years = 1.0 (Balducci)."""
        # issue 2010-01-01; death 2020-06-15 (policy year 11, mid-year)
        segs = _segs(
            None,
            issue_date=date(2010, 1, 1),
            status_code="DEATH",
            termination_date=date(2020, 6, 15),
            termination_cause_code="DEATH_BENEFIT_CLAIM",
            level_period_years=20,
        )
        death_segs = [s for s in segs if s["decrement_type"] == "DEATH"]
        assert len(death_segs) == 1
        assert death_segs[0]["exposure_years"] == pytest.approx(1.0)
        assert death_segs[0]["decrement_flag"] is True

    def test_exactly_one_decrement_segment_per_terminated_policy(self):
        """Only the final segment of a terminated policy has decrement_flag=True."""
        segs = _segs(
            None,
            issue_date=date(2010, 1, 1),
            status_code="DEATH",
            termination_date=date(2019, 3, 20),
            termination_cause_code="DEATH_BENEFIT_CLAIM",
            level_period_years=20,
        )
        decrement_segs = [s for s in segs if s["decrement_flag"]]
        assert len(decrement_segs) == 1
        non_decrement_segs = [s for s in segs if not s["decrement_flag"]]
        for s in non_decrement_segs:
            assert s["decrement_type"] is None

    def test_in_force_policy_has_no_decrement_segment(self):
        """An IF policy produces zero segments with decrement_flag=True."""
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        assert all(not s["decrement_flag"] for s in segs)


class TestFractionalExposure:
    def test_lapse_month6_gets_half_year_exposure(self):
        """A lapse in month 6 of a policy year yields exposure ≈ 0.5 (fractional)."""
        # issue 2010-01-01; lapse 2020-07-02 — roughly mid-year of policy year 11
        segs = _segs(
            None,
            issue_date=date(2010, 1, 1),
            status_code="LAPSE",
            termination_date=date(2020, 7, 2),
            termination_cause_code="LAPSE",
            level_period_years=20,
        )
        lapse_segs = [s for s in segs if s["decrement_type"] == "LAPSE"]
        assert len(lapse_segs) == 1
        exp = lapse_segs[0]["exposure_years"]
        # 2020-01-01 to 2020-07-02 = 183 days / 366 days in 2020 (leap year) ≈ 0.50
        assert 0.45 < exp < 0.56

    def test_all_non_death_exposures_less_than_one(self):
        """Non-Balducci segments should never produce exposure > 1.0."""
        segs = _segs(None, issue_date=date(2008, 6, 15), level_period_years=20)
        for s in segs:
            assert s["exposure_years"] <= 1.0001, f"Segment has exposure {s['exposure_years']}"


# ---------------------------------------------------------------------------
# PLT flag tests
# ---------------------------------------------------------------------------

class TestPLTFlag:
    def test_plt_flag_not_set_before_level_period_ends(self):
        """Segments within the level period must have is_plt_flag=False."""
        # T20 issued 2010-01-01; level period ends 2030-01-01 (after study)
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        for s in segs:
            # policy year during study: max is 2023-2010=13, so all within level period
            assert s["is_plt_flag"] is False

    def test_plt_flag_set_after_level_period_ends(self):
        """Segments beyond the level period must have is_plt_flag=True."""
        # T10 issued 2005-01-01; level period ends 2015-01-01; study starts 2016-01-01
        # By 2016, we're in year 12 (policy_year > 10), so all segments are PLT
        segs = _segs(
            None,
            issue_date=date(2005, 1, 1),
            level_period_years=10,
            plan_code="T10",
            premium_jump_ratio=5.0,
            plt_structure_code="JUMP_TO_ART",
        )
        for s in segs:
            assert s["is_plt_flag"] is True, f"Expected PLT=True for year {s['policy_year']}"

    def test_plt_duration_populated_for_plt_segments(self):
        """plt_duration should be set and positive for PLT segments."""
        segs = _segs(
            None,
            issue_date=date(2005, 1, 1),
            level_period_years=10,
            plan_code="T10",
            premium_jump_ratio=5.0,
        )
        for s in segs:
            assert s["plt_duration"] is not None
            assert s["plt_duration"] >= 1

    def test_plt_flag_transition_mid_study(self):
        """For a T10 issued 2008-01-01, level period ends 2018-01-01.
        Segments before 2018-01-01 should be level; segments from 2018 onward PLT."""
        segs = _segs(
            None,
            issue_date=date(2008, 1, 1),
            level_period_years=10,
            plan_code="T10",
            premium_jump_ratio=4.5,
            plt_structure_code="GRADED",
        )
        for s in segs:
            if s["policy_year"] <= 10:
                assert s["is_plt_flag"] is False
            else:
                assert s["is_plt_flag"] is True

    def test_premium_jump_ratio_band_set_in_plt_segment(self):
        """PLT segments with premium_jump_ratio should have jump band populated."""
        segs = _segs(
            None,
            issue_date=date(2005, 1, 1),
            level_period_years=10,
            plan_code="T10",
            premium_jump_ratio=4.0,   # 3-5x band
        )
        for s in segs:
            assert s["premium_jump_ratio_band"] == "3-5x"


# ---------------------------------------------------------------------------
# Policy year assignment
# ---------------------------------------------------------------------------

class TestPolicyYear:
    def test_segment_starting_on_anniversary_gets_correct_policy_year(self):
        """Segment starting on the 6th anniversary of 2010-01-01 is policy year 7."""
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        # Find the segment starting on 2016-01-01 (6th anniversary)
        seg_2016 = [s for s in segs if s["segment_start_date"] == date(2016, 1, 1)]
        assert len(seg_2016) >= 1
        assert seg_2016[0]["policy_year"] == 7

    def test_first_segment_of_recently_issued_policy_is_policy_year_1(self):
        """Newly issued policy segment must be in policy year 1."""
        segs = _segs(None, issue_date=date(2020, 3, 15), level_period_years=20)
        assert segs[0]["policy_year"] == 1


# ---------------------------------------------------------------------------
# Dimensional field population
# ---------------------------------------------------------------------------

class TestDimensionalFields:
    def test_all_required_fields_present(self):
        """Every segment must contain all gold_exposure_segments columns."""
        required = {
            "segment_id", "study_run_id", "policy_id", "product_code",
            "segment_start_date", "segment_end_date", "exposure_years",
            "face_amount_start", "face_amount_end", "face_amount_wtd_avg",
            "ci_rider_sum_assured", "ci_rider_in_force_flag",
            "attained_age_start", "attained_age_end", "attained_age_band",
            "issue_age_anb", "issue_age_band", "policy_year", "duration_band",
            "calendar_year", "gender", "smoker_status", "risk_class", "plan_code",
            "is_plt_flag", "plt_duration", "plt_structure_code",
            "premium_jump_ratio", "premium_jump_ratio_band",
            "distribution_channel", "decrement_flag", "decrement_type",
            "illness_code", "face_amount_at_decrement", "exposure_method",
        }
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        assert segs, "Expected at least one segment"
        for key in required:
            assert key in segs[0], f"Missing field: {key}"

    def test_calendar_year_matches_segment_start(self):
        """calendar_year must equal the year of segment_start_date."""
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        for s in segs:
            assert s["calendar_year"] == s["segment_start_date"].year

    def test_face_amount_at_decrement_set_only_on_decrement_segment(self):
        """face_amount_at_decrement should be None for non-decrement segments."""
        segs = _segs(
            None,
            issue_date=date(2010, 1, 1),
            status_code="LAPSE",
            termination_date=date(2020, 7, 2),
            termination_cause_code="LAPSE",
            level_period_years=20,
        )
        for s in segs:
            if s["decrement_flag"]:
                assert s["face_amount_at_decrement"] == pytest.approx(500_000.0)
            else:
                assert s["face_amount_at_decrement"] is None

    def test_ci_rider_in_force_false_after_ci_claim(self):
        """The CI_CLAIM decrement segment must have ci_rider_in_force_flag=False."""
        segs = _segs(
            None,
            issue_date=date(2010, 1, 1),
            status_code="CI_CLAIM",
            termination_date=date(2020, 5, 10),
            termination_cause_code="CI_ACCELERATED_BENEFIT",
            level_period_years=20,
            ci_rider_flag=True,
            ci_rider_sum_assured=250_000.0,
        )
        ci_seg = [s for s in segs if s["decrement_flag"]]
        assert len(ci_seg) == 1
        assert ci_seg[0]["ci_rider_in_force_flag"] is False
        # Prior segments should have it True
        prior = [s for s in segs if not s["decrement_flag"]]
        for s in prior:
            assert s["ci_rider_in_force_flag"] is True


# ---------------------------------------------------------------------------
# Exposure method constant
# ---------------------------------------------------------------------------

class TestExposureMethod:
    def test_exposure_method_field_is_annual(self):
        """All segments should have exposure_method='ANNUAL'."""
        segs = _segs(None, issue_date=date(2010, 1, 1), level_period_years=20)
        assert all(s["exposure_method"] == "ANNUAL" for s in segs)
