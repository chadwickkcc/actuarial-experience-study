"""Synthetic-truth recovery validation for GLM factors (Session 15).

Realises FR-3A-26 (tolerance-table recovery) and FR-3A-27 (95% CI coverage).
The synthetic generator's true decrement factors are known, so the GLM's
proposals can be checked against ground truth — a test most production builds
cannot perform.
"""
from __future__ import annotations

import math

from src.utils.types import GLMFitResult, ValidationResult


def validate_against_truth(
    fitted: GLMFitResult,
    truth_factors: dict[tuple, float],
    tolerance_pct: float,
    min_expected_events: float = 30,
    coverage_min: float = 0.90,
) -> ValidationResult:
    """Compare published factors to known truth at the output grain.

    Over cells with at least ``min_expected_events`` (FR-3A-26 floor), counts
    cells whose factor is within ``tolerance_pct`` of the true factor and the
    share whose 95% bootstrap CI contains the truth (FR-3A-27). The check passes
    when every validated cell is within tolerance and coverage is at least
    ``coverage_min``.

    Args:
        fitted:              A converged GLMFitResult with bootstrap CIs set.
        truth_factors:       Output-grain true factors keyed by the tuple of
                             grain values in output-grain order (as produced by
                             ``synthetic_data.true_factors.output_grain_true_factors``).
        tolerance_pct:       Relative tolerance for this decrement-product.
        min_expected_events: Validation floor (default 30, FR-3A-26).
        coverage_min:        Minimum CI coverage share (default 0.90, FR-3A-27).
    """
    validated = 0
    within = 0
    covered = 0
    for fc in fitted.factors:
        if fc.expected_events < min_expected_events:
            continue
        key = tuple(fc.grain_key.values())
        truth = truth_factors.get(key)
        if truth is None or not math.isfinite(truth) or truth == 0:
            continue
        validated += 1
        if abs(fc.factor - truth) / truth <= tolerance_pct:
            within += 1
        if (math.isfinite(fc.ci_low) and math.isfinite(fc.ci_high)
                and fc.ci_low <= truth <= fc.ci_high):
            covered += 1

    coverage = covered / validated if validated else 0.0
    passed = validated > 0 and within == validated and coverage >= coverage_min
    return ValidationResult(
        decrement=fitted.decrement,
        product_code=fitted.product_code,
        cells_validated=validated,
        cells_within_tol=within,
        tolerance_pct=tolerance_pct,
        coverage_pct=coverage,
        passed=passed,
    )
