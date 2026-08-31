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


# ---------------------------------------------------------------------------
# Generation config (demo refresh P4) — config/synthetic_data.yaml
# ---------------------------------------------------------------------------

def _load_gen_config() -> dict:
    """Load config/synthetic_data.yaml (empty dict when absent)."""
    import yaml

    cfg_path = (
        __import__("pathlib").Path(__file__).resolve().parent.parent.parent
        / "config" / "synthetic_data.yaml"
    )
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


GEN_CONFIG: dict = _load_gen_config()


def gen_volume(product_key: str, default: int) -> int:
    """Policy volume for a product from config (falls back to the legacy count)."""
    return int((GEN_CONFIG.get("volumes") or {}).get(product_key, default))


def ci_penetration(product_key: str, default: float) -> float:
    """CI-rider penetration probability for a product from config."""
    return float(((GEN_CONFIG.get("ci") or {}).get("penetration") or {}).get(product_key, default))


def ci_incidence_multiplier() -> float:
    """Multiplier applied to BOTH CI draws and the CI reference table (volume, not signal)."""
    return float((GEN_CONFIG.get("ci") or {}).get("incidence_multiplier", 1.0))


def ci_incidence_rate(attained_age: float) -> float:
    """Annual CI claim probability for one policy-year (draw side).

    base × age factor × the configured incidence multiplier, capped so the
    per-year probability stays sane even at extreme ages.
    """
    mult = ci_incidence_multiplier()
    rate = CI_BASE_INCIDENCE_PER_1000 * ci_age_factor(attained_age) * mult / 1_000.0
    return min(rate, 0.05 * max(mult, 1.0))


def _story_multiplier(story_key: str, cal_year: int, product_code: str) -> float:
    """Draw-side story multiplier from config ``stories.<story_key>`` (1.0 default)."""
    story = (GEN_CONFIG.get("stories") or {}).get(story_key) or {}
    products = story.get("products") or []
    if product_code not in products:
        return 1.0
    factors = story.get("factors") or {}
    return float(factors.get(cal_year, factors.get(str(cal_year), 1.0)))


def mortality_story_multiplier(cal_year: int, product_code: str) -> float:
    """Planted mortality-deterioration multiplier (draw side only)."""
    return _story_multiplier("mortality_deterioration", cal_year, product_code)


def lapse_story_multiplier(cal_year: int, product_code: str) -> float:
    """Planted lapse-spike multiplier (draw side only)."""
    return _story_multiplier("lapse_spike", cal_year, product_code)


# ---------------------------------------------------------------------------
# Institutional entities + claim-level identity fields (demo refresh P4)
# ---------------------------------------------------------------------------

# US census-style region map for claim_region (reference geography, not config).
_REGION_STATES: dict[str, tuple[str, ...]] = {
    "NORTHEAST":  ("CT", "ME", "MA", "NH", "NJ", "NY", "PA", "RI", "VT"),
    "MIDWEST":    ("IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI"),
    "SOUTH_ATL":  ("DE", "FL", "GA", "MD", "NC", "SC", "VA", "WV", "DC"),
    "SOUTH_CENT": ("AL", "AR", "KY", "LA", "MS", "OK", "TN", "TX"),
    "MOUNTAIN":   ("AZ", "CO", "ID", "MT", "NM", "UT", "WY"),
    "SOUTHWEST":  ("NV",),
    "PACIFIC":    ("AK", "CA", "HI", "OR", "WA"),
}
STATE_TO_REGION: dict[str, str] = {
    st: region for region, states in _REGION_STATES.items() for st in states
}


def sample_offices_and_agents(rng: np.random.Generator, n: int) -> tuple[list[str], list[str]]:
    """Sample an agency office + agent for ``n`` policies.

    Offices are OFF-001…OFF-<n_offices> (uniform); each office has
    ``agents_per_office`` agents AGT-<office#><agent#> so agent → office is fixed.
    """
    ent = GEN_CONFIG.get("entities") or {}
    n_offices = int(ent.get("n_offices", 40))
    agents_per = int(ent.get("agents_per_office", 8))
    office_ix = rng.integers(1, n_offices + 1, size=n)
    agent_ix = rng.integers(1, agents_per + 1, size=n)
    offices = [f"OFF-{int(o):03d}" for o in office_ix]
    agents = [f"AGT-{int(o):03d}{int(a)}" for o, a in zip(office_ix, agent_ix)]
    return offices, agents


#: Keeps each product's reuse stream distinct while staying reproducible.
_PREFIX_SALT = {"TRM": 0, "WL": 1, "UL": 2, "VUL": 3, "DA": 4}


def assign_claim_fields(
    df: pd.DataFrame, rng: np.random.Generator, id_prefix: str
) -> pd.DataFrame:
    """Stamp claimant/hospital/region identity onto claim rows (DEATH / CI_CLAIM).

    Non-claim rows get None. ``claimant_id`` is unique per policy by default
    (CLM-<prefix>-<seq>); the fraud-ring planting later overrides a shared one.
    ``claim_region`` derives from issue_state (claims are serviced in-region).
    """
    ent = GEN_CONFIG.get("entities") or {}
    n_hosp = int(ent.get("n_hospitals", 60))

    is_claim = df["status_code"].isin(["DEATH", "CI_CLAIM"])
    n = len(df)
    hosp_ix = rng.integers(1, n_hosp + 1, size=n)

    df = df.copy()
    # A claimant id per policy makes a repeat claimant structurally impossible, so
    # the "similar claims, same claimant" rule could only ever fire on the planted
    # ring — a plant-detector, not a detector (adversarial review m-11). Real books
    # have cross-holding: a share of people hold more than one policy.
    #
    # The reuse map is drawn from a DEDICATED generator, never the shared ``rng``.
    # ``generate_all`` threads one rng sequentially through all five products, so
    # taking even one extra draw here would reshuffle every later product's
    # decrements and move every demo figure. This way only claimant_id changes.
    reuse_frac = float(ent.get("claimant_reuse_fraction", 0.0))
    person_seed = int(ent.get("claimant_reuse_seed", 4242))

    claimants = [
        f"CLM-{id_prefix}-{i + 1:06d}" if flag else None
        for i, flag in enumerate(is_claim)
    ]
    if reuse_frac > 0:
        prng = np.random.default_rng(person_seed + _PREFIX_SALT.get(id_prefix, 0))
        idx = [i for i, c in enumerate(claimants) if c is not None]
        # Pair up a fraction of claimants: the second of each pair takes the
        # first's identity, i.e. one person holding two policies.
        n_pairs = int(len(idx) * reuse_frac / 2)
        if n_pairs > 0 and len(idx) >= 2:
            chosen = prng.choice(len(idx), size=min(2 * n_pairs, len(idx)),
                                 replace=False)
            for a, b in zip(chosen[0::2], chosen[1::2]):
                claimants[idx[b]] = claimants[idx[a]]
    df["claimant_id"] = claimants
    df["hospital_id"] = [
        f"HOSP-{int(h):03d}" if flag else None for h, flag in zip(hosp_ix, is_claim)
    ]
    df["claim_region"] = [
        STATE_TO_REGION.get(st, "OTHER") if flag else None
        for st, flag in zip(df["issue_state"], is_claim)
    ]
    return df


def plant_fraud_ring(term_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Overwrite a handful of Term policies as the planted fraud ring.

    Ring pattern (all parameters in config ``fraud_ring``): policies issued in
    the configured window through one rogue office, each with a CI claim inside
    policy year 1, all treated at one out-of-roster hospital in one state's
    region, several sharing a claimant with the same illness and near-identical
    amounts. The P5 rule engine is expected to surface exactly this cluster.
    """
    ring = GEN_CONFIG.get("fraud_ring") or {}
    if not ring:
        return term_df

    n_ring = int(ring.get("n_ring_policies", 14))
    n_shared = int(ring.get("n_shared_claimant", 4))
    office = str(ring.get("office_id", "OFF-013"))
    hospital = str(ring.get("hospital_id", "HOSP-066"))
    shared_claimant = str(ring.get("claimant_id", "CLM-424242"))
    illness = str(ring.get("illness_code", "CI-001"))
    face = float(ring.get("face_amount", 120000))
    y0 = int(ring.get("issue_year_from", 2021))
    y1 = int(ring.get("issue_year_to", 2023))
    ring_state = str(ring.get("ring_state", "NV"))

    df = term_df.copy()
    # Overwrite the LAST n_ring rows (stable, order-independent of the draws).
    idx = df.index[-n_ring:]
    ent = GEN_CONFIG.get("entities") or {}
    agents_per = int(ent.get("agents_per_office", 8))
    office_no = int(office.split("-")[1])

    for k, i in enumerate(idx):
        issue_year = int(rng.integers(y0, y1 + 1))
        issue_d = random_date_between(
            rng, date(issue_year, 1, 1), date(issue_year, 9, 30)
        )
        claim_d = issue_d + timedelta(days=int(rng.integers(45, 300)))
        claim_d = min(claim_d, STUDY_END)
        ring_face = round(face * float(rng.uniform(0.95, 1.05)) / 1000) * 1000
        sa = round(ring_face * 0.50, 2)
        df.loc[i, "issue_date"] = issue_d.isoformat()
        df.loc[i, "date_of_birth"] = issue_age_to_dob(issue_d, 42).isoformat()
        df.loc[i, "issue_age_anb"] = 42
        df.loc[i, "face_amount"] = float(ring_face)
        df.loc[i, "status_code"] = "CI_CLAIM"
        df.loc[i, "termination_date"] = claim_d.isoformat()
        df.loc[i, "termination_cause_code"] = "CI_ACCELERATED_BENEFIT"
        df.loc[i, "illness_code"] = illness
        df.loc[i, "ci_rider_flag"] = True
        df.loc[i, "ci_rider_sum_assured"] = sa
        df.loc[i, "ci_rider_premium"] = round(0.0003 * sa, 2)
        df.loc[i, "conversion_flag"] = False
        df.loc[i, "agency_office_id"] = office
        df.loc[i, "agent_id"] = f"AGT-{office_no:03d}{int(rng.integers(1, agents_per + 1))}"
        df.loc[i, "issue_state"] = ring_state
        df.loc[i, "hospital_id"] = hospital
        df.loc[i, "claim_region"] = STATE_TO_REGION.get(ring_state, "OTHER")
        df.loc[i, "claimant_id"] = (
            shared_claimant if k < n_shared else f"CLM-RING-{k + 1:03d}"
        )
    return df
