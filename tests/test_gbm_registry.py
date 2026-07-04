"""Tests for register_gbm_model — persistence + registry (Session 16).

Covers FR-3A-35 / §D.1/§D.5: a gold_ai_model_registry row with model_type='GBM',
cv_metric_* and shap_json_path populated, GLM-only stats NULL, the full
reproducibility stamp; the booster serialized as native XGBoost JSON; and a
deterministic round-trip to identical predictions.
"""
import numpy as np
import duckdb

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells
from src.ai.gbm.fit import fit_gbm, register_gbm_model, load_gbm_model, _fit_gbm_core
from tests.conftest import ARTIFACT_ROOT


def _fit_lapse(synthetic_db, glm_config, gbm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.LAPSE, "TERM")
    cov = glm_config["covariates"]["lapse"]
    grain = glm_config["output_grain"]["lapse"]
    fitted = fit_gbm(cells, DecrementType.LAPSE, "TERM", cov, grain,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     glm_config["min_events_to_fit"], gbm_config["seed"])
    return cells, cov, grain, fitted


def test_register_writes_gbm_row_with_stamp(synthetic_db, glm_config, gbm_config,
                                            feature_to_assumption_map):
    # Mortality has many fitting cells (> CV folds), so the CV metric is a real
    # number rather than NaN — exercising the cv_metric_* columns end to end.
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.MORTALITY, "TERM")
    cov = glm_config["covariates"]["mortality"]
    grain = glm_config["output_grain"]["mortality"]
    fitted = fit_gbm(cells, DecrementType.MORTALITY, "TERM", cov, grain,
                     gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                     glm_config["min_events_to_fit"], gbm_config["seed"])
    models_dir = ARTIFACT_ROOT / "ai_models_gbm_registry"
    model_id = register_gbm_model(
        fitted, cells, cov, grain, gbm_config["hyperparams"], synthetic_db.db_path,
        feature_to_assumption=feature_to_assumption_map["MORTALITY"],
        data_snapshot_hash="snapG", config_hash="cfgG", code_version="v0.16",
        models_dir=models_dir,
    )
    con = duckdb.connect(str(synthetic_db.db_path))
    try:
        row = con.execute(
            "SELECT model_type, decrement, product_code, converged, seed, "
            "cv_metric_name, cv_metric_value, deviance, dispersion, aic, "
            "artifact_path, shap_json_path, data_snapshot_hash, config_hash, code_version "
            "FROM gold_ai_model_registry WHERE model_id = ?", [model_id]
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    (mtype, dec, prod, conv, seed, cvn, cvv, dev, disp, aic,
     artifact, shap_p, snap, cfg, ver) = row
    assert mtype == "GBM" and dec == "MORTALITY" and prod == "TERM"
    assert conv is True and seed == gbm_config["seed"]
    assert cvn == "deviance" and cvv is not None
    assert dev is None and disp is None and aic is None    # GLM-only stats NULL for GBM
    assert artifact.endswith(f"{model_id}.json")
    assert shap_p.endswith(f"{model_id}.json")
    assert (snap, cfg, ver) == ("snapG", "cfgG", "v0.16")
    assert fitted.shap_json_path == shap_p


def test_booster_roundtrips_identical_predictions(synthetic_db, glm_config, gbm_config,
                                                  feature_to_assumption_map):
    cells, cov, grain, fitted = _fit_lapse(synthetic_db, glm_config, gbm_config)
    models_dir = ARTIFACT_ROOT / "ai_models_gbm_roundtrip"
    register_gbm_model(
        fitted, cells, cov, grain, gbm_config["hyperparams"], synthetic_db.db_path,
        feature_to_assumption=feature_to_assumption_map["LAPSE"],
        data_snapshot_hash="s", config_hash="c", code_version="v",
        models_dir=models_dir,
    )
    artifact = models_dir / "gbm" / f"{fitted.model_id}.json"
    loaded = load_gbm_model(artifact)
    # A fresh deterministic re-fit reproduces the saved booster's predictions.
    core = _fit_gbm_core(cells, DecrementType.LAPSE, cov, grain,
                         gbm_config["hyperparams"], gbm_config["seed"])
    np.testing.assert_array_equal(loaded.predict(core["dtrain"]),
                                  core["booster"].predict(core["dtrain"]))
