"""Variable Universal Life (VUL) synthetic data generator.

Produces vul_policies.csv: 800 policies per experience_study_technical_spec_v1.2.md Section C.3
and experience_study_requirements_spec_v2.1.md Section 8.3.

Key features:
- GBM-based separate account values using MACRO_SCENARIO equity returns
- Withdrawal persistence state (FR-1C-04): once active, stays active
- CI rider for 15% of policies
- Moneyness multiplier for lapse: min(2.0, max(0.5, 1/fund_value_to_spec_amount_ratio))
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .common import (
    CI_BASE_INCIDENCE_PER_1000,
    CI_ILLNESS_CODES,
    CI_ILLNESS_WEIGHTS,
    MACRO_SCENARIO,
    US_STATES,
    _STATE_WEIGHTS,
    attained_age_float,
    ci_age_factor,
    get_lapse_multiplier,
    issue_age_to_dob,
    pert_sample,
    random_date_between,
)

N_VUL = 800

RISK_CLASSES = ["SUPER_PREF", "PREF_NS", "STD_NS", "PREF_SM", "STD_SM"]
RISK_PROBS   = [0.12, 0.28, 0.45, 0.07, 0.08]

CHANNELS = ["CAREER", "INDEPENDENT", "DIRECT", "BANK"]
CHANNEL_PROBS = [0.30, 0.45, 0.10, 0.15]

PREMIUM_MODES = ["ANNUAL", "SEMI", "QUARTERLY", "MONTHLY"]
PREMIUM_MODE_PROBS = [0.40, 0.15, 0.20, 0.25]

# Base annual lapse rates by policy year (requirements spec Section 8.3)
VUL_LAPSE = [0.06, 0.04, 0.03, 0.025, 0.025, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02]

# Sub-account fund definitions (equity_allocation drives equity_return exposure)
FUND_DEFS = [
    {"fund_id": "EQ_LARGE_CAP",   "equity": 1.0, "base_return": 0.07},
    {"fund_id": "EQ_INTL",        "equity": 1.0, "base_return": 0.065},
    {"fund_id": "BALANCED",       "equity": 0.6, "base_return": 0.055},
    {"fund_id": "BOND_INTMED",    "equity": 0.0, "base_return": 0.035},
    {"fund_id": "MONEY_MARKET",   "equity": 0.0, "base_return": 0.025},
]

# Equity allocation profiles: high / balanced / conservative
ALLOC_PROFILES = [
    # (name, alloc_pct_per_fund) — indices match FUND_DEFS
    ("high",         [0.50, 0.30, 0.15, 0.05, 0.00]),  # ≥75% equity
    ("balanced",     [0.20, 0.15, 0.40, 0.20, 0.05]),  # 25-50% equity
    ("conservative", [0.05, 0.05, 0.25, 0.40, 0.25]),  # <25% equity
]
ALLOC_PROFILE_PROBS = [0.50, 0.30, 0.20]


def _advance_year(d: date) -> date:
    """Advance date by one year, clamping Feb 29 to Mar 1 in non-leap years."""
    try:
        return date(d.year + 1, d.month, d.day)
    except ValueError:
        return date(d.year + 1, 3, 1)


def _coi_rate(attained_age: int, smoker: str) -> float:
    """Approximate current COI per $1,000 NAR (2001 CSO proxy × 1.20)."""
    base = 0.0005 + 0.000015 * (1.09 ** attained_age)
    return round(min(base * (2.0 if smoker == "SM" else 1.0) * 1.20, 0.999), 6)


def _guaranteed_coi_rate(attained_age: int, smoker: str) -> float:
    """Guaranteed COI per $1,000 NAR (2001 CSO proxy × 1.50)."""
    base = 0.0005 + 0.000015 * (1.09 ** attained_age)
    return round(min(base * (2.0 if smoker == "SM" else 1.0) * 1.50, 0.999), 6)


def _mortality_rate(issue_age: int, policy_year: int, smoker: str, risk_class: str) -> float:
    """Simplified mortality for policy simulation (Makeham-style)."""
    att_age = issue_age + policy_year - 1
    base_q  = 0.0007 + 0.000022 * (1.095 ** att_age)
    if smoker == "SM":
        base_q *= 2.2
    factors = {
        "SUPER_PREF": 0.55, "PREF_NS": 0.75, "STD_NS": 1.00,
        "PREF_SM": 2.00,    "STD_SM": 2.50,
    }
    return min(base_q * factors.get(risk_class, 1.0), 0.999)


def _lapse_rate(policy_year: int) -> float:
    """Return base annual VUL lapse rate."""
    idx = min(policy_year - 1, len(VUL_LAPSE) - 1)
    return VUL_LAPSE[idx]


def _gbm_fund_return(equity_alloc: float, year: int, rng: np.random.Generator) -> float:
    """
    Compute GBM-based fund return for a given calendar year.

    Regime 1 (years 1-5, calendar 2016-2021): mu=7%, sigma=15%
    Regime 2 (years 6-8, calendar 2022-2023): mu=5%, sigma=20%
    Uses MACRO_SCENARIO equity_return as the equity component.
    """
    macro = MACRO_SCENARIO.get(min(year, 2023), MACRO_SCENARIO[2023])
    equity_return = macro["equity_return"]
    bond_return   = macro.get("credited_rate", 0.03) - 0.005

    # Weighted return by equity allocation
    portfolio_return = equity_alloc * equity_return + (1 - equity_alloc) * bond_return
    # Add idiosyncratic noise
    sigma = 0.20 if year >= 2022 else 0.15
    noise = rng.normal(0, sigma * 0.3)  # fund-specific noise around market
    return portfolio_return + noise


def _build_sub_account_allocations(
    profile_allocs: list[float],
    fund_values: list[float],
) -> str:
    """Build JSON string for sub_account_allocations field."""
    items = []
    for i, fd in enumerate(FUND_DEFS):
        items.append({
            "fund_id":    fd["fund_id"],
            "alloc_pct":  round(profile_allocs[i], 4),
            "fund_value": round(fund_values[i], 2),
        })
    return json.dumps(items)


def generate_vul_policies(rng: np.random.Generator) -> pd.DataFrame:
    """Generate 800 VUL policy records per requirements spec Section 8.3.

    Args:
        rng: Seeded NumPy random Generator.

    Returns:
        DataFrame with all columns from experience_study_technical_spec_v1.2.md Section C.3 for VUL.
    """
    study_start = date(2016, 1, 1)
    study_end   = date(2023, 12, 31)
    issue_start = date(2008, 1, 1)
    issue_end   = date(2023, 6, 30)

    ages    = np.clip(pert_sample(rng, 35, 47, 65, size=N_VUL).astype(int), 18, 75)
    offsets = rng.integers(0, (issue_end - issue_start).days + 1, size=N_VUL)
    rc_arr  = rng.choice(RISK_CLASSES, p=RISK_PROBS, size=N_VUL)
    ch_arr  = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_VUL)
    st_arr  = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_VUL)
    pm_arr  = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_VUL)
    # Equity allocation profile: 50% high, 30% balanced, 20% conservative
    profile_idx_arr = rng.choice([0, 1, 2], p=ALLOC_PROFILE_PROBS, size=N_VUL)

    records: list[dict] = []

    for j in range(N_VUL):
        issue_age  = int(ages[j])
        issue_date = issue_start + timedelta(days=int(offsets[j]))
        risk_class = str(rc_arr[j])
        channel    = str(ch_arr[j])
        state      = str(st_arr[j])
        prem_mode  = str(pm_arr[j])
        smoker     = "SM" if risk_class in ("PREF_SM", "STD_SM") else "NS"
        gender     = "M" if rng.random() < 0.55 else "F"
        dob        = issue_age_to_dob(issue_date, issue_age)

        profile_name, profile_allocs = ALLOC_PROFILES[int(profile_idx_arr[j])]

        # Specified amount: high face, high net worth
        spec_amount = round(
            np.clip(rng.lognormal(math.log(400_000), 0.85), 50_000, 5_000_000)
            / 1_000
        ) * 1_000

        dbo = rng.choice(["A", "B"], p=[0.60, 0.40])

        policy_years_elapsed = max(1, study_end.year - issue_date.year)
        att_age_now = issue_age + policy_years_elapsed - 1

        # --- Simulate separate account by GBM year-by-year ---
        initial_premium = spec_amount * 0.012  # ~1.2% of face as initial premium
        planned_premium = round(max(initial_premium, spec_amount * 0.008), 2)

        # Per-fund initial allocation amounts
        fund_values = [planned_premium * a for a in profile_allocs]

        # Track whether withdrawal has ever been activated
        withdrawal_ever_active = False
        withdrawal_rate_pct = 0.0
        withdrawal_regime = "NONE"

        for yr_offset in range(policy_years_elapsed):
            yr = issue_date.year + yr_offset
            att_age_yr = issue_age + yr_offset

            # Annual premium deposit (with some variability)
            prem_this_yr = planned_premium * rng.uniform(0.85, 1.10)

            # M&E and COI charges
            total_fv = sum(fund_values)
            me_charge   = total_fv * 0.014  # 1.40% M&E
            nar         = max(0.0, spec_amount - total_fv)
            coi_charge  = nar * _coi_rate(att_age_yr, smoker) / 1_000.0
            expense_load = prem_this_yr * 0.06 + 8.0

            # Deposits and charges
            net_deposit = prem_this_yr - me_charge - coi_charge - expense_load

            # Grow each fund by GBM return
            for fi, fd in enumerate(FUND_DEFS):
                fund_ret = _gbm_fund_return(fd["equity"], yr, rng)
                fund_values[fi] = max(0.0, (fund_values[fi] + net_deposit * profile_allocs[fi])) * (1 + fund_ret)

            total_fv = sum(fund_values)

            # Withdrawal activation (FR-1C-04): once active stays active
            # ~15% of policies with duration > 5 activate withdrawals
            if not withdrawal_ever_active and yr_offset >= 5 and rng.random() < 0.035:
                withdrawal_ever_active = True
                withdrawal_rate_pct = round(rng.uniform(0.04, 0.06), 4)
                withdrawal_regime = rng.choice(["LOW", "MAX"], p=[0.60, 0.40])

            if withdrawal_ever_active and total_fv > 0:
                withdrawal_amt = total_fv * withdrawal_rate_pct
                # Proportional withdrawal from each fund
                for fi in range(len(fund_values)):
                    fund_values[fi] = max(0.0, fund_values[fi] - withdrawal_amt * profile_allocs[fi])

        total_sa = round(sum(fund_values), 2)
        # Ensure non-negative
        fund_values = [max(0.0, fv) for fv in fund_values]
        total_sa = max(0.0, sum(fund_values))

        # Fixed account: ~10% of total
        fixed_av = round(total_sa * rng.uniform(0.05, 0.15), 2)
        sa_total  = round(total_sa, 2)

        sub_alloc_json = _build_sub_account_allocations(profile_allocs, fund_values)

        # Equity allocation pct (weighted average across funds)
        equity_alloc_pct = round(
            sum(FUND_DEFS[fi]["equity"] * profile_allocs[fi] for fi in range(len(FUND_DEFS))),
            4,
        )

        fund_to_spec_ratio = round(sa_total / spec_amount, 4) if spec_amount > 0 else 1.0

        # Surrender charge: declines over 15 years
        sc_yr  = min(policy_years_elapsed, 16)
        sc_rate = max(0.0, 0.10 - 0.007 * (sc_yr - 1))
        surrender_charge = round((sa_total + fixed_av) * sc_rate, 2)

        # MEC
        cumul_prem = planned_premium * policy_years_elapsed
        seven_pay  = round(spec_amount * 0.0025 * (1 + att_age_now / 100), 2)
        mec_flag   = (cumul_prem > seven_pay * 7) and (rng.random() < 0.05)

        av_bom = round((sa_total + fixed_av) * 0.98, 2)
        av_eom = round(sa_total + fixed_av, 2)

        # CI rider: 15% of policies
        ci_flag = rng.random() < 0.15
        ci_sa   = round(spec_amount * 0.30, 2) if ci_flag else None
        ci_prem = round(0.00035 * ci_sa, 2) if ci_flag else None

        # Reinsurance: ~12% for high-face policies
        reins_flag = (rng.random() < 0.12) and (spec_amount > 500_000)

        # --- Policy life simulation ---
        status_code  = "IF"
        term_date    = None
        term_cause   = None
        illness_code = None

        cur_date = issue_date
        py = 0
        alive = True

        while alive and cur_date.year <= study_end.year:
            py += 1
            yr = cur_date.year
            if yr < study_start.year:
                cur_date = _advance_year(cur_date)
                continue

            att_age = issue_age + py - 1

            # Mortality
            q = _mortality_rate(issue_age, py, smoker, risk_class)
            if rng.random() < q:
                status_code = "DEATH"
                term_cause  = "DEATH_BENEFIT_CLAIM"
                term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                alive = False
                break

            # CI claim
            if ci_flag and ci_sa:
                age_factor = ci_age_factor(att_age)
                ci_rate = CI_BASE_INCIDENCE_PER_1000 * age_factor / 1000.0
                if rng.random() < ci_rate:
                    status_code  = "CI_CLAIM"
                    term_cause   = "CI_ACCELERATED_BENEFIT"
                    term_date    = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    illness_code = str(rng.choice(CI_ILLNESS_CODES, p=CI_ILLNESS_WEIGHTS))
                    alive = False
                    break

            # VUL lapse: moneyness multiplier applied
            base_lapse = _lapse_rate(py)
            macro_yr   = min(yr, 2023)
            dyn_mult   = get_lapse_multiplier(macro_yr, MACRO_SCENARIO[macro_yr]["credited_rate"], "VUL")

            # Moneyness multiplier: FR-1C-03
            mono_mult = min(2.0, max(0.5, 1.0 / fund_to_spec_ratio)) if fund_to_spec_ratio > 0 else 1.0

            eff_lapse = min(base_lapse * dyn_mult * mono_mult, 0.99)
            if rng.random() < eff_lapse:
                status_code = "LAPSE"
                term_cause  = "LAPSE"
                term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                alive = False
                break

            cur_date = _advance_year(cur_date)

        records.append({
            "policy_id":                  f"VUL-{j + 1:07d}",
            "product_code":               "VUL",
            "plan_code":                  "VUL_STANDARD",
            "issue_date":                 issue_date.isoformat(),
            "date_of_birth":              dob.isoformat(),
            "issue_age_anb":              issue_age,
            "gender":                     gender,
            "smoker_status":              smoker,
            "risk_class":                 risk_class,
            "annual_premium":             round(planned_premium, 2),
            "premium_mode":               prem_mode,
            "status_code":                status_code,
            "termination_date":           term_date.isoformat() if term_date else None,
            "termination_cause_code":     term_cause,
            "specified_amount":           spec_amount,
            "death_benefit_option":       dbo,
            "separate_account_total_value": sa_total,
            "fixed_account_value":        fixed_av,
            "sub_account_allocations":    sub_alloc_json,
            "equity_allocation_pct":      equity_alloc_pct,
            "fund_value_to_spec_amount_ratio": fund_to_spec_ratio,
            "ma_charge_annual_rate":      0.014,
            "withdrawal_active_flag":     withdrawal_ever_active,
            "withdrawal_rate_pct":        withdrawal_rate_pct if withdrawal_ever_active else 0.0,
            "withdrawal_regime":          withdrawal_regime,
            "account_value_bom":          av_bom,
            "account_value_eom":          av_eom,
            "current_coi_rate":           _coi_rate(att_age_now, smoker),
            "guaranteed_coi_rate":        _guaranteed_coi_rate(att_age_now, smoker),
            "surrender_charge_remaining": surrender_charge,
            "planned_premium":            planned_premium,
            "mec_status_flag":            mec_flag,
            "reinsurance_flag":           reins_flag,
            "ci_rider_flag":              ci_flag,
            "ci_rider_sum_assured":       ci_sa,
            "ci_rider_premium":           ci_prem,
            "illness_code":               illness_code,
            "distribution_channel":       channel,
            "issue_state":                state,
        })

    return pd.DataFrame(records)
