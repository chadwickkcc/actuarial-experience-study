"""GLM fitting for assumption-adjustment-factor proposals (Session 15).

Realises FR-3A-12/13/14/15/18/19/20/23/29 (Req §7.4; Tech Spec §E.3).

The GLM proposes A/E adjustment factors (multipliers on the existing reference
tables) from the aggregated cells in the Gold A/E fact table — never seriatim
data (FR-3A-15). Mortality uses a Poisson GLM with ``log(expected)`` offset, so
``exp(linear predictor)`` is the factor directly (FR-3A-13). Lapse and CI use a
binomial (logit) GLM; the factor is ``predicted_rate / benchmark_rate`` via the
distinct, unit-tested :func:`derive_factor` (FR-3A-14).

All database reads go through ``src.utils.sql_boundary.execute_safe_select``
(FR-3A-01). The SQL is a static string (no interpolation, FR-3A-02); run/product
selection is applied in-memory after the read.
"""
from __future__ import annotations

import math
import uuid
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tools.sm_exceptions import PerfectSeparationWarning

from src.utils.types import DecrementType, FactorCell, GLMFitResult
from src.utils.sql_boundary import execute_safe_select, load_allowlist
from src.calculation.ae_engine import compute_credibility_z

# Repo-root-relative config (config/ai_config.yaml holds the allowlist + glm block).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AI_CONFIG_PATH = _REPO_ROOT / "config" / "ai_config.yaml"

# A generous static row cap for trusted internal analytical reads of the Gold
# fact table (the chatbot's 500-row cap protects untrusted SQL, not this path).
_READ_ROW_CAP = 2_000_000

# Per-decrement gold_ae_results measure columns (all on the allowlist).
_MEASURES = {
    DecrementType.MORTALITY:    ("actual_deaths_count", "expected_deaths_count", "exposure_count"),
    DecrementType.LAPSE:        ("actual_lapses",       "expected_lapses",       "lapse_exposure_count"),
    DecrementType.CI_INCIDENCE: ("actual_ci_claims",    "expected_ci_claims",    "ci_exposure_count"),
}

# Static SELECTs (no value interpolation): detail rows (illness NULL) carry
# mortality + lapse; CI rows carry illness_code. Filtering by run/product is
# applied in pandas after the boundary read.
_SQL_DETAIL = (
    "SELECT study_run_id, product_code, gender, smoker_status, risk_class, "
    "attained_age_band, duration_band, premium_jump_ratio_band, illness_code, "
    "exposure_count, expected_deaths_count, actual_deaths_count, "
    "lapse_exposure_count, expected_lapses, actual_lapses "
    "FROM gold_ae_results WHERE illness_code IS NULL LIMIT 2000000"
)
_SQL_CI = (
    "SELECT study_run_id, product_code, gender, smoker_status, "
    "attained_age_band, illness_code, "
    "ci_exposure_count, expected_ci_claims, actual_ci_claims "
    "FROM gold_ae_results WHERE illness_code IS NOT NULL LIMIT 2000000"
)

# Output-grain token -> gold_ae_results column.
_GRAIN_TOKEN_TO_COLUMN = {"product": "product_code", "sex": "gender", "smoker": "smoker_status"}


def derive_factor(predicted_rate: float, benchmark_rate: float) -> float:
    """Lapse/CI adjustment factor = predicted ÷ benchmark (FR-3A-14).

    Distinct and unit-tested per FR-3A-14. A zero benchmark rate yields NaN so
    the cell is excluded from publication (never a divide-by-zero or a borrowed
    value).

    Args:
        predicted_rate: GLM-predicted decrement rate for the cell.
        benchmark_rate: Reference-table rate for the cell (expected ÷ exposure).

    Returns:
        The adjustment factor, or ``float('nan')`` when ``benchmark_rate == 0``.
    """
    if benchmark_rate == 0 or not math.isfinite(benchmark_rate):
        return float("nan")
    return predicted_rate / benchmark_rate


def load_cells(
    db_path: Path,
    run_id: str,
    decrement: DecrementType,
    product_code: str,
) -> pd.DataFrame:
    """Aggregated A/E cells for one decrement-product, via the SQL boundary.

    Reads only the Gold A/E fact table (FR-3A-08/15) through
    :func:`execute_safe_select` (FR-3A-01) with a static SELECT (FR-3A-02), then
    filters to ``run_id`` (and ``product_code`` unless it is ``None``/``"ALL"``)
    in memory. Mortality and lapse come from the detail rows (``illness_code``
    NULL); CI incidence from the per-illness rows.

    Returns:
        A DataFrame of cells with the decrement's covariates and its
        actual/expected/exposure columns. Empty if the run has no such rows.
    """
    decrement = DecrementType(decrement)
    sql = _SQL_CI if decrement is DecrementType.CI_INCIDENCE else _SQL_DETAIL
    allowlist = load_allowlist(_AI_CONFIG_PATH)
    validation, df = execute_safe_select(db_path, sql, allowlist, row_cap=_READ_ROW_CAP)
    if df is None:
        raise RuntimeError(f"load_cells boundary read rejected: {validation.detail}")

    df = df[df["study_run_id"] == run_id]
    if product_code not in (None, "ALL") and "product_code" in df.columns:
        df = df[df["product_code"] == product_code]
    return df.reset_index(drop=True)


def _used_covariates(cells: pd.DataFrame, covariates: list[str]) -> list[str]:
    """Covariates present with ≥2 distinct values — drops degenerate columns (FR-3A-17)."""
    used = []
    for cov in covariates:
        if cov not in cells.columns:
            continue
        if cells[cov].astype("string").fillna("NA").nunique() >= 2:
            used.append(cov)
    return used


def _design_matrix(cells: pd.DataFrame, used_covariates: list[str]) -> pd.DataFrame:
    """One-hot design with an intercept (drop_first to avoid collinearity)."""
    parts = []
    for cov in used_covariates:
        series = cells[cov].astype("string").fillna("NA")
        parts.append(pd.get_dummies(series, prefix=cov, drop_first=True, dtype=float))
    design = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=cells.index)
    return sm.add_constant(design, has_constant="add")


def _aggregate_to_covariates(
    cells: pd.DataFrame, group_cols: list[str],
    actual_col: str, expected_col: str, exposure_col: str,
) -> pd.DataFrame:
    """Sum events/expected/exposure to the fitting grain (FR-3A-15).

    ``group_cols`` is the union of the model's used covariates and the
    output-grain columns, so degenerate output-grain columns (e.g. a constant
    ``product_code``) survive aggregation for later keying even though they
    carry no design-matrix term.
    """
    out_cols = {actual_col: "sum", expected_col: "sum", exposure_col: "sum"}
    if not group_cols:
        return cells[[actual_col, expected_col, exposure_col]].sum().to_frame().T
    return cells.groupby(group_cols, dropna=False).agg(out_cols).reset_index()


def _fit_from_fitting_cells(
    fit_cells: pd.DataFrame,
    used_covariates: list[str],
    decrement: DecrementType,
) -> dict:
    """Fit the GLM on already-aggregated fitting cells.

    Shared by :func:`_fit_core` (first fit) and the bootstrap (each refit), so a
    refit on resampled actuals is structurally identical to the original fit.
    """
    actual_col, expected_col, exposure_col = _MEASURES[decrement]
    actual = fit_cells[actual_col].astype(float).to_numpy()
    expected = fit_cells[expected_col].astype(float).to_numpy()
    exposure = fit_cells[exposure_col].astype(float).to_numpy()
    X = _design_matrix(fit_cells, used_covariates)

    # Saturated/near-saturated small cells make statsmodels emit benign
    # PerfectSeparation / divide-by-zero (df_resid == 0) warnings. We rely on
    # the bootstrap (not model-based SEs) for CIs and guard the dispersion ratio
    # below, so these are suppressed locally to keep output clean.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PerfectSeparationWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        if decrement is DecrementType.MORTALITY:
            with np.errstate(divide="ignore"):
                offset = np.log(np.where(expected > 0, expected, np.nan))
            model = sm.GLM(actual, X.to_numpy(), family=sm.families.Poisson(), offset=offset)
            res = model.fit()
            predicted_events = np.asarray(res.fittedvalues, dtype=float)
        else:
            rate = np.divide(actual, exposure, out=np.zeros_like(actual), where=exposure > 0)
            rate = np.clip(rate, 0.0, 1.0)
            model = sm.GLM(rate, X.to_numpy(), family=sm.families.Binomial(), var_weights=exposure)
            res = model.fit()
            predicted_rate = np.asarray(res.fittedvalues, dtype=float)
            predicted_events = predicted_rate * exposure

    converged = bool(np.all(np.isfinite(res.params)))
    dispersion = float(res.pearson_chi2 / res.df_resid) if res.df_resid > 0 else float("nan")
    out = fit_cells.copy()
    out["_predicted_events"] = predicted_events
    out["_reference_events"] = expected
    out["_actual_events"] = actual
    out["_exposure"] = exposure
    return {
        "results": res,
        "fit_cells": out,
        "used_covariates": used_covariates,
        "converged": converged,
        "deviance": float(res.deviance),
        "dispersion": dispersion,
        "aic": float(res.aic),
        "n_cells": int(len(out)),
    }


def _fit_core(
    cells: pd.DataFrame,
    decrement: DecrementType,
    covariates: list[str],
    output_grain: list[str],
    seed: int,
) -> dict:
    """Aggregate raw Gold cells to the fitting grain, then fit the GLM.

    Shared by :func:`fit_glm`, the bootstrap, and the registry so every refit is
    identical for a given seed (FR-3A-24). ``seed`` is accepted for interface
    symmetry; the GLM fit itself is deterministic (randomness lives only in the
    bootstrap).
    """
    actual_col, expected_col, exposure_col = _MEASURES[decrement]
    used = _used_covariates(cells, covariates)
    grain_cols = [c for c in _output_grain_columns(output_grain) if c in cells.columns]
    group_cols = list(dict.fromkeys(used + grain_cols))   # union, order-preserving
    fit_cells = _aggregate_to_covariates(cells, group_cols, actual_col, expected_col, exposure_col)

    # Drop cells whose offset/weight denominator is non-positive: a Poisson
    # log(expected) offset is undefined when expected == 0, and a binomial
    # exposure weight of 0 carries no information. Such cells have no reference
    # basis and cannot form a factor — excluding them is the correct treatment
    # (real Gold data has zero-expected cells; the fit must not be poisoned).
    denom_col = expected_col if decrement is DecrementType.MORTALITY else exposure_col
    fit_cells = fit_cells[fit_cells[denom_col].astype(float) > 0].reset_index(drop=True)
    if fit_cells.empty:
        raise ValueError(f"no fittable cells (all {denom_col} <= 0)")
    return _fit_from_fitting_cells(fit_cells, used, decrement)


def _output_grain_columns(output_grain: list[str]) -> list[str]:
    """Translate config grain tokens to gold_ae_results column names."""
    return [_GRAIN_TOKEN_TO_COLUMN.get(tok, tok) for tok in output_grain]


def _factors_at_output_grain(
    fit_cells: pd.DataFrame, output_grain: list[str],
) -> list[FactorCell]:
    """Aggregate per-cell predictions to the output grain into FactorCells (FR-3A-18/19)."""
    grain_cols = [c for c in _output_grain_columns(output_grain) if c in fit_cells.columns]
    factors: list[FactorCell] = []
    grouped = fit_cells.groupby(grain_cols, dropna=False) if grain_cols else [((), fit_cells)]
    for key, sub in grouped:
        ref = float(sub["_reference_events"].sum())
        if ref <= 0:
            continue
        pred = float(sub["_predicted_events"].sum())
        actual = float(sub["_actual_events"].sum())
        key_tuple = key if isinstance(key, tuple) else (key,)
        grain_key = {tok: str(val) for tok, val in zip(output_grain, key_tuple)}
        factors.append(FactorCell(
            grain_key=grain_key,
            factor=pred / ref,
            ci_low=float("nan"),     # populated by bootstrap_cis
            ci_high=float("nan"),
            expected_events=ref,
            credibility_z=compute_credibility_z(actual),
            ae_derived_factor=actual / ref,
        ))
    return factors


def fit_glm(
    cells: pd.DataFrame,
    decrement: DecrementType,
    product_code: str,
    covariates: list[str],
    output_grain: list[str],
    min_events_to_fit: int,
    seed: int,
) -> GLMFitResult:
    """Fit one GLM and publish adjustment factors at ``output_grain``.

    Guardrail (FR-3A-29): if total events fall below ``min_events_to_fit`` or the
    fit fails to converge, returns ``GLMFitResult(converged=False, factors=[])``
    with a reason — never an extrapolated or borrowed value. The raw fitted
    factor and its (later) bootstrap CI are published unblended (FR-3A-20).
    """
    decrement = DecrementType(decrement)
    model_id = str(uuid.uuid4())
    run_id = str(cells["study_run_id"].iloc[0]) if len(cells) else ""
    actual_col = _MEASURES[decrement][0]
    total_events = float(cells[actual_col].sum()) if actual_col in cells.columns else 0.0

    def _no_proposal(message: str) -> GLMFitResult:
        return GLMFitResult(
            model_id=model_id, run_id=run_id, decrement=decrement,
            product_code=product_code, converged=False, n_cells=int(len(cells)),
            deviance=float("nan"), dispersion=float("nan"), aic=float("nan"),
            factors=[], diagnostics_path="", seed=seed, message=message,
        )

    if total_events < min_events_to_fit:
        return _no_proposal(
            f"No AI proposal available: {int(total_events)} events "
            f"< min_events_to_fit ({min_events_to_fit})."
        )
    try:
        core = _fit_core(cells, decrement, covariates, output_grain, seed)
    except Exception as err:   # noqa: BLE001 — fail loudly, no fallback (FR-3A-29)
        return _no_proposal(f"No AI proposal available: GLM fit failed ({err}).")
    if not core["converged"]:
        return _no_proposal("No AI proposal available: GLM did not converge.")

    factors = _factors_at_output_grain(core["fit_cells"], output_grain)
    return GLMFitResult(
        model_id=model_id, run_id=run_id, decrement=decrement,
        product_code=product_code, converged=True, n_cells=core["n_cells"],
        deviance=core["deviance"], dispersion=core["dispersion"], aic=core["aic"],
        factors=factors, diagnostics_path="", seed=seed, message=None,
    )
