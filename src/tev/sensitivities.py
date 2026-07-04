"""TEV Sensitivity Grid Runner — Phase 2.

Implements Technical Specification Section B.10:
    - run_sensitivity_grid()     runs all 11 standard sensitivities
    - apply_sensitivity_shock()  returns perturbed AssumptionSet copy

Sensitivity definitions exactly match SENSITIVITY_DEFINITIONS from the spec.
Perturbed assumption sets are NOT persisted; only results go to DB.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.utils.types import SensitivityGridResult, TEVRunResult
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier, load_assumption_set
from src.tev.tev_core import run_tev


# ---------------------------------------------------------------------------
# Sensitivity definitions — exactly per Technical Specification Section B.10
# ---------------------------------------------------------------------------

SENSITIVITY_DEFINITIONS: dict[str, dict] = {
    "SENS-01": {"description": "Lapse -10%",            "decrement": "lapse",            "shock": 0.90},
    "SENS-02": {"description": "Lapse +10%",            "decrement": "lapse",            "shock": 1.10},
    "SENS-03": {"description": "Mortality -5% (life)",  "decrement": "mortality_life",   "shock": 0.95},
    "SENS-04": {"description": "Mortality +5% (life)",  "decrement": "mortality_life",   "shock": 1.05},
    "SENS-05": {"description": "Longevity +5% (annuity)", "decrement": "mortality_annuity", "shock": 0.95},
    "SENS-06": {"description": "CI incidence -10%",     "decrement": "ci_incidence",     "shock": 0.90},
    "SENS-07": {"description": "CI incidence +10%",     "decrement": "ci_incidence",     "shock": 1.10},
    "SENS-08": {"description": "Expense -10%",          "decrement": "expense",          "shock": 0.90},
    "SENS-09": {"description": "Expense +10%",          "decrement": "expense",          "shock": 1.10},
    "SENS-10": {"description": "RDR +100bp",            "decrement": "rdr",              "shock": +0.01},
    "SENS-11": {"description": "RDR -100bp",            "decrement": "rdr",              "shock": -0.01},
}


def apply_sensitivity_shock(
    assumption_set: AssumptionSet,
    sensitivity_id: str,
) -> AssumptionSet:
    """Return a new AssumptionSet with the sensitivity shock applied.

    Does not mutate the input. Does not persist the new set.
    The returned set has a fresh UUID so it can be used in run_tev() without
    conflicting with the original.

    Args:
        assumption_set: Base assumption set to perturb.
        sensitivity_id: Key from SENSITIVITY_DEFINITIONS (e.g., "SENS-01").

    Returns:
        Perturbed AssumptionSet (in-memory only, yaml_file_path = "").

    Raises:
        KeyError: if sensitivity_id is not in SENSITIVITY_DEFINITIONS.
    """
    if sensitivity_id not in SENSITIVITY_DEFINITIONS:
        raise KeyError(f"Unknown sensitivity_id: {sensitivity_id}. "
                       f"Valid IDs: {list(SENSITIVITY_DEFINITIONS)}")

    defn = SENSITIVITY_DEFINITIONS[sensitivity_id]
    decrement = defn["decrement"]
    shock = float(defn["shock"])

    # Deep-copy so we don't mutate the original
    perturbed = _deep_copy_assumption_set(assumption_set)

    if decrement == "lapse":
        perturbed.lapse_multipliers = _scale_multipliers(
            assumption_set.lapse_multipliers, shock
        )

    elif decrement == "mortality_life":
        # Applies to Term, WL, UL, ULSG, VUL (not DA annuity)
        life_products = {"TERM", "WL", "UL", "ULSG", "IUL", "VUL"}
        perturbed.mortality_multipliers = [
            _scale_single(m, shock) if m.product.upper() in life_products
            else m
            for m in assumption_set.mortality_multipliers
        ]

    elif decrement == "mortality_annuity":
        # Applies only to DA; shock < 1 means lives longer (worse for insurer)
        perturbed.mortality_multipliers = [
            _scale_single(m, shock) if m.product.upper() in {"DA", "DA_FIXED", "DA_FIA", "DA_VA"}
            else m
            for m in assumption_set.mortality_multipliers
        ]

    elif decrement == "ci_incidence":
        perturbed.ci_incidence_multipliers = _scale_multipliers(
            assumption_set.ci_incidence_multipliers, shock
        )

    elif decrement == "expense":
        # Additive shock to maintenance expense parameters
        perturbed.maintenance_per_policy = assumption_set.maintenance_per_policy * shock
        perturbed.maintenance_pct_premium = assumption_set.maintenance_pct_premium * shock

    elif decrement == "rdr":
        # Additive shock: shock is +0.01 or -0.01
        perturbed.rdr = assumption_set.rdr + shock

    return perturbed


def run_sensitivity_grid(
    db_path: Path,
    assumption_set_id: str,
    baseline_tev_run_id: str,
) -> SensitivityGridResult:
    """Run all 11 standard sensitivities against the baseline assumption set.

    For each sensitivity:
        1. Create perturbed copy of assumption set (in-memory, not persisted).
        2. Run run_tev() with the perturbed set.
        3. Compute ΔTEV vs baseline.
    4. Build impact_matrix_df from delta_tev values.

    Args:
        db_path:                Path to DuckDB file.
        assumption_set_id:      UUID of the base assumption set.
        baseline_tev_run_id:    TEV run ID of the baseline to compare against.

    Returns:
        SensitivityGridResult with all sensitivity results and impact matrix.
    """
    base_aset = load_assumption_set(assumption_set_id, db_path)

    # Load baseline TEV
    import duckdb
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT l.total_tev, r.product_code, r.tev FROM gold_tev_run_log l "
            "LEFT JOIN gold_tev_results r USING (tev_run_id) "
            "WHERE l.tev_run_id = ? AND l.sensitivity_id IS NULL "
            "LIMIT 1",
            [baseline_tev_run_id],
        ).fetchone()
        baseline_total_tev_row = con.execute(
            "SELECT total_tev FROM gold_tev_run_log WHERE tev_run_id = ?",
            [baseline_tev_run_id],
        ).fetchone()
        baseline_total_tev = float(baseline_total_tev_row[0]) if baseline_total_tev_row else 0.0

        # Load per-product baseline TEV
        baseline_by_product = {}
        rows = con.execute(
            "SELECT product_code, tev FROM gold_tev_results WHERE tev_run_id = ? AND sensitivity_id IS NULL",
            [baseline_tev_run_id],
        ).fetchall()
        for prod, tev_val in rows:
            baseline_by_product[prod] = float(tev_val) if tev_val is not None else 0.0
    finally:
        con.close()

    sensitivity_results: list[TEVRunResult] = []

    for sens_id in SENSITIVITY_DEFINITIONS:
        perturbed = apply_sensitivity_shock(base_aset, sens_id)
        sens_run_id = str(uuid.uuid4())

        sens_result = run_tev(
            db_path=db_path,
            assumption_set_id=assumption_set_id,
            prior_tev_run_id=baseline_tev_run_id,
            sensitivity_id=sens_id,
            tev_run_id=sens_run_id,
            assumption_set=perturbed,
        )

        sensitivity_results.append(sens_result)

    # Build impact matrix DataFrame
    impact_matrix_df = _build_impact_matrix(
        sensitivity_results, baseline_by_product, baseline_total_tev
    )

    return SensitivityGridResult(
        baseline_run_id=baseline_tev_run_id,
        assumption_set_id=assumption_set_id,
        sensitivity_results=sensitivity_results,
        impact_matrix_df=impact_matrix_df,
    )


def _build_impact_matrix(
    sensitivity_results: list[TEVRunResult],
    baseline_by_product: dict[str, float],
    baseline_total_tev: float,
) -> pd.DataFrame:
    """Construct the TEV-impact matrix from sensitivity run results.

    Rows = products + TOTAL. Columns = sensitivity IDs.
    Values = ΔTEV (sensitivity TEV - baseline TEV).

    Args:
        sensitivity_results: List of TEVRunResult for each sensitivity.
        baseline_by_product: Baseline TEV by product code.
        baseline_total_tev:  Total baseline TEV.

    Returns:
        DataFrame with products as index and sensitivity IDs as columns.
    """
    all_products = sorted(set(
        pr.product_code
        for sr in sensitivity_results
        for pr in sr.product_results
    ))

    rows_data: dict[str, dict[str, float]] = {}
    for prod in all_products:
        rows_data[prod] = {}
    rows_data["TOTAL"] = {}

    for sr in sensitivity_results:
        sens_id = sr.sensitivity_id
        for pr in sr.product_results:
            prod = pr.product_code
            baseline_prod = baseline_by_product.get(prod, 0.0)
            delta = pr.tev - baseline_prod
            rows_data[prod][sens_id] = delta

        total_delta = sr.total_tev - baseline_total_tev
        rows_data["TOTAL"][sens_id] = total_delta

    df = pd.DataFrame(rows_data).T
    # Reorder columns
    col_order = [s for s in SENSITIVITY_DEFINITIONS if s in df.columns]
    df = df[col_order]

    # Add sensitivity range column
    df["total_sensitivity_range"] = df[col_order].abs().max(axis=1)

    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scale_multipliers(
    mults: list[DecrementMultiplier],
    shock: float,
) -> list[DecrementMultiplier]:
    """Return new list with all multipliers scaled by shock factor."""
    return [_scale_single(m, shock) for m in mults]


def _scale_single(m: DecrementMultiplier, shock: float) -> DecrementMultiplier:
    """Return a new DecrementMultiplier with multiplier scaled by shock."""
    return DecrementMultiplier(
        product=m.product,
        gender=m.gender,
        risk_class=m.risk_class,
        duration_band=list(m.duration_band),
        multiplier=m.multiplier * shock,
        credibility_z=m.credibility_z,
        credibility_lower=m.credibility_lower,
        credibility_upper=m.credibility_upper,
        override_rationale=m.override_rationale,
    )


def _deep_copy_assumption_set(aset: AssumptionSet) -> AssumptionSet:
    """Return a deep copy of an AssumptionSet with a fresh ID."""
    # Copy all multiplier lists
    new = AssumptionSet(
        id=str(uuid.uuid4()),
        version=aset.version,
        status=aset.status,
        effective_date=aset.effective_date,
        author_id=aset.author_id,
        basis=aset.basis,
        source_study_run_id=aset.source_study_run_id,
        rdr=aset.rdr,
        earned_rate_ga=aset.earned_rate_ga,
        earned_rate_sa=aset.earned_rate_sa,
        tax_rate=aset.tax_rate,
        expense_inflation=aset.expense_inflation,
        rc_pct_reserve=dict(aset.rc_pct_reserve),
        acquisition_per_policy=aset.acquisition_per_policy,
        maintenance_per_policy=aset.maintenance_per_policy,
        maintenance_pct_premium=aset.maintenance_pct_premium,
        mortality_multipliers=list(aset.mortality_multipliers),
        lapse_multipliers=list(aset.lapse_multipliers),
        surrender_multipliers=list(aset.surrender_multipliers),
        ci_incidence_multipliers=list(aset.ci_incidence_multipliers),
        premium_persistency=list(aset.premium_persistency),
        shock_lapse_plt=dict(aset.shock_lapse_plt),
        yaml_file_path="",
    )
    return new
