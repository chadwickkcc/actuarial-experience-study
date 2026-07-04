"""Tests for ui/ai_comparison_logic — comparison table + what-if set builder.

Session 17 (FR-3A-42/43). These exercise the pure logic with hand-built
GLM/GBM results and a minimal in-memory assumption set, so they need no DB and
no model fitting.
"""
from __future__ import annotations

import math

import pandas as pd

from ui import ai_comparison_logic as logic
from src.utils.types import DecrementType, FactorCell, GLMFitResult, GBMFitResult
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.utils.types import AssumptionSetStatus


def _factor(grain, factor, ae=1.0, z=0.8, exp=300.0):
    return FactorCell(
        grain_key=grain, factor=factor, ci_low=factor * 0.9, ci_high=factor * 1.1,
        expected_events=exp, credibility_z=z, ae_derived_factor=ae,
    )


def _glm(factors):
    return GLMFitResult(
        model_id="glm-1", run_id="R1", decrement=DecrementType.MORTALITY,
        product_code="TERM", converged=True, n_cells=len(factors),
        deviance=1.0, dispersion=1.0, aic=1.0, factors=factors,
        diagnostics_path="", seed=42, message=None,
    )


def _gbm(factors, flags):
    return GBMFitResult(
        model_id="gbm-1", run_id="R1", decrement=DecrementType.MORTALITY,
        product_code="TERM", n_cells=len(factors), cv_metric_name="deviance",
        cv_metric_value=2.0, factors=factors, divergence_flags=flags,
        shap_json_path="", seed=42,
    )


def _minimal_aset(mort_mults):
    return AssumptionSet(
        id="A1", version=1, status=AssumptionSetStatus.APPROVED,
        effective_date="2024-01-01", author_id="tester", basis="best-estimate",
        source_study_run_id="R1", rdr=0.09, earned_rate_ga=0.05, earned_rate_sa=0.06,
        tax_rate=0.21, expense_inflation=0.025, rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0, maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02, mortality_multipliers=mort_mults,
        lapse_multipliers=[], surrender_multipliers=[], ci_incidence_multipliers=[],
        premium_persistency=[], shock_lapse_plt={},
    )


def _mult(product, gender, mult):
    return DecrementMultiplier(
        product=product, gender=gender, risk_class="STD_NS", duration_band=[1, 10],
        multiplier=mult, credibility_z=0.8, credibility_lower=0.7, credibility_upper=1.3,
    )


def test_build_comparison_table_labels_and_interaction_flag():
    g1 = {"product": "TERM", "sex": "M"}
    g2 = {"product": "TERM", "sex": "F"}
    glm = _glm([_factor(g1, 0.92), _factor(g2, 1.05)])
    # GBM agrees on g1, diverges on g2 (flagged).
    gbm = _gbm(
        [_factor(g1, 0.93), _factor(g2, 1.40)],
        [{"grain_key": g2, "glm_factor": 1.05, "gbm_factor": 1.40, "rel_diff": 0.33}],
    )
    df = logic.build_comparison_table(glm, gbm, None, DecrementType.MORTALITY)

    assert len(df) == 2
    for col in ("ae_derived_factor", "glm_factor", "glm_ci_low", "glm_ci_high",
                "gbm_factor", "interaction_flag", "credibility_z",
                "expected_events", "approved_factor"):
        assert col in df.columns

    row_m = df[df["sex"] == "M"].iloc[0]
    row_f = df[df["sex"] == "F"].iloc[0]
    assert row_m["interaction_flag"] is False or row_m["interaction_flag"] == False  # noqa: E712
    assert bool(row_f["interaction_flag"]) is True
    assert math.isclose(row_f["gbm_factor"], 1.40)
    # No approved set → approved_factor is None.
    assert row_m["approved_factor"] is None


def test_build_comparison_table_no_gbm_passthrough():
    g1 = {"product": "TERM", "sex": "M"}
    glm = _glm([_factor(g1, 0.92)])
    df = logic.build_comparison_table(glm, None, None, DecrementType.MORTALITY)
    assert len(df) == 1
    assert math.isnan(df.iloc[0]["gbm_factor"])
    assert bool(df.iloc[0]["interaction_flag"]) is False


def test_lookup_approved_factor_matches_grain():
    aset = _minimal_aset([_mult("TERM", "M", 0.90), _mult("TERM", "M", 0.95),
                          _mult("TERM", "F", 1.10)])
    val = logic.lookup_approved_factor(
        aset, DecrementType.MORTALITY, {"product": "TERM", "sex": "M"}
    )
    assert math.isclose(val, (0.90 + 0.95) / 2)
    # Non-matching product → None.
    assert logic.lookup_approved_factor(
        aset, DecrementType.MORTALITY, {"product": "WL", "sex": "M"}
    ) is None


def test_build_whatif_assumption_set_is_in_memory_and_nonmutating():
    base_mults = [_mult("TERM", "M", 0.80), _mult("WL", "M", 0.85)]
    aset = _minimal_aset(base_mults)
    glm = _glm([_factor({"product": "TERM", "sex": "M"}, 0.92)])

    whatif = logic.build_whatif_assumption_set(aset, DecrementType.MORTALITY, "TERM", glm)

    # Fresh, in-memory only — never persisted.
    assert whatif.id != aset.id
    assert whatif.yaml_file_path == ""
    # Baseline untouched.
    assert aset.mortality_multipliers[0].multiplier == 0.80
    # TERM moved toward the GLM proposal; WL unchanged.
    term = [m for m in whatif.mortality_multipliers if m.product == "TERM"][0]
    wl = [m for m in whatif.mortality_multipliers if m.product == "WL"][0]
    assert math.isclose(term.multiplier, 0.92)
    assert math.isclose(wl.multiplier, 0.85)
    assert "AI what-if" in term.override_rationale
