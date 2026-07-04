"""SHAP explainability artifacts for the GBM overlay (Session 16).

Realises FR-3A-37/38/39 (Req §7.5; Tech Spec §E.4) and produces the SHAP-JSON
contract of §D.6 — the exact input the Session-19 ``explain_shap_results`` Skill
consumes.

SHAP is computed via ``shap.TreeExplainer`` (exact for tree models) at **fit
time**, never at runtime (FR-3A-38) — :func:`generate_shap_artifacts` is called
from ``register_gbm_model``. Values are in the model's **margin (link) space**
(log link for ``count:poisson``, logit for ``binary:logistic``), so the
contributions are additive: ``base_value + Σ shap_value = margin``.

The model trains on one-hot columns, but the SHAP-JSON exposes only the
actuarial-meaningful **covariate** names (FR-3A-39): each covariate's one-hot
child contributions are summed back to the parent, so the Skill never sees a raw
feature name. Every emitted JSON is validated against §D.6 on write
(:func:`_validate_shap_json`), with ``schema_version`` checked against the formal
schema document ``src/ai/gbm/shap_schema.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import shap

from src.utils.types import DecrementType
from src.ai.glm.fit import _output_grain_columns
from src.ai.gbm.fit import ONEHOT_SEP

_SCHEMA_PATH = Path(__file__).resolve().parent / "shap_schema.json"
_ADDITIVITY_TOL = 1e-6


def _supported_versions() -> list:
    """Read the supported schema versions from the formal §D.6 schema document."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return list(schema.get("supported_schema_versions", ["1.0"]))


def _validate_shap_json(payload: dict, output_grain: list) -> None:
    """Enforce the §D.6 validation rules on write (consumer re-checks on read).

    Lightweight, dependency-free: (1) ``schema_version`` is supported; (2) every
    ``feature_to_assumption`` key appears in ``feature_names`` (FR-3A-39); (3)
    ``base_value + Σ shap_value ≈ prediction`` within 1e-6 per cell (SHAP
    additivity); (4) each ``grain_key`` carries exactly the configured output
    grain. Raises ``ValueError`` on any violation.
    """
    if payload.get("schema_version") not in _supported_versions():
        raise ValueError(f"unsupported SHAP schema_version: {payload.get('schema_version')!r}")

    feature_names = set(payload["feature_names"])
    extra = set(payload["feature_to_assumption"]) - feature_names
    if extra:
        raise ValueError(f"feature_to_assumption keys not in feature_names: {sorted(extra)}")

    grain_tokens = set(output_grain)
    for cell in payload["cells"]:
        total = cell["base_value"] + sum(c["shap_value"] for c in cell["contributions"])
        if abs(total - cell["prediction"]) > _ADDITIVITY_TOL:
            raise ValueError(
                f"SHAP additivity violated for {cell['grain_key']}: "
                f"base+Σshap={total} vs prediction={cell['prediction']}"
            )
        if set(cell["grain_key"]) != grain_tokens:
            raise ValueError(
                f"grain_key {sorted(cell['grain_key'])} != output grain {sorted(grain_tokens)}"
            )


def generate_shap_artifacts(
    booster,
    features: pd.DataFrame,
    cells: pd.DataFrame,
    output_grain: list,
    feature_to_assumption: dict,
    model_id: str,
    out_dir: Path,
    *,
    decrement,
    product_code: str,
) -> str:
    """Compute SHAP for a fitted GBM and persist the §D.6 SHAP-JSON.

    Args:
        booster: the trained ``xgb.Booster``.
        features: the design matrix (one-hot columns) the booster trained on,
            row-aligned to ``cells``. This is the dense SHAP input; ``decrement``
            and ``product_code`` are passed explicitly because the §D.6 JSON
            requires them and a ``DMatrix`` does not carry them.
        cells: the aggregated fitting cells (with covariate + output-grain
            columns), row-aligned to ``features``.
        output_grain: configured output-grain tokens for this decrement.
        feature_to_assumption: the decrement's feature→actuarial map (FR-3A-39);
            sub-selected to the model's covariates on output.
        model_id: registry model id (file name).
        out_dir: ``data/ai_models/shap`` (tests pass a path under tests/_artifacts).

    Returns:
        Path to the written, schema-validated SHAP-JSON.
    """
    decrement = DecrementType(decrement)
    cells = cells.reset_index(drop=True)
    features = features.reset_index(drop=True)
    n_rows = len(features)

    # One-hot column -> parent covariate; "_const" (and any column without the
    # separator) maps to no covariate and is excluded from the covariate view.
    feature_cols = list(features.columns)
    onehot_to_cov = {c: c.split(ONEHOT_SEP, 1)[0] for c in feature_cols if ONEHOT_SEP in c}
    covariates = list(dict.fromkeys(onehot_to_cov.values()))   # stable order

    # Exact tree SHAP in margin space (FR-3A-37/38). tree_path_dependent needs no
    # background data → deterministic. The booster was trained positionally (no
    # feature_names), so SHAP is computed on the positional matrix and named from
    # the design-matrix columns here.
    explainer = shap.TreeExplainer(booster)
    shap_values = np.asarray(explainer.shap_values(features.to_numpy(dtype=float)), dtype=float)
    if shap_values.ndim == 1:
        shap_values = shap_values.reshape(n_rows, -1)
    base_value = float(np.ravel(explainer.expected_value)[0])

    # Sum each covariate's one-hot child SHAP values back to the parent (FR-3A-39).
    col_index = {c: j for j, c in enumerate(feature_cols)}
    cov_contrib = {cov: np.zeros(n_rows) for cov in covariates}
    for col, cov in onehot_to_cov.items():
        cov_contrib[cov] += shap_values[:, col_index[col]]

    global_summary = [
        {"feature": cov, "mean_abs_shap": float(np.mean(np.abs(cov_contrib[cov])))}
        for cov in covariates
    ]

    grain_cols = [c for c in _output_grain_columns(output_grain) if c in cells.columns]
    cells_out = []
    grouped = cells.groupby(grain_cols, dropna=False) if grain_cols else [((), cells)]
    for key, sub in grouped:
        idx = sub.index.to_numpy()
        contributions = []
        prediction = base_value
        for cov in covariates:
            shap_v = float(np.mean(cov_contrib[cov][idx]))
            if cov in sub.columns and not sub[cov].astype("string").fillna("NA").mode().empty:
                feature_value = str(sub[cov].astype("string").fillna("NA").mode().iloc[0])
            else:
                feature_value = "NA"
            contributions.append({"feature": cov, "shap_value": shap_v, "feature_value": feature_value})
            prediction += shap_v
        key_tuple = key if isinstance(key, tuple) else (key,)
        grain_key = {tok: str(val) for tok, val in zip(output_grain, key_tuple)}
        cells_out.append({
            "grain_key": grain_key,
            "base_value": base_value,
            "prediction": prediction,
            "contributions": contributions,
        })

    dependence = []
    for cov in covariates:
        levels = cells[cov].astype("string").fillna("NA").to_numpy() if cov in cells.columns else None
        points = [
            {"feature_value": (str(levels[i]) if levels is not None else "NA"),
             "shap_value": float(cov_contrib[cov][i])}
            for i in range(n_rows)
        ]
        dependence.append({"feature": cov, "points": points})

    fta = {cov: feature_to_assumption[cov] for cov in covariates if cov in feature_to_assumption}

    payload = {
        "schema_version": "1.0",
        "model_id": model_id,
        "decrement": decrement.value,
        "product_code": product_code,
        "feature_names": covariates,
        "feature_to_assumption": fta,
        "global_summary": global_summary,
        "cells": cells_out,
        "dependence": dependence,
    }
    _validate_shap_json(payload, output_grain)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)
