"""Unit tests for A/E calculation engine."""

import math
import pandas as pd
import pytest
from src.calculation.ae_engine import (
    compute_credibility_z,
    compute_poisson_ci,
    _vectorised_z,
)


class TestCredibilityZ:
    def test_full_credibility_at_threshold(self):
        """Z = 1.0 when actual_claims == threshold."""
        z = compute_credibility_z(1082.0, method="LF", threshold=1082.0)
        assert z == pytest.approx(1.0)

    def test_full_credibility_above_threshold(self):
        """Z = 1.0 when actual_claims > threshold."""
        z = compute_credibility_z(5000.0, method="LF", threshold=1082.0)
        assert z == pytest.approx(1.0)

    def test_partial_credibility_formula(self):
        """Z = sqrt(actual / threshold) when actual < threshold."""
        actual = 400.0
        threshold = 1082.0
        expected_z = math.sqrt(actual / threshold)
        z = compute_credibility_z(actual, method="LF", threshold=threshold)
        assert z == pytest.approx(expected_z, rel=1e-6)

    def test_zero_actual_returns_zero(self):
        """Z = 0.0 when actual_claims = 0."""
        z = compute_credibility_z(0.0, method="LF", threshold=1082.0)
        assert z == 0.0

    def test_negative_actual_returns_zero(self):
        """Z = 0.0 for negative input."""
        z = compute_credibility_z(-5.0, method="LF", threshold=1082.0)
        assert z == 0.0

    def test_z_between_zero_and_one(self):
        """Z is always in [0, 1]."""
        for actual in [0, 1, 100, 500, 1082, 10000]:
            z = compute_credibility_z(float(actual), method="LF", threshold=1082.0)
            assert 0.0 <= z <= 1.0


class TestCredibilityZBuhlmann:
    def test_buhlmann_at_K_equals_half_root(self):
        """n = K = 1082 -> Z = sqrt(1082 / 2164) = sqrt(0.5)."""
        z = compute_credibility_z(1082.0, method="BUHLMANN", threshold=1082.0)
        assert z == pytest.approx(math.sqrt(0.5))

    def test_buhlmann_partial_formula(self):
        """Z = sqrt(n / (n + K)) for the simplified fixed-K Buhlmann form."""
        n, k = 400.0, 1082.0
        z = compute_credibility_z(n, method="BUHLMANN", threshold=k)
        assert z == pytest.approx(math.sqrt(n / (n + k)), rel=1e-9)

    def test_buhlmann_zero_returns_zero(self):
        """Z = 0.0 when actual_claims = 0."""
        assert compute_credibility_z(0.0, method="BUHLMANN", threshold=1082.0) == 0.0

    def test_buhlmann_negative_returns_zero(self):
        """Z = 0.0 for negative input."""
        assert compute_credibility_z(-5.0, method="BUHLMANN", threshold=1082.0) == 0.0

    def test_buhlmann_never_reaches_one(self):
        """sqrt(n / (n + K)) is strictly < 1 even for very large n."""
        z = compute_credibility_z(1_000_000.0, method="BUHLMANN", threshold=1082.0)
        assert 0.0 < z < 1.0

    def test_buhlmann_below_lf_under_threshold(self):
        """For n < K, Buhlmann Z is strictly below LF Z."""
        n = 500.0
        z_lf = compute_credibility_z(n, method="LF", threshold=1082.0)
        z_b = compute_credibility_z(n, method="BUHLMANN", threshold=1082.0)
        assert z_b < z_lf

    def test_method_casing_normalised(self):
        """Lower-case 'buhlmann' resolves to the Buhlmann branch."""
        z = compute_credibility_z(1082.0, method="buhlmann", threshold=1082.0)
        assert z == pytest.approx(math.sqrt(0.5))

    def test_unknown_method_defaults_to_lf(self):
        """An unrecognised method falls back to LF (Z = 1.0 at threshold)."""
        z = compute_credibility_z(1082.0, method="XYZ", threshold=1082.0)
        assert z == pytest.approx(1.0)


class TestVectorisedZ:
    def test_vectorised_lf_matches_scalar(self):
        """LF vectorised Z matches the scalar formula and caps at 1.0."""
        s = pd.Series([0.0, 400.0, 1082.0, 5000.0])
        out = _vectorised_z(s, "LF", 1082.0)
        assert out.iloc[0] == 0.0
        assert out.iloc[1] == pytest.approx(math.sqrt(400.0 / 1082.0))
        assert out.iloc[2] == pytest.approx(1.0)
        assert out.iloc[3] == pytest.approx(1.0)  # capped

    def test_vectorised_buhlmann_formula(self):
        """Buhlmann vectorised Z follows sqrt(n / (n + K))."""
        s = pd.Series([0.0, 1082.0])
        out = _vectorised_z(s, "BUHLMANN", 1082.0)
        assert out.iloc[0] == 0.0
        assert out.iloc[1] == pytest.approx(math.sqrt(0.5))


class TestPoissonCI:
    def test_ci_symmetric_around_ae(self):
        """95% CI is symmetric: upper - ae == ae - lower."""
        ae = 0.92
        actual = 91.0
        lower, upper = compute_poisson_ci(ae, actual, confidence=0.95)
        assert not math.isnan(lower)
        assert not math.isnan(upper)
        assert upper - ae == pytest.approx(ae - lower, rel=1e-6)

    def test_ae_1_with_equal_actuals_expected(self):
        """When actual == expected, A/E == 1.0 and CI is symmetric about 1.0."""
        # Simulate: actual = expected = 100, so A/E = 1.0
        ae = 1.0
        actual = 100.0
        lower, upper = compute_poisson_ci(ae, actual, confidence=0.95)
        assert ae - lower == pytest.approx(upper - ae, rel=1e-6)
        assert lower < 1.0 < upper

    def test_zero_actuals_returns_nan(self):
        """With zero actual claims, CI cannot be computed."""
        lower, upper = compute_poisson_ci(0.9, 0.0)
        assert math.isnan(lower)
        assert math.isnan(upper)

    def test_ci_width_decreases_with_more_claims(self):
        """Wider CI for fewer claims; narrower for more."""
        ae = 0.90
        lower_few, upper_few = compute_poisson_ci(ae, 10.0)
        lower_many, upper_many = compute_poisson_ci(ae, 1000.0)
        width_few = upper_few - lower_few
        width_many = upper_many - lower_many
        assert width_few > width_many

    def test_se_formula(self):
        """SE = ae / sqrt(actual_claims)."""
        ae = 0.92
        actual = 91.0
        expected_se = ae / math.sqrt(actual)
        lower, upper = compute_poisson_ci(ae, actual)
        computed_se = (upper - lower) / (2 * 1.96)
        assert computed_se == pytest.approx(expected_se, rel=1e-6)
