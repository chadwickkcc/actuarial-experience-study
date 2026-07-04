"""TEV statutory book profit module — Universal Life (UL / ULSG / IUL).

Implements FR-2-18 (UL formula). Vectorised NumPy over model points.

BP_t = Revenue_t
       where Revenue_t = COI_charges_t + expense_loads_t + surrender_charges_collected_t
     - Benefit_t
       where Benefit_t = death_claims_t + CI_benefit_t + surrender_benefits_t
     + Spread_income_t        (AV_t × (earned_rate_ga - credited_rate))
     - Maintenance_expense_t
     + Investment_income_on_RC_t
     - DeltaReserve_t
     - Tax_t
"""
from __future__ import annotations

import numpy as np


# Base COI rate approximation (% of NAR per year): calibrated to 2001 CSO × 1.2
def _base_coi_rate(attained_age: np.ndarray) -> np.ndarray:
    """Simplified COI rate per dollar of NAR (annual)."""
    # Approximation: 0.001 + 0.00006 × exp(0.085 × age)
    return np.minimum(1.0, 0.001 + 0.00006 * np.exp(0.085 * attained_age))


def compute_statutory_profit(
    in_force: np.ndarray,
    in_force_prev: np.ndarray,
    face: np.ndarray,
    account_value: np.ndarray,
    reserve_bom: np.ndarray,
    reserve_eom: np.ndarray,
    q_x: np.ndarray,
    lapse_rate: np.ndarray,
    ci_sa: np.ndarray,
    ci_rate: np.ndarray,
    attained_age: np.ndarray,
    credited_rate: float,
    expense_inflation: float,
    earned_rate_ga: float,
    tax_rate: float,
    maintenance_per_policy: float,
    maintenance_pct_premium: float,
    coi_load_factor: float,
    expense_load_pct_premium: float,
    surrender_charge_rate: float,
    rc_pct: float,
    t: int,
) -> np.ndarray:
    """Compute statutory book profit for UL/ULSG/IUL model points.

    All arrays shape (n_model_points,). Returns bp array (n_model_points,).

    Args:
        in_force:            In-force count at period end.
        in_force_prev:       In-force count at period start.
        face:                Specified amount total (= death benefit Type A).
        account_value:       Account value total at BOM.
        reserve_bom:         Statutory reserve at BOM.
        reserve_eom:         Statutory reserve at EOM.
        q_x:                 Mortality rate.
        lapse_rate:          Lapse/surrender rate.
        ci_sa:               CI rider sum assured total.
        ci_rate:             CI incidence rate.
        attained_age:        Weighted average attained age per model point.
        credited_rate:       Credited interest rate (scalar, from macro scenario).
        expense_inflation:   Annual expense inflation.
        earned_rate_ga:      GA earned rate.
        tax_rate:            Corporate tax rate.
        maintenance_per_policy: Per-policy maintenance expense.
        maintenance_pct_premium: Maintenance % of premium.
        coi_load_factor:     Multiplier on base COI rate (1.0 = use base COI).
        expense_load_pct_premium: Expense load % of AV charged to policyholders.
        surrender_charge_rate: Surrender charge rate (declines with duration).
        rc_pct:              Required capital % of reserve.
        t:                   Projection year index.

    Returns:
        bp: Statutory book profit array.
    """
    safe_prev = np.maximum(in_force_prev, 1e-9)

    # NAR = face - account_value (per model point, total)
    nar = np.maximum(face - account_value, 0.0)

    # COI charge = NAR × COI_rate × coi_load_factor
    coi_rate = _base_coi_rate(attained_age) * coi_load_factor
    coi_charges = nar * coi_rate * (in_force / safe_prev)

    # Expense loads (% of account value)
    expense_loads = expense_load_pct_premium * account_value * (in_force / safe_prev)

    # Surrender charges collected on surrendering policies
    surrenders = in_force_prev * lapse_rate
    av_per_policy = np.where(in_force_prev > 0, account_value / safe_prev, 0.0)
    surrender_charges = av_per_policy * surrenders * surrender_charge_rate

    revenue = coi_charges + expense_loads + surrender_charges

    # Death claims = face × q_x (NAR basis simplified to face for death benefit)
    face_per_policy = np.where(in_force_prev > 0, face / safe_prev, 0.0)
    deaths = in_force_prev * q_x
    death_claims = face_per_policy * deaths

    # CI benefit
    ci_claims = in_force_prev * ci_rate
    ci_sa_per_policy = np.where(in_force_prev > 0, ci_sa / safe_prev, 0.0)
    ci_benefit = ci_sa_per_policy * ci_claims

    # Surrender benefits (account value paid out, net of surrender charges)
    surrender_benefits = av_per_policy * surrenders * (1.0 - surrender_charge_rate)

    benefits = death_claims + ci_benefit + surrender_benefits

    # Spread income: AV × (earned_rate_ga - credited_rate)
    spread_income = account_value * (earned_rate_ga - credited_rate) * (in_force / safe_prev)

    # Maintenance
    inflation_factor = (1.0 + expense_inflation) ** t
    maint = maintenance_per_policy * inflation_factor * in_force

    # Investment income on required capital
    rc = reserve_bom * rc_pct
    inv_income_rc = rc * earned_rate_ga

    # Delta reserve
    delta_reserve = reserve_eom - reserve_bom

    pre_tax = revenue - benefits + spread_income - maint + inv_income_rc - delta_reserve
    tax = np.maximum(0.0, pre_tax * tax_rate)
    return pre_tax - tax
