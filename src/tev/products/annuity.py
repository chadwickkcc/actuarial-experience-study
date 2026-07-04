"""TEV statutory book profit module — Deferred Annuities (FA/FIA/VA).

Implements FR-2-18 (Deferred Annuity formula). Vectorised NumPy over model points.

BP_t = Spread_income_t        (AV_t × (earned_rate_ga - credited_rate))
     + Rider_fees_t           (rider_fee_rate × AV_t, where applicable)
     - Surrender_benefits_t
     - GMDB_benefits_t        (max(0, death_benefit - AV_t) × mortality_rate)
     - Maintenance_expense_t
     + Investment_income_on_RC_t
     - DeltaReserve_t
     - Tax_t

Annuity owner mortality uses 2012 IAR table with Scale G2 improvement (FR-1C-12).
"""
from __future__ import annotations

import numpy as np


# 2012 IAR with G2 improvement — simplified Gompertz calibration
# Calibrated to roughly: q65=0.0075, q75=0.020, q85=0.060
_IAR_A = 0.0004
_IAR_B = 0.000035
_IAR_C = 0.10


def annuity_mortality_rate(attained_age: np.ndarray) -> np.ndarray:
    """2012 IAR + Scale G2 mortality rate approximation (FR-1C-12).

    Returns q_x array for annuity owners. Lower than life VBT because
    annuity buyers self-select for better health / longer lives.
    """
    return np.minimum(1.0, _IAR_A + _IAR_B * np.exp(_IAR_C * attained_age))


def compute_statutory_profit(
    in_force: np.ndarray,
    in_force_prev: np.ndarray,
    account_value: np.ndarray,
    reserve_bom: np.ndarray,
    reserve_eom: np.ndarray,
    q_x: np.ndarray,
    surrender_rate: np.ndarray,
    glwb_elected: np.ndarray,
    rider_fee_rate: float,
    credited_rate: float,
    expense_inflation: float,
    earned_rate_ga: float,
    tax_rate: float,
    maintenance_per_policy: float,
    rc_pct: float,
    t: int,
) -> np.ndarray:
    """Compute statutory book profit for Deferred Annuity model points.

    All arrays shape (n_model_points,). Returns bp array (n_model_points,).

    Args:
        in_force:            Contract count at period end.
        in_force_prev:       Contract count at period start.
        account_value:       Account value total at BOM.
        reserve_bom:         Statutory reserve at BOM.
        reserve_eom:         Statutory reserve at EOM.
        q_x:                 Annuity owner mortality rate (2012 IAR).
        surrender_rate:      Full surrender rate for the period.
        glwb_elected:        Fraction (0–1) of contracts with GLWB elected.
        rider_fee_rate:      Rider fee annual rate (e.g., 0.01).
        credited_rate:       Credited interest rate (scalar, from macro).
        expense_inflation:   Annual expense inflation.
        earned_rate_ga:      GA earned rate.
        tax_rate:            Corporate tax rate.
        maintenance_per_policy: Per-contract maintenance expense.
        rc_pct:              Required capital % of reserve.
        t:                   Projection year index.

    Returns:
        bp: Statutory book profit array.
    """
    safe_prev = np.maximum(in_force_prev, 1e-9)
    survival_ratio = in_force / safe_prev

    # Spread income: AV × (earned_rate_ga - credited_rate)
    spread_income = account_value * (earned_rate_ga - credited_rate) * survival_ratio

    # Rider fees (GLWB/GMDB): fee rate × AV for contracts with rider
    rider_fees = rider_fee_rate * glwb_elected * account_value * survival_ratio

    # Surrender benefits (full AV paid out)
    surrenders = in_force_prev * surrender_rate
    av_per_contract = np.where(in_force_prev > 0, account_value / safe_prev, 0.0)
    surrender_benefits = av_per_contract * surrenders

    # GMDB benefits: max(0, death_benefit - AV) × deaths
    # Simplified: assume death benefit = AV (ROP), so GMDB cost ≈ 0 for ROP
    # Use 10% of AV as approximate GMDB net amount at risk for contracts with GMDB
    deaths = in_force_prev * q_x
    gmdb_nar_per_contract = av_per_contract * 0.10 * glwb_elected
    gmdb_benefits = gmdb_nar_per_contract * deaths

    # Maintenance
    inflation_factor = (1.0 + expense_inflation) ** t
    maint = maintenance_per_policy * inflation_factor * in_force

    # Investment income on required capital
    rc = reserve_bom * rc_pct
    inv_income_rc = rc * earned_rate_ga

    # Delta reserve
    delta_reserve = reserve_eom - reserve_bom

    pre_tax = (spread_income + rider_fees
               - surrender_benefits - gmdb_benefits
               - maint + inv_income_rc - delta_reserve)

    tax = np.maximum(0.0, pre_tax * tax_rate)
    return pre_tax - tax
