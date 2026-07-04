"""Tests for src/ai/glm/bootstrap.py — parametric bootstrap CIs (Session 15).

Covers FR-3A-21 (CIs populated), determinism / order-independence (seed-derived
child seeds), and FR-3A-22 / NFR-T-05 (no resample arrays persisted to disk).
"""
import math

import numpy as np
import pytest

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.glm.bootstrap import bootstrap_cis


def _fit_lapse(synthetic_db, glm_config):
    """Lapse fit is small (duration-only) — quick to bootstrap."""
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.LAPSE, "TERM")
    fitted = fit_glm(
        cells, DecrementType.LAPSE, "TERM",
        covariates=glm_config["covariates"]["lapse"],
        output_grain=glm_config["output_grain"]["lapse"],
        min_events_to_fit=glm_config["min_events_to_fit"], seed=42,
    )
    return cells, fitted


def test_bootstrap_populates_finite_cis(synthetic_db, glm_config):
    cells, fitted = _fit_lapse(synthetic_db, glm_config)
    out = bootstrap_cis(
        cells, DecrementType.LAPSE, "TERM",
        glm_config["covariates"]["lapse"], glm_config["output_grain"]["lapse"],
        fitted, n_resamples=200, ci_level=0.95, seed=42,
    )
    assert out.factors
    for fc in out.factors:
        assert math.isfinite(fc.ci_low) and math.isfinite(fc.ci_high)
        assert fc.ci_low <= fc.factor <= fc.ci_high


def test_bootstrap_is_deterministic(synthetic_db, glm_config):
    """Same seed -> identical CIs (child seeds make it order-independent)."""
    cells, fitted = _fit_lapse(synthetic_db, glm_config)
    kw = dict(covariates=glm_config["covariates"]["lapse"],
              output_grain=glm_config["output_grain"]["lapse"])
    a = bootstrap_cis(cells, DecrementType.LAPSE, "TERM", fitted=fitted,
                      n_resamples=150, seed=42, **kw)
    b = bootstrap_cis(cells, DecrementType.LAPSE, "TERM", fitted=fitted,
                      n_resamples=150, seed=42, **kw)
    for fa, fb in zip(a.factors, b.factors):
        assert fa.ci_low == fb.ci_low and fa.ci_high == fb.ci_high


def test_no_resample_arrays_persisted(synthetic_db, glm_config, tmp_path):
    """Bootstrap writes nothing to disk (FR-3A-22 / NFR-T-05)."""
    cells, fitted = _fit_lapse(synthetic_db, glm_config)
    before = {p for p in tmp_path.rglob("*")}
    bootstrap_cis(
        cells, DecrementType.LAPSE, "TERM",
        glm_config["covariates"]["lapse"], glm_config["output_grain"]["lapse"],
        fitted, n_resamples=100, seed=42,
    )
    after = {p for p in tmp_path.rglob("*")}
    assert before == after


def test_bootstrap_passthrough_when_no_proposal(glm_config):
    """A non-converged fit is returned unchanged (nothing to bound)."""
    import pandas as pd
    from src.ai.glm.fit import fit_glm as _fg
    tiny = pd.DataFrame([
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "1",
         "expected_lapses": 3.0, "actual_lapses": 2, "lapse_exposure_count": 60.0},
    ])
    fitted = _fg(tiny, DecrementType.LAPSE, "TERM",
                 covariates=["duration_band"], output_grain=["duration_band"],
                 min_events_to_fit=200, seed=42)
    out = bootstrap_cis(tiny, DecrementType.LAPSE, "TERM",
                        ["duration_band"], ["duration_band"], fitted,
                        n_resamples=50, seed=42)
    assert out.factors == [] and out.converged is False
