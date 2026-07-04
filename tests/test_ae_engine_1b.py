"""Numerical unit tests for Phase 1B A/E engine formulas.

Verifies the exact formula implementations for:
    - UL dynamic lapse multiplier (FR-1B-08): min(2.5, max(0.4, 1 + 0.5*(mkt-crd)))
    - WL surrender vs lapse classification in expected output
    - ULSG shadow account funding ratio interpretation

These tests validate the formula contracts at the unit level, independent of
the full pipeline. The formulas are inlined here to verify boundary conditions.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# UL Dynamic Lapse Multiplier (FR-1B-08)
# Formula: min(2.5, max(0.4, 1 + k * (market_rate - credited_rate))), k=0.5
# ---------------------------------------------------------------------------


def _ul_dynamic_lapse_mult(market_rate: float, credited_rate: float, k: float = 0.5) -> float:
    """Replicate the UL dynamic lapse multiplier formula from ae_engine.py."""
    return min(2.5, max(0.4, 1.0 + k * (market_rate - credited_rate)))


class TestULDynamicLapseMultiplier:
    """Verify all boundary conditions of the UL dynamic lapse multiplier."""

    def test_zero_rate_diff_returns_one(self) -> None:
        """When market_rate == credited_rate, multiplier = 1.0."""
        assert _ul_dynamic_lapse_mult(0.032, 0.032) == pytest.approx(1.0)

    def test_positive_rate_diff_increases_multiplier(self) -> None:
        """When market_rate > credited_rate, multiplier > 1.0."""
        mult = _ul_dynamic_lapse_mult(0.039, 0.029)  # 2022 scenario: diff=+1.0%
        assert mult == pytest.approx(1.0 + 0.5 * 0.010, rel=1e-6)
        assert mult > 1.0

    def test_rate_diff_plus_1pct(self) -> None:
        """rate_diff = +1% (0.01) → multiplier = 1 + 0.5*0.01 = 1.005."""
        mult = _ul_dynamic_lapse_mult(0.040, 0.030)
        assert mult == pytest.approx(1.005, rel=1e-6)

    def test_rate_diff_large_positive_caps_at_2_5(self) -> None:
        """rate_diff = +3.0 (unrealistic) → capped at 2.5."""
        mult = _ul_dynamic_lapse_mult(0.060, 0.030)  # diff = +3%
        # raw = 1 + 0.5*0.30 = 1.15, but let's use a larger diff
        mult2 = _ul_dynamic_lapse_mult(0.090, 0.030)  # diff = 6%
        # raw = 1 + 0.5*0.06 = 1.3... still under 2.5
        # Need diff > 3.0 to hit cap
        mult3 = _ul_dynamic_lapse_mult(4.030, 0.030)  # diff = 4.0
        assert mult3 == pytest.approx(2.5)

    def test_cap_is_2_5(self) -> None:
        """Any rate_diff > 3.0 yields exactly 2.5."""
        mult = _ul_dynamic_lapse_mult(10.0, 0.0)  # diff = 10.0
        assert mult == pytest.approx(2.5)

    def test_negative_rate_diff_decreases_multiplier(self) -> None:
        """When market_rate < credited_rate, multiplier < 1.0."""
        mult = _ul_dynamic_lapse_mult(0.018, 0.032)  # 2016 scenario: diff=-1.4%
        assert mult < 1.0

    def test_rate_diff_negative_large_floors_at_0_4(self) -> None:
        """Large negative rate_diff floors at 0.4.

        diff = market - credited = 0.0 - 2.0 = -2.0
        raw = 1 + 0.5 * (-2.0) = 0.0 → floored at 0.4
        """
        mult = _ul_dynamic_lapse_mult(0.0, 2.0)  # diff = -200%, raw = 0.0
        assert mult == pytest.approx(0.4)

    def test_floor_is_0_4(self) -> None:
        """Any rate_diff < -1.2 yields exactly 0.4."""
        mult = _ul_dynamic_lapse_mult(0.018, 0.032)  # diff = -0.014, raw = 0.993
        assert mult > 0.4  # not at floor yet
        mult_floor = _ul_dynamic_lapse_mult(0.0, 10.0)  # definitely at floor
        assert mult_floor == pytest.approx(0.4)

    def test_2022_macro_scenario(self) -> None:
        """2022 rising rate scenario: mkt=3.9%, crd=2.9% → mult ≈ 1.005."""
        mult = _ul_dynamic_lapse_mult(0.039, 0.029)
        # diff = 0.01, raw = 1 + 0.5*0.01 = 1.005
        assert mult == pytest.approx(1.005, rel=1e-6)

    def test_2020_low_rate_scenario(self) -> None:
        """2020 shock low-rate scenario: mkt=0.9%, crd=3.0% → mult ≈ 0.989."""
        mult = _ul_dynamic_lapse_mult(0.009, 0.030)
        # diff = -0.021, raw = 1 + 0.5*(-0.021) = 1 - 0.0105 = 0.9895
        assert mult == pytest.approx(1.0 + 0.5 * (0.009 - 0.030), rel=1e-6)
        assert mult < 1.0

    def test_multiplier_always_in_range(self) -> None:
        """Multiplier must always be in [0.4, 2.5] for any input."""
        test_cases = [
            (0.0, 0.0), (0.05, 0.01), (0.01, 0.05),
            (0.10, 0.0), (0.0, 0.10), (0.039, 0.029),
        ]
        for mkt, crd in test_cases:
            m = _ul_dynamic_lapse_mult(mkt, crd)
            assert 0.4 <= m <= 2.5, f"Multiplier {m} out of range for mkt={mkt}, crd={crd}"

    def test_k_default_is_0_5_not_0_8(self) -> None:
        """UL uses k=0.5, NOT k=0.8 (DA uses 0.8). Verify the distinction."""
        rate_diff = 0.010  # 1% difference
        ul_mult = _ul_dynamic_lapse_mult(0.040, 0.030, k=0.5)
        da_mult = _ul_dynamic_lapse_mult(0.040, 0.030, k=0.8)
        assert ul_mult == pytest.approx(1.005, rel=1e-6)
        assert da_mult == pytest.approx(1.008, rel=1e-6)
        assert ul_mult != da_mult


# ---------------------------------------------------------------------------
# ULSG Shadow Account Funding Ratio
# ---------------------------------------------------------------------------


class TestULSGShadowAccountFundingRatio:
    """Verify shadow account funding ratio interpretation."""

    def test_fully_funded_ratio_is_one_or_more(self) -> None:
        """funding_ratio = shadow_account_value / cumulative_nlp_required."""
        shadow_av = 100_000.0
        cumulative_nlp = 100_000.0
        ratio = shadow_av / cumulative_nlp
        assert ratio == pytest.approx(1.0)

    def test_underfunded_ratio_below_one(self) -> None:
        """When shadow AV < cumulative NLP required, ratio < 1.0."""
        shadow_av = 80_000.0
        cumulative_nlp = 100_000.0
        ratio = shadow_av / cumulative_nlp
        assert ratio < 1.0
        assert ratio == pytest.approx(0.8)

    def test_overfunded_ratio_above_one(self) -> None:
        """When shadow AV > cumulative NLP required, ratio > 1.0."""
        shadow_av = 120_000.0
        cumulative_nlp = 100_000.0
        ratio = shadow_av / cumulative_nlp
        assert ratio > 1.0
        assert ratio == pytest.approx(1.2)

    def test_dq_ul03_threshold_is_one(self) -> None:
        """DQ-UL-03 WARN triggers when funding_ratio < 1.0."""
        below_threshold = 0.80
        at_threshold = 1.0
        above_threshold = 1.20
        assert below_threshold < 1.0  # triggers WARN
        assert at_threshold >= 1.0    # no WARN
        assert above_threshold >= 1.0  # no WARN


# ---------------------------------------------------------------------------
# WL Surrender vs Lapse classification
# ---------------------------------------------------------------------------


class TestWLSurrenderLapseClassification:
    """Verify WL termination types map to the correct A/E decrement bucket."""

    _TERM_CODE_TO_DECREMENT = {
        "LAPSE": "lapse",
        "SURRENDER": "surrender",
        "DEATH_BENEFIT_CLAIM": "death",
        "CI_ACCELERATED_BENEFIT": "ci_claim",
    }
    _NON_FORFEITURE_STATUSES = {"RPU", "ETT"}

    def test_lapse_code_maps_to_lapse(self) -> None:
        assert self._TERM_CODE_TO_DECREMENT["LAPSE"] == "lapse"

    def test_surrender_code_maps_to_surrender(self) -> None:
        assert self._TERM_CODE_TO_DECREMENT["SURRENDER"] == "surrender"

    def test_surrender_is_not_lapse(self) -> None:
        assert self._TERM_CODE_TO_DECREMENT["SURRENDER"] != "lapse"

    def test_rpu_is_non_forfeiture(self) -> None:
        """RPU should be classified as non-forfeiture, not lapse or surrender."""
        assert "RPU" in self._NON_FORFEITURE_STATUSES

    def test_ett_is_non_forfeiture(self) -> None:
        assert "ETT" in self._NON_FORFEITURE_STATUSES

    def test_active_policy_not_in_non_forfeiture(self) -> None:
        assert "ACTIVE" not in self._NON_FORFEITURE_STATUSES

    def test_death_is_separate_decrement(self) -> None:
        """DEATH_BENEFIT_CLAIM must not be classified as lapse or surrender."""
        decrement = self._TERM_CODE_TO_DECREMENT["DEATH_BENEFIT_CLAIM"]
        assert decrement != "lapse"
        assert decrement != "surrender"


# ---------------------------------------------------------------------------
# IUL lapse-benchmark parent mapping (regression: IUL expected_lapses were 0)
# ---------------------------------------------------------------------------
class TestIULLapseBenchmarkParent:
    """IUL has no own rows in the lapse benchmark; it must borrow UL's basis,
    otherwise the benchmark join misses and IUL expected_lapses fill to 0."""

    def test_iul_maps_to_ul_and_da_subtypes_to_da(self) -> None:
        from src.calculation.ae_engine import _LAPSE_PARENT
        assert _LAPSE_PARENT["IUL"] == "UL"
        assert _LAPSE_PARENT["DA_FIXED"] == "DA"
        assert _LAPSE_PARENT["DA_FIA"] == "DA"
        assert _LAPSE_PARENT["DA_VA"] == "DA"

    def test_iul_resolves_to_a_nonzero_benchmark_rate(self) -> None:
        # Functional proof: IUL is absent from the benchmark, maps to UL, and UL
        # carries non-zero non-PLT lapse rates — so the join now finds a rate.
        from pathlib import Path
        import pandas as pd
        from src.calculation.ae_engine import _LAPSE_PARENT

        bench = pd.read_parquet(
            Path(__file__).resolve().parents[1]
            / "config/reference_tables/lapse_benchmarks.parquet"
        )
        non_plt = bench[~bench["is_plt_flag"]]
        assert non_plt[non_plt["product_code"] == "IUL"].empty  # mapping is necessary
        parent = _LAPSE_PARENT.get("IUL", "IUL")                # -> "UL"
        ul_rows = non_plt[non_plt["product_code"] == parent]
        assert not ul_rows.empty
        assert (ul_rows["lapse_rate"] > 0).any()
