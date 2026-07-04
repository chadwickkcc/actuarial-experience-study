"""Tests for src/ai/gbm/fit.py — GBM overlay fitting (Session 16).

Covers FR-3A-31 (Poisson base_margin offset / exposure-weighted logistic),
FR-3A-33 (divergence flag fires on disagreement, silent on agreement),
FR-3A-29 (loud-failure guardrail), FR-3A-34 (bootstrap CIs), and determinism
(same seed → identical booster; no resample arrays persisted).
"""
import math

import numpy as np
import pandas as pd

from src.utils.types import DecrementType, FactorCell, GLMFitResult
from src.ai.glm.fit import load_cells
from src.ai.gbm.fit import (
    fit_gbm, bootstrap_gbm_cis, _fit_gbm_core, _divergence_flags,
)

_MORT_COV = ["product_code", "gender", "smoker_status", "risk_class",
             "attained_age_band", "duration_band", "premium_jump_ratio_band"]
_MORT_GRAIN = ["product", "sex", "smoker", "attained_age_band"]


def _const_factor_cells(k: float, run_id: str = "R-CONST") -> pd.DataFrame:
    """Mortality cells with actual = round(expected·k) and no covariate signal."""
    rows = []
    for gender in ["M", "F"]:
        for smoker in ["NS", "SM"]:
            for age in ["40-44", "45-49", "50-54"]:
                for dur in ["1", "2-5", "6-10"]:
                    exp = 40.0
                    rows.append({
                        "study_run_id": run_id, "product_code": "TERM",
                        "gender": gender, "smoker_status": smoker, "risk_class": "STD_NS",
                        "attained_age_band": age, "duration_band": dur,
                        "premium_jump_ratio_band": None, "illness_code": None,
                        "exposure_count": exp / 0.01, "expected_deaths_count": exp,
                        "actual_deaths_count": int(round(exp * k)),
                        "lapse_exposure_count": 0.0, "expected_lapses": 0.0, "actual_lapses": 0,
                    })
    return pd.DataFrame(rows)


def test_poisson_base_margin_offset_handcheck(gbm_config):
    """base_margin=log(expected): with actual=expected·k the booster predicts
    ≈ k·expected per cell and the published factor ≈ k (FR-3A-31)."""
    k = 1.2
    cells = _const_factor_cells(k)
    core = _fit_gbm_core(cells, DecrementType.MORTALITY, _MORT_COV, _MORT_GRAIN,
                         gbm_config["hyperparams"], gbm_config["seed"])
    fit_cells = core["fit_cells"]
    np.testing.assert_allclose(
        fit_cells["_predicted_events"].to_numpy(),
        k * fit_cells["_reference_events"].to_numpy(),
        rtol=0.10,
    )
    fitted = fit_gbm(cells, DecrementType.MORTALITY, "TERM", _MORT_COV, _MORT_GRAIN,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     min_events_to_fit=200, seed=gbm_config["seed"])
    assert fitted.factors
    for fc in fitted.factors:
        assert abs(fc.factor - k) / k < 0.10


def _glm_like(gbm_fitted, tweak_index=None, tweak_mult=1.0) -> GLMFitResult:
    """A GLMFitResult whose factors mirror the GBM's, optionally perturbing one."""
    factors = []
    for i, fc in enumerate(gbm_fitted.factors):
        val = fc.factor * tweak_mult if i == tweak_index else fc.factor
        factors.append(FactorCell(
            grain_key=dict(fc.grain_key), factor=val, ci_low=float("nan"),
            ci_high=float("nan"), expected_events=fc.expected_events,
            credibility_z=0.0, ae_derived_factor=val,
        ))
    return GLMFitResult(
        model_id="glm", run_id="R", decrement=gbm_fitted.decrement,
        product_code=gbm_fitted.product_code, converged=True, n_cells=len(factors),
        deviance=0.0, dispersion=1.0, aic=0.0, factors=factors,
        diagnostics_path="", seed=42, message=None,
    )


def test_divergence_flag_fires_and_silent(synthetic_db, glm_config, gbm_config):
    """Identical GLM factors → no flags; one factor beyond threshold → exactly one (FR-3A-33)."""
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    gbm = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                  gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                  glm_config["min_events_to_fit"], gbm_config["seed"])
    assert len(gbm.factors) >= 2
    thr = gbm_config["divergence_threshold"]

    silent = _glm_like(gbm)
    assert _divergence_flags(gbm.factors, silent, thr) == []

    fire = _glm_like(gbm, tweak_index=0, tweak_mult=1.0 + thr + 0.05)
    flags = _divergence_flags(gbm.factors, fire, thr)
    assert len(flags) == 1
    assert flags[0]["grain_key"] == dict(gbm.factors[0].grain_key)
    assert flags[0]["rel_diff"] > thr


def test_no_glm_means_no_flags(synthetic_db, glm_config, gbm_config):
    """With no GLM supplied, divergence_flags is empty (challenge model has nothing to challenge)."""
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    gbm = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                  gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                  glm_config["min_events_to_fit"], gbm_config["seed"])
    assert gbm.divergence_flags == []


def test_guardrail_no_proposal_below_threshold(gbm_config):
    """Sub-min_events_to_fit returns empty factors, never a number (FR-3A-29)."""
    tiny = pd.DataFrame([
        {"study_run_id": "R", "product_code": "TERM", "duration_band": "1",
         "premium_jump_ratio_band": None, "expected_lapses": 5.0, "actual_lapses": 3,
         "lapse_exposure_count": 100.0},
    ])
    fitted = fit_gbm(tiny, DecrementType.LAPSE, "TERM",
                     ["product_code", "duration_band", "premium_jump_ratio_band"],
                     ["product", "duration_band"], gbm_config["hyperparams"], None,
                     gbm_config["divergence_threshold"], min_events_to_fit=200,
                     seed=gbm_config["seed"])
    assert fitted.factors == []
    assert math.isnan(fitted.cv_metric_value)
    assert fitted.divergence_flags == []


def test_same_seed_identical_booster(synthetic_db, glm_config, gbm_config):
    """Determinism: same seed → identical predictions and serialized booster."""
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    a = _fit_gbm_core(cells, DecrementType.LAPSE, cov, grain, gbm_config["hyperparams"], 42)
    b = _fit_gbm_core(cells, DecrementType.LAPSE, cov, grain, gbm_config["hyperparams"], 42)
    np.testing.assert_array_equal(a["booster"].predict(a["dtrain"]),
                                  b["booster"].predict(b["dtrain"]))
    assert a["booster"].save_raw() == b["booster"].save_raw()


def test_bootstrap_populates_cis(synthetic_db, glm_config, gbm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    fitted = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     glm_config["min_events_to_fit"], gbm_config["seed"])
    out = bootstrap_gbm_cis(cells, DecrementType.LAPSE, "TERM", cov, grain,
                            gbm_config["hyperparams"], fitted, n_resamples=40, seed=42)
    assert out.factors
    for fc in out.factors:
        assert math.isfinite(fc.ci_low) and math.isfinite(fc.ci_high)
        assert fc.ci_low <= fc.ci_high


def test_bootstrap_deterministic(synthetic_db, glm_config, gbm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    fitted = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     glm_config["min_events_to_fit"], gbm_config["seed"])
    kw = dict(hyperparams=gbm_config["hyperparams"], n_resamples=30, seed=42)
    a = bootstrap_gbm_cis(cells, DecrementType.LAPSE, "TERM", cov, grain, fitted=fitted, **kw)
    b = bootstrap_gbm_cis(cells, DecrementType.LAPSE, "TERM", cov, grain, fitted=fitted, **kw)
    for fa, fb in zip(a.factors, b.factors):
        assert fa.ci_low == fb.ci_low and fa.ci_high == fb.ci_high


def test_no_resample_arrays_persisted(synthetic_db, glm_config, gbm_config, tmp_path):
    """Bootstrap writes nothing to disk (FR-3A-22 / NFR-T-05)."""
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    fitted = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     glm_config["min_events_to_fit"], gbm_config["seed"])
    before = {p for p in tmp_path.rglob("*")}
    bootstrap_gbm_cis(cells, DecrementType.LAPSE, "TERM", cov, grain,
                      gbm_config["hyperparams"], fitted, n_resamples=30, seed=42)
    after = {p for p in tmp_path.rglob("*")}
    assert before == after
