"""Tests for src/ai/glm/registry.py — model persistence + registry (Session 15).

Covers FR-3A-24: serialize the fitted model, register a gold_ai_model_registry
row with the full reproducibility stamp, and reproduce identical coefficients
from the pickle.
"""
import numpy as np
import duckdb

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.glm.registry import register_glm_model, load_glm_model
from tests.conftest import ARTIFACT_ROOT


def _fit_mortality(synthetic_db, glm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.MORTALITY, "TERM")
    fitted = fit_glm(
        cells, DecrementType.MORTALITY, "TERM",
        glm_config["covariates"]["mortality"],
        glm_config["output_grain"]["mortality"],
        glm_config["min_events_to_fit"], glm_config["seed"],
    )
    return cells, fitted


def test_register_writes_row_with_stamp(synthetic_db, glm_config, tmp_path):
    cells, fitted = _fit_mortality(synthetic_db, glm_config)
    models_dir = ARTIFACT_ROOT / "ai_models_registry_test"
    model_id = register_glm_model(
        fitted, cells, glm_config["covariates"]["mortality"],
        glm_config["output_grain"]["mortality"], synthetic_db.db_path,
        data_snapshot_hash="snap123", config_hash="cfg456",
        code_version="v0.15", models_dir=models_dir,
    )
    con = duckdb.connect(str(synthetic_db.db_path))
    try:
        row = con.execute(
            "SELECT model_type, decrement, product_code, converged, seed, "
            "data_snapshot_hash, config_hash, code_version, artifact_path "
            "FROM gold_ai_model_registry WHERE model_id = ?", [model_id]
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    (mtype, dec, prod, conv, seed, snap, cfg, ver, artifact) = row
    assert mtype == "GLM" and dec == "MORTALITY" and prod == "TERM"
    assert conv is True and seed == glm_config["seed"]
    assert (snap, cfg, ver) == ("snap123", "cfg456", "v0.15")
    assert artifact.endswith(f"{model_id}.pkl")
    assert fitted.diagnostics_path  # set in place


def test_pickle_roundtrips_identical_coefficients(synthetic_db, glm_config):
    cells, fitted = _fit_mortality(synthetic_db, glm_config)
    models_dir = ARTIFACT_ROOT / "ai_models_roundtrip_test"
    register_glm_model(
        fitted, cells, glm_config["covariates"]["mortality"],
        glm_config["output_grain"]["mortality"], synthetic_db.db_path,
        data_snapshot_hash="s", config_hash="c", code_version="v",
        models_dir=models_dir,
    )
    artifact = models_dir / "glm" / f"{fitted.model_id}.pkl"
    loaded = load_glm_model(artifact)
    # A fresh deterministic fit must reproduce the saved coefficients (FR-3A-24).
    from src.ai.glm.fit import _fit_core
    core = _fit_core(cells, DecrementType.MORTALITY,
                     glm_config["covariates"]["mortality"],
                     glm_config["output_grain"]["mortality"], glm_config["seed"])
    np.testing.assert_allclose(
        np.asarray(loaded["results"].params),
        np.asarray(core["results"].params),
    )
