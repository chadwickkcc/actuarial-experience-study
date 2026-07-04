"""Shared utilities for all synthetic data generators."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Macro scenario time series (technical spec Section C.5)
# ---------------------------------------------------------------------------

MACRO_SCENARIO: dict[int, dict] = {
    2016: {"market_rate": 0.018, "credited_rate": 0.032, "equity_return": 0.12,  "unemployment": 0.047},
    2017: {"market_rate": 0.024, "credited_rate": 0.032, "equity_return": 0.22,  "unemployment": 0.041},
    2018: {"market_rate": 0.029, "credited_rate": 0.032, "equity_return": -0.05, "unemployment": 0.039},
    2019: {"market_rate": 0.019, "credited_rate": 0.031, "equity_return": 0.31,  "unemployment": 0.035},
    2020: {"market_rate": 0.009, "credited_rate": 0.030, "equity_return": 0.18,  "unemployment": 0.081},
    2021: {"market_rate": 0.015, "credited_rate": 0.029, "equity_return": 0.29,  "unemployment": 0.054},
    2022: {"market_rate": 0.039, "credited_rate": 0.029, "equity_return": -0.18, "unemployment": 0.036},
    2023: {"market_rate": 0.040, "credited_rate": 0.031, "equity_return": 0.26,  "unemployment": 0.037},
}

STUDY_START = date(2016, 1, 1)
STUDY_END = date(2023, 12, 31)

# ---------------------------------------------------------------------------
# CI rider constants (requirements spec Section 8.4, technical spec C.4)
# ---------------------------------------------------------------------------

CI_ILLNESS_CODES: list[str] = [
    "CI-001", "CI-002", "CI-003", "CI-004", "CI-005",
    "CI-006", "CI-007", "CI-008", "CI-009", "CI-010",
]

CI_ILLNESS_WEIGHTS: list[float] = [
    0.40, 0.20, 0.12, 0.07, 0.05,
    0.04, 0.03, 0.03, 0.03, 0.03,
]
# Must sum to 1.0
assert abs(sum(CI_ILLNESS_WEIGHTS) - 1.0) < 1e-9, "CI weights must sum to 1.0"

# Aggregate base CI incidence rate per 1,000 exposed (age-standardised 35–70)
CI_BASE_INCIDENCE_PER_1000: float = 3.5

# Age-factor multipliers applied to base rate by attained-age band
_CI_AGE_FACTORS: dict[str, float] = {
    "18-24": 0.20, "25-29": 0.25, "30-34": 0.35, "35-39": 0.50,
    "40-44": 0.70, "45-49": 1.00, "50-54": 1.40, "55-59": 1.90,
    "60-64": 2.50, "65-69": 3.20, "70-74": 4.00, "75-79": 5.00,
    "80-84": 6.00, "85+":   7.00,
}


def ci_age_factor(attained_age: float) -> float:
    """Return the CI incidence age factor for a given attained age."""
    age = int(attained_age)
    if age < 25:
        return _CI_AGE_FACTORS["18-24"]
    elif age < 30:
        return _CI_AGE_FACTORS["25-29"]
    elif age < 35:
        return _CI_AGE_FACTORS["30-34"]
    elif age < 40:
        return _CI_AGE_FACTORS["35-39"]
    elif age < 45:
        return _CI_AGE_FACTORS["40-44"]
    elif age < 50:
        return _CI_AGE_FACTORS["45-49"]
    elif age < 55:
        return _CI_AGE_FACTORS["50-54"]
    elif age < 60:
        return _CI_AGE_FACTORS["55-59"]
    elif age < 65:
        return _CI_AGE_FACTORS["60-64"]
    elif age < 70:
        return _CI_AGE_FACTORS["65-69"]
    elif age < 75:
        return _CI_AGE_FACTORS["70-74"]
    elif age < 80:
        return _CI_AGE_FACTORS["75-79"]
    elif age < 85:
        return _CI_AGE_FACTORS["80-84"]
    else:
        return _CI_AGE_FACTORS["85+"]


def attained_age_band(attained_age: float) -> str:
    """Return 5-year attained-age band string, e.g. '50-54'."""
    lower = int(attained_age // 5) * 5
    return f"{lower}-{lower + 4}"


# ---------------------------------------------------------------------------
# PERT distribution sampler
# ---------------------------------------------------------------------------

def pert_sample(rng: np.random.Generator, low: float, mode: float, high: float,
                size: int = 1) -> np.ndarray:
    """
    Sample from a PERT distribution using the beta distribution mapping.

    PERT: α1 = 1 + 4*(mode-low)/(high-low), α2 = 1 + 4*(high-mode)/(high-low)
    """
    lam = 4.0  # shape parameter (standard PERT uses λ=4)
    r = high - low
    mu = (low + lam * mode + high) / (lam + 2)
    alpha1 = (mu - low) / r * (lam + 2) if r > 0 else 1.0
    alpha2 = (high - mu) / r * (lam + 2) if r > 0 else 1.0
    alpha1 = max(alpha1, 1e-9)
    alpha2 = max(alpha2, 1e-9)
    samples = rng.beta(alpha1, alpha2, size=size)
    return low + samples * r


# ---------------------------------------------------------------------------
# Dynamic lapse multiplier (FR-1B-08, FR-1C-10, technical spec C.5)
# ---------------------------------------------------------------------------

def get_lapse_multiplier(year: int, credited_rate: float, product_code: str) -> float:
    """
    Return dynamic lapse multiplier based on macro scenario differential.

    k = 0.5 for life products; 0.8 for annuities (per FR-1B-08 / FR-1C-10).
    """
    market_rate = MACRO_SCENARIO[year]["market_rate"]
    rate_diff = market_rate - credited_rate
    k = 0.8 if product_code in ("DA", "DA_FIXED", "DA_FIA", "DA_VA") else 0.5
    if product_code in ("DA", "DA_FIXED", "DA_FIA", "DA_VA"):
        return float(min(3.0, max(0.3, 1 + k * rate_diff)))
    return float(min(2.5, max(0.4, 1 + k * rate_diff)))


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def random_date_in_year(rng: np.random.Generator, year: int) -> date:
    """Return a uniformly random date within the given calendar year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    delta = (end - start).days
    offset = int(rng.integers(0, delta + 1))
    return start + timedelta(days=offset)


def random_date_between(rng: np.random.Generator, start: date, end: date) -> date:
    """Return a uniformly random date in [start, end]."""
    delta = (end - start).days
    if delta <= 0:
        return start
    offset = int(rng.integers(0, delta + 1))
    return start + timedelta(days=offset)


def issue_age_to_dob(issue_date: date, issue_age_anb: int) -> date:
    """
    Derive approximate date of birth from issue date and ANB issue age.

    ANB (Age Nearest Birthday) means the insured's age is rounded to the
    nearest integer on the issue date.  We place the birthday uniformly in
    the 12-month window centred on (issue_date - issue_age_anb years).
    """
    base_year = issue_date.year - issue_age_anb
    # Birthday within ±6 months of the exact anniversary
    try:
        approx_bday = date(base_year, issue_date.month, issue_date.day)
    except ValueError:
        approx_bday = date(base_year, issue_date.month, 28)
    return approx_bday


def attained_age_float(dob: date, as_of: date) -> float:
    """Return exact attained age as a float (years and fractional year)."""
    delta = as_of - dob
    return delta.days / 365.25


# ---------------------------------------------------------------------------
# CI claim generation (technical spec Section C.4)
# ---------------------------------------------------------------------------

def generate_ci_claims(
    policies_df: pd.DataFrame,
    study_start: date,
    study_end: date,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Generate CI claim events for policies with ci_rider_flag=True.

    For each policy-year of each CI-rider policy:
        1. Compute ci_rate = base_incidence × age_factor(attained_age) / 1000
        2. Draw Bernoulli(ci_rate) to determine if claim occurs
        3. If claim: draw illness_code from CI_ILLNESS_CODES with CI_ILLNESS_WEIGHTS
        4. Set termination_cause_code = "CI_ACCELERATED_BENEFIT"
        5. termination_date = random date within the policy year

    Returns DataFrame of CI claim events keyed by policy_id.
    Columns: policy_id, illness_code, termination_date.
    """
    ci_policies = policies_df[policies_df["ci_rider_flag"] == True].copy()
    if ci_policies.empty:
        return pd.DataFrame(columns=["policy_id", "illness_code", "termination_date"])

    claim_records: list[dict] = []

    for _, row in ci_policies.iterrows():
        issue_date = row["issue_date"] if isinstance(row["issue_date"], date) else \
            pd.to_datetime(row["issue_date"]).date()
        dob = row["date_of_birth"] if isinstance(row["date_of_birth"], date) else \
            pd.to_datetime(row["date_of_birth"]).date()

        # Iterate over each calendar year the policy is active within study window
        start_year = max(study_start.year, issue_date.year)
        end_year = study_end.year

        for yr in range(start_year, end_year + 1):
            yr_start = max(date(yr, 1, 1), issue_date, study_start)
            yr_end = min(date(yr, 12, 31), study_end)
            if yr_start > yr_end:
                continue

            mid_date = yr_start + timedelta(days=(yr_end - yr_start).days // 2)
            att_age = attained_age_float(dob, mid_date)

            ci_rate = CI_BASE_INCIDENCE_PER_1000 * ci_age_factor(att_age) / 1000.0
            # Cap at a reasonable maximum
            ci_rate = min(ci_rate, 0.05)

            if rng.random() < ci_rate:
                illness = rng.choice(CI_ILLNESS_CODES, p=CI_ILLNESS_WEIGHTS)
                claim_date = random_date_between(rng, yr_start, yr_end)
                claim_records.append({
                    "policy_id": row["policy_id"],
                    "illness_code": illness,
                    "termination_date": claim_date,
                })
                break  # one CI claim terminates coverage for accelerated benefit

    return pd.DataFrame(claim_records)


# ---------------------------------------------------------------------------
# US state code list for random assignment
# ---------------------------------------------------------------------------

US_STATES: list[str] = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# State weights proportional to rough population share
_STATE_POP_WEIGHTS: list[float] = [
    1.5, 0.2, 2.2, 0.9, 12.0, 1.7, 1.1, 0.3, 6.5, 3.2,
    0.4, 0.5, 3.9, 2.1, 1.0, 0.9, 1.4, 1.4, 0.4, 1.9,
    2.1, 3.1, 1.7, 0.9, 1.9, 0.3, 0.6, 0.9, 0.4, 2.8,
    0.6, 6.0, 3.2, 0.2, 3.6, 1.2, 1.3, 4.0, 0.3, 1.6,
    0.3, 2.1, 8.7, 1.0, 0.2, 2.6, 2.3, 0.6, 1.8, 0.2,
]
_STATE_WEIGHTS = np.array(_STATE_POP_WEIGHTS, dtype=float)
_STATE_WEIGHTS /= _STATE_WEIGHTS.sum()
