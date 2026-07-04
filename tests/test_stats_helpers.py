"""Unit tests for ui/stats_helpers.py aggregate credibility helpers."""

import math
import numpy as np
import pytest
from ui.stats_helpers import credibility_z


class TestCredibilityZLF:
    def test_default_method_is_lf(self):
        """No method arg -> Limited Fluctuation (Z = 1.0 at threshold)."""
        assert credibility_z(1082.0) == pytest.approx(1.0)

    def test_lf_partial(self):
        assert credibility_z(400.0) == pytest.approx(math.sqrt(400.0 / 1082.0))

    def test_lf_caps_at_one(self):
        assert credibility_z(5000.0) == pytest.approx(1.0)

    def test_zero_returns_zero(self):
        assert credibility_z(0.0) == 0.0


class TestCredibilityZBuhlmann:
    def test_buhlmann_scalar_at_K(self):
        """n = K = 1082 -> Z = sqrt(0.5)."""
        assert credibility_z(1082.0, method="BUHLMANN") == pytest.approx(math.sqrt(0.5))

    def test_buhlmann_array(self):
        out = credibility_z(np.array([0.0, 1082.0]), method="BUHLMANN")
        assert out[0] == 0.0
        assert out[1] == pytest.approx(math.sqrt(0.5))

    def test_buhlmann_case_insensitive(self):
        assert credibility_z(1082.0, method="buhlmann") == pytest.approx(math.sqrt(0.5))

    def test_buhlmann_below_lf_under_threshold(self):
        n = 500.0
        assert credibility_z(n, method="BUHLMANN") < credibility_z(n, method="LF")

    def test_unknown_method_defaults_to_lf(self):
        assert credibility_z(1082.0, method="XYZ") == pytest.approx(1.0)
