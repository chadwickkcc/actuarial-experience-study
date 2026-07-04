"""TEV statutory book profit module — Variable Universal Life.

Implements FR-2-18 (VUL formula). Vectorised NumPy over model points.

BP_t = ME_charge_t            (M&E = me_rate × SA_value_t)
     + COI_charges_t
     + Surrender_charges_collected_t
     - Death_claims_t
     - CI_benefit_t
     - Surrender_benefits_t
     - Maintenance_expense_t
     + Investment_income_on_GA_RC_t
     - DeltaReserve_t
     - Tax_t

Note: Separate account assets earn for the policyholder, not the insurer.
"""
from __future__ import annotations

import numpy as np


def _base_coi_rate(attained_age: np.ndarray) -> np.ndarray:
    """Simplified COI rate per dollar of NAR (annual)."""
    return np.minimum(1.0, 0.001 + 0.00006 * np.exp(0.085 * attained_age))


def compute_statutory_profit(
    in_force: np.ndarray,
    in_force_prev: np.ndarray,
    face: np.ndarray,
    separate_account_value: np.ndarray,
    reserve_bom: np.ndarray,
    reserve_eom: np.ndarray,
    q_x: np.ndarray,
    lapse_rate: np.ndarray,
    ci_sa: np.ndarray,
    ci_rate: np.ndarray,
    attained_age: np.ndarray,
    me_charge_rate: float,
    surrender_charge_rate: float,
    expense_inflation: float,
    earned_rate_ga: float,
    tax_rate: float,
    maintenance_per_policy: float,
    rc_pct: float,
    t: int,
) -> np.ndarray:
    """Compute statutory book profit for VUL model points.

    All arrays shape (n_model_points,). Returns bp array (n_model_points,).

    Args:
        in_force:                 In-force count at period end.
        in_force_prev:            In-force count at period start.
        face:                     Specified amount total.
        separate_account_value:   Separate account total value (AV).
        reserve_bom:              Statutory reserve at BOM.
        reserve_eom:              Statutory reserve at EOM.
        q_x:                      Mortality rate.
        lapse_rate:               Lapse/surrender rate.
        ci_sa:                    CI rider sum assured total.
        ci_rate:                  CI incidence rate.
        attained_age:             Weighted average attained age.
        me_charge_rate:           M&E charge annual rate (e.g., 0.014).
        surrender_charge_rate:    Surrender charge rate (declining).
        expense_inflation:        Annual expense inflation.
        earned_rate_ga:           GA earned rate (for RC investment income).
        tax_rate:                 Corporate tax rate.
        maintenance_per_policy:   Per-policy maintenance expense.
        rc_pct:                   Required capital % of reserve.
        t:                        Projection year index.

    Returns:
        bp: Statutory book profit array.
    """
    safe_prev = np.maximum(in_force_prev, 1e-9)
    survival_ratio = in_force / safe_prev

    # M&E charge on separate account value
    me_charge = me_charge_rate * separate_account_value * survival_ratio

    # COI charges (on NAR = face - SA_value)
    nar = np.maximum(face - separate_account_value, 0.0)
    coi_rate = _base_coi_rate(attained_age)
    coi_charges = nar * coi_rate * survival_ratio

    # Surrender charges collected on lapses
    surrenders = in_force_prev * lapse_rate
    av_per_policy = np.where(in_force_prev > 0, separate_account_value / safe_prev, 0.0)
    surrender_charges = av_per_policy * surrenders * surrender_charge_rate

    # Death claims
    face_per_policy = np.where(in_force_prev > 0, face / safe_prev, 0.0)
    deaths = in_force_prev * q_x
    death_claims = face_per_policy * deaths

    # CI benefit
    ci_claims = in_force_prev * ci_rate
    ci_sa_per_policy = np.where(in_force_prev > 0, ci_sa / safe_prev, 0.0)
    ci_benefit = ci_sa_per_policy * ci_claims

    # Surrender benefits (AV net of surrender charges)
    surrender_benefits = av_per_policy * surrenders * (1.0 - surrender_charge_rate)

    # Maintenance
    inflation_factor = (1.0 + expense_inflation) ** t
    maint = maintenance_per_policy * inflation_factor * in_force

    # Investment income on GA required capital
    rc = reserve_bom * rc_pct
    inv_income_rc = rc * earned_rate_ga

    # Delta reserve
    delta_reserve = reserve_eom - reserve_bom

    pre_tax = (me_charge + coi_charges + surrender_charges
               - death_claims - ci_benefit - surrender_benefits
               - maint + inv_income_rc - delta_reserve)

    tax = np.maximum(0.0, pre_tax * tax_rate)
    return pre_tax - tax
