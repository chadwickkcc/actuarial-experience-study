"""Whole Life synthetic data generator.

Produces wl_policies.csv: 2,800 policies per experience_study_technical_spec_v1.2.md Section C.3.
Distributional parameters from experience_study_requirements_spec_v2.1.md Section 8.3.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .common import (
    MACRO_SCENARIO,
    US_STATES,
    _STATE_WEIGHTS,
    attained_age_float,
    generate_ci_claims,
    get_lapse_multiplier,
    issue_age_to_dob,
    pert_sample,
    random_date_between,
)

N_POLICIES = 2_800

PLAN_CODES = ["WL_LIFE_PAY", "WL_20_PAY", "WL_10_PAY"]
PLAN_PROBS = [0.55, 0.30, 0.15]
PLAN_PAY_MAP = {"WL_LIFE_PAY": "LIFE_PAY", "WL_20_PAY": "20_PAY", "WL_10_PAY": "10_PAY"}

RISK_CLASSES = ["SUPER_PREF", "PREF_NS", "STD_NS", "PREF_SM", "STD_SM"]
RISK_PROBS   = [0.18, 0.32, 0.35, 0.07, 0.08]

CHANNELS = ["CAREER", "INDEPENDENT", "DIRECT", "BANK"]
CHANNEL_PROBS = [0.55, 0.30, 0.10, 0.05]
PREMIUM_MODES = ["ANNUAL", "SEMI", "QUARTERLY", "MONTHLY"]
PREMIUM_MODE_PROBS = [0.30, 0.20, 0.25, 0.25]

DIVIDEND_OPTIONS = ["PUA", "CASH", "ACCUM", "OFFSET"]
DIVIDEND_PROBS   = [0.50, 0.20, 0.20, 0.10]

# Annual base lapse rates by policy year (0-indexed: yr1, yr2, …, yr11+)
BASE_LAPSE_RATES = [0.11, 0.07, 0.05, 0.04, 0.03, 0.025, 0.025, 0.025, 0.025, 0.025, 0.020]


def _lapse_rate(policy_year: int) -> float:
    """Return the base annual lapse rate for a given policy year (1-indexed)."""
    idx = min(policy_year - 1, len(BASE_LAPSE_RATES) - 1)
    return BASE_LAPSE_RATES[idx]


def _calc_csv(policy_year: int, face_amount: float, plan_code: str) -> float:
    """Simplified guaranteed cash value approximation."""
    if plan_code == "WL_10_PAY":
        max_years = 10
    elif plan_code == "WL_20_PAY":
        max_years = 20
    else:
        max_years = 35

    pct = min(1.0, (policy_year - 1) / max_years)
    return round(max(0.0, pct * face_amount * 0.40), 2)


def _calc_wl_premium(face_amount: float, issue_age: int, plan_code: str, smoker: str) -> float:
    """Simplified WL annual premium per $1,000 face."""
    base_rate = 0.010 + 0.0003 * max(0, issue_age - 35)
    if smoker == "SM":
        base_rate *= 2.0
    if plan_code == "WL_10_PAY":
        base_rate *= 2.5
    elif plan_code == "WL_20_PAY":
        base_rate *= 1.6
    return round(face_amount * base_rate, 2)


def _mortality_rate(issue_age: int, policy_year: int, smoker: str, risk_class: str) -> float:
    """Simplified Makeham mortality approximation (calibrated to VBT-like rates)."""
    att_age = issue_age + policy_year - 1
    # Gompertz-Makeham: A + B*c^age
    base_q = 0.0007 + 0.00002 * (1.095 ** att_age)
    if smoker == "SM":
        base_q *= 2.2
    factors = {
        "SUPER_PREF": 0.55, "PREF_NS": 0.75, "STD_NS": 1.00,
        "PREF_SM": 2.00, "STD_SM": 2.50,
    }
    return min(base_q * factors.get(risk_class, 1.0), 0.999)


def _advance_year(d: date) -> date:
    """Advance date by one year, clamping Feb 29 to Mar 1 in non-leap years."""
    try:
        return date(d.year + 1, d.month, d.day)
    except ValueError:
        return date(d.year + 1, 3, 1)


def generate_wl_policies(rng: np.random.Generator) -> pd.DataFrame:
    """Generate 2,800 Whole Life policy records per requirements spec Section 8.3.

    Args:
        rng: Seeded NumPy random Generator.

    Returns:
        DataFrame with all columns from experience_study_technical_spec_v1.2.md Section C.3 for WL.
    """
    # 60% young-adult block (PERT 25-42-65), 40% final-expense (PERT 55-72-88)
    n_young = int(N_POLICIES * 0.60)
    n_final = N_POLICIES - n_young

    ages_young = np.clip(pert_sample(rng, 25, 42, 65, size=n_young).astype(int), 18, 85)
    ages_final = np.clip(pert_sample(rng, 55, 72, 88, size=n_final).astype(int), 40, 90)
    issue_ages = np.concatenate([ages_young, ages_final])

    # Face amounts: main block lognormal, final-expense sub-block $5K-$25K
    fa_young = np.round(
        np.clip(rng.lognormal(math.log(120_000), 1.2, size=n_young), 25_001, 2_000_000) / 1_000
    ) * 1_000
    fa_final = np.round(
        rng.uniform(5_000, 25_000, size=n_final) / 1_000
    ) * 1_000
    face_amounts = np.concatenate([fa_young, fa_final])
    small_face_flags = np.concatenate([
        np.zeros(n_young, dtype=bool),
        np.ones(n_final, dtype=bool),
    ])

    risk_classes   = rng.choice(RISK_CLASSES, p=RISK_PROBS, size=N_POLICIES)
    plan_codes     = rng.choice(PLAN_CODES, p=PLAN_PROBS, size=N_POLICIES)
    channels       = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_POLICIES)
    states         = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_POLICIES)
    prem_modes     = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_POLICIES)
    par_flags      = rng.random(size=N_POLICIES) < 0.50
    div_opts       = rng.choice(DIVIDEND_OPTIONS, p=DIVIDEND_PROBS, size=N_POLICIES)
    apl_flags      = rng.random(size=N_POLICIES) < 0.10
    reins_flags    = (rng.random(size=N_POLICIES) < 0.10) & (face_amounts > 250_000)

    issue_start = date(2008, 1, 1)
    issue_end   = date(2023, 6, 30)
    issue_offsets = rng.integers(0, (issue_end - issue_start).days + 1, size=N_POLICIES)
    issue_dates   = [issue_start + timedelta(days=int(d)) for d in issue_offsets]

    study_start = date(2016, 1, 1)
    study_end   = date(2023, 12, 31)

    records: list[dict] = []

    for i in range(N_POLICIES):
        issue_age  = int(issue_ages[i])
        face       = float(face_amounts[i])
        is_final   = bool(small_face_flags[i])
        risk_class = str(risk_classes[i])
        plan_code  = str(plan_codes[i])
        channel    = str(channels[i])
        state      = str(states[i])
        prem_mode  = str(prem_modes[i])
        par_flag   = bool(par_flags[i])
        apl_flag   = bool(apl_flags[i])
        reins_flag = bool(reins_flags[i])
        issue_date = issue_dates[i]
        smoker     = "SM" if risk_class in ("PREF_SM", "STD_SM") else "NS"

        dob            = issue_age_to_dob(issue_date, issue_age)
        annual_premium = _calc_wl_premium(face, issue_age, plan_code, smoker)

        # CI rider: 20% of non-small-face policies
        ci_flag = (not is_final) and (rng.random() < 0.20)
        ci_sum_assured = round(face * 0.50, 2) if ci_flag else None
        ci_premium     = round(0.00025 * ci_sum_assured, 2) if ci_flag else None

        # Dividend fields
        div_option   = div_opts[i] if par_flag else None
        div_scale    = 0.055 if par_flag else None
        div_deposit  = 0.0
        pua_face     = 0.0
        loan_balance = 0.0

        # Simulate policy life year by year through study window
        policy_year  = 0
        alive        = True
        status_code  = "IF"
        term_date    = None
        term_cause   = None
        non_forfeiture_status = "ACTIVE"
        illness_code = None
        csv_at_term  = 0.0

        max_study_year = study_end.year
        cur_date = issue_date

        while alive and cur_date.year <= max_study_year:
            policy_year += 1
            yr = cur_date.year
            if yr < study_start.year:
                cur_date = _advance_year(cur_date)
                continue

            # Mortality check
            q = _mortality_rate(issue_age, policy_year, smoker, risk_class)
            if rng.random() < q:
                status_code = "DEATH"
                term_cause  = "DEATH_BENEFIT_CLAIM"
                term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                alive = False
                break

            # CI claim check (if rider in force)
            if ci_flag and ci_sum_assured:
                att_age = issue_age + policy_year - 1
                band_lo = (att_age // 5) * 5
                ci_age_band = f"{band_lo}-{band_lo + 4}"
                from .common import CI_BASE_INCIDENCE_PER_1000, ci_age_factor, CI_ILLNESS_CODES, CI_ILLNESS_WEIGHTS
                age_factor = ci_age_factor(att_age)
                ci_rate = CI_BASE_INCIDENCE_PER_1000 * age_factor / 1000.0
                if rng.random() < ci_rate:
                    status_code  = "CI_CLAIM"
                    term_cause   = "CI_ACCELERATED_BENEFIT"
                    term_date    = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    illness_code = rng.choice(CI_ILLNESS_CODES, p=CI_ILLNESS_WEIGHTS)
                    alive = False
                    break

            # Lapse check with dynamic multiplier
            base_rate   = _lapse_rate(policy_year)
            macro_year  = min(yr, 2023)
            if macro_year in MACRO_SCENARIO:
                dyn_mult = get_lapse_multiplier(macro_year, MACRO_SCENARIO[macro_year]["credited_rate"], "WL")
            else:
                dyn_mult = 1.0
            eff_lapse = base_rate * dyn_mult

            if rng.random() < eff_lapse:
                csv_at_term = _calc_csv(policy_year, face, plan_code)
                # Lapse+surrender are the two counted discontinuances (FR-1B-03).
                # Non-forfeiture elections are excluded from the lapse A/E study and
                # are not simulated here — the base_rate represents lapse+surrender only.
                if csv_at_term > 0 and rng.random() < 0.60:
                    status_code = "SURRENDER"
                    term_cause  = "SURRENDER"
                else:
                    status_code = "LAPSE"
                    term_cause  = "LAPSE"
                term_date = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                alive = False
                break

            cur_date = _advance_year(cur_date)

        # Policy year at end of study for in-force policies
        if status_code == "IF":
            policy_year_final = (study_end.year - issue_date.year) + 1
        else:
            policy_year_final = policy_year

        csv_now = _calc_csv(policy_year_final, face, plan_code)
        loan_bal = round(rng.uniform(0, csv_now * 0.40), 2) if (policy_year_final > 3 and rng.random() < 0.05) else 0.0
        pua_face_val = round(rng.uniform(0, face * 0.05), 2) if par_flag else 0.0
        div_dep_val  = round(rng.uniform(0, annual_premium * 2), 2) if par_flag else 0.0

        records.append({
            "policy_id":              f"WL-{i + 1:07d}",
            "product_code":           "WL",
            "plan_code":              plan_code,
            "issue_date":             issue_date.isoformat(),
            "date_of_birth":          dob.isoformat(),
            "issue_age_anb":          issue_age,
            "gender":                 "M" if rng.random() < 0.48 else "F",
            "smoker_status":          smoker,
            "risk_class":             risk_class,
            "face_amount":            face,
            "premium_mode":           prem_mode,
            "annual_premium":         annual_premium,
            "status_code":            status_code,
            "termination_date":       term_date.isoformat() if term_date else None,
            "termination_cause_code": term_cause,
            "premium_paying_period":  PLAN_PAY_MAP[plan_code],
            "guaranteed_cash_value":  csv_now,
            "dividend_option_code":   div_option,
            "dividend_on_deposit_bal": div_dep_val,
            "paid_up_additions_face": pua_face_val,
            "policy_loan_balance":    loan_bal,
            "auto_premium_loan_flag": apl_flag,
            "non_forfeiture_status":  non_forfeiture_status,
            "participating_flag":     par_flag,
            "dividend_scale_rate":    div_scale,
            "small_face_flag":        is_final,
            "reinsurance_flag":       reins_flag,
            "ci_rider_flag":          ci_flag,
            "ci_rider_sum_assured":   ci_sum_assured,
            "ci_rider_premium":       ci_premium,
            "illness_code":           illness_code,
            "distribution_channel":   channel,
            "issue_state":            state,
        })

    return pd.DataFrame(records)
