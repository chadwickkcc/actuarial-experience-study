"""Real-data smoke tests for the GLM engine (Session 15).

These run against a session copy of the production DB (skip-if-absent) to
exercise data shapes the synthetic fixture cannot: zero-expected cells, mixed
null covariates, and sparse cells that make individual bootstrap resamples
degenerate. They guard the two real-data robustness fixes — dropping
non-positive-denominator cells and surviving a degenerate bootstrap refit — so
neither regresses. Correctness against known truth is covered by
test_glm_validate.py; here we assert only that the pipeline runs cleanly.
"""
import math

import duckdb
import pytest
import yaml

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.glm.bootstrap import bootstrap_cis


@pytest.fixture(scope="module")
def glm_cfg() -> dict:
    with open("config/ai_config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["glm"]


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
    ("mortality", "WL"),   # WL mortality has zero-expected cells + enough events
    ("lapse", "TERM"),
    ("lapse", "WL"),
])
def test_real_data_fit_and_bootstrap_runs_clean(prod_db, glm_cfg, decrement_key, product):
    """Fit + bootstrap on real Gold data raises nothing; CIs are finite & ordered."""
    run_id = _latest_run_id(prod_db)
    decrement = DecrementType(decrement_key.upper())
    cells = load_cells(prod_db, run_id, decrement, product)
    fitted = fit_glm(
        cells, decrement, product,
        glm_cfg["covariates"][decrement_key], glm_cfg["output_grain"][decrement_key],
        glm_cfg["min_events_to_fit"], glm_cfg["seed"],
    )
    if not fitted.converged:
        pytest.skip(f"{decrement_key}/{product} below data threshold: {fitted.message}")

    out = bootstrap_cis(
        cells, decrement, product,
        glm_cfg["covariates"][decrement_key], glm_cfg["output_grain"][decrement_key],
        fitted, n_resamples=80, seed=glm_cfg["seed"],
    )
    assert out.factors
    for fc in out.factors:
        assert math.isfinite(fc.factor) and fc.factor >= 0
        assert math.isfinite(fc.ci_low) and math.isfinite(fc.ci_high)
        assert fc.ci_low <= fc.factor <= fc.ci_high
