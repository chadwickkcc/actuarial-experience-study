"""GBM synthetic-truth recovery — REPORTED, not gated (Session 16, FR-3A-36).

The GBM is the challenge/explain overlay, not the proposal engine; unlike the
GLM (whose recovery is the Phase 3a accuracy gate), the GBM's recovery is
computed and recorded against the same tolerance table but is NOT a completion
gate. This test asserts a ValidationResult is produced — it does NOT assert it
passed.
"""
from src.utils.types import DecrementType
from src.ai.glm.fit import load_cells
from src.ai.gbm.fit import fit_gbm, bootstrap_gbm_cis
from src.ai.glm.validate import validate_against_truth
from synthetic_data.true_factors import output_grain_true_factors


def test_gbm_truth_recovery_is_reported_not_gated(synthetic_db, glm_config, gbm_config):
    cells = load_cells(synthetic_db.db_path, synthetic_db.run_id, DecrementType.MORTALITY, "TERM")
    cov = glm_config["covariates"]["mortality"]
    grain = glm_config["output_grain"]["mortality"]

    gbm = fit_gbm(cells, DecrementType.MORTALITY, "TERM", cov, grain,
                  gbm_config["hyperparams"], None, gbm_config["divergence_threshold"],
                  glm_config["min_events_to_fit"], gbm_config["seed"])
    gbm = bootstrap_gbm_cis(cells, DecrementType.MORTALITY, "TERM", cov, grain,
                            gbm_config["hyperparams"], gbm, n_resamples=40, seed=42)

    truth = output_grain_true_factors(synthetic_db.cells[("MORTALITY", "TERM")], grain)
    tol = glm_config["validation"]["tolerance_pct"]["mortality"]["TERM"]
    result = validate_against_truth(
        gbm, truth, tolerance_pct=tol,
        min_expected_events=glm_config["validation"]["min_expected_events"],
        coverage_min=glm_config["validation"]["coverage_min"],
    )

    # Reported: a ValidationResult is produced over real validated cells.
    assert result.decrement == DecrementType.MORTALITY
    assert result.product_code == "TERM"
    assert result.cells_validated > 0
    # Not gated: pass/fail is recorded but its value is NOT asserted (FR-3A-36).
    assert isinstance(result.passed, bool)
    assert 0.0 <= result.coverage_pct <= 1.0
