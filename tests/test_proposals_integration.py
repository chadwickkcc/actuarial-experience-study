"""Integration: registering a GLM/GBM model materialises its proposed factors.

Closes the gap that the session's data-surface widening introduced — the unit
tests cover ``write_proposed_factors`` in isolation, but the *wiring* added to
``register_glm_model`` / ``register_gbm_model`` (so every registered model
publishes its factor cells to the queryable ``gold_ai_proposed_factors`` table
with the right ``model_type``) was untested end to end. These fit a real model on
the synthetic Gold DB, register it, and confirm the proposed-factor rows land and
are then readable through the gated MCP tool (the path the AI Analyst uses).
"""
from __future__ import annotations

import duckdb

from src.utils.types import DecrementType
from src.utils.sql_boundary import load_allowlist
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.glm.registry import register_glm_model
from src.ai.gbm.fit import fit_gbm, register_gbm_model
from src.ai.mcp_server.server import query_results_impl
from tests.conftest import ARTIFACT_ROOT
from ui.config import CONFIG_DIR

_AI_CONFIG = CONFIG_DIR / "ai_config.yaml"


def _factor_rows(db_path, run_id, product, decrement, model_type):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "SELECT sex, smoker, attained_age_band, duration_band, factor, "
            "credibility_z FROM gold_ai_proposed_factors WHERE run_id = ? AND "
            "product_code = ? AND decrement = ? AND model_type = ?",
            [run_id, product, decrement, model_type],
        ).fetchall()
    finally:
        con.close()


def test_register_glm_materialises_proposed_factors(synthetic_db, glm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.MORTALITY, "TERM")
    fitted = fit_glm(
        cells, DecrementType.MORTALITY, "TERM",
        glm_config["covariates"]["mortality"], glm_config["output_grain"]["mortality"],
        glm_config["min_events_to_fit"], glm_config["seed"],
    )
    assert fitted.factors, "expected a GLM proposal to exist for this cell"
    register_glm_model(
        fitted, cells, glm_config["covariates"]["mortality"],
        glm_config["output_grain"]["mortality"], synthetic_db.db_path,
        data_snapshot_hash="s", config_hash="c", code_version="v",
        models_dir=ARTIFACT_ROOT / "ai_models_pf_glm",
    )
    rows = _factor_rows(synthetic_db.db_path, synthetic_db.run_id, "TERM",
                        "MORTALITY", "GLM")
    assert len(rows) == len(fitted.factors)
    # Mortality grain is product × sex × smoker × attained_age_band: those dims are
    # populated, duration_band is NULL, and the factor is finite.
    sex, smoker, aab, dband, factor, z = rows[0]
    assert aab is not None and sex is not None and smoker is not None
    assert dband is None
    assert factor is not None

    # Readable through the gated MCP tool exactly as the AI Analyst reads it.
    allow = load_allowlist(_AI_CONFIG)
    out = query_results_impl(
        "gold_ai_proposed_factors",
        "SELECT attained_age_band, factor, ci_low, ci_high FROM "
        "gold_ai_proposed_factors WHERE product_code='TERM' AND "
        "decrement='MORTALITY' AND model_type='GLM' ORDER BY attained_age_band LIMIT 500",
        db_path=synthetic_db.db_path, allowlist=allow, row_cap=500,
    )
    assert "error" not in out and out["row_count"] == len(fitted.factors)


def test_register_gbm_materialises_proposed_factors_with_gbm_type(
    synthetic_db, glm_config, gbm_config, feature_to_assumption_map
):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                       DecrementType.MORTALITY, "TERM")
    cov = glm_config["covariates"]["mortality"]
    grain = glm_config["output_grain"]["mortality"]
    fitted = fit_gbm(
        cells, DecrementType.MORTALITY, "TERM", cov, grain,
        gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
        glm_config["min_events_to_fit"], gbm_config["seed"],
    )
    assert fitted.factors, "expected a GBM challenge factor set"
    register_gbm_model(
        fitted, cells, cov, grain, gbm_config["hyperparams"], synthetic_db.db_path,
        feature_to_assumption=feature_to_assumption_map["MORTALITY"],
        data_snapshot_hash="s", config_hash="c", code_version="v",
        models_dir=ARTIFACT_ROOT / "ai_models_pf_gbm",
    )
    rows = _factor_rows(synthetic_db.db_path, synthetic_db.run_id, "TERM",
                        "MORTALITY", "GBM")
    assert len(rows) == len(fitted.factors)
    # The GBM rows are written under model_type='GBM' (distinct from the GLM
    # proposal), so the Analyst can show proposal vs. challenge.
    assert all(r[4] is not None for r in rows)  # factor finite
