"""Real-data smoke tests for the GBM overlay (Session 16).

Run against a session copy of the production DB (skip-if-absent) to exercise data
shapes the synthetic fixture cannot: zero-expected cells (dropped via the
inherited GLM aggregation), mixed-null covariates, PLT premium-jump bands whose
levels contain '<'/'>' (which XGBoost forbids in feature names — handled by
positional DMatrix construction), and sparse single-class binomial cells. We
assert the GBM either produces a clean proposal (finite factors, registers,
emits a schema-valid SHAP-JSON) or a clean no-proposal — never a crash. Recovery
against known truth is reported (not gated) in test_gbm_validate.py.
"""
import math

import duckdb
import pytest
import yaml

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.gbm.fit import fit_gbm, bootstrap_gbm_cis, _fit_gbm_core
from src.ai.gbm.explain import generate_shap_artifacts
from tests.conftest import ARTIFACT_ROOT


@pytest.fixture(scope="module")
def ai_cfg() -> dict:
    with open("config/ai_config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def fta_map() -> dict:
    with open("config/feature_to_assumption.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["feature_to_assumption"]


def _latest_run_id(db_path) -> str:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute("SELECT study_run_id FROM gold_ae_results LIMIT 1").fetchone()
    finally:
        con.close()
    if row is None:
        pytest.skip("production DB has no A/E results")
    return row[0]


@pytest.mark.parametrize("decrement_key,product", [
    ("mortality", "TERM"),
    ("mortality", "WL"),    # zero-expected cells in real data
    ("lapse", "TERM"),      # PLT premium-jump bands with '<'/'>'
    ("lapse", "WL"),
    ("ci_incidence", "TERM"),
    ("lapse", "DA"),        # annuity surrender
])
def test_real_data_gbm_runs_clean(prod_db, ai_cfg, fta_map, decrement_key, product):
    run_id = _latest_run_id(prod_db)
    decrement = DecrementType(decrement_key.upper())
    glm_cfg, gbm_cfg = ai_cfg["glm"], ai_cfg["gbm"]
    cov = glm_cfg["covariates"][decrement_key]
    grain = glm_cfg["output_grain"][decrement_key]

    cells = load_cells(prod_db, run_id, decrement, product)
    glm = fit_glm(cells, decrement, product, cov, grain,
                  glm_cfg["min_events_to_fit"], glm_cfg["seed"])
    gbm = fit_gbm(cells, decrement, product, cov, grain, gbm_cfg["hyperparams"],
                  glm, gbm_cfg["divergence_threshold"],
                  glm_cfg["min_events_to_fit"], gbm_cfg["seed"])

    if not gbm.factors:
        # A clean no-proposal (sparse / sub-threshold) is the correct guardrail outcome.
        assert math.isnan(gbm.cv_metric_value)
        assert gbm.divergence_flags == []
        return

    for fc in gbm.factors:
        assert math.isfinite(fc.factor) and fc.factor >= 0
    for flag in gbm.divergence_flags:
        assert math.isfinite(flag["rel_diff"]) and flag["rel_diff"] > gbm_cfg["divergence_threshold"]

    gbm = bootstrap_gbm_cis(cells, decrement, product, cov, grain,
                            gbm_cfg["hyperparams"], gbm, n_resamples=20, seed=42)
    for fc in gbm.factors:
        assert math.isfinite(fc.ci_low) and math.isfinite(fc.ci_high)
        assert fc.ci_low <= fc.ci_high

    # Generate SHAP directly on real-data shapes (the registry INSERT is covered
    # on the synthetic DB; the production copy predates the AI Gold tables). This
    # exercises SHAP additivity/validation on real one-hot levels incl. PLT bands.
    core = _fit_gbm_core(cells, decrement, cov, grain, gbm_cfg["hyperparams"], gbm_cfg["seed"])
    shap_path = generate_shap_artifacts(
        core["booster"], core["features"], core["fit_cells"], grain,
        fta_map[decrement.value], f"rt-{decrement.value}-{product}",
        ARTIFACT_ROOT / "gbm_realdata_shap",
        decrement=decrement, product_code=product,
    )
    assert shap_path
