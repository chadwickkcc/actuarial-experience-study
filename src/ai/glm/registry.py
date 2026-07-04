"""Persistence and registration of fitted GLM models (Session 15).

Realises FR-3A-24/25: serialize the fitted model (coefficients, covariance,
metadata) to ``data/ai_models/glm/`` and register it in the Gold
``gold_ai_model_registry`` table with the full reproducibility stamp, so a
re-fit with identical inputs and seed reproduces identical coefficients.

The registry INSERT is a *static, parameterized* statement (``?`` placeholders)
on a writable connection — permitted under FR-3A-02 (no string interpolation)
and FR-3A-09 (writes confined to ``data/ai_models/`` and the AI Gold tables).
Reads still go through the SQL boundary; only this controlled write does not.
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb

from src.utils.types import DecrementType, GLMFitResult
from src.ai.glm.fit import _fit_core

_MODELS_DIR = Path("data/ai_models")

_INSERT_SQL = (
    "INSERT INTO gold_ai_model_registry ("
    "model_id, run_id, model_type, decrement, product_code, fit_ts, converged, "
    "n_cells, deviance, dispersion, aic, cv_metric_name, cv_metric_value, "
    "artifact_path, shap_json_path, data_snapshot_hash, config_hash, "
    "code_version, seed, message"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _write_diagnostics(core: dict, diag_dir: Path) -> str:
    """Persist residual-by-covariate diagnostics as JSON (FR-3A-23)."""
    diag_dir.mkdir(parents=True, exist_ok=True)
    res = core["results"]
    fit_cells = core["fit_cells"]
    resid = np.asarray(res.resid_pearson, dtype=float)
    by_covariate: dict[str, dict] = {}
    for cov in core["used_covariates"]:
        levels = fit_cells[cov].astype("string").fillna("NA")
        frame = pd.DataFrame({"level": levels.to_numpy(), "resid": resid})
        means = frame.groupby("level")["resid"].mean()
        by_covariate[cov] = {str(k): float(v) for k, v in means.items()}
    payload = {
        "deviance": core["deviance"],
        "dispersion": core["dispersion"],
        "aic": core["aic"],
        "n_cells": core["n_cells"],
        "pearson_residual_mean_by_covariate": by_covariate,
    }
    path = diag_dir / "diagnostics.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


def register_glm_model(
    fitted: GLMFitResult,
    cells: pd.DataFrame,
    covariates: list[str],
    output_grain: list[str],
    db_path: Path,
    *,
    data_snapshot_hash: str,
    config_hash: str,
    code_version: str,
    models_dir: Path = _MODELS_DIR,
) -> str:
    """Serialize ``fitted`` and write its ``gold_ai_model_registry`` row.

    The fitted statsmodels results object is reconstructed by a deterministic
    re-fit (identical coefficients for the same inputs/seed, FR-3A-24) and
    pickled, since ``GLMFitResult`` carries the published factors, not the raw
    model. Diagnostics are written under ``data/ai_models/diagnostics/`` and the
    GLMFitResult's ``diagnostics_path`` is updated in place.

    Returns:
        The ``model_id`` of the registered model.
    """
    decrement = DecrementType(fitted.decrement)
    core = _fit_core(cells, decrement, covariates, output_grain, fitted.seed)

    glm_dir = models_dir / "glm"
    glm_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = glm_dir / f"{fitted.model_id}.pkl"
    with open(artifact_path, "wb") as fh:
        pickle.dump(
            {
                "results": core["results"],
                "used_covariates": core["used_covariates"],
                "decrement": decrement.value,
                "product_code": fitted.product_code,
                "seed": fitted.seed,
            },
            fh,
        )

    diag_path = _write_diagnostics(core, models_dir / "diagnostics" / fitted.model_id)
    fitted.diagnostics_path = diag_path

    con = duckdb.connect(str(db_path))
    try:
        con.execute(_INSERT_SQL, [
            fitted.model_id, fitted.run_id, "GLM", decrement.value,
            fitted.product_code, datetime.utcnow(), fitted.converged,
            fitted.n_cells, fitted.deviance, fitted.dispersion, fitted.aic,
            None, None, str(artifact_path), None,
            data_snapshot_hash, config_hash, code_version, fitted.seed,
            fitted.message,
        ])
    finally:
        con.close()

    # Materialise the published factor cells to the queryable Gold table so the
    # AI Analyst can read the proposed assumptions by grain (2026-06-27).
    from src.ai.proposals import write_proposed_factors
    write_proposed_factors(
        fitted.model_id, fitted.run_id, "GLM", decrement.value,
        fitted.product_code, fitted.factors, db_path,
    )
    return fitted.model_id


def load_glm_model(artifact_path: Path) -> dict:
    """Load a serialized GLM artifact (results + metadata) for reproducibility checks."""
    with open(artifact_path, "rb") as fh:
        return pickle.load(fh)
