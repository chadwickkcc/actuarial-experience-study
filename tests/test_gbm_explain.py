"""Tests for src/ai/gbm/explain.py — SHAP-JSON artifacts (Session 16).

Covers FR-3A-37/38 (SHAP at fit time) and FR-3A-39 / §D.6: schema-conformant
JSON, additivity (base_value + Σ shap ≈ prediction), feature_to_assumption keys
⊆ feature_names, and that feature_names are actuarial covariates (never raw
one-hot columns).
"""
import json

import pytest

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells
from src.ai.gbm.fit import _fit_gbm_core
from src.ai.gbm.explain import generate_shap_artifacts, _validate_shap_json, _supported_versions
from tests.conftest import ARTIFACT_ROOT


def test_shap_json_validates_and_is_additive(synthetic_db, glm_config, gbm_config,
                                              feature_to_assumption_map):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.MORTALITY, "TERM")
    cov = glm_config["covariates"]["mortality"]
    grain = glm_config["output_grain"]["mortality"]
    core = _fit_gbm_core(cells, DecrementType.MORTALITY, cov, grain,
                         gbm_config["hyperparams"], gbm_config["seed"])
    out_dir = ARTIFACT_ROOT / "shap_explain_test"
    path = generate_shap_artifacts(
        core["booster"], core["features"], core["fit_cells"], grain,
        feature_to_assumption_map["MORTALITY"], "m-shap-1", out_dir,
        decrement=DecrementType.MORTALITY, product_code="TERM",
    )
    payload = json.loads(open(path, encoding="utf-8").read())

    assert payload["schema_version"] in _supported_versions()
    assert payload["decrement"] == "MORTALITY" and payload["product_code"] == "TERM"
    # feature_names are actuarial covariates, never raw one-hot columns (FR-3A-39).
    assert payload["feature_names"]
    assert all("=" not in f for f in payload["feature_names"])
    # Every mapped feature appears in feature_names (§D.6 rule).
    assert set(payload["feature_to_assumption"]).issubset(set(payload["feature_names"]))
    # All three SHAP artifact kinds present and well-formed (FR-3A-37):
    # global summary, per-cell waterfall, and per-feature dependence.
    feat_set = set(payload["feature_names"])
    assert {g["feature"] for g in payload["global_summary"]} == feat_set
    assert all(g["mean_abs_shap"] >= 0 for g in payload["global_summary"])
    assert {d["feature"] for d in payload["dependence"]} == feat_set
    assert all(d["points"] for d in payload["dependence"])   # one point per fitting cell

    # SHAP additivity per cell, and grain_key carries exactly the output grain.
    assert payload["cells"]
    for cell in payload["cells"]:
        assert {c["feature"] for c in cell["contributions"]} == feat_set
        total = cell["base_value"] + sum(c["shap_value"] for c in cell["contributions"])
        assert abs(total - cell["prediction"]) < 1e-6
        assert set(cell["grain_key"]) == set(grain)
    # The producer's own validator accepts its output.
    _validate_shap_json(payload, grain)


def _good_payload() -> dict:
    return {
        "schema_version": "1.0", "model_id": "x", "decrement": "LAPSE",
        "product_code": "TERM", "feature_names": ["duration_band"],
        "feature_to_assumption": {
            "duration_band": {"actuarial_term": "policy duration",
                              "assumption_dimension": "lapse-by-duration"}},
        "global_summary": [{"feature": "duration_band", "mean_abs_shap": 0.1}],
        "cells": [{
            "grain_key": {"product": "TERM", "duration_band": "1"},
            "base_value": 0.0, "prediction": 0.5,
            "contributions": [{"feature": "duration_band", "shap_value": 0.5,
                               "feature_value": "1"}]}],
        "dependence": [{"feature": "duration_band",
                        "points": [{"feature_value": "1", "shap_value": 0.5}]}],
    }


def test_validate_accepts_good_and_rejects_violations():
    grain = ["product", "duration_band"]
    _validate_shap_json(_good_payload(), grain)   # no raise

    bad_additivity = _good_payload()
    bad_additivity["cells"][0]["prediction"] = 9.9
    with pytest.raises(ValueError):
        _validate_shap_json(bad_additivity, grain)

    bad_mapping = _good_payload()
    bad_mapping["feature_to_assumption"]["ghost"] = {
        "actuarial_term": "x", "assumption_dimension": "y"}
    with pytest.raises(ValueError):
        _validate_shap_json(bad_mapping, grain)

    with pytest.raises(ValueError):   # grain mismatch
        _validate_shap_json(_good_payload(), ["product", "attained_age_band"])

    bad_version = _good_payload()
    bad_version["schema_version"] = "9.9"
    with pytest.raises(ValueError):
        _validate_shap_json(bad_version, grain)
