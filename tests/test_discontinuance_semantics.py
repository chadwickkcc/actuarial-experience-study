"""Discontinuance vs surrender semantics (adversarial review M-1, M-2, M-3).

`lapse_benchmarks.parquet` holds ONE rate per (product, policy_year), and that
rate measures different things per product:

    TERM / UL / ULSG / IUL / VUL -> lapse only (no SURRENDER decrement exists)
    WL                           -> lapse + surrender combined
    DA                           -> the FRDA surrender curve

It is therefore a **discontinuance** basis. Two defects followed:

M-2  Only WL folded surrenders into ``actual_lapses``, so annuities contributed a
     full expected denominator (1,422) against a zero numerator, understating the
     portfolio lapse A/E by ~24% and contradicting the demo's lapse-spike story.

M-1  ``expected_surrenders`` was a verbatim copy of ``expected_lapses``, so
     ``ae_surrender`` read 0.0000 for five life products and 0.4589 for WL (a
     subset numerator over a combined denominator). There is no separate surrender
     basis anywhere, so no separate surrender A/E is computable.

Owner decisions (2026-08-31): universal discontinuance; surrender A/E NULL where
no genuine basis exists, with actual surrender counts retained.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _seg(product: str, decrement: str | None, n: int = 1) -> list[dict]:
    return [{
        "product_code": product,
        "decrement_type": decrement,
        "lapse_exposure_years": 1.0,
        "lapse_rate": 0.10,
    } for _ in range(n)]


def _discontinuance(df: pd.DataFrame) -> np.ndarray:
    """The rule under test, as the engine must implement it."""
    from src.calculation.ae_engine import _discontinuance_flag
    return _discontinuance_flag(df)


class TestUniversalDiscontinuance:
    """M-2: every product's actual discontinuances must match what its benchmark
    measures — LAPSE or SURRENDER, whichever that product produces."""

    @pytest.mark.parametrize("product", ["TERM", "WL", "UL", "ULSG", "IUL", "VUL",
                                         "DA_FIXED", "DA_FIA", "DA_VA"])
    def test_lapse_counts_as_discontinuance(self, product):
        df = pd.DataFrame(_seg(product, "LAPSE", 3))
        assert _discontinuance(df).sum() == 3

    @pytest.mark.parametrize("product", ["WL", "DA_FIXED", "DA_FIA", "DA_VA"])
    def test_surrender_counts_as_discontinuance(self, product):
        """Annuities were the gap: their decrement is SURRENDER and their
        benchmark IS the surrender curve, yet they counted zero."""
        df = pd.DataFrame(_seg(product, "SURRENDER", 4))
        assert _discontinuance(df).sum() == 4, (
            f"{product} surrenders must count against its discontinuance basis"
        )

    def test_deaths_are_not_discontinuances(self):
        df = pd.DataFrame(_seg("TERM", "DEATH", 5))
        assert _discontinuance(df).sum() == 0

    def test_annuity_portfolio_is_no_longer_zero_over_a_real_denominator(self):
        df = pd.DataFrame(_seg("DA_FIXED", "SURRENDER", 10) + _seg("DA_FIXED", None, 90))
        actual = _discontinuance(df).sum()
        expected = (df["lapse_exposure_years"] * df["lapse_rate"]).sum()
        assert actual == 10 and expected == pytest.approx(10.0)
        assert actual / expected == pytest.approx(1.0), (
            "an annuity book that surrenders at the benchmark rate must show A/E 1.0, "
            "not 0.0"
        )


class TestSurrenderHasNoSeparateBasis:
    """M-1: publishing a surrender A/E against the lapse basis is not a statistic."""

    def test_expected_surrenders_is_null(self):
        from src.calculation.ae_engine import _surrender_expected
        exp = _surrender_expected(pd.DataFrame(_seg("WL", "SURRENDER", 3)))
        assert exp.isna().all(), (
            "there is no separate surrender basis, so expected_surrenders must be "
            "NULL rather than a copy of the lapse expectation"
        )

    def test_actual_surrender_counts_are_still_reported(self):
        """The count is real experience and must survive — only the ratio goes."""
        df = pd.DataFrame(_seg("WL", "SURRENDER", 7))
        assert (df["decrement_type"] == "SURRENDER").sum() == 7
