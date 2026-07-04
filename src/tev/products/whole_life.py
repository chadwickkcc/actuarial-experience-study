"""TEV statutory book profit module — Whole Life.

Implements FR-2-18 (Whole Life formula). Vectorised NumPy over model points.

BP_t = Premium_income_t
     - Death_benefit_t
     - CI_benefit_t          (if CI rider)
     - Surrender_benefit_t   (CSV_t × surrender_rate_t × in_force_t)
     - Dividend_t            (dividend_rate × reserve_t × in_force_t, par only)
     - Commission_t
     - Maintenance_expense_t
     + Investment_income_t   (reserve_t × earned_rate_ga)
     - DeltaReserve_t
     - Tax_t
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
    surrender_rate: np.ndarray,
    ci_sa: np.ndarray,
    ci_rate: np.ndarray,
    participating_flag: np.ndarray,
    expense_inflation: float,
    earned_rate_ga: float,
    tax_rate: float,
    maintenance_per_policy: float,
    maintenance_pct_premium: float,
    commission_rate: float,
    dividend_rate: float,
    t: int,
) -> np.ndarray:
    """Compute statutory book profit for Whole Life model points.

    All arrays shape (n_model_points,). Returns bp array (n_model_points,).

    Args:
        in_force:            In-force count at period end.
        in_force_prev:       In-force count at period start.
        face:                Face amount total per model point.
        premium:             Annual premium total per model point.
        reserve_bom:         Reserve at beginning of period.
        reserve_eom:         Reserve at end of period.
        q_x:                 Mortality rate for the period.
        surrender_rate:      Surrender (lapse) rate for the period.
        ci_sa:               CI rider sum assured total.
        ci_rate:             CI incidence rate for the period.
        participating_flag:  1.0 if participating WL, 0.0 otherwise.
        expense_inflation:   Annual expense inflation.
        earned_rate_ga:      Earned rate on GA assets.
        tax_rate:            Corporate tax rate.
        maintenance_per_policy: Per-policy maintenance expense.
        maintenance_pct_premium: Maintenance % of premium.
        commission_rate:     Commission % of premium income.
        dividend_rate:       Dividend interest rate (for par WL).
        t:                   Projection year index (0-based).

    Returns:
        bp: Statutory book profit array.
    """
    safe_prev = np.maximum(in_force_prev, 1e-9)
    face_per_policy = np.where(in_force_prev > 0, face / safe_prev, 0.0)

    # Premium income scaled by survival
    premium_income = premium * (in_force / safe_prev)

    # Death benefits
    deaths = in_force_prev * q_x
    death_benefit = face_per_policy * deaths

    # CI benefit
    ci_claims = in_force_prev * ci_rate
    ci_sa_per_policy = np.where(in_force_prev > 0, ci_sa / safe_prev, 0.0)
    ci_benefit = ci_sa_per_policy * ci_claims

    # Surrender benefit: approximate CSV as fraction of reserve per policy
    # CSV ≈ reserve_bom / in_force_prev (per policy CSV)
    csv_per_policy = np.where(in_force_prev > 0, reserve_bom / safe_prev, 0.0)
    surrenders = in_force_prev * surrender_rate
    surrender_benefit = csv_per_policy * surrenders

    # Dividend (par WL only)
    dividend = participating_flag * dividend_rate * reserve_bom

    # Commission
    commission = commission_rate * premium_income

    # Maintenance expense
    inflation_factor = (1.0 + expense_inflation) ** t
    maint = (maintenance_per_policy * inflation_factor + maintenance_pct_premium * premium_income) * in_force

    # Investment income
    investment_income = reserve_bom * earned_rate_ga

    # Delta reserve
    delta_reserve = reserve_eom - reserve_bom

    # Pre-tax profit
    pre_tax = (premium_income
               - death_benefit
               - ci_benefit
               - surrender_benefit
               - dividend
               - commission
               - maint
               + investment_income
               - delta_reserve)

    tax = np.maximum(0.0, pre_tax * tax_rate)
    return pre_tax - tax
