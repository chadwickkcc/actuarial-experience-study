"""TEV statutory book profit module — Term Life.

Implements FR-2-18 (Term Life formula). All operations are vectorised
NumPy over the model point array; no Python loops over individual model points.

BP_t = Premium_income_t
     - Death_benefit_t       (face × q_x_t × in_force_t)
     - CI_benefit_t          (ci_sa × ci_rate_t × in_force_t)
     - Commission_t          (commission_rate × Premium_income_t)
     - Maintenance_expense_t (maint_per_policy × in_force_t × (1+inflation)^t)
     + Investment_income_t   (reserve_t × earned_rate_ga)
     - DeltaReserve_t        (reserve_t - reserve_{t-1} × in_force_t/in_force_{t-1})
     - Tax_t                 (max(0, pre_tax_profit_t × tax_rate))
"""
from __future__ import annotations

import numpy as np


def compute_statutory_profit(
    in_force: np.ndarray,
    in_force_prev: np.ndarray,
    face: np.ndarray,
    premium: np.ndarray,
    reserve_bom: np.ndarray,
    reserve_eom: np.ndarray,
    q_x: np.ndarray,
    ci_sa: np.ndarray,
    ci_rate: np.ndarray,
    expense_inflation: float,
    earned_rate_ga: float,
    tax_rate: float,
    maintenance_per_policy: float,
    maintenance_pct_premium: float,
    commission_rate: float,
    t: int,
) -> np.ndarray:
    """Compute statutory book profit for Term Life model points.

    All arrays shape (n_model_points,). Returns bp array (n_model_points,).

    Args:
        in_force:            In-force count at period end (after decrements).
        in_force_prev:       In-force count at period start.
        face:                Face amount total per model point.
        premium:             Annual premium total per model point.
        reserve_bom:         Reserve at beginning of period (per model point).
        reserve_eom:         Reserve at end of period (per model point).
        q_x:                 Mortality rate for the period.
        ci_sa:               CI rider sum assured total per model point.
        ci_rate:             CI incidence rate for the period.
        expense_inflation:   Annual expense inflation factor.
        earned_rate_ga:      Earned rate on general account assets.
        tax_rate:            Corporate tax rate.
        maintenance_per_policy: Per-policy maintenance expense (year 0 dollars).
        maintenance_pct_premium: Maintenance as % of premium.
        commission_rate:     Commission as % of premium income.
        t:                   Projection year index (0-based).

    Returns:
        bp: Statutory book profit array of shape (n_model_points,).
    """
    n = len(in_force)

    # Per-policy face amount (distributes proportionally with in_force)
    face_per_policy = np.where(in_force_prev > 0, face / np.maximum(in_force_prev, 1e-9), 0.0)

    # Premium income: scales with beginning-of-period in_force
    premium_income = premium * np.where(in_force_prev > 0, in_force / np.maximum(in_force_prev, 1e-9), 0.0)

    # Death benefit outgo
    deaths = in_force_prev * q_x
    death_benefit = face_per_policy * deaths

    # CI benefit outgo (CI accelerated benefit)
    ci_claims = in_force_prev * ci_rate
    ci_benefit = np.where(ci_sa > 0, ci_sa / np.maximum(in_force_prev, 1e-9) * ci_claims, 0.0)

    # Commission
    commission = commission_rate * premium_income

    # Maintenance expense (inflated)
    inflation_factor = (1.0 + expense_inflation) ** t
    maint = (maintenance_per_policy * inflation_factor + maintenance_pct_premium * premium_income) * in_force
    maint_total = maint

    # Investment income on reserves (BOM reserve)
    investment_income = reserve_bom * earned_rate_ga

    # Reserve change: ΔReserve = eom_reserve - bom_reserve
    # BOM reserve for this period = last period EOM reserve scaled by in_force ratio
    delta_reserve = reserve_eom - reserve_bom

    # Pre-tax profit
    pre_tax = (premium_income
               - death_benefit
               - ci_benefit
               - commission
               - maint_total
               + investment_income
               - delta_reserve)

    # Tax (only on positive profits)
    tax = np.maximum(0.0, pre_tax * tax_rate)

    return pre_tax - tax
