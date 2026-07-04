"""Synthetic-truth recovery tests (Session 15) — the Phase 3a accuracy gate.

Covers FR-3A-26 (factors within the per-decrement tolerance table on all cells
with >=30 expected events) and FR-3A-27 (true factor inside the 95% bootstrap CI
for >=90% of validated cells, pooled per decrement).
"""
import pytest

from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells, fit_glm
from src.ai.glm.bootstrap import bootstrap_cis
from src.ai.glm.validate import validate_against_truth
from synthetic_data.true_factors import output_grain_true_factors

# Products fit per decrement. Lapse's output grain (product x duration_band) is
# coarse, so it is pooled across products to give the coverage check enough cells.
_DECREMENT_PRODUCTS = {
    "mortality": ["TERM"],
    "lapse": ["TERM", "WL", "UL"],
    "ci_incidence": ["TERM", "WL", "UL"],
}


def _tolerance(glm_config, decrement_key: str, product: str) -> float:
    tol = glm_config["validation"]["tolerance_pct"][decrement_key]
    return tol[product] if isinstance(tol, dict) else tol


@pytest.fixture(scope="module")
def fitted_with_cis(request):
    """Fit + bootstrap each (decrement, product) once; share across tests."""
    synthetic_db = request.getfixturevalue("synthetic_db")
    glm_config = request.getfixturevalue("glm_config")
    out: dict = {}
    for key, products in _DECREMENT_PRODUCTS.items():
        decrement = DecrementType(key.upper())
        grain = glm_config["output_grain"][key]
        cov = glm_config["covariates"][key]
        for product in products:
            cells = load_cells(synthetic_db.db_path, synthetic_db.run_id,
                               decrement, product)
            fitted = fit_glm(cells, decrement, product, cov, grain,
                             glm_config["min_events_to_fit"], glm_config["seed"])
            fitted = bootstrap_cis(cells, decrement, product, cov, grain, fitted,
                                   n_resamples=300, ci_level=0.95,
                                   seed=glm_config["seed"])
            truth = output_grain_true_factors(
                synthetic_db.cells[(key.upper(), product)], grain)
            out[(key, product)] = (fitted, truth)
    return out


@pytest.mark.parametrize("decrement_key", list(_DECREMENT_PRODUCTS))
def test_synthetic_truth_recovery(decrement_key, fitted_with_cis, glm_config):
    """Every validated cell within tolerance AND >=90% pooled CI coverage."""
    validated = within = covered = 0
    for product in _DECREMENT_PRODUCTS[decrement_key]:
        fitted, truth = fitted_with_cis[(decrement_key, product)]
        tol = _tolerance(glm_config, decrement_key, product)
        result = validate_against_truth(
            fitted, truth, tolerance_pct=tol,
            min_expected_events=glm_config["validation"]["min_expected_events"],
            coverage_min=glm_config["validation"]["coverage_min"],
        )
        validated += result.cells_validated
        within += result.cells_within_tol
        covered += round(result.coverage_pct * result.cells_validated)

    assert validated > 0
    assert within == validated, f"{decrement_key}: {within}/{validated} within tolerance"
    coverage = covered / validated
    assert coverage >= glm_config["validation"]["coverage_min"], (
        f"{decrement_key}: pooled coverage {coverage:.2%} over {validated} cells"
    )
