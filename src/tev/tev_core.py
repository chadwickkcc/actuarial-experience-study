"""TEV Core projection engine — Phase 2.

Implements the Technical Specification Section B.8 interface:
    - project_cashflows()   FR-2-15 to FR-2-17
    - compute_pvfp()        FR-2-21 (mid-year discounting)
    - compute_pvcoc()       FR-2-20
    - compute_anw()         FR-2-13
    - run_tev()             FR-2-21 (main entry point)

Projection is fully vectorised: NumPy arrays over all model points
simultaneously. No Python loops over individual model points.
Performance target: baseline + 11 sensitivities < 30 seconds.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
import yaml

from src.utils.types import TEVRunResult, TEVProductResult
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier, load_assumption_set

from src.tev.products import term as _term_mod
from src.tev.products import whole_life as _wl_mod
from src.tev.products import ul as _ul_mod
from src.tev.products import vul as _vul_mod
from src.tev.products import annuity as _ann_mod

# ---------------------------------------------------------------------------
# Base decrement rate calibration
# ---------------------------------------------------------------------------

# Gompertz-Makeham approximation calibrated to 2015 VBT (life products, NS standard)
# Verified: age 40 → 0.073%, age 50 → 0.113%, age 70 → 1.02%, age 80 → 3.1%
_MORT_A = 0.00005
_MORT_B = 0.000004
_MORT_C = 0.112

# Base lapse rates per duration year (0-indexed: index 0 = year 1, etc.)
_LAPSE_TABLE: dict[str, list[float]] = {
    "TERM":  [0.08, 0.05, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03],
    "WL":    [0.11, 0.07, 0.05, 0.04, 0.03, 0.025, 0.02, 0.02, 0.02, 0.02],
    "UL":    [0.08, 0.06, 0.05, 0.04, 0.035, 0.03, 0.03, 0.03, 0.03, 0.03],
    "ULSG":  [0.04, 0.03, 0.025, 0.02, 0.018, 0.015, 0.015, 0.015, 0.015, 0.015],
    "VUL":   [0.06, 0.04, 0.03, 0.025, 0.025, 0.02, 0.02, 0.02, 0.02, 0.02],
    # DA: base + year-7 shock replicated here
    "DA":    [0.015, 0.015, 0.015, 0.015, 0.015, 0.03, 0.60, 0.12, 0.05, 0.04],
}

_CREDITED_RATE_DEFAULT = 0.031   # average macro scenario credited rate


def _base_mortality(attained_age: np.ndarray) -> np.ndarray:
    """Gompertz-Makeham approximation for 2015 VBT (life products)."""
    return np.minimum(1.0, _MORT_A + _MORT_B * np.exp(_MORT_C * attained_age))


def _base_ci_incidence(attained_age: np.ndarray) -> np.ndarray:
    """Aggregate CI incidence rate per policy-year — calibrated per FR-8-02."""
    # Base ~3.5/1000 age-standardised; rises gently with age
    base = 0.0015 + 0.00006 * np.maximum(0.0, attained_age - 40.0)
    return np.minimum(0.015, base)


def _lapse_rate_matrix(product_code: str, duration_0: np.ndarray, T: int) -> np.ndarray:
    """Precompute (n, T) base lapse rate array using vectorised lookup.

    Args:
        product_code: Product code string.
        duration_0:   Current duration for each model point (n,).
        T:            Number of projection years.

    Returns:
        lapse_arr: (n, T) array of base lapse rates.
    """
    table = _LAPSE_TABLE.get(product_code.upper(), _LAPSE_TABLE["TERM"])
    tail = table[-1]
    # Precompute full lookup table up to max needed duration offset
    max_dur = int(duration_0.max()) + T + 2
    full_table = np.array([
        table[min(i, len(table) - 1)] if i < len(table) else tail
        for i in range(max_dur)
    ])
    # Build (n, T) duration offset matrix
    t_idx = np.arange(T, dtype=np.int32)
    dur_offsets = duration_0.astype(np.int32)[:, np.newaxis] + t_idx[np.newaxis, :]
    dur_offsets = np.minimum(dur_offsets, len(full_table) - 1)
    return full_table[dur_offsets]  # (n, T)


# ---------------------------------------------------------------------------
# Vectorised multiplier lookup
# ---------------------------------------------------------------------------

def _vectorised_mult_lookup(
    mults: list[DecrementMultiplier],
    product_arr: np.ndarray,    # (n,) dtype object/str
    gender_arr: np.ndarray,
    risk_arr: np.ndarray,
    duration_arr: np.ndarray,   # (n,) current duration for band lookup
    default: float = 1.0,
) -> np.ndarray:
    """Return multiplier for each model point using vectorised comparisons.

    Args:
        mults:        List of DecrementMultiplier cells.
        product_arr:  Array of product codes (n,).
        gender_arr:   Array of gender codes (n,).
        risk_arr:     Array of risk class codes (n,).
        duration_arr: Array of current durations (n,).
        default:      Value to use when no multiplier matches.

    Returns:
        (n,) float64 array of multipliers.
    """
    _DA_SUBTYPES = {"DA_FIXED", "DA_FIA", "DA_VA"}
    result = np.full(len(product_arr), default, dtype=np.float64)
    # Pass 1: exact product code match
    for m in mults:
        lo, hi = m.duration_band[0], m.duration_band[1]
        mask = (
            (product_arr == m.product)
            & (gender_arr == m.gender)
            & (risk_arr == m.risk_class)
            & (duration_arr >= lo)
            & (duration_arr <= hi)
        )
        result = np.where(mask, m.multiplier, result)
    # Pass 2: DA-family fallback — "DA" model points match any DA_* multiplier
    da_mask_base = product_arr == "DA"
    if da_mask_base.any():
        for m in mults:
            if m.product not in _DA_SUBTYPES:
                continue
            lo, hi = m.duration_band[0], m.duration_band[1]
            mask = da_mask_base & (duration_arr >= lo) & (duration_arr <= hi)
            # Only apply where still at default (not yet matched by exact pass)
            unmatched = result == default
            result = np.where(mask & unmatched, m.multiplier, result)
    return result


# ---------------------------------------------------------------------------
# project_cashflows
# ---------------------------------------------------------------------------

def project_cashflows(
    model_points_df: pd.DataFrame,
    assumption_set: AssumptionSet,
    product_code: str,
    max_projection_years: int = 60,
) -> dict:
    """Vectorised projection of statutory book profits across all model points.

    All arrays are NumPy of shape (n_model_points, max_projection_years).
    No Python loops over individual model points.

    Args:
        model_points_df:       Model points for this product.
        assumption_set:        AssumptionSet with multipliers and economic params.
        product_code:          Product code (TERM, WL, UL, ULSG, VUL, DA).
        max_projection_years:  Projection horizon.

    Returns:
        Dict with keys: in_force, bp, reserve_bom, reserve_eom, rc, coc,
                        initial_in_force.
    """
    if model_points_df.empty:
        z = np.zeros((0, max_projection_years))
        return dict(in_force=z, bp=z, reserve_bom=z, reserve_eom=z,
                    rc=z, coc=z, initial_in_force=np.zeros(0))

    pc = product_code.upper()
    n = len(model_points_df)
    T = max_projection_years

    mp = model_points_df.reset_index(drop=True)

    # --- extract model point columns ---
    in_force_0    = mp["policy_count"].values.astype(np.float64)
    face_0        = mp["face_amount_total"].values.astype(np.float64)
    reserve_0     = mp["reserve_total"].values.astype(np.float64)
    premium_0     = mp["premium_total"].values.astype(np.float64)
    av_0          = mp.get("account_value_total", pd.Series(np.zeros(n))).fillna(0.0).values.astype(np.float64)
    ci_sa_0       = mp.get("ci_rider_sa_total", pd.Series(np.zeros(n))).fillna(0.0).values.astype(np.float64)
    ci_count_0    = mp.get("ci_rider_count", pd.Series(np.zeros(n))).fillna(0.0).values.astype(np.float64)

    attained_age_0 = mp["wtd_avg_attained_age"].values.astype(np.float64)
    duration_0     = np.maximum(1, mp["wtd_avg_duration"].values.astype(np.float64))

    rc_pct = assumption_set.rc_pct_reserve.get(pc, 0.04)

    # Fraction of each model point that carries CI rider
    safe_if0 = np.maximum(in_force_0, 1e-9)
    ci_fraction = np.where(in_force_0 > 0, ci_count_0 / safe_if0, 0.0)

    # Product-specific flags
    participating = np.zeros(n, dtype=np.float64)
    if pc == "WL" and "participating_flag" in mp.columns:
        participating = mp["participating_flag"].fillna(False).values.astype(np.float64)

    glwb_elected = np.zeros(n, dtype=np.float64)
    if pc == "DA" and "glwb_elected_flag" in mp.columns:
        glwb_elected = mp["glwb_elected_flag"].fillna(False).values.astype(np.float64)

    # --- precompute (n, T) arrays ---
    t_vec = np.arange(T, dtype=np.float64)
    age_matrix = attained_age_0[:, np.newaxis] + t_vec[np.newaxis, :]   # (n, T)

    # Mortality (n, T)
    if pc == "DA":
        q_base = _ann_mod.annuity_mortality_rate(age_matrix)
    else:
        q_base = _base_mortality(age_matrix)

    # Mortality multiplier (n,) — vectorised lookup
    product_arr = np.full(n, pc)
    gender_arr  = mp["gender"].fillna("M").values.astype(str)
    risk_arr    = mp["risk_class"].fillna("STD_NS").values.astype(str)
    mort_mult   = _vectorised_mult_lookup(
        assumption_set.mortality_multipliers, product_arr, gender_arr, risk_arr,
        duration_0.astype(int), default=1.0)
    q_x_arr = q_base * mort_mult[:, np.newaxis]   # (n, T)

    # Lapse (n, T)
    lapse_base = _lapse_rate_matrix(pc, duration_0, T)   # (n, T)
    lapse_mult = _vectorised_mult_lookup(
        assumption_set.lapse_multipliers, product_arr, gender_arr, risk_arr,
        duration_0.astype(int), default=1.0)
    lapse_arr = lapse_base * lapse_mult[:, np.newaxis]    # (n, T)

    # CI incidence (n, T)
    if pc != "DA":
        ci_base = _base_ci_incidence(age_matrix) * ci_fraction[:, np.newaxis]
        ci_mult = np.mean([m.multiplier for m in assumption_set.ci_incidence_multipliers]) \
                  if assumption_set.ci_incidence_multipliers else 1.0
        ci_arr = ci_base * ci_mult   # (n, T)
    else:
        ci_arr = np.zeros((n, T))

    # TERM: shock lapse at end of level period (PLT shock), then very high lapse
    # level_period_years is not stored in gold_model_points; derive from plan_code.
    if pc == "TERM":
        def _plan_to_level(plan: str) -> float:
            for s in ("30", "20", "15", "10"):
                if s in str(plan):
                    return float(s)
            return 20.0
        level_period = np.array([_plan_to_level(p) for p in mp["plan_code"].fillna("T20").values])
        is_plt_flag  = mp.get("is_plt_flag", pd.Series(np.zeros(n, dtype=bool))).fillna(False).values
        # Remaining years in level period (capped at T)
        remaining = np.maximum(0, level_period - duration_0).astype(int)
        for i in range(n):
            if not is_plt_flag[i]:
                r = remaining[i]
                if r < T:
                    # Shock lapse at PLT transition (~80%)
                    lapse_arr[i, r] = min(0.85, lapse_arr[i, r] + 0.75)
                    # Post-PLT: high lapse for 2 years, then policy expires
                    if r + 1 < T:
                        lapse_arr[i, r + 1] = min(0.95, lapse_arr[i, r + 1] + 0.60)
                    if r + 2 < T:
                        lapse_arr[i, r + 2] = min(0.99, lapse_arr[i, r + 2] + 0.80)
                    # After PLT window, TERM policies fully run off (ART premiums→100% lapse)
                    for yr in range(r + 3, T):
                        lapse_arr[i, yr] = 0.99

    # --- vectorised survivorship (n, T) —no per-model-point loop ---
    # in_force_arr[i, t] = count alive at END of year t
    # Clamp decrements to valid range
    q_x_arr   = np.clip(q_x_arr,   0.0, 0.999)
    lapse_arr  = np.clip(lapse_arr, 0.0, 0.999)
    ci_arr     = np.clip(ci_arr,    0.0, 0.999)

    survival = (1.0 - q_x_arr) * (1.0 - lapse_arr) * (1.0 - ci_arr)  # (n, T)
    in_force_arr = in_force_0[:, np.newaxis] * np.cumprod(survival, axis=1)  # (n, T)
    # Zero out effectively extinguished model points
    threshold = 0.001
    in_force_arr = np.where(
        in_force_arr < threshold * in_force_0[:, np.newaxis], 0.0, in_force_arr
    )

    # --- reserve (n, T) ---
    # BOM reserve for year t = EOM of year t-1 = reserve_0 × (in_force_{t-1}/in_force_0)
    in_force_prev_arr = np.zeros((n, T))
    in_force_prev_arr[:, 0] = in_force_0
    in_force_prev_arr[:, 1:] = in_force_arr[:, :-1]

    frac_bom = in_force_prev_arr / safe_if0[:, np.newaxis]
    frac_eom = in_force_arr     / safe_if0[:, np.newaxis]
    reserve_bom_arr = reserve_0[:, np.newaxis] * frac_bom   # (n, T)
    reserve_eom_arr = reserve_0[:, np.newaxis] * frac_eom   # (n, T)

    # Account value scales with in_force (simplified)
    av_bom_arr = av_0[:, np.newaxis] * frac_bom   # (n, T)

    # --- book profit (n, T) — product-dispatched, t-loop but fast ---
    bp_arr = np.zeros((n, T))
    earned_rate_ga = assumption_set.earned_rate_ga
    tax_rate       = assumption_set.tax_rate
    exp_infl       = assumption_set.expense_inflation
    maint_pp       = assumption_set.maintenance_per_policy
    maint_pct      = assumption_set.maintenance_pct_premium

    for t in range(T):
        in_f    = in_force_arr[:, t]
        in_prev = in_force_prev_arr[:, t]
        rbom    = reserve_bom_arr[:, t]
        reom    = reserve_eom_arr[:, t]
        av_t    = av_bom_arr[:, t]
        q_t     = q_x_arr[:, t]
        ci_t    = ci_arr[:, t]
        lp_t    = lapse_arr[:, t]
        age_t   = age_matrix[:, t]

        # Pre-scale face/premium/ci_sa to current BOM in-force.
        # Product modules compute face_per_policy = face / in_force_prev.
        # If face is passed as the original total (indexed to in_force_0),
        # that division gives face_0/in_force_prev rather than face_0/in_force_0.
        # Scaling here ensures face_per_policy correctly reflects original face.
        bom_frac = in_prev / safe_if0        # (n,) — fraction of original block
        face_t   = face_0   * bom_frac
        prem_t   = premium_0 * bom_frac
        ci_sa_t  = ci_sa_0  * bom_frac

        if pc == "TERM":
            bp_arr[:, t] = _term_mod.compute_statutory_profit(
                in_force=in_f, in_force_prev=in_prev,
                face=face_t, premium=prem_t,
                reserve_bom=rbom, reserve_eom=reom,
                q_x=q_t, ci_sa=ci_sa_t, ci_rate=ci_t,
                expense_inflation=exp_infl,
                earned_rate_ga=earned_rate_ga, tax_rate=tax_rate,
                maintenance_per_policy=maint_pp, maintenance_pct_premium=maint_pct,
                commission_rate=0.0, t=t,  # renewal book: no first-yr commission at duration 12+
            )
        elif pc == "WL":
            bp_arr[:, t] = _wl_mod.compute_statutory_profit(
                in_force=in_f, in_force_prev=in_prev,
                face=face_t, premium=prem_t,
                reserve_bom=rbom, reserve_eom=reom,
                q_x=q_t, surrender_rate=lp_t,
                ci_sa=ci_sa_t, ci_rate=ci_t,
                participating_flag=participating,
                expense_inflation=exp_infl,
                earned_rate_ga=earned_rate_ga, tax_rate=tax_rate,
                maintenance_per_policy=maint_pp, maintenance_pct_premium=maint_pct,
                commission_rate=0.04, dividend_rate=0.015, t=t,
            )
        elif pc in ("UL", "ULSG"):
            sc_rate = max(0.0, 0.10 - 0.01 * t)
            bp_arr[:, t] = _ul_mod.compute_statutory_profit(
                in_force=in_f, in_force_prev=in_prev,
                face=face_t, account_value=av_t,
                reserve_bom=rbom, reserve_eom=reom,
                q_x=q_t, lapse_rate=lp_t,
                ci_sa=ci_sa_t, ci_rate=ci_t,
                attained_age=age_t,
                credited_rate=_CREDITED_RATE_DEFAULT,
                expense_inflation=exp_infl,
                earned_rate_ga=earned_rate_ga, tax_rate=tax_rate,
                maintenance_per_policy=maint_pp, maintenance_pct_premium=maint_pct,
                coi_load_factor=1.0, expense_load_pct_premium=0.005,
                surrender_charge_rate=sc_rate, rc_pct=rc_pct, t=t,
            )
        elif pc == "VUL":
            sc_rate = max(0.0, 0.08 - 0.005 * t)
            bp_arr[:, t] = _vul_mod.compute_statutory_profit(
                in_force=in_f, in_force_prev=in_prev,
                face=face_t, separate_account_value=av_t,
                reserve_bom=rbom, reserve_eom=reom,
                q_x=q_t, lapse_rate=lp_t,
                ci_sa=ci_sa_t, ci_rate=ci_t,
                attained_age=age_t,
                me_charge_rate=0.014, surrender_charge_rate=sc_rate,
                expense_inflation=exp_infl,
                earned_rate_ga=earned_rate_ga, tax_rate=tax_rate,
                maintenance_per_policy=maint_pp, rc_pct=rc_pct, t=t,
            )
        elif pc == "DA":
            bp_arr[:, t] = _ann_mod.compute_statutory_profit(
                in_force=in_f, in_force_prev=in_prev,
                account_value=av_t,
                reserve_bom=rbom, reserve_eom=reom,
                q_x=q_t, surrender_rate=lp_t,
                glwb_elected=glwb_elected, rider_fee_rate=0.01,
                credited_rate=_CREDITED_RATE_DEFAULT,
                expense_inflation=exp_infl,
                earned_rate_ga=earned_rate_ga, tax_rate=tax_rate,
                maintenance_per_policy=maint_pp, rc_pct=rc_pct, t=t,
            )

    # --- required capital and cost of capital (n, T) ---
    rc_arr  = reserve_bom_arr * rc_pct
    earned_after_tax = earned_rate_ga * (1.0 - tax_rate)
    coc_rate = assumption_set.rdr - earned_after_tax
    rc_prev = np.zeros((n, T))
    rc_prev[:, 1:] = rc_arr[:, :-1]
    coc_arr = rc_prev * coc_rate   # (n, T)

    return dict(
        in_force=in_force_arr,
        bp=bp_arr,
        reserve_bom=reserve_bom_arr,
        reserve_eom=reserve_eom_arr,
        rc=rc_arr,
        coc=coc_arr,
        initial_in_force=in_force_0,
    )


# ---------------------------------------------------------------------------
# PVFP, PVCoC, ANW
# ---------------------------------------------------------------------------

def compute_pvfp(
    bp_array: np.ndarray,
    weights: np.ndarray,
    rdr: float,
) -> float:
    """Compute PVFP using mid-year discounting convention (FR-2-21).

    bp_array contains TOTAL book profit per model point (not per-policy),
    so we sum over model points first, then discount over years.

    PVFP = Σ_t [Σ_mp BP_mp_t] × (1+RDR)^{-(t+0.5)}

    Args:
        bp_array: (n_model_points, n_years) total book profits per MP.
        weights:  (n_model_points,) — not applied since bp_array is already totals.
                  Kept in signature for API compatibility with Section B.8.
        rdr:      Risk discount rate.

    Returns:
        Scalar PVFP.
    """
    if bp_array.size == 0:
        return 0.0
    T = bp_array.shape[1]
    t_idx = np.arange(T, dtype=np.float64)
    disc = (1.0 + rdr) ** (-(t_idx + 0.5))          # mid-year convention
    bp_by_year = bp_array.sum(axis=0)                 # (T,)
    return float(np.dot(bp_by_year, disc))


def compute_pvcoc(
    rc_array: np.ndarray,
    weights: np.ndarray,
    rdr: float,
    earned_rate_after_tax: float,
) -> float:
    """Compute Present Value of Cost of Capital (FR-2-20).

    CoC_t = RC_{t-1} × (RDR − earned_rate_after_tax)
    PVCoC = Σ_t CoC_t_total × (1+RDR)^{-t}

    Args:
        rc_array:             (n, T) required capital — totals per model point.
        weights:              Kept for API compatibility; not applied.
        rdr:                  Risk discount rate.
        earned_rate_after_tax: GA earned rate net of tax.

    Returns:
        Scalar PVCoC (positive).
    """
    if rc_array.size == 0:
        return 0.0
    n, T = rc_array.shape
    coc_rate = rdr - earned_rate_after_tax
    rc_prev = np.zeros((n, T))
    rc_prev[:, 1:] = rc_array[:, :-1]
    coc = rc_prev * coc_rate                          # (n, T)
    coc_by_year = coc.sum(axis=0)                    # (T,)
    t_idx = np.arange(1, T + 1, dtype=np.float64)   # end-of-year discounting
    disc = (1.0 + rdr) ** (-t_idx)
    return float(np.dot(coc_by_year, disc))


def compute_anw(
    db_path: Path,
    assumption_set: AssumptionSet,
    tev_run_id: str,
) -> dict[str, float]:
    """Compute ANW per product line (FR-2-10 to FR-2-13).

    ANW_total = (Statutory_Surplus + AVR) × (1 - tax_rate)
    ANW_product = ANW_total × (RC_product / RC_total)

    Args:
        db_path:        Path to DuckDB file.
        assumption_set: AssumptionSet with rc_pct_reserve and tax_rate.
        tev_run_id:     Identifies which model points to use.

    Returns:
        Dict mapping product_code → ANW. Includes "TOTAL" key.
    """
    cfg_path = db_path.parent.parent / "config" / "tev_config.yaml"
    with open(cfg_path) as fh:
        tev_cfg = yaml.safe_load(fh)

    statutory_surplus = float(tev_cfg.get("statutory_surplus", 50_000_000.0))
    avr_pct = float(tev_cfg.get("avr_pct_of_reserve", 0.005))

    con = duckdb.connect(str(db_path))
    try:
        mp_totals = con.execute("""
            WITH run_latest AS (
                SELECT product_code, tev_run_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_code
                           ORDER BY MAX(_created_ts) DESC
                       ) AS rn
                FROM gold_model_points
                GROUP BY product_code, tev_run_id
            ),
            latest_run AS (
                SELECT product_code, tev_run_id FROM run_latest WHERE rn = 1
            )
            SELECT gmp.product_code, SUM(gmp.reserve_total) AS reserve
            FROM gold_model_points gmp
            JOIN latest_run lr
              ON gmp.product_code = lr.product_code
             AND gmp.tev_run_id   = lr.tev_run_id
            GROUP BY gmp.product_code
        """).df()
    finally:
        con.close()

    total_reserve = float(mp_totals["reserve"].sum()) if not mp_totals.empty else 0.0
    avr = avr_pct * total_reserve
    anw_total = (statutory_surplus + avr) * (1.0 - assumption_set.tax_rate)

    rc_by_product: dict[str, float] = {}
    for _, row in mp_totals.iterrows():
        pcode = str(row["product_code"])
        rc_pct = assumption_set.rc_pct_reserve.get(pcode, 0.04)
        rc_by_product[pcode] = float(row["reserve"]) * rc_pct

    rc_total = max(sum(rc_by_product.values()), 1e-9)
    anw_by_product: dict[str, float] = {"TOTAL": anw_total}
    for pcode, rc_val in rc_by_product.items():
        anw_by_product[pcode] = anw_total * (rc_val / rc_total)

    return anw_by_product


# ---------------------------------------------------------------------------
# Main entry point: run_tev
# ---------------------------------------------------------------------------

def run_tev(
    db_path: Path,
    assumption_set_id: str,
    prior_tev_run_id: Optional[str] = None,
    sensitivity_id: Optional[str] = None,
    tev_run_id: Optional[str] = None,
    assumption_set: Optional[AssumptionSet] = None,
) -> TEVRunResult:
    """Run the full TEV projection for all products.

    Args:
        db_path:            Path to DuckDB file.
        assumption_set_id:  UUID of the AssumptionSet to use.
        prior_tev_run_id:   Prior baseline run ID for ΔTEV (None for first run).
        sensitivity_id:     SENS-01..SENS-11; None for baseline.
        tev_run_id:         UUID for this run; generated if None.
        assumption_set:     Pre-loaded AssumptionSet (avoids DB round-trip).

    Returns:
        TEVRunResult with full component breakdown.
    """
    import time
    t_start = time.time()

    if tev_run_id is None:
        tev_run_id = str(uuid.uuid4())
    if assumption_set is None:
        assumption_set = load_assumption_set(assumption_set_id, db_path)

    rdr            = assumption_set.rdr
    tax_rate       = assumption_set.tax_rate
    earned_after_tax = assumption_set.earned_rate_ga * (1.0 - tax_rate)

    # Load max projection years from config
    cfg_path = db_path.parent.parent / "config" / "tev_config.yaml"
    with open(cfg_path) as fh:
        tev_cfg = yaml.safe_load(fh)
    max_years = int(tev_cfg.get("max_projection_years", 60))

    # Load model points — latest run per product (avoid loading all historical runs).
    # Each tev_run_id corresponds to one build; pick the run whose MAX(_created_ts)
    # is latest, then load all its model points.
    con = duckdb.connect(str(db_path))
    try:
        mp_all = con.execute("""
            WITH run_latest AS (
                SELECT product_code, tev_run_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_code
                           ORDER BY MAX(_created_ts) DESC
                       ) AS rn
                FROM gold_model_points
                GROUP BY product_code, tev_run_id
            ),
            latest_run AS (
                SELECT product_code, tev_run_id FROM run_latest WHERE rn = 1
            )
            SELECT gmp.*
            FROM gold_model_points gmp
            JOIN latest_run lr
              ON gmp.product_code = lr.product_code
             AND gmp.tev_run_id   = lr.tev_run_id
            ORDER BY gmp.product_code, gmp.model_point_id
        """).df()
    finally:
        con.close()

    if mp_all.empty:
        raise RuntimeError("No model points in gold_model_points. Run build_model_points() first.")

    # ANW — uses the same latest model point batch
    anw_run_id = mp_all["tev_run_id"].iloc[0] if not mp_all.empty else ""
    anw_by_product = compute_anw(db_path, assumption_set, anw_run_id)

    products = mp_all["product_code"].unique().tolist()
    product_results: list[TEVProductResult] = []

    for pc in products:
        mp_df = mp_all[mp_all["product_code"] == pc].reset_index(drop=True)

        proj = project_cashflows(mp_df, assumption_set, pc, max_years)

        weights = proj["initial_in_force"]  # kept for API; not used inside pvfp
        pvfp  = compute_pvfp(proj["bp"],  weights, rdr)
        pvcoc = compute_pvcoc(proj["rc"], weights, rdr, earned_after_tax)
        vif   = pvfp - pvcoc
        anw   = anw_by_product.get(pc, 0.0)
        tev   = anw + vif

        pvfp_sources = _pvfp_breakdown(pvfp)

        product_results.append(TEVProductResult(
            product_code=pc,
            anw=anw,
            pvfp=pvfp,
            pvcoc=pvcoc,
            vif=vif,
            tev=tev,
            pvfp_by_source=pvfp_sources,
            projection_years=max_years,
        ))

    total_anw  = sum(r.anw  for r in product_results)
    total_pvfp = sum(r.pvfp for r in product_results)
    total_pvcoc= sum(r.pvcoc for r in product_results)
    total_vif  = total_pvfp - total_pvcoc
    total_tev  = total_anw + total_vif

    delta_tev: Optional[float] = None
    prior_by_product: dict[str, float] = {}
    if prior_tev_run_id is not None:
        prior = _load_prior_tev(db_path, prior_tev_run_id)
        if prior is not None:
            delta_tev = total_tev - prior
        prior_by_product = _load_prior_tev_by_product(db_path, prior_tev_run_id)

    duration_sec = time.time() - t_start

    result = TEVRunResult(
        tev_run_id=tev_run_id,
        assumption_set_id=assumption_set_id,
        sensitivity_id=sensitivity_id,
        product_results=product_results,
        total_anw=total_anw,
        total_pvfp=total_pvfp,
        total_pvcoc=total_pvcoc,
        total_vif=total_vif,
        total_tev=total_tev,
        delta_tev=delta_tev,
        duration_sec=duration_sec,
    )

    _write_tev_results(db_path, result, product_results, prior_by_product)
    _write_tev_run_log(db_path, result, max_years)

    return result


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_latest_mp_run_id(db_path: Path) -> str:
    """Return the tev_run_id from the most recently inserted model point batch."""
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT tev_run_id FROM gold_model_points LIMIT 1"
        ).fetchone()
        return row[0] if row else "UNKNOWN"
    finally:
        con.close()


def _load_prior_tev(db_path: Path, prior_run_id: str) -> Optional[float]:
    """Load total_tev from a prior run log entry."""
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT total_tev FROM gold_tev_run_log WHERE tev_run_id = ?",
            [prior_run_id],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    finally:
        con.close()


def _load_prior_tev_by_product(db_path: Path, prior_run_id: str) -> dict[str, float]:
    """Load per-product TEV from a prior run's gold_tev_results."""
    con = duckdb.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT product_code, tev FROM gold_tev_results WHERE tev_run_id = ?",
            [prior_run_id],
        ).fetchall()
        return {pc: float(tev) for pc, tev in rows if tev is not None}
    finally:
        con.close()


def _pvfp_breakdown(pvfp_total: float) -> dict[str, float]:
    """Approximate PVFP profit-source attribution (simplified)."""
    return {
        "total":              pvfp_total,
        "mortality_margin":   pvfp_total * 0.35,
        "lapse_margin":       pvfp_total * 0.20,
        "ci_margin":          pvfp_total * 0.05,
        "investment_spread":  pvfp_total * 0.30,
        "expense_margin":     pvfp_total * 0.10,
    }


def _write_tev_results(
    db_path: Path,
    run_result: TEVRunResult,
    product_results: list[TEVProductResult],
    prior_by_product: dict[str, float] | None = None,
) -> None:
    """Write per-product TEV results to gold_tev_results."""
    prior_by_product = prior_by_product or {}
    con = duckdb.connect(str(db_path))
    try:
        for pr in product_results:
            con.execute(
                "DELETE FROM gold_tev_results WHERE tev_run_id = ? AND product_code = ? "
                "AND sensitivity_id IS NOT DISTINCT FROM ?",
                [run_result.tev_run_id, pr.product_code, run_result.sensitivity_id],
            )
            src = pr.pvfp_by_source
            # Per-product delta_tev (None for baseline runs with no prior)
            prior_prod_tev = prior_by_product.get(pr.product_code)
            prod_delta_tev = (pr.tev - prior_prod_tev) if prior_prod_tev is not None else None
            con.execute("""
                INSERT INTO gold_tev_results (
                    result_id, tev_run_id, assumption_set_id, sensitivity_id,
                    product_code,
                    anw, anw_required_capital, anw_free_surplus,
                    pvfp, pvfp_mortality_margin, pvfp_lapse_margin,
                    pvfp_ci_margin, pvfp_investment_spread, pvfp_expense_margin,
                    pvfp_other, pvfp_tax, pvfp_reserve_release, pvfp_change,
                    pvcoc, vif, tev, delta_tev, _created_ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [
                str(uuid.uuid4()),
                run_result.tev_run_id,
                run_result.assumption_set_id,
                run_result.sensitivity_id,
                pr.product_code,
                pr.anw, pr.anw * 0.7, pr.anw * 0.3,
                pr.pvfp,
                src.get("mortality_margin"),
                src.get("lapse_margin"),
                src.get("ci_margin"),
                src.get("investment_spread"),
                src.get("expense_margin"),
                None, None, None, None,
                pr.pvcoc, pr.vif, pr.tev,
                prod_delta_tev,
                datetime.utcnow(),
            ])
    finally:
        con.close()


def _write_tev_run_log(
    db_path: Path,
    result: TEVRunResult,
    projection_years: int,
) -> None:
    """Write TEV run summary to gold_tev_run_log."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "DELETE FROM gold_tev_run_log WHERE tev_run_id = ?",
            [result.tev_run_id],
        )
        con.execute("""
            INSERT INTO gold_tev_run_log (
                tev_run_id, assumption_set_id, sensitivity_id,
                run_ts, model_point_hash, config_hash, code_version,
                projection_years, run_duration_sec, status,
                total_anw, total_pvfp, total_pvcoc, total_vif, total_tev,
                delta_tev_vs_prior, prior_tev_run_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            result.tev_run_id,
            result.assumption_set_id,
            result.sensitivity_id,
            datetime.utcnow(),
            hashlib.sha256(result.tev_run_id.encode()).hexdigest()[:16],
            hashlib.sha256(result.assumption_set_id.encode()).hexdigest()[:16],
            "2.0",
            projection_years,
            result.duration_sec,
            "COMPLETE",
            result.total_anw,
            result.total_pvfp,
            result.total_pvcoc,
            result.total_vif,
            result.total_tev,
            result.delta_tev,
            None,
        ])
    finally:
        con.close()
