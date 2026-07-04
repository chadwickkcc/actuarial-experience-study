"""Tests for src/ai/glm/fit.py — GLM fitting and factor derivation (Session 15).

Covers FR-3A-13 (Poisson offset), FR-3A-14 (derive_factor), FR-3A-15 (Gold-only
reads), FR-3A-18/19 (output-grain factors), FR-3A-24 (determinism), FR-3A-29
(loud-failure guardrail).
"""
import math

import numpy as np
import pandas as pd
import pytest

from src.utils.types import DecrementType
from src.ai.glm.fit import derive_factor, load_cells, fit_glm


# --------------------------------------------------------------------------- #
# derive_factor (FR-3A-14)
# --------------------------------------------------------------------------- #

def test_derive_factor_normal():
    assert derive_factor(0.06, 0.05) == pytest.approx(1.2)


def test_derive_factor_zero_benchmark_is_nan():
    """A zero benchmark yields NaN so the cell is excluded — never a borrow."""
    assert math.isnan(derive_factor(0.05, 0.0))
    assert math.isnan(derive_factor(0.05, float("nan")))


# --------------------------------------------------------------------------- #
# Poisson offset hand-calc (FR-3A-13) — factor matches to 4 decimal places
# --------------------------------------------------------------------------- #

def _exact_mortality_cells() -> pd.DataFrame:
    """Two exact cells: actual = expected x known factor (no Poisson noise)."""
    return pd.DataFrame([
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "1",
         "expected_deaths_count": 100.0, "actual_deaths_count": 90,
         "exposure_count": 9000.0},
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "2-5",
         "expected_deaths_count": 200.0, "actual_deaths_count": 220,
         "exposure_count": 18000.0},
    ])


def test_poisson_offset_handcalc():
    """A saturated Poisson+offset fit recovers actual/expected exactly (4 dp)."""
    cells = _exact_mortality_cells()
    res = fit_glm(
        cells, DecrementType.MORTALITY, "TERM",
        covariates=["duration_band"], output_grain=["duration_band"],
        min_events_to_fit=10, seed=42,
    )
    assert res.converged
    factors = {fc.grain_key["duration_band"]: fc.factor for fc in res.factors}
    assert factors["1"] == pytest.approx(0.9000, abs=1e-4)
    assert factors["2-5"] == pytest.approx(1.1000, abs=1e-4)


# --------------------------------------------------------------------------- #
# Determinism (FR-3A-24)
# --------------------------------------------------------------------------- #

def test_fit_is_deterministic():
    cells = _exact_mortality_cells()
    kw = dict(decrement=DecrementType.MORTALITY, product_code="TERM",
              covariates=["duration_band"], output_grain=["duration_band"],
              min_events_to_fit=10, seed=42)
    a = fit_glm(cells, **kw)
    b = fit_glm(cells, **kw)
    assert a.deviance == b.deviance and a.aic == b.aic
    fa = {fc.grain_key["duration_band"]: fc.factor for fc in a.factors}
    fb = {fc.grain_key["duration_band"]: fc.factor for fc in b.factors}
    assert fa == fb


# --------------------------------------------------------------------------- #
# Guardrail (FR-3A-29) — below min_events_to_fit returns "no proposal"
# --------------------------------------------------------------------------- #

def test_guardrail_below_min_events_returns_no_proposal():
    tiny = pd.DataFrame([
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "1",
         "expected_deaths_count": 5.0, "actual_deaths_count": 4,
         "exposure_count": 500.0},
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "2-5",
         "expected_deaths_count": 5.0, "actual_deaths_count": 6,
         "exposure_count": 500.0},
    ])
    res = fit_glm(
        tiny, DecrementType.MORTALITY, "TERM",
        covariates=["duration_band"], output_grain=["duration_band"],
        min_events_to_fit=200, seed=42,
    )
    assert res.converged is False
    assert res.factors == []
    assert "min_events_to_fit" in (res.message or "")


# --------------------------------------------------------------------------- #
# load_cells via the SQL boundary (FR-3A-08/15)
# --------------------------------------------------------------------------- #

def test_fit_excludes_zero_expected_cells():
    """A cell with zero expected events (undefined Poisson offset) is dropped,
    not allowed to poison the fit (real Gold data contains such cells)."""
    cells = pd.DataFrame([
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "1",
         "expected_deaths_count": 60.0, "actual_deaths_count": 54,
         "exposure_count": 6000.0},
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "2-5",
         "expected_deaths_count": 140.0, "actual_deaths_count": 150,
         "exposure_count": 14000.0},
        # Only zero-expected rows for this level -> the aggregated cell is unfittable.
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "6-10",
         "expected_deaths_count": 0.0, "actual_deaths_count": 0,
         "exposure_count": 0.0},
    ])
    res = fit_glm(
        cells, DecrementType.MORTALITY, "TERM",
        covariates=["duration_band"], output_grain=["duration_band"],
        min_events_to_fit=200, seed=42,
    )
    assert res.converged
    bands = {fc.grain_key["duration_band"] for fc in res.factors}
    assert bands == {"1", "2-5"}            # the zero-expected band is excluded


def test_load_cells_mortality_detail_rows(synthetic_db):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.MORTALITY, "TERM")
    assert len(cells) == 480
    assert cells["illness_code"].isna().all()
    assert (cells["study_run_id"] == synthetic_db.run_id).all()
    assert (cells["product_code"] == "TERM").all()


def test_load_cells_ci_rows(synthetic_db):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.CI_INCIDENCE, "TERM")
    assert len(cells) == 24
    assert cells["illness_code"].notna().all()


def test_load_cells_filters_other_runs(synthetic_db):
    cells = load_cells(synthetic_db.db_path, "no-such-run",
                       DecrementType.MORTALITY, "TERM")
    assert cells.empty


def test_fit_publishes_at_output_grain(synthetic_db, glm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.MORTALITY, "TERM")
    res = fit_glm(
        cells, DecrementType.MORTALITY, "TERM",
        covariates=glm_config["covariates"]["mortality"],
        output_grain=glm_config["output_grain"]["mortality"],
        min_events_to_fit=glm_config["min_events_to_fit"], seed=glm_config["seed"],
    )
    assert res.converged
    # product x sex x smoker x attained_age_band = 1 x 2 x 2 x 6 = 24 cells
    assert len(res.factors) == 24
    for fc in res.factors:
        assert set(fc.grain_key) == {"product", "sex", "smoker", "attained_age_band"}
        assert np.isfinite(fc.factor) and fc.expected_events > 0
