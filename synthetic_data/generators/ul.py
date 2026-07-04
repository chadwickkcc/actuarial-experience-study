"""Universal Life (Trad UL, ULSG, IUL) synthetic data generator.

Produces ul_policies.csv: 1,800 policies (800 Trad UL, 800 ULSG, 200 IUL)
per experience_study_technical_spec_v1.2.md Section C.3 and experience_study_requirements_spec_v2.1.md Section 8.3.
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
    get_lapse_multiplier,
    issue_age_to_dob,
    pert_sample,
    random_date_between,
)

def _advance_year(d: date) -> date:
    """Advance date by one year, clamping Feb 29 to Mar 1 in non-leap years."""
    try:
        return date(d.year + 1, d.month, d.day)
    except ValueError:
        return date(d.year + 1, 3, 1)


N_TRAD_UL = 800
N_ULSG    = 800
N_IUL     = 200
N_POLICIES = N_TRAD_UL + N_ULSG + N_IUL

RISK_CLASSES = ["SUPER_PREF", "PREF_NS", "STD_NS", "PREF_SM", "STD_SM"]
RISK_PROBS   = [0.15, 0.30, 0.40, 0.07, 0.08]

CHANNELS = ["CAREER", "INDEPENDENT", "DIRECT", "BANK"]
CHANNEL_PROBS = [0.40, 0.40, 0.10, 0.10]
PREMIUM_MODES = ["ANNUAL", "SEMI", "QUARTERLY", "MONTHLY"]
PREMIUM_MODE_PROBS = [0.35, 0.15, 0.20, 0.30]

# Base lapse rates by policy year (0-indexed, yr11+ = last entry)
TRAD_UL_LAPSE = [0.08, 0.06, 0.05, 0.04, 0.035, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03]
ULSG_FACTOR   = 0.50  # ULSG lapses at 50% of Trad UL rate (FR requirement)

# Annual COI rate per $1,000 NAR (simplified, from 2001 CSO × factor)
def _coi_rate(attained_age: int, smoker: str) -> float:
    """Approximate current COI per $1,000 NAR (× 1.20 vs 2001 CSO)."""
    base = 0.0005 + 0.000015 * (1.09 ** attained_age)
    return round(min(base * (2.0 if smoker == "SM" else 1.0) * 1.20, 0.999), 6)


def _guaranteed_coi_rate(attained_age: int, smoker: str) -> float:
    """Guaranteed COI per $1,000 NAR (× 1.50 vs 2001 CSO)."""
    base = 0.0005 + 0.000015 * (1.09 ** attained_age)
    return round(min(base * (2.0 if smoker == "SM" else 1.0) * 1.50, 0.999), 6)


def _mortality_rate(issue_age: int, policy_year: int, smoker: str, risk_class: str) -> float:
    """Simplified mortality for policy simulation."""
    att_age = issue_age + policy_year - 1
    base_q  = 0.0007 + 0.000022 * (1.095 ** att_age)
    if smoker == "SM":
        base_q *= 2.2
    factors = {
        "SUPER_PREF": 0.55, "PREF_NS": 0.75, "STD_NS": 1.00,
        "PREF_SM": 2.00, "STD_SM": 2.50,
    }
    return min(base_q * factors.get(risk_class, 1.0), 0.999)


def _lapse_rate(policy_year: int, product_type: str) -> float:
    """Return base annual lapse rate."""
    idx = min(policy_year - 1, len(TRAD_UL_LAPSE) - 1)
    base = TRAD_UL_LAPSE[idx]
    return base * (ULSG_FACTOR if product_type == "ULSG" else 1.0)


def _shadow_account_value(
    account_value: float,
    nlp: float,
    policy_year: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Compute ULSG shadow account value and funding ratio."""
    shadow_av = account_value * rng.uniform(0.80, 1.20)
    cumul_nlp = nlp * policy_year
    funding_ratio = shadow_av / cumul_nlp if cumul_nlp > 0 else 1.0
    return round(shadow_av, 2), round(funding_ratio, 4)


def generate_ul_policies(rng: np.random.Generator) -> pd.DataFrame:
    """Generate 1,800 UL/ULSG/IUL policy records per requirements spec Section 8.3.

    Args:
        rng: Seeded NumPy random Generator.

    Returns:
        DataFrame with all columns from experience_study_technical_spec_v1.2.md Section C.3 for UL.
    """
    study_start = date(2016, 1, 1)
    study_end   = date(2023, 12, 31)
    issue_start = date(2008, 1, 1)
    issue_end   = date(2023, 6, 30)

    records: list[dict] = []

    # -- Trad UL --
    ages_ul    = np.clip(pert_sample(rng, 30, 48, 70, size=N_TRAD_UL).astype(int), 18, 80)
    offsets_ul = rng.integers(0, (issue_end - issue_start).days + 1, size=N_TRAD_UL)
    rc_ul      = rng.choice(RISK_CLASSES, p=RISK_PROBS, size=N_TRAD_UL)
    ch_ul      = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_TRAD_UL)
    st_ul      = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_TRAD_UL)
    pm_ul      = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_TRAD_UL)

    # -- ULSG --
    ages_ulsg    = np.clip(pert_sample(rng, 50, 62, 78, size=N_ULSG).astype(int), 40, 85)
    offsets_ulsg = rng.integers(0, (issue_end - issue_start).days + 1, size=N_ULSG)
    rc_ulsg      = rng.choice(RISK_CLASSES, p=RISK_PROBS, size=N_ULSG)
    ch_ulsg      = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_ULSG)
    st_ulsg      = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_ULSG)
    pm_ulsg      = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_ULSG)

    # -- IUL (simplified: same age range as Trad UL) --
    ages_iul    = np.clip(pert_sample(rng, 30, 48, 70, size=N_IUL).astype(int), 18, 80)
    offsets_iul = rng.integers(0, (issue_end - issue_start).days + 1, size=N_IUL)
    rc_iul      = rng.choice(RISK_CLASSES, p=RISK_PROBS, size=N_IUL)
    ch_iul      = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_IUL)
    st_iul      = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_IUL)
    pm_iul      = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_IUL)

    batches = [
        ("UL",   N_TRAD_UL, ages_ul,   offsets_ul,   rc_ul,   ch_ul,   st_ul,   pm_ul,   "UL"),
        ("ULSG", N_ULSG,    ages_ulsg, offsets_ulsg, rc_ulsg, ch_ulsg, st_ulsg, pm_ulsg, "ULSG"),
        ("IUL",  N_IUL,     ages_iul,  offsets_iul,  rc_iul,  ch_iul,  st_iul,  pm_iul,  "IUL"),
    ]

    global_idx = 0
    for product_code, n, ages, offsets, risk_arr, ch_arr, st_arr, pm_arr, prod_type in batches:
        for j in range(n):
            issue_age  = int(ages[j])
            issue_date = issue_start + timedelta(days=int(offsets[j]))
            risk_class = str(risk_arr[j])
            channel    = str(ch_arr[j])
            state      = str(st_arr[j])
            prem_mode  = str(pm_arr[j])
            smoker     = "SM" if risk_class in ("PREF_SM", "STD_SM") else "NS"
            gender     = "M" if rng.random() < 0.55 else "F"
            dob        = issue_age_to_dob(issue_date, issue_age)

            is_ulsg = (product_code == "ULSG")

            # Face / specified amount
            spec_amount = round(
                np.clip(rng.lognormal(math.log(300_000 if is_ulsg else 200_000), 0.8), 50_000, 3_000_000)
                / 1_000
            ) * 1_000

            # Death benefit option
            dbo = rng.choice(["A", "B"], p=[0.65, 0.35])

            # Account value: grows over policy years
            policy_year_at_study_end = max(1, (study_end.year - issue_date.year))
            att_age_now = issue_age + policy_year_at_study_end - 1

            # Credited rate from most recent macro year
            last_macro = MACRO_SCENARIO.get(min(study_end.year, 2023), MACRO_SCENARIO[2023])
            credited_rate = last_macro["credited_rate"] + rng.uniform(-0.005, 0.010)
            credited_rate = round(max(0.015, min(0.07, credited_rate)), 4)

            gmir = 0.015 if issue_date.year >= 2009 else 0.030

            # Premium planning
            coi  = _coi_rate(att_age_now, smoker)
            gcoi = _guaranteed_coi_rate(att_age_now, smoker)
            planned_premium = round(spec_amount * (coi * 1.3 + 0.001), 2)
            target_premium  = round(planned_premium * 1.15, 2)

            # 7-pay premium (simplified)
            seven_pay = round(spec_amount * 0.0025 * (1 + att_age_now / 100), 2)

            # Min no-lapse premium (ULSG only)
            nlp = round(spec_amount * 0.0020 * (1 + att_age_now / 80), 2) if is_ulsg else None

            # Build account value by accumulation
            av = 0.0
            cumul_prem = 0.0
            for yr_offset in range(policy_year_at_study_end):
                yr = issue_date.year + yr_offset
                macro_yr = min(yr, 2023)
                cr = MACRO_SCENARIO.get(macro_yr, last_macro)["credited_rate"]
                prem_this_yr = planned_premium * rng.uniform(0.8, 1.1)
                coi_charge   = max(0.0, spec_amount - av) * _coi_rate(issue_age + yr_offset, smoker) / 1000.0
                expense_load = prem_this_yr * 0.05 + 5.0
                av = max(0.0, av + prem_this_yr - coi_charge - expense_load) * (1 + cr)
                cumul_prem += prem_this_yr

            av_bom = round(av / (1 + credited_rate / 12), 2)
            av_eom = round(av, 2)

            # Surrender charge: declines over 15 years
            sc_yr = min(policy_year_at_study_end, 16)
            sc_rate = max(0.0, 0.10 - 0.007 * (sc_yr - 1))
            surrender_charge = round(av * sc_rate, 2)

            # MEC status
            cumul_nlp_required = nlp * policy_year_at_study_end if nlp else None
            prem_pers = round(cumul_prem / (planned_premium * policy_year_at_study_end), 4) if planned_premium > 0 else 1.0
            mec_flag  = cumul_prem > seven_pay * 7

            # CI rider: 15% of Trad UL and IUL; none for ULSG
            ci_flag = (not is_ulsg) and (rng.random() < 0.15)
            ci_sa   = round(spec_amount * 0.40, 2) if ci_flag else None
            ci_prem = round(0.00030 * ci_sa, 2) if ci_flag else None

            # Reinsurance
            reins_flag = (rng.random() < 0.10) and (spec_amount > 500_000)

            # Policy life simulation
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
                q = _mortality_rate(issue_age, py, smoker, risk_class)
                if rng.random() < q:
                    status_code = "DEATH"
                    term_cause  = "DEATH_BENEFIT_CLAIM"
                    term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    alive = False
                    break

                # CI claim
                if ci_flag and ci_sa:
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

                # Lapse
                base_lapse = _lapse_rate(py, product_code)
                macro_yr   = min(yr, 2023)
                if macro_yr in MACRO_SCENARIO:
                    dyn_mult = get_lapse_multiplier(macro_yr, MACRO_SCENARIO[macro_yr]["credited_rate"], "UL")
                else:
                    dyn_mult = 1.0
                eff_lapse = min(base_lapse * dyn_mult, 0.99)

                if rng.random() < eff_lapse:
                    status_code = "LAPSE"
                    term_cause  = "LAPSE"
                    term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    alive = False
                    break

                cur_date = _advance_year(cur_date)

            # ULSG shadow account
            if is_ulsg:
                sha_val, sha_ratio = _shadow_account_value(av_eom, nlp or planned_premium, py, rng)
                nlg_period = rng.choice(["LIFETIME", "TO_95", "TO_90", "20_YEAR"], p=[0.40, 0.30, 0.20, 0.10])
                sec_guar_type = rng.choice(["SHADOW_ACCT", "SPEC_PREM"], p=[0.60, 0.40])
            else:
                sha_val      = None
                sha_ratio    = None
                nlg_period   = None
                sec_guar_type= None

            pid_prefix = {"UL": "UL", "ULSG": "ULSG", "IUL": "IUL"}[product_code]
            records.append({
                "policy_id":                  f"{pid_prefix}-{global_idx + 1:07d}",
                "product_code":               product_code,
                "plan_code":                  f"{product_code}_STANDARD",
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
                "account_value_bom":          av_bom,
                "account_value_eom":          av_eom,
                "current_coi_rate":           _coi_rate(att_age_now, smoker),
                "guaranteed_coi_rate":        _guaranteed_coi_rate(att_age_now, smoker),
                "credited_interest_rate":     credited_rate,
                "guaranteed_min_interest_rate": gmir,
                "surrender_charge_remaining": surrender_charge,
                "planned_premium":            planned_premium,
                "target_premium":             target_premium,
                "min_no_lapse_premium":       nlp,
                "seven_pay_premium":          seven_pay,
                "mec_status_flag":            mec_flag,
                "is_ulsg_flag":               is_ulsg,
                "shadow_account_value":       sha_val,
                "shadow_account_funding_ratio": sha_ratio,
                "no_lapse_guarantee_period":  nlg_period,
                "secondary_guarantee_type":   sec_guar_type,
                "cumulative_premiums_paid":   round(cumul_prem, 2),
                "cumulative_nlp_required":    round(cumul_nlp_required, 2) if cumul_nlp_required else None,
                "premium_persistency_ratio":  prem_pers,
                "reinsurance_flag":           reins_flag,
                "ci_rider_flag":              ci_flag,
                "ci_rider_sum_assured":       ci_sa,
                "ci_rider_premium":           ci_prem,
                "illness_code":               illness_code,
                "distribution_channel":       channel,
                "issue_state":                state,
            })
            global_idx += 1

    return pd.DataFrame(records)
