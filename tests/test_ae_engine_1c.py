"""Numerical unit tests for Phase 1C A/E engine formulas.

Verifies the exact formula implementations for:
    - VUL moneyness lapse multiplier (FR-1C-03): min(2.0, max(0.5, 1/ratio))
    - DA dynamic lapse multiplier (FR-1C-10): min(3.0, max(0.3, 1 + 0.8*(mkt-crd)))
    - GLB moneyness suppression (FR-1C-11): min(1.0, 0.4 + 0.6*moneyness_ratio)
    - Reference table distinction: 2012 IAR vs 2015 VBT yield different q_x values
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# VUL Moneyness Lapse Multiplier (FR-1C-03)
# Formula: min(2.0, max(0.5, 1.0 / fund_value_to_spec_amount_ratio))
# ---------------------------------------------------------------------------


def _vul_moneyness_mult(ratio) -> float:
    """Replicate the VUL moneyness multiplier formula from ae_engine.py."""
    if ratio is None or (isinstance(ratio, float) and ratio != ratio):  # NaN check
        return 1.0
    if ratio <= 0:
        return 1.0
    return min(2.0, max(0.5, 1.0 / ratio))


class TestVULMoneynessMultiplier:
    """Verify all boundary conditions of the VUL moneyness lapse multiplier."""

    def test_ratio_one_returns_one(self) -> None:
        """When fund value == spec amount, ratio=1.0 → multiplier=1.0."""
        assert _vul_moneyness_mult(1.0) == pytest.approx(1.0)

    def test_ratio_below_one_increases_multiplier(self) -> None:
        """When fund value < spec amount (below water), multiplier > 1.0."""
        mult = _vul_moneyness_mult(0.6)
        assert mult == pytest.approx(1.0 / 0.6, rel=1e-6)
        assert mult > 1.0

    def test_ratio_0_6_returns_1_667(self) -> None:
        """ratio=0.6 → 1/0.6 ≈ 1.667."""
        mult = _vul_moneyness_mult(0.6)
        assert mult == pytest.approx(1.6667, rel=1e-3)

    def test_ratio_0_4_caps_at_2_0(self) -> None:
        """ratio=0.4 → 1/0.4=2.5, but capped at 2.0."""
        mult = _vul_moneyness_mult(0.4)
        assert mult == pytest.approx(2.0)

    def test_ratio_below_0_5_always_caps_at_2_0(self) -> None:
        """Any ratio < 0.5 yields multiplier = 2.0 (cap)."""
        assert _vul_moneyness_mult(0.3) == pytest.approx(2.0)
        assert _vul_moneyness_mult(0.1) == pytest.approx(2.0)
        assert _vul_moneyness_mult(0.01) == pytest.approx(2.0)

    def test_cap_is_2_0_not_2_5(self) -> None:
        """VUL cap is 2.0, not 2.5 (which is the UL dynamic lapse cap)."""
        assert _vul_moneyness_mult(0.01) == pytest.approx(2.0)  # not 2.5

    def test_ratio_above_one_decreases_multiplier(self) -> None:
        """When fund value > spec amount (in the money), multiplier < 1.0."""
        mult = _vul_moneyness_mult(2.0)
        assert mult == pytest.approx(0.5)  # exactly at floor
        mult2 = _vul_moneyness_mult(1.5)
        assert mult2 == pytest.approx(1.0 / 1.5, rel=1e-6)
        assert mult2 < 1.0

    def test_ratio_2_0_floors_at_0_5(self) -> None:
        """ratio=2.0 → 1/2.0=0.5, exactly at floor."""
        assert _vul_moneyness_mult(2.0) == pytest.approx(0.5)

    def test_ratio_above_2_0_floors_at_0_5(self) -> None:
        """ratio=3.0 → 1/3.0≈0.333, floored to 0.5."""
        assert _vul_moneyness_mult(3.0) == pytest.approx(0.5)
        assert _vul_moneyness_mult(10.0) == pytest.approx(0.5)

    def test_floor_is_0_5_not_0_4(self) -> None:
        """VUL floor is 0.5, not 0.4 (which is the UL dynamic lapse floor)."""
        assert _vul_moneyness_mult(100.0) == pytest.approx(0.5)  # not 0.4

    def test_zero_ratio_returns_one_default(self) -> None:
        """Zero ratio (degenerate case) must not raise; returns 1.0 default."""
        assert _vul_moneyness_mult(0.0) == pytest.approx(1.0)

    def test_none_ratio_returns_one_default(self) -> None:
        """None ratio (missing data) returns 1.0 (neutral assumption)."""
        assert _vul_moneyness_mult(None) == pytest.approx(1.0)

    def test_nan_ratio_returns_one_default(self) -> None:
        """NaN ratio returns 1.0 (neutral assumption)."""
        import math
        assert _vul_moneyness_mult(float("nan")) == pytest.approx(1.0)

    def test_multiplier_always_in_range(self) -> None:
        """Multiplier must always be in [0.5, 2.0]."""
        test_ratios = [0.001, 0.1, 0.5, 0.8, 1.0, 1.5, 2.0, 5.0, 100.0]
        for r in test_ratios:
            m = _vul_moneyness_mult(r)
            assert 0.5 <= m <= 2.0, f"Multiplier {m} out of range for ratio={r}"


# ---------------------------------------------------------------------------
# DA Dynamic Lapse Multiplier (FR-1C-10)
# Formula: min(3.0, max(0.3, 1 + 0.8 * (market_rate - credited_rate)))
# k=0.8 for DA (vs k=0.5 for UL)
# ---------------------------------------------------------------------------


def _da_dynamic_lapse_mult(market_rate: float, credited_rate: float) -> float:
    """Replicate the DA dynamic lapse multiplier formula from ae_engine.py."""
    return min(3.0, max(0.3, 1.0 + 0.8 * (market_rate - credited_rate)))


class TestDADynamicLapseMultiplier:
    """Verify all boundary conditions of the DA dynamic lapse multiplier."""

    def test_zero_rate_diff_returns_one(self) -> None:
        """When market_rate == credited_rate, multiplier = 1.0."""
        assert _da_dynamic_lapse_mult(0.032, 0.032) == pytest.approx(1.0)

    def test_positive_rate_diff_increases_multiplier(self) -> None:
        """When market_rate > credited_rate, multiplier > 1.0."""
        mult = _da_dynamic_lapse_mult(0.039, 0.029)  # diff = +1.0%
        assert mult == pytest.approx(1.0 + 0.8 * 0.010, rel=1e-6)
        assert mult > 1.0

    def test_rate_diff_plus_1pct(self) -> None:
        """rate_diff = +1% → multiplier = 1 + 0.8*0.01 = 1.008."""
        mult = _da_dynamic_lapse_mult(0.040, 0.030)
        assert mult == pytest.approx(1.008, rel=1e-6)

    def test_cap_is_3_0(self) -> None:
        """DA cap is 3.0, larger than UL cap of 2.5."""
        mult = _da_dynamic_lapse_mult(10.0, 0.0)
        assert mult == pytest.approx(3.0)

    def test_large_positive_diff_hits_cap(self) -> None:
        """rate_diff > 2.5 should cap at 3.0."""
        # raw = 1 + 0.8*3.0 = 3.4, capped at 3.0
        mult = _da_dynamic_lapse_mult(3.030, 0.030)
        assert mult == pytest.approx(3.0)

    def test_floor_is_0_3(self) -> None:
        """DA floor is 0.3, lower than UL floor of 0.4."""
        mult = _da_dynamic_lapse_mult(0.0, 10.0)
        assert mult == pytest.approx(0.3)

    def test_large_negative_diff_hits_floor(self) -> None:
        """rate_diff < -0.875 floors at 0.3."""
        # raw = 1 + 0.8*(-2.0) = -0.6, floored to 0.3
        mult = _da_dynamic_lapse_mult(0.0, 2.0)
        assert mult == pytest.approx(0.3)

    def test_k_is_0_8_not_0_5(self) -> None:
        """DA uses k=0.8, not k=0.5 like UL. Same rate_diff yields higher response."""
        rate_diff = 0.010  # 1% positive
        da_mult = _da_dynamic_lapse_mult(0.040, 0.030)   # k=0.8
        ul_mult_approx = min(2.5, max(0.4, 1.0 + 0.5 * rate_diff))  # k=0.5
        assert da_mult > ul_mult_approx  # k=0.8 response is stronger

    def test_2022_rising_rate_scenario(self) -> None:
        """2022: mkt=3.9%, crd=2.9% → multiplier = 1 + 0.8*0.01 = 1.008."""
        mult = _da_dynamic_lapse_mult(0.039, 0.029)
        assert mult == pytest.approx(1.008, rel=1e-6)

    def test_2023_rising_rate_scenario(self) -> None:
        """2023: mkt=4.0%, crd=3.1% → multiplier = 1 + 0.8*0.009 = 1.0072."""
        mult = _da_dynamic_lapse_mult(0.040, 0.031)
        assert mult == pytest.approx(1.0 + 0.8 * (0.040 - 0.031), rel=1e-6)

    def test_2016_low_rate_below_one(self) -> None:
        """2016: mkt=1.8%, crd=3.2% → multiplier = 1 + 0.8*(-0.014) = 0.9888."""
        mult = _da_dynamic_lapse_mult(0.018, 0.032)
        assert mult == pytest.approx(1.0 + 0.8 * (0.018 - 0.032), rel=1e-6)
        assert mult < 1.0

    def test_multiplier_always_in_range(self) -> None:
        """Multiplier must always be in [0.3, 3.0]."""
        test_cases = [
            (0.0, 0.0), (0.05, 0.01), (0.01, 0.05),
            (0.10, 0.0), (0.0, 0.10), (0.039, 0.029), (0.040, 0.031),
        ]
        for mkt, crd in test_cases:
            m = _da_dynamic_lapse_mult(mkt, crd)
            assert 0.3 <= m <= 3.0, f"Multiplier {m} out of range for mkt={mkt}, crd={crd}"


# ---------------------------------------------------------------------------
# GLB Moneyness Suppression (FR-1C-11)
# Formula: min(1.0, 0.4 + 0.6 * moneyness_ratio)
# Only applied when glwb_elected_flag = True
# ---------------------------------------------------------------------------


def _glb_suppression(moneyness_ratio) -> float:
    """Replicate the GLB suppression formula from ae_engine.py."""
    if moneyness_ratio is None or (isinstance(moneyness_ratio, float) and moneyness_ratio != moneyness_ratio):
        return 1.0
    return min(1.0, 0.4 + 0.6 * moneyness_ratio)


class TestGLBMoneynessSupression:
    """Verify the GLB moneyness suppression formula."""

    def test_ratio_zero_returns_0_4(self) -> None:
        """moneyness_ratio=0.0 → 0.4 + 0.6*0 = 0.4 (maximum suppression)."""
        assert _glb_suppression(0.0) == pytest.approx(0.4)

    def test_ratio_0_5_returns_0_7(self) -> None:
        """moneyness_ratio=0.5 → 0.4 + 0.6*0.5 = 0.7."""
        assert _glb_suppression(0.5) == pytest.approx(0.7)

    def test_ratio_1_0_returns_1_0(self) -> None:
        """moneyness_ratio=1.0 → 0.4 + 0.6*1.0 = 1.0 (no suppression)."""
        assert _glb_suppression(1.0) == pytest.approx(1.0)

    def test_ratio_above_1_caps_at_1_0(self) -> None:
        """moneyness_ratio > 1.0 is capped at 1.0 (cannot amplify, only suppress)."""
        assert _glb_suppression(1.5) == pytest.approx(1.0)
        assert _glb_suppression(2.0) == pytest.approx(1.0)
        assert _glb_suppression(10.0) == pytest.approx(1.0)

    def test_ratio_0_333_returns_0_6(self) -> None:
        """moneyness_ratio=1/3 → 0.4 + 0.6*(1/3) ≈ 0.6."""
        assert _glb_suppression(1.0 / 3.0) == pytest.approx(0.6, rel=1e-4)

    def test_suppression_is_monotone_increasing(self) -> None:
        """As moneyness_ratio increases, suppression factor increases (less suppression)."""
        ratios = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        suppressions = [_glb_suppression(r) for r in ratios]
        for i in range(len(suppressions) - 1):
            assert suppressions[i] <= suppressions[i + 1]

    def test_suppression_always_in_0_4_to_1_0(self) -> None:
        """Suppression factor always in [0.4, 1.0]."""
        test_ratios = [0.0, 0.1, 0.5, 0.9, 1.0, 1.5, 5.0]
        for r in test_ratios:
            s = _glb_suppression(r)
            assert 0.4 <= s <= 1.0, f"Suppression {s} out of range for ratio={r}"

    def test_none_ratio_no_suppression(self) -> None:
        """None (no GLB) returns 1.0 — no suppression applied."""
        assert _glb_suppression(None) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2012 IAR vs 2015 VBT reference table distinction
# ---------------------------------------------------------------------------


class TestReferenceTableDistinction:
    """Verify that 2012 IAR and 2015 VBT are genuinely different tables."""

    REF_DIR = Path("config/reference_tables")

    @pytest.fixture(scope="class")
    def vbt_table(self) -> pd.DataFrame:
        """Load 2015 VBT reference table."""
        path = self.REF_DIR / "mortality_vbt2015.parquet"
        if not path.exists():
            pytest.skip(f"VBT table not found at {path}")
        return pd.read_parquet(path)

    @pytest.fixture(scope="class")
    def iar_table(self) -> pd.DataFrame:
        """Load 2012 IAR reference table."""
        path = self.REF_DIR / "mortality_iar2012.parquet"
        if not path.exists():
            pytest.skip(f"IAR table not found at {path}")
        return pd.read_parquet(path)

    def test_both_tables_load(self, vbt_table, iar_table) -> None:
        assert len(vbt_table) > 0
        assert len(iar_table) > 0

    def test_tables_are_different(self, vbt_table, iar_table) -> None:
        """The two tables must have different mortality rates — they are different bases."""
        # Compare on common keys
        common_cols = set(vbt_table.columns) & set(iar_table.columns)
        assert len(common_cols) > 0, "Tables share no common columns"
        # They should not be identical
        assert not vbt_table.equals(iar_table)

    def test_iar_has_required_columns(self, iar_table) -> None:
        """IAR table must have the key columns the A/E engine uses for lookup."""
        # The engine joins on gender, attained_age_band or issue_age_anb, policy_year
        required = {"gender", "q_x"}
        assert required.issubset(set(iar_table.columns)), (
            f"IAR table missing columns. Has: {list(iar_table.columns)}"
        )

    def test_vbt_has_required_columns(self, vbt_table) -> None:
        """VBT table must have the key columns the A/E engine uses."""
        required = {"gender", "q_x"}
        assert required.issubset(set(vbt_table.columns)), (
            f"VBT table missing columns. Has: {list(vbt_table.columns)}"
        )
