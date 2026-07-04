"""Term Life Insurance synthetic data generator.

Generates 3,200 Term Life policy records matching experience_study_technical_spec_v1.2.md Section C.3.

Key generation rules (experience_study_requirements_spec_v2.1.md Section 8.3):
  - Issue age: PERT(18, 38, 75)
  - Face amount: Lognormal mu=ln(250000), sigma=0.9, clipped [50000, 5000000]
  - Gender: 58% M / 42% F
  - Risk class: 22% SUPER_PREF / 28% PREF_NS / 33% STD_NS / 9% PREF_SM / 8% STD_SM
  - Level period: 55% T20 / 25% T10 / 15% T30 / 5% T15
  - PLT structure: 65% JUMP_TO_ART / 35% GRADED
  - 25% of policies carry CI rider (sum assured = 50% of face amount)
  - Mortality: Makeham law calibrated to 2015 VBT x class factors x selection factors
  - Dynamic lapse adjustment from macro scenario
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from synthetic_data.generators.common import (
    MACRO_SCENARIO,
    STUDY_START,
    STUDY_END,
    CI_ILLNESS_CODES,
    CI_ILLNESS_WEIGHTS,
    CI_BASE_INCIDENCE_PER_1000,
    US_STATES,
    _STATE_WEIGHTS,
    ci_age_factor,
    pert_sample,
    get_lapse_multiplier,
    issue_age_to_dob,
    attained_age_float,
    random_date_between,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

N_POLICIES = 3_200

RISK_CLASS_NAMES  = ["SUPER_PREF", "PREF_NS", "STD_NS", "PREF_SM", "STD_SM"]
RISK_CLASS_PROBS  = [0.22,          0.28,      0.33,     0.09,      0.08]

RISK_CLASS_MORTALITY_FACTOR: dict[str, float] = {
    "SUPER_PREF": 0.55,
    "PREF_NS":    0.75,
    "STD_NS":     1.00,
    "PREF_SM":    2.00,
    "STD_SM":     2.50,
}

# (plan_code, level_period_years, probability)
LEVEL_PERIODS = [
    ("T20", 20, 0.55),
    ("T10", 10, 0.25),
    ("T30", 30, 0.15),
    ("T15", 15, 0.05),
]

PLT_STRUCTURES      = ["JUMP_TO_ART", "GRADED"]
PLT_STRUCTURE_PROBS = [0.65, 0.35]

CHANNELS       = ["CAREER", "INDEPENDENT", "DIRECT", "BANK"]
CHANNEL_PROBS  = [0.40,     0.35,          0.15,     0.10]

PREMIUM_MODES       = ["ANNUAL", "SEMI", "QUARTERLY", "MONTHLY"]
PREMIUM_MODE_PROBS  = [0.30,     0.20,   0.20,        0.30]


# ---------------------------------------------------------------------------
# Rate helpers
# ---------------------------------------------------------------------------

def _selection_factor(policy_year: int) -> float:
    """UW selection (anti-selective mortality improvement) by policy year."""
    if policy_year <= 2:
        return 0.80
    elif policy_year <= 5:
        return 0.90
    elif policy_year <= 10:
        return 0.95
    return 1.00


def vbt_q_x(issue_age_anb: int, policy_year: int, gender: str,
            smoker_status: str, risk_class: str) -> float:
    """Makeham-law q_x calibrated to approximate 2015 VBT select & ultimate."""
    if gender == "M":
        A, B, c = 0.00022, 0.000027, 1.104
    else:
        A, B, c = 0.00018, 0.000018, 1.102

    attained_age = min(max(issue_age_anb + policy_year - 1, 0), 110)
    q_base = A + B * (c ** attained_age)

    if smoker_status == "SM":
        q_base *= 1.90
    q_base *= RISK_CLASS_MORTALITY_FACTOR[risk_class]
    q_base *= _selection_factor(policy_year)
    return min(q_base, 1.0)


def _base_lapse_rate(policy_year: int) -> float:
    """Base annual lapse rate for level-period Term Life."""
    if policy_year == 1:
        return 0.08
    elif policy_year <= 3:
        return 0.05
    elif policy_year <= 5:
        return 0.04
    return 0.03


def _plt_shock_lapse(jump_ratio: float) -> float:
    """PLT year-1 shock lapse rate by premium jump ratio (SOA 2021 PLT)."""
    if jump_ratio <= 2.0:
        return 0.30
    elif jump_ratio <= 5.0:
        return 0.55
    elif jump_ratio <= 8.0:
        return 0.70
    elif jump_ratio <= 12.0:
        return 0.80
    return 0.88


def _plt_continuing_lapse(plt_duration: int) -> float:
    """Post-shock PLT annual lapse rate (duration-graded)."""
    if plt_duration == 1:
        return 0.30   # captured separately via shock
    elif plt_duration <= 3:
        return 0.15
    elif plt_duration <= 5:
        return 0.10
    return 0.08


def calc_annual_premium(face_amount: float, issue_age_anb: int,
                        smoker_status: str, risk_class: str,
                        level_period_years: int) -> float:
    """Simplified gross premium per $1,000 face amount."""
    base_rate_per_thou = 0.90 + 0.015 * max(0, issue_age_anb - 25)
    smoker_load = 2.2 if smoker_status == "SM" else 1.0
    class_load  = RISK_CLASS_MORTALITY_FACTOR[risk_class]
    term_load   = {10: 0.85, 15: 0.90, 20: 1.00, 30: 1.25}.get(level_period_years, 1.00)
    rate = base_rate_per_thou * smoker_load * class_load * term_load
    return round(rate * face_amount / 1_000.0, 2)


# ---------------------------------------------------------------------------
# Policy life simulation
# ---------------------------------------------------------------------------

def _simulate_policy_life(
    rng: np.random.Generator,
    issue_date: date,
    issue_age: int,
    dob: date,
    gender: str,
    smoker: str,
    risk_class: str,
    level_period: int,
    jump_ratio: float,            # pre-sampled PLT jump ratio
    ci_flag: bool,
) -> tuple[str, Optional[date], Optional[str], Optional[str]]:
    """
    Simulate one Term Life policy from issue to termination or study end.

    Returns (status_code, termination_date, termination_cause_code, illness_code).
    """
    policy_year  = 0
    current_date = issue_date
    plt_start    = issue_date + timedelta(days=int(level_period * 365.25))

    while current_date < STUDY_END:
        policy_year += 1
        year_end = current_date + timedelta(days=365)

        # PLT determination (use policy year count, not date comparison)
        in_plt     = policy_year > level_period
        plt_dur    = max(0, policy_year - level_period)

        # Study-window clipping
        seg_start = max(current_date, STUDY_START)
        seg_end   = min(year_end, STUDY_END)
        if seg_start > seg_end:
            current_date = year_end
            continue

        # Attained age at segment midpoint
        mid     = seg_start + timedelta(days=(seg_end - seg_start).days // 2)
        att_age = attained_age_float(dob, mid)

        # --- Mortality ---
        q = vbt_q_x(issue_age, policy_year, gender, smoker, risk_class)
        if rng.random() < q:
            death_date = random_date_between(rng, seg_start, seg_end)
            return ("DEATH", death_date, "DEATH_BENEFIT_CLAIM", None)

        # --- CI claim ---
        if ci_flag:
            ci_rate = min(CI_BASE_INCIDENCE_PER_1000 * ci_age_factor(att_age) / 1_000.0, 0.05)
            if rng.random() < ci_rate:
                illness   = rng.choice(CI_ILLNESS_CODES, p=CI_ILLNESS_WEIGHTS)
                ci_date   = random_date_between(rng, seg_start, seg_end)
                return ("CI_CLAIM", ci_date, "CI_ACCELERATED_BENEFIT", illness)

        # --- Lapse ---
        cal_year   = min(max(seg_start.year, 2016), 2023)
        macro      = MACRO_SCENARIO[cal_year]
        lapse_mult = get_lapse_multiplier(cal_year, macro["credited_rate"], "TERM")

        if in_plt:
            if plt_dur == 1:
                base_lapse = _plt_shock_lapse(jump_ratio)
            else:
                base_lapse = _plt_continuing_lapse(plt_dur)
        else:
            base_lapse = _base_lapse_rate(policy_year)

        adj_lapse = min(base_lapse * lapse_mult, 0.95)
        if rng.random() < adj_lapse:
            lapse_date = random_date_between(rng, seg_start, seg_end)
            return ("LAPSE", lapse_date, "LAPSE", None)

        current_date = year_end

    return ("IF", None, None, None)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_term_policies(rng: np.random.Generator) -> pd.DataFrame:
    """
    Generate N_POLICIES Term Life policy records.

    Args:
        rng: NumPy random Generator (seeded externally).

    Returns:
        DataFrame with exactly the columns from experience_study_technical_spec_v1.2.md C.3.
    """
    # Batch-sample static distributions
    issue_ages   = np.clip(pert_sample(rng, 18, 38, 75, size=N_POLICIES).astype(int), 18, 75)
    face_amounts = np.round(
        np.clip(rng.lognormal(math.log(250_000), 0.9, size=N_POLICIES), 50_000, 5_000_000)
        / 1_000
    ) * 1_000

    genders        = rng.choice(["M", "F"], p=[0.58, 0.42], size=N_POLICIES)
    risk_classes   = rng.choice(RISK_CLASS_NAMES, p=RISK_CLASS_PROBS, size=N_POLICIES)
    lp_names       = [lp[0] for lp in LEVEL_PERIODS]
    lp_year_map    = {lp[0]: lp[1] for lp in LEVEL_PERIODS}
    lp_probs       = [lp[2] for lp in LEVEL_PERIODS]
    plan_codes     = rng.choice(lp_names, p=lp_probs, size=N_POLICIES)
    plt_structures = rng.choice(PLT_STRUCTURES, p=PLT_STRUCTURE_PROBS, size=N_POLICIES)
    channels       = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_POLICIES)
    states         = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_POLICIES)
    prem_modes     = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_POLICIES)

    issue_start      = date(2008, 1, 1)
    issue_end        = date(2023, 6, 30)
    issue_offsets    = rng.integers(0, (issue_end - issue_start).days + 1, size=N_POLICIES)
    issue_dates      = [issue_start + timedelta(days=int(d)) for d in issue_offsets]

    ci_flags         = rng.random(size=N_POLICIES) < 0.25
    reins_flags      = (rng.random(size=N_POLICIES) < 0.15) & (face_amounts > 500_000)
    conv_flags       = rng.random(size=N_POLICIES) < 0.03

    # Pre-sample PLT jump ratios (Jump-to-ART ~5x, Graded ~2.5x lognormal)
    jump_ratios_jta  = np.clip(rng.lognormal(math.log(5.0),  0.4, size=N_POLICIES), 1.05, 30.0)
    jump_ratios_grd  = np.clip(rng.lognormal(math.log(2.5),  0.3, size=N_POLICIES), 1.05, 10.0)

    records: list[dict] = []

    for i in range(N_POLICIES):
        issue_age    = int(issue_ages[i])
        face         = float(face_amounts[i])
        gender       = str(genders[i])
        risk_class   = str(risk_classes[i])
        plan_code    = str(plan_codes[i])
        level_period = lp_year_map[plan_code]
        plt_structure= str(plt_structures[i])
        channel      = str(channels[i])
        state        = str(states[i])
        prem_mode    = str(prem_modes[i])
        issue_date   = issue_dates[i]
        ci_flag      = bool(ci_flags[i])
        reins_flag   = bool(reins_flags[i])
        conv_flag    = bool(conv_flags[i])
        smoker       = "SM" if risk_class in ("PREF_SM", "STD_SM") else "NS"

        dob            = issue_age_to_dob(issue_date, issue_age)
        annual_premium = calc_annual_premium(face, issue_age, smoker, risk_class, level_period)

        # PLT jump ratio (needed before simulation to determine shock lapse)
        jump_ratio = float(jump_ratios_jta[i] if plt_structure == "JUMP_TO_ART"
                          else jump_ratios_grd[i])

        # Simulate life
        status_code, term_date, term_cause, illness_code = _simulate_policy_life(
            rng=rng,
            issue_date=issue_date,
            issue_age=issue_age,
            dob=dob,
            gender=gender,
            smoker=smoker,
            risk_class=risk_class,
            level_period=level_period,
            jump_ratio=jump_ratio,
            ci_flag=ci_flag,
        )

        # PLT fields: populate only if policy reaches PLT during study window
        plt_start = issue_date + timedelta(days=int(level_period * 365.25))
        enters_plt_in_study = plt_start <= STUDY_END

        if enters_plt_in_study:
            plt_premium_year_1 = round(annual_premium * jump_ratio, 2)
            plt_structure_code = plt_structure
            premium_jump_ratio = round(jump_ratio, 4)
        else:
            plt_premium_year_1 = None
            plt_structure_code = None
            premium_jump_ratio = None

        # CI rider fields
        ci_rider_sum_assured = round(face * 0.50, 2) if ci_flag else None
        ci_rider_premium     = round(0.0003 * ci_rider_sum_assured, 2) if ci_flag else None

        records.append({
            "policy_id":              f"TRM-{i + 1:07d}",
            "product_code":           "TERM",
            "plan_code":              plan_code,
            "issue_date":             issue_date.isoformat(),
            "date_of_birth":          dob.isoformat(),
            "issue_age_anb":          issue_age,
            "gender":                 gender,
            "smoker_status":          smoker,
            "risk_class":             risk_class,
            "face_amount":            face,
            "premium_mode":           prem_mode,
            "annual_premium":         annual_premium,
            "status_code":            status_code,
            "termination_date":       term_date.isoformat() if term_date else None,
            "termination_cause_code": term_cause,
            "level_period_years":     level_period,
            "plt_premium_year_1":     plt_premium_year_1,
            "plt_structure_code":     plt_structure_code,
            "premium_jump_ratio":     premium_jump_ratio,
            "distribution_channel":   channel,
            "issue_state":            state,
            "conversion_flag":        conv_flag,
            "reinsurance_flag":       reins_flag,
            "ci_rider_flag":          ci_flag,
            "ci_rider_sum_assured":   ci_rider_sum_assured,
            "ci_rider_premium":       ci_rider_premium,
            "illness_code":           illness_code,
        })

    df = pd.DataFrame(records)

    # Apply conversion_flag override: convert LAPSE → CONVERSION for flagged rows
    mask_conv = df["conversion_flag"] & (df["status_code"] == "LAPSE")
    df.loc[mask_conv, "status_code"]            = "CONVERSION"
    df.loc[mask_conv, "termination_cause_code"] = "CONVERSION"

    return df
