"""Tests for the TEV what-if path (Session 17; FR-3A-43).

The what-if must (a) flag the TEV run `what_if_ai_proposal`, (b) pass an
in-memory perturbed assumption set, and (c) create or modify no assumption set.
The heavy TEV engine is monkeypatched so the test is fast and DB-light.
"""
from __future__ import annotations

import duckdb

from ui import ai_comparison_logic as logic
from src.utils.types import (
    DecrementType, FactorCell, GLMFitResult, TEVRunResult, AssumptionSetStatus,
)
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.utils.db_init import init_database


def _aset(aset_id, mults):
    return AssumptionSet(
        id=aset_id, version=1, status=AssumptionSetStatus.APPROVED,
        effective_date="2024-01-01", author_id="t", basis="best-estimate",
        source_study_run_id="R1", rdr=0.09, earned_rate_ga=0.05, earned_rate_sa=0.06,
        tax_rate=0.21, expense_inflation=0.025, rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0, maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02, mortality_multipliers=mults,
        lapse_multipliers=[], surrender_multipliers=[], ci_incidence_multipliers=[],
        premium_persistency=[], shock_lapse_plt={},
    )


def _mult(mult):
    return DecrementMultiplier(
        product="TERM", gender="M", risk_class="STD_NS", duration_band=[1, 10],
        multiplier=mult, credibility_z=0.8, credibility_lower=0.7, credibility_upper=1.3,
    )


def _fake_run_result(tev_run_id, sensitivity_id, total):
    return TEVRunResult(
        tev_run_id=tev_run_id, assumption_set_id="A1", sensitivity_id=sensitivity_id,
        product_results=[], total_anw=0.0, total_pvfp=0.0, total_pvcoc=0.0,
        total_vif=0.0, total_tev=total, delta_tev=(total - 1000.0), duration_sec=0.0,
    )


def test_whatif_flags_run_and_passes_in_memory_set(monkeypatch, tmp_path):
    import src.tev.tev_core as tev_core

    calls = []

    def fake_run_tev(db_path, assumption_set_id, assumption_set=None,
                     sensitivity_id=None, prior_tev_run_id=None, tev_run_id=None):
        calls.append({
            "assumption_set_id": assumption_set_id,
            "assumption_set": assumption_set,
            "sensitivity_id": sensitivity_id,
            "prior_tev_run_id": prior_tev_run_id,
            "tev_run_id": tev_run_id,
        })
        total = 1000.0 if sensitivity_id is None else 1100.0
        return _fake_run_result(tev_run_id, sensitivity_id, total)

    monkeypatch.setattr(tev_core, "run_tev", fake_run_tev)

    approved = _aset("A1", [_mult(0.80)])
    glm = GLMFitResult(
        model_id="g", run_id="R1", decrement=DecrementType.MORTALITY, product_code="TERM",
        converged=True, n_cells=1, deviance=1.0, dispersion=1.0, aic=1.0,
        factors=[FactorCell({"product": "TERM", "sex": "M"}, 0.92, 0.8, 1.0, 300.0, 0.8, 1.0)],
        diagnostics_path="", seed=42, message=None,
    )
    whatif_aset = logic.build_whatif_assumption_set(approved, DecrementType.MORTALITY, "TERM", glm)

    whatif, baseline_total = logic.run_whatif_tev(tmp_path / "x.duckdb", approved, whatif_aset)

    # Two engine calls: baseline (None) then what-if (flagged).
    assert len(calls) == 2
    baseline_call, whatif_call = calls
    assert baseline_call["sensitivity_id"] is None
    assert whatif_call["sensitivity_id"] == "what_if_ai_proposal"
    assert whatif_call["assumption_set"] is whatif_aset            # in-memory set
    assert whatif_call["assumption_set_id"] == "A1"               # references approved
    assert whatif_call["prior_tev_run_id"] == baseline_call["tev_run_id"]
    assert baseline_total == 1000.0
    assert whatif.total_tev == 1100.0


def test_build_whatif_creates_no_assumption_set_row(tmp_path):
    """build_whatif_assumption_set must not persist anything to gold_assumption_sets."""
    db = tmp_path / "noaset.duckdb"
    init_database(str(db))

    def _count():
        con = duckdb.connect(str(db), read_only=True)
        try:
            return con.execute("SELECT COUNT(*) FROM gold_assumption_sets").fetchone()[0]
        finally:
            con.close()

    before = _count()
    approved = _aset("A1", [_mult(0.80)])
    glm = GLMFitResult(
        model_id="g", run_id="R1", decrement=DecrementType.MORTALITY, product_code="TERM",
        converged=True, n_cells=1, deviance=1.0, dispersion=1.0, aic=1.0,
        factors=[FactorCell({"product": "TERM", "sex": "M"}, 0.92, 0.8, 1.0, 300.0, 0.8, 1.0)],
        diagnostics_path="", seed=42, message=None,
    )
    whatif = logic.build_whatif_assumption_set(approved, DecrementType.MORTALITY, "TERM", glm)
    assert whatif.id != approved.id
    assert _count() == before  # nothing written
