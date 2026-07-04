"""GBM (XGBoost) challenge-model overlay for the GLM proposals (Session 16).

Realises FR-3A-31/32/33/34/35/29 (Req §7.5; Tech Spec §E.4).

The GBM is the *challenge/explain overlay, never the proposal engine* (FR-3A-31).
The GLM (``src/ai/glm/``) proposes the adjustment factors; the GBM detects the
interactions and non-linearities the GLM's main-effects structure misses, and
its published factors are a clearly-labelled reference column at the **same
output grain on the same covariates** as the GLM, so the two are directly
comparable. Where the GBM materially diverges from the GLM for a cell, that
divergence is surfaced as an "interaction signal — investigate" only (FR-3A-33)
— it is never itself adopted (FR-3A-36).

To guarantee that comparability, the fitting cells, the output-grain factor
aggregation, the zero-/non-positive-denominator-cell drop, and the
determinism-first bootstrap are all built by **reusing the proven GLM internals**
(``src/ai/glm/fit.py`` / ``bootstrap.py``); only the model itself (XGBoost core
API, ``base_margin`` offset for ``count:poisson``, exposure-weighted
``binary:logistic``) and the SHAP explainability differ.

All Gold reads still go through ``src.ai.glm.fit.load_cells`` →
``src.utils.sql_boundary.execute_safe_select`` (FR-3A-01/02). The registry INSERT
is a static, parameterized write (FR-3A-09), exactly as for the GLM.
"""
from __future__ import annotations

import math
import uuid
import warnings
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import duckdb
import xgboost as xgb

from src.utils.types import DecrementType, GBMFitResult, GLMFitResult
from src.ai.glm.fit import (
    _MEASURES,
    _used_covariates,
    _output_grain_columns,
    _aggregate_to_covariates,
    _factors_at_output_grain,
)

_MODELS_DIR = Path("data/ai_models")

#: One-hot column separator (``duration_band=6-10``). Shared with the SHAP
#: producer (``explain.py``) so one-hot columns aggregate back to their parent
#: covariate (FR-3A-39).
ONEHOT_SEP = "="

_INSERT_SQL = (
    "INSERT INTO gold_ai_model_registry ("
    "model_id, run_id, model_type, decrement, product_code, fit_ts, converged, "
    "n_cells, deviance, dispersion, aic, cv_metric_name, cv_metric_value, "
    "artifact_path, shap_json_path, data_snapshot_hash, config_hash, "
    "code_version, seed, message"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


# ---------------------------------------------------------------------------
# Fitting-cell construction (reuses the GLM aggregation verbatim)
# ---------------------------------------------------------------------------
def _aggregate_fit_cells(
    cells: pd.DataFrame,
    decrement: DecrementType,
    covariates: list[str],
    output_grain: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Build the *identical* fitting cells the GLM uses, then drop dead cells.

    Mirrors the pre-fit steps of ``src.ai.glm.fit._fit_core`` (reusing its
    exported helpers): aggregate raw Gold cells to the union of the used
    covariates and the output-grain columns, then drop rows whose offset/weight
    denominator is non-positive (a Poisson ``log(expected)`` offset is undefined
    when ``expected == 0``; a binomial exposure weight of 0 carries no
    information). Reusing the same aggregation is what makes the GBM and GLM
    factors directly comparable for divergence flagging (FR-3A-33).
    """
    actual_col, expected_col, exposure_col = _MEASURES[decrement]
    used = _used_covariates(cells, covariates)
    grain_cols = [c for c in _output_grain_columns(output_grain) if c in cells.columns]
    group_cols = list(dict.fromkeys(used + grain_cols))   # union, order-preserving
    fit_cells = _aggregate_to_covariates(cells, group_cols, actual_col, expected_col, exposure_col)

    denom_col = expected_col if decrement is DecrementType.MORTALITY else exposure_col
    fit_cells = fit_cells[fit_cells[denom_col].astype(float) > 0].reset_index(drop=True)
    if fit_cells.empty:
        raise ValueError(f"no fittable cells (all {denom_col} <= 0)")
    return fit_cells, used


def _encode_features(
    fit_cells: pd.DataFrame, used: list[str],
) -> tuple[pd.DataFrame, list[str], dict[str, str]]:
    """One-hot encode the used covariates with a stable, sorted column order.

    Returns the design matrix, the (sorted) feature-column list, and a
    ``onehot_column -> covariate`` map so the SHAP producer can aggregate
    contributions back to the parent covariate (FR-3A-39). When no covariate has
    >=2 levels (degenerate single-product slice), a single constant feature is
    added so XGBoost can fit an intercept-only model; that column maps to no
    covariate and is excluded from SHAP per-covariate aggregation.
    """
    parts = []
    onehot_to_cov: dict[str, str] = {}
    for cov in used:
        series = fit_cells[cov].astype("string").fillna("NA")
        dummies = pd.get_dummies(series, prefix=cov, prefix_sep=ONEHOT_SEP, dtype=float)
        dummies = dummies.reindex(sorted(dummies.columns), axis=1)   # stable order
        for col in dummies.columns:
            onehot_to_cov[col] = cov
        parts.append(dummies)
    if parts:
        design = pd.concat(parts, axis=1)
    else:
        design = pd.DataFrame({"_const": np.ones(len(fit_cells))}, index=fit_cells.index)
    feature_cols = list(design.columns)
    return design, feature_cols, onehot_to_cov


def _objective(decrement: DecrementType) -> str:
    """``count:poisson`` for mortality, ``binary:logistic`` for lapse/CI (FR-3A-31)."""
    return "count:poisson" if decrement is DecrementType.MORTALITY else "binary:logistic"


def _build_params(decrement: DecrementType, hyperparams: dict, seed: int) -> dict:
    """Assemble the fixed XGBoost training params (FR-3A-32; no tuning).

    Only the known fixed hyperparameters are mapped onto XGBoost keys; non-XGBoost
    keys in the dict (``n_estimators``, ``cv_folds``) are read by name elsewhere
    and never passed through, so the single config ``hyperparams`` block can carry
    them without leaking into the trainer.
    """
    return {
        "objective": _objective(decrement),
        "max_depth": int(hyperparams["max_depth"]),
        "eta": float(hyperparams["learning_rate"]),
        "min_child_weight": float(hyperparams["min_child_weight"]),
        "gamma": float(hyperparams["gamma"]),
        "lambda": float(hyperparams["reg_lambda"]),
        "seed": int(seed),
        "nthread": int(hyperparams.get("nthread", 1)),   # 1 → bit-stable refits
        "verbosity": 0,
    }


def _fit_gbm_from_fitting_cells(
    fit_cells: pd.DataFrame,
    features: pd.DataFrame,
    decrement: DecrementType,
    params: dict,
    num_round: int,
) -> dict:
    """Train one XGBoost model on already-aggregated fitting cells.

    Shared by :func:`_fit_gbm_core` (first fit) and the bootstrap (each refit), so
    a refit on resampled actuals is structurally identical to the original fit.
    ``features`` is the design matrix row-aligned to ``fit_cells``.

    Mortality (FR-3A-31): ``base_margin = log(expected)`` so the offset enters
    exactly like the GLM's Poisson offset; the prediction is ``expected·exp(raw)``
    = predicted deaths directly. Lapse/CI (FR-3A-31): label = ``actual/exposure``,
    ``weight = exposure``, ``binary:logistic``; predicted events =
    ``predicted_rate · exposure``. The published factor is then computed by the
    GLM's ``_factors_at_output_grain`` as ``Σ predicted ÷ Σ reference`` — which for
    lapse/CI equals the exposure-weighted ``predicted_rate ÷ benchmark_rate`` of
    ``derive_factor`` (FR-3A-14), keeping the GBM and GLM factors identical in form.
    """
    actual_col, expected_col, exposure_col = _MEASURES[decrement]
    actual = fit_cells[actual_col].astype(float).to_numpy()
    expected = fit_cells[expected_col].astype(float).to_numpy()
    exposure = fit_cells[exposure_col].astype(float).to_numpy()
    feature_cols = list(features.columns)
    matrix = features.to_numpy(dtype=float)

    # Build the DMatrix positionally (no feature_names): one-hot levels such as
    # the PLT premium-jump bands ("<=2x", ">12x") contain characters XGBoost
    # forbids in feature names ('<', '[', ']'). SHAP names features positionally
    # from the design-matrix columns instead (explain.py), so nothing is lost.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if decrement is DecrementType.MORTALITY:
            dtrain = xgb.DMatrix(matrix, label=actual)
            dtrain.set_base_margin(np.log(expected))   # expected > 0 guaranteed upstream
            booster = xgb.train(params, dtrain, num_boost_round=num_round)
            predicted_events = np.asarray(booster.predict(dtrain), dtype=float)
        else:
            rate = np.divide(actual, exposure, out=np.zeros_like(actual), where=exposure > 0)
            rate = np.clip(rate, 0.0, 1.0)
            dtrain = xgb.DMatrix(matrix, label=rate)
            dtrain.set_weight(exposure)
            booster = xgb.train(params, dtrain, num_boost_round=num_round)
            predicted_rate = np.asarray(booster.predict(dtrain), dtype=float)
            predicted_events = predicted_rate * exposure

    out = fit_cells.copy()
    out["_predicted_events"] = predicted_events
    out["_reference_events"] = expected
    out["_actual_events"] = actual
    out["_exposure"] = exposure
    return {"booster": booster, "dtrain": dtrain, "fit_cells": out, "feature_cols": feature_cols}


def _cv_metric_name(decrement: DecrementType) -> str:
    """Registry/JSON name for the CV metric (§D.1: GBM ``deviance`` / ``logloss``)."""
    return "deviance" if decrement is DecrementType.MORTALITY else "logloss"


def _cross_validate(
    dtrain: "xgb.DMatrix", decrement: DecrementType, params: dict, num_round: int, nfold: int,
) -> tuple[str, float]:
    """5-fold CV deviance (mortality) / log-loss (lapse, CI), reported only (FR-3A-32).

    The fold split inherits each row's ``base_margin``/``weight`` from ``dtrain``.
    Never fatal: if there are fewer rows than folds, or CV raises, the value is
    ``NaN`` (CV is a reported diagnostic, not a completion gate).
    """
    name = _cv_metric_name(decrement)
    if dtrain.num_row() < nfold:
        return name, float("nan")
    metric = "poisson-nloglik" if decrement is DecrementType.MORTALITY else "logloss"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cv = xgb.cv(
                params, dtrain, num_boost_round=num_round, nfold=nfold,
                metrics=[metric], seed=int(params.get("seed", 42)), as_pandas=True,
            )
        value = float(cv[f"test-{metric}-mean"].iloc[-1])
    except Exception:   # noqa: BLE001 — CV is reported, never fatal
        value = float("nan")
    return name, value


def _fit_gbm_core(
    cells: pd.DataFrame,
    decrement: DecrementType,
    covariates: list[str],
    output_grain: list[str],
    hyperparams: dict,
    seed: int,
) -> dict:
    """Aggregate to the fitting grain, encode, fit XGBoost, and cross-validate.

    Shared by :func:`fit_gbm`, the bootstrap, and the registry so every refit is
    deterministic for a given seed (FR-3A-24/25). Returns the booster, the
    training ``DMatrix``, the design matrix, the per-cell predictions on
    ``fit_cells``, the one-hot→covariate map, and the CV metric.
    """
    fit_cells, used = _aggregate_fit_cells(cells, decrement, covariates, output_grain)
    features, feature_cols, onehot_to_cov = _encode_features(fit_cells, used)
    params = _build_params(decrement, hyperparams, seed)
    num_round = int(hyperparams["n_estimators"])
    fitted = _fit_gbm_from_fitting_cells(fit_cells, features, decrement, params, num_round)
    cv_name, cv_value = _cross_validate(
        fitted["dtrain"], decrement, params, num_round, int(hyperparams.get("cv_folds", 5)),
    )
    return {
        **fitted,
        "features": features,
        "used": used,
        "onehot_to_cov": onehot_to_cov,
        "cv_metric_name": cv_name,
        "cv_metric_value": cv_value,
    }


# ---------------------------------------------------------------------------
# Divergence flagging (FR-3A-33)
# ---------------------------------------------------------------------------
def _grain_id(grain_key: dict) -> tuple:
    """Order-independent identity for an output-grain cell (mirrors the GLM bootstrap)."""
    return tuple(sorted(grain_key.items()))


def _divergence_flags(
    gbm_factors: list, glm_result: Optional[GLMFitResult], threshold: float,
) -> list[dict]:
    """Cells where ``|GBM − GLM| / |GLM| > threshold`` — the interaction signal (FR-3A-33).

    Returns an empty list when no GLM is supplied or no cells match; never raises.
    Each flag carries the grain key, both factors, and the relative difference.
    """
    flags: list[dict] = []
    if glm_result is None or not getattr(glm_result, "factors", None):
        return flags
    glm_by = {_grain_id(fc.grain_key): fc.factor for fc in glm_result.factors}
    for fc in gbm_factors:
        glm_factor = glm_by.get(_grain_id(fc.grain_key))
        if (glm_factor is None or not math.isfinite(glm_factor) or glm_factor == 0
                or not math.isfinite(fc.factor)):
            continue
        rel_diff = abs(fc.factor - glm_factor) / abs(glm_factor)
        if rel_diff > threshold:
            flags.append({
                "grain_key": dict(fc.grain_key),
                "glm_factor": float(glm_factor),
                "gbm_factor": float(fc.factor),
                "rel_diff": float(rel_diff),
            })
    return flags


# ---------------------------------------------------------------------------
# Public fit
# ---------------------------------------------------------------------------
def fit_gbm(
    cells: pd.DataFrame,
    decrement: DecrementType,
    product_code: str,
    covariates: list[str],
    output_grain: list[str],
    hyperparams: dict,
    glm_result: Optional[GLMFitResult],
    divergence_threshold: float,
    min_events_to_fit: int,
    seed: int,
) -> GBMFitResult:
    """Fit one GBM and publish reference factors + divergence flags at ``output_grain``.

    Guardrail (FR-3A-29), same loud-failure contract as the GLM: if total events
    fall below ``min_events_to_fit`` or the fit raises, returns a
    ``GBMFitResult`` with ``factors=[]`` (never an extrapolated or borrowed
    value). Factors are computed by the GLM's ``_factors_at_output_grain`` so they
    are directly comparable to the GLM proposal; divergence flags mark cells where
    the GBM materially disagrees (FR-3A-33).
    """
    decrement = DecrementType(decrement)
    model_id = str(uuid.uuid4())
    run_id = str(cells["study_run_id"].iloc[0]) if len(cells) else ""
    actual_col = _MEASURES[decrement][0]
    total_events = float(cells[actual_col].sum()) if actual_col in cells.columns else 0.0

    def _no_proposal() -> GBMFitResult:
        return GBMFitResult(
            model_id=model_id, run_id=run_id, decrement=decrement,
            product_code=product_code, n_cells=int(len(cells)),
            cv_metric_name="", cv_metric_value=float("nan"),
            factors=[], divergence_flags=[], shap_json_path="", seed=seed,
        )

    if total_events < min_events_to_fit:
        return _no_proposal()
    try:
        core = _fit_gbm_core(cells, decrement, covariates, output_grain, hyperparams, seed)
    except Exception:   # noqa: BLE001 — fail loudly, no fallback (FR-3A-29)
        return _no_proposal()

    factors = _factors_at_output_grain(core["fit_cells"], output_grain)
    flags = _divergence_flags(factors, glm_result, divergence_threshold)
    return GBMFitResult(
        model_id=model_id, run_id=run_id, decrement=decrement,
        product_code=product_code, n_cells=int(len(core["fit_cells"])),
        cv_metric_name=core["cv_metric_name"], cv_metric_value=core["cv_metric_value"],
        factors=factors, divergence_flags=flags, shap_json_path="", seed=seed,
    )


# ---------------------------------------------------------------------------
# Parametric bootstrap CIs (FR-3A-34) — mirrors src/ai/glm/bootstrap.py
# ---------------------------------------------------------------------------
def bootstrap_gbm_cis(
    cells: pd.DataFrame,
    decrement: DecrementType,
    product_code: str,
    covariates: list[str],
    output_grain: list[str],
    hyperparams: dict,
    fitted: GBMFitResult,
    n_resamples: int = 200,
    ci_level: float = 0.95,
    seed: int = 42,
) -> GBMFitResult:
    """Populate bootstrap 95% CIs on each GBM factor (FR-3A-34).

    Uses the same determinism-first design as the GLM bootstrap (master RNG → one
    child seed per resample, order-independent and reproducible): event counts are
    drawn from the fitted distribution (``Poisson(mu_hat)`` for mortality;
    ``Binomial(n, p_hat)`` for lapse/CI), the GBM is refit on the same design
    matrix, and the output-grain factors recomputed. The per-cell CI is the
    percentile interval over the resampled factors. The loop is deliberately a
    near-copy of ``src.ai.glm.bootstrap.bootstrap_cis`` (refitting a booster
    instead of a GLM) rather than refactoring the tested GLM signature — zero GLM
    regression risk. Resample arrays live only in memory and are discarded
    (FR-3A-22 / NFR-T-05). A no-proposal input is returned unchanged.
    """
    decrement = DecrementType(decrement)
    if not fitted.factors:
        return fitted

    core = _fit_gbm_core(cells, decrement, covariates, output_grain, hyperparams, seed)
    base = core["fit_cells"]
    features = core["features"]
    params = _build_params(decrement, hyperparams, seed)
    num_round = int(hyperparams["n_estimators"])
    actual_col = _MEASURES[decrement][0]
    mu = np.clip(base["_predicted_events"].to_numpy(dtype=float), 0.0, None)
    exposure = base["_exposure"].to_numpy(dtype=float)

    master = np.random.default_rng(seed)
    child_seeds = master.integers(0, 2 ** 63 - 1, size=n_resamples)

    samples: dict[tuple, list[float]] = defaultdict(list)
    for child in child_seeds:
        rng_i = np.random.default_rng(int(child))
        if decrement is DecrementType.MORTALITY:
            new_actual = rng_i.poisson(mu).astype(float)
        else:
            n = np.maximum(1, np.round(exposure).astype(np.int64))
            with np.errstate(divide="ignore", invalid="ignore"):
                p = np.where(exposure > 0, mu / exposure, 0.0)
            p = np.clip(p, 0.0, 1.0)
            new_actual = rng_i.binomial(n, p).astype(float)

        resampled = base.copy()
        resampled[actual_col] = new_actual
        try:
            refit = _fit_gbm_from_fitting_cells(resampled, features, decrement, params, num_round)
        except Exception:   # noqa: BLE001 — a degenerate resample is simply dropped
            continue
        for fc in _factors_at_output_grain(refit["fit_cells"], output_grain):
            samples[_grain_id(fc.grain_key)].append(fc.factor)

    lo_pct = (1.0 - ci_level) / 2.0 * 100.0
    hi_pct = 100.0 - lo_pct
    new_factors = []
    for fc in fitted.factors:
        vals = samples.get(_grain_id(fc.grain_key), [])
        if vals:
            lo = float(np.percentile(vals, lo_pct))
            hi = float(np.percentile(vals, hi_pct))
        else:
            lo = hi = float("nan")
        new_factors.append(replace(fc, ci_low=lo, ci_high=hi))
    return replace(fitted, factors=new_factors)


# ---------------------------------------------------------------------------
# Persistence + registry (FR-3A-24/35; §D.1/§D.5)
# ---------------------------------------------------------------------------
def _nan_to_none(value: float) -> Optional[float]:
    """Store NaN as SQL NULL in the registry."""
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def register_gbm_model(
    fitted: GBMFitResult,
    cells: pd.DataFrame,
    covariates: list[str],
    output_grain: list[str],
    hyperparams: dict,
    db_path: Path,
    *,
    feature_to_assumption: dict,
    data_snapshot_hash: str,
    config_hash: str,
    code_version: str,
    models_dir: Path = _MODELS_DIR,
) -> str:
    """Serialize the booster + SHAP artifacts and write the registry row (§D.1/§D.5).

    Mirrors ``register_glm_model``: the booster is recovered by a deterministic
    re-fit (identical for the same inputs/seed, FR-3A-24/25), saved as native
    XGBoost JSON to ``data/ai_models/gbm/``, and the SHAP-JSON is generated at fit
    time (FR-3A-38) under ``data/ai_models/shap/`` and recorded on the result. The
    registry INSERT is static and parameterized: ``model_type='GBM'``,
    ``converged`` = whether a proposal was produced, GLM-only stats NULL, the CV
    metric set, and the full reproducibility stamp. Tests pass ``models_dir``
    under ``tests/_artifacts/`` so nothing touches ``data/``.

    Returns the ``model_id`` of the registered model.
    """
    from src.ai.gbm.explain import generate_shap_artifacts

    decrement = DecrementType(fitted.decrement)
    core = _fit_gbm_core(cells, decrement, covariates, output_grain, hyperparams, fitted.seed)

    gbm_dir = models_dir / "gbm"
    gbm_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = gbm_dir / f"{fitted.model_id}.json"
    core["booster"].save_model(str(artifact_path))

    shap_path = generate_shap_artifacts(
        core["booster"], core["features"], core["fit_cells"], output_grain,
        feature_to_assumption, fitted.model_id, models_dir / "shap",
        decrement=decrement, product_code=fitted.product_code,
    )
    fitted.shap_json_path = shap_path

    con = duckdb.connect(str(db_path))
    try:
        con.execute(_INSERT_SQL, [
            fitted.model_id, fitted.run_id, "GBM", decrement.value,
            fitted.product_code, datetime.utcnow(), bool(fitted.factors),
            fitted.n_cells, None, None, None,
            fitted.cv_metric_name or None, _nan_to_none(fitted.cv_metric_value),
            str(artifact_path), shap_path,
            data_snapshot_hash, config_hash, code_version, fitted.seed, None,
        ])
    finally:
        con.close()

    # Materialise the GBM challenge factors to the queryable Gold table alongside
    # the GLM proposal so the AI Analyst can read both by grain (2026-06-27).
    from src.ai.proposals import write_proposed_factors
    write_proposed_factors(
        fitted.model_id, fitted.run_id, "GBM", decrement.value,
        fitted.product_code, fitted.factors, db_path,
    )
    return fitted.model_id


def load_gbm_model(artifact_path: Path) -> "xgb.Booster":
    """Load a serialized GBM booster (native XGBoost JSON) for reproducibility checks."""
    booster = xgb.Booster()
    booster.load_model(str(artifact_path))
    return booster
