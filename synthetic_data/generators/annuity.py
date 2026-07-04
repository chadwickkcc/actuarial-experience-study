"""Deferred Annuity synthetic data generator.

Produces annuity_contracts.csv: 1,400 contracts (900 fixed DAF-, 500 variable DAV-)
per experience_study_technical_spec_v1.2.md Section C.3 and experience_study_requirements_spec_v2.1.md Section 8.3.

Key features:
- 60% surrender shock in surrender-charge expiry year (7-year schedule)
- Dynamic lapse with k=0.8 (cap [0.3, 3.0]) using MACRO_SCENARIO
- GLB moneyness suppression: min(1.0, 0.4 + 0.6 × moneyness_ratio) for GLWB contracts
- 2012 IAR mortality for owner death
- No CI rider (annuities do not carry CI riders per spec)
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

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

N_FIXED    = 900   # DA_FIXED + DA_FIA
N_VARIABLE = 500   # DA_VA
N_TOTAL    = N_FIXED + N_VARIABLE

CHANNELS = ["CAREER", "BANK", "IBD", "RIA"]
CHANNEL_PROBS = [0.20, 0.30, 0.25, 0.25]

PREMIUM_MODES = ["SINGLE", "FLEXIBLE"]
PREMIUM_MODE_PROBS = [0.70, 0.30]

MARKET_TYPES = ["NQ", "TRAD_IRA", "ROTH_IRA", "QUAL"]
MARKET_TYPE_PROBS = [0.40, 0.30, 0.20, 0.10]

GMDB_TYPES = ["ROP", "RATCHET", "ROLLUP"]

# Base surrender curve by year (requirements spec Section 8.3)
# 7-year schedule: year 7 has 60% shock; 10-year schedule: slightly lower
SC_SCHEDULE_7YR = [
    {"year": 1, "rate": 0.015},
    {"year": 2, "rate": 0.015},
    {"year": 3, "rate": 0.015},
    {"year": 4, "rate": 0.015},
    {"year": 5, "rate": 0.015},
    {"year": 6, "rate": 0.030},
    {"year": 7, "rate": 0.60},   # shock year
    {"year": 8, "rate": 0.12},
]

SC_SCHEDULE_10YR = [
    {"year": 1,  "rate": 0.015},
    {"year": 2,  "rate": 0.015},
    {"year": 3,  "rate": 0.015},
    {"year": 4,  "rate": 0.015},
    {"year": 5,  "rate": 0.015},
    {"year": 6,  "rate": 0.030},
    {"year": 7,  "rate": 0.030},
    {"year": 8,  "rate": 0.050},
    {"year": 9,  "rate": 0.050},
    {"year": 10, "rate": 0.55},  # shock year
    {"year": 11, "rate": 0.12},
]


def _advance_year(d: date) -> date:
    """Advance date by one year, clamping Feb 29 to Mar 1 in non-leap years."""
    try:
        return date(d.year + 1, d.month, d.day)
    except ValueError:
        return date(d.year + 1, 3, 1)


def _annuity_mortality_rate(issue_age: int, policy_year: int, gender: str) -> float:
    """
    Simplified 2012 IAR-calibrated mortality for annuity owners.

    Uses Makeham form calibrated to approximate 2012 IAR with Scale G2 improvement.
    Males have slightly higher mortality; improvement of ~1% per year applied.
    """
    att_age = issue_age + policy_year - 1
    # 2012 IAR-calibrated base (lower than life insurance tables — annuity selection)
    base_q = 0.0003 + 0.000012 * (1.105 ** max(0, att_age - 40))
    # Sex differential
    if gender == "M":
        base_q *= 1.15
    # G2 improvement: ~1% per year reduction, but floor so not negative
    improvement = min(0.12, 0.01 * policy_year)
    q = base_q * (1 - improvement)
    return min(float(q), 0.999)


def _get_surrender_rate(sc_schedule: list[dict], sc_year: int, default: float = 0.12) -> float:
    """Return the rate for the given schedule year, or ``default`` past the schedule end.

    ``default`` is 0.12 for the surrender-*probability* curve (the post-SC base surrender
    rate) and 0.0 for the surrender-*charge* schedule (no charge once expired).
    """
    for item in sc_schedule:
        if item["year"] == sc_year:
            return float(item["rate"])
    return default


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "products" / "annuity.yaml"


def _load_charge_schedules() -> dict:
    """Load surrender-CHARGE schedules from config/products/annuity.yaml.

    Returns ``{"7yr": [{"year": n, "rate": r}, ...], "10yr": [...]}``. This is the penalty
    schedule, distinct from the hardcoded surrender-*probability* curve (SC_SCHEDULE_*).
    """
    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    scs = cfg.get("surrender_charge_schedules", {})
    return {
        key: [{"year": int(d["year"]), "rate": float(d["rate"])} for d in scs.get(key, [])]
        for key in ("7yr", "10yr")
    }


def _glb_moneyness_suppression(moneyness_ratio: float) -> float:
    """
    GLB moneyness suppression multiplier (FR-1C-11).

    Returns min(1.0, 0.4 + 0.6 × moneyness_ratio).
    Low moneyness (in-the-money GLB) suppresses lapses.
    """
    return min(1.0, 0.4 + 0.6 * moneyness_ratio)


def generate_annuity_contracts(rng: np.random.Generator) -> pd.DataFrame:
    """Generate 1,400 deferred annuity contract records per requirements spec Section 8.3.

    Args:
        rng: Seeded NumPy random Generator.

    Returns:
        DataFrame with all columns from experience_study_technical_spec_v1.2.md Section C.3 for DA.
    """
    # Surrender-CHARGE schedules (penalty %) from config — distinct from the hardcoded
    # surrender-probability curves (SC_SCHEDULE_*) that drive the simulation below.
    _charge_cfg = _load_charge_schedules()
    sc_charge_7yr = _charge_cfg["7yr"]
    sc_charge_10yr = _charge_cfg["10yr"]

    study_start = date(2016, 1, 1)
    study_end   = date(2023, 12, 31)
    issue_start = date(2008, 1, 1)
    issue_end   = date(2023, 6, 30)

    # -- Fixed/FIA: 900 contracts --
    ages_f    = np.clip(pert_sample(rng, 45, 62, 80, size=N_FIXED).astype(int), 40, 85)
    offsets_f = rng.integers(0, (issue_end - issue_start).days + 1, size=N_FIXED)
    ch_f      = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_FIXED)
    st_f      = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_FIXED)
    pm_f      = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_FIXED)
    mt_f      = rng.choice(MARKET_TYPES, p=MARKET_TYPE_PROBS, size=N_FIXED)

    # -- Variable: 500 contracts --
    ages_v    = np.clip(pert_sample(rng, 45, 62, 80, size=N_VARIABLE).astype(int), 40, 85)
    offsets_v = rng.integers(0, (issue_end - issue_start).days + 1, size=N_VARIABLE)
    ch_v      = rng.choice(CHANNELS, p=CHANNEL_PROBS, size=N_VARIABLE)
    st_v      = rng.choice(US_STATES, p=_STATE_WEIGHTS, size=N_VARIABLE)
    pm_v      = rng.choice(PREMIUM_MODES, p=PREMIUM_MODE_PROBS, size=N_VARIABLE)
    mt_v      = rng.choice(MARKET_TYPES, p=MARKET_TYPE_PROBS, size=N_VARIABLE)

    batches = [
        ("FIXED", N_FIXED,    ages_f, offsets_f, ch_f, st_f, pm_f, mt_f),
        ("VAR",   N_VARIABLE, ages_v, offsets_v, ch_v, st_v, pm_v, mt_v),
    ]

    records: list[dict] = []
    global_idx = 0

    for batch_type, n, ages, offsets, ch_arr, st_arr, pm_arr, mt_arr in batches:
        for j in range(n):
            issue_age  = int(ages[j])
            issue_date = issue_start + timedelta(days=int(offsets[j]))
            channel    = str(ch_arr[j])
            state      = str(st_arr[j])
            prem_type  = str(pm_arr[j])
            market_type = str(mt_arr[j])
            gender     = "F" if rng.random() < 0.55 else "M"
            dob        = issue_age_to_dob(issue_date, issue_age)

            is_variable = (batch_type == "VAR")

            # Product code and type
            if is_variable:
                product_code = "DA_VA"
                product_type = "DA_VA"
                contract_prefix = "DAV"
            else:
                # ~70% fixed, ~30% FIA among fixed batch
                if rng.random() < 0.70:
                    product_code = "DA_FIXED"
                    product_type = "DA_FIXED"
                    contract_prefix = "DAF"
                else:
                    product_code = "DA_FIA"
                    product_type = "DA_FIA"
                    contract_prefix = "DAF"

            # Surrender charge schedule: 70% 7-year, 30% 10-year
            use_7yr = rng.random() < 0.70
            sc_schedule = SC_SCHEDULE_7YR if use_7yr else SC_SCHEDULE_10YR
            sc_schedule_len = 7 if use_7yr else 10
            sc_charge_schedule = sc_charge_7yr if use_7yr else sc_charge_10yr

            # Initial premium / account value seed
            init_av = round(
                np.clip(rng.lognormal(math.log(80_000), 1.0), 5_000, 1_000_000)
                / 100
            ) * 100

            # Accumulate account value over policy years
            policy_years_elapsed = max(1, study_end.year - issue_date.year)
            att_age_now = issue_age + policy_years_elapsed - 1

            av = float(init_av)
            benefit_base = float(init_av)  # GMDB benefit base tracks with ratchet/rollup

            # SC year at study end
            sc_year_at_study = min(policy_years_elapsed, sc_schedule_len + 1)

            for yr_offset in range(policy_years_elapsed):
                yr = issue_date.year + yr_offset
                macro_yr = min(yr, 2023)
                cr = MACRO_SCENARIO.get(macro_yr, MACRO_SCENARIO[2023])["credited_rate"]

                if is_variable:
                    # Variable: GBM equity growth
                    macro = MACRO_SCENARIO.get(macro_yr, MACRO_SCENARIO[2023])
                    equity_ret = macro["equity_return"]
                    av_growth = 0.60 * equity_ret + 0.40 * (cr - 0.005)
                else:
                    av_growth = cr - 0.005  # spread earned for insurer

                av = max(0.0, av * (1 + av_growth))

                # Flexible premium: additional deposits
                if prem_type == "FLEXIBLE" and yr_offset > 0 and yr_offset < 5:
                    av += init_av * 0.10 * rng.uniform(0.5, 1.5)

                # Benefit base ratchet for VA (simplified annual high-water mark)
                if is_variable:
                    benefit_base = max(benefit_base, av)

            av = round(max(0.0, av), 2)
            # Benefit base applies to any contract with a guaranteed benefit (GMDB or
            # GLWB), not just variable contracts. For non-variable GMDB contracts it is
            # the return-of-premium / rollup base (no high-water ratchet). Whether it is
            # emitted is decided at output time based on the presence of a rider.
            benefit_base = round(benefit_base, 2)

            # GMIR
            gmir = 0.030 if issue_date.year < 2009 else 0.010

            # Credited rate (most recent year)
            last_macro = MACRO_SCENARIO.get(min(study_end.year, 2023), MACRO_SCENARIO[2023])
            cr_current = round(last_macro["credited_rate"] + rng.uniform(-0.005, 0.010), 4)
            cr_current = round(max(gmir, min(0.08, cr_current)), 4)

            # SC remaining dollar amount = AV × the surrender-CHARGE rate for the current
            # SC year (penalty schedule), 0 once expired. Matches DQ-DA-01's expectation.
            sc_charge_rate = _get_surrender_rate(sc_charge_schedule, sc_year_at_study, default=0.0)
            sc_remaining = round(av * sc_charge_rate, 2)
            sc_expired = sc_year_at_study > sc_schedule_len

            if sc_expired:
                sc_remaining = 0.0

            # GLB features
            if is_variable:
                glwb_flag = rng.random() < 0.60
            elif product_type == "DA_FIA":
                glwb_flag = rng.random() < 0.40
            else:
                glwb_flag = False

            glwb_rate = None
            glwb_status = "WAITING"
            rider_fee = 0.0
            moneyness = None

            if glwb_flag:
                glwb_rate = 0.05  # standard 5% withdrawal rate
                rider_fee = round(rng.uniform(0.0075, 0.0125), 4)
                # Benefit base for GLWB: initial deposit × rollup or ratchet
                glwb_benefit_base = benefit_base if benefit_base else init_av * (1.05 ** policy_years_elapsed)
                glwb_benefit_base = round(glwb_benefit_base, 2)
                moneyness = round(av / glwb_benefit_base, 4) if glwb_benefit_base > 0 else 1.0
                benefit_base = glwb_benefit_base

                # Utilisation status: active for ~60% of policies past year 5
                if policy_years_elapsed > 5 and rng.random() < 0.60:
                    glwb_status = "ACTIVE"
                else:
                    glwb_status = "WAITING"

            # GMDB type
            gmdb_type = None
            if rng.random() < 0.70:  # ~70% have some GMDB
                gmdb_type = str(rng.choice(GMDB_TYPES, p=[0.50, 0.30, 0.20]))

            # MVA flag: 20% of DA_FIXED are MYGA
            mva_flag = (product_type == "DA_FIXED") and (rng.random() < 0.20)

            # Free withdrawal: 10%
            free_wd_pct = 0.10

            # --- Policy life simulation ---
            status_code = "IF"
            term_date   = None
            term_cause  = None

            cur_date = issue_date
            py = 0
            alive = True
            sc_yr = 1

            while alive and cur_date.year <= study_end.year:
                py += 1
                yr = cur_date.year
                sc_yr = min(py, sc_schedule_len + 1)

                if yr < study_start.year:
                    cur_date = _advance_year(cur_date)
                    continue

                att_age = issue_age + py - 1

                # Owner mortality (2012 IAR)
                q = _annuity_mortality_rate(issue_age, py, gender)
                if rng.random() < q:
                    status_code = "DEATH"
                    term_cause  = "DEATH_BENEFIT_CLAIM"
                    term_date   = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    alive = False
                    break

                # Surrender
                base_sc_rate = _get_surrender_rate(sc_schedule, sc_yr)
                macro_yr = min(yr, 2023)
                dyn_mult = get_lapse_multiplier(
                    macro_yr, MACRO_SCENARIO[macro_yr]["credited_rate"], "DA"
                )

                # GLB moneyness suppression (FR-1C-11)
                if glwb_flag and moneyness is not None:
                    mono_supp = _glb_moneyness_suppression(moneyness)
                else:
                    mono_supp = 1.0

                eff_surrender = min(base_sc_rate * dyn_mult * mono_supp, 0.99)

                if rng.random() < eff_surrender:
                    # After SC expires, high probability is full surrender
                    if sc_yr > sc_schedule_len:
                        status_code = "SURRENDER"
                        term_cause  = "FULL_SURRENDER"
                    elif sc_yr == sc_schedule_len:
                        # At SC expiry year: mostly full surrenders
                        status_code = "SURRENDER"
                        term_cause  = "FULL_SURRENDER"
                    else:
                        status_code = "SURRENDER"
                        term_cause  = "FULL_SURRENDER"
                    term_date = random_date_between(rng, cur_date, date(cur_date.year, 12, 31))
                    alive = False
                    break

                cur_date = _advance_year(cur_date)

            contract_id = f"{contract_prefix}-{global_idx + 1:07d}"

            records.append({
                "contract_id":                contract_id,
                "product_code":               product_code,
                "product_type":               product_type,
                "premium_type":               prem_type,
                "issue_date":                 issue_date.isoformat(),
                "date_of_birth":              dob.isoformat(),
                "issue_age_anb":              issue_age,
                "gender":                     gender,
                "market_type":                market_type,
                "account_value":              av,
                "benefit_base":               (round(benefit_base, 2)
                                               if (gmdb_type is not None or glwb_flag)
                                               else None),
                "surrender_charge_schedule":  json.dumps(sc_charge_schedule),
                "surrender_charge_remaining": sc_remaining,
                "surrender_charge_year":      sc_year_at_study,
                "free_withdrawal_allowance_pct": free_wd_pct,
                "guaranteed_min_interest_rate": gmir,
                "credited_rate_current":      cr_current,
                "market_value_adjustment_flag": mva_flag,
                "glwb_elected_flag":          glwb_flag,
                "gmdb_type":                  gmdb_type,
                "glwb_withdrawal_rate_pct":   glwb_rate,
                "glwb_utilization_status":    glwb_status if glwb_flag else "WAITING",
                "rider_fee_annual_rate":      rider_fee,
                "moneyness_ratio":            moneyness,
                "is_surrender_charge_expired_flag": sc_expired,
                "status_code":                status_code,
                "termination_date":           term_date.isoformat() if term_date else None,
                "termination_cause_code":     term_cause,
                "distribution_channel":       channel,
                "issue_state":                state,
            })
            global_idx += 1

    return pd.DataFrame(records)
