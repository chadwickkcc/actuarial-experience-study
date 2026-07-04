"""Known true A/E adjustment factors for synthetic-truth GLM validation.

Session 15 (Phase 3a) — supports FR-3A-26/27.

The GLM proposes *A/E adjustment factors* (multipliers on the reference tables)
and must be shown to recover the synthetic generator's known true factors. This
module is the single source of those known factors, used by both:

  * the ``synthetic_db`` test fixture, which draws actual decrement counts as
    ``Poisson(expected x true_factor)`` per cell (seed 42), and
  * ``src.ai.glm.validate.validate_against_truth``, which compares the GLM's
    published factors to the expected-weighted true factor at the output grain.

Because both sides read the *same* factor functions here, the ground truth is
consistent by construction. The factor structure is deliberately multiplicative
(log-linear in the GLM covariates) — mirroring the real generator, whose rates
are a Makeham base times multiplicative risk-class / selection / smoker factors
(``synthetic_data/generators/term.py``) — so a correctly-specified main-effects
GLM can recover it. The risk-class key list and the CI age structure are reused
directly from the generators so the truth stays tied to the generator's design.

This module is read-only: it computes factors, never writes data or models.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from synthetic_data.generators.term import RISK_CLASS_MORTALITY_FACTOR
from synthetic_data.generators.common import ci_age_factor


# ---------------------------------------------------------------------------
# Covariate value sets used by the synthetic fixture (kept tight for runtime)
# ---------------------------------------------------------------------------
RISK_CLASSES = list(RISK_CLASS_MORTALITY_FACTOR.keys())   # reuse generator's class list
ATTAINED_AGE_BANDS = ["35-39", "40-44", "45-49", "50-54", "55-59", "60-64"]
DURATION_BANDS = ["1", "2-5", "6-10", "11-15"]


def _age_band_mid(band: str) -> float:
    """Representative midpoint age of an attained-age band (e.g. "45-49" -> 47)."""
    lo, hi = band.split("-")
    return (int(lo) + int(hi)) / 2.0


# ---------------------------------------------------------------------------
# Mortality true A/E factor — log-linear in the GLM covariates
# ---------------------------------------------------------------------------
# Each component is a residual deviation that survives *after* the reference
# table is applied (so values sit in a realistic A/E range ~0.8-1.15).
_MORT_GENDER = {"M": 1.00, "F": 0.97}
_MORT_SMOKER = {"NS": 1.00, "SM": 1.06}
_MORT_RISK = {                       # residual A/E by risk class (keys reused from generator)
    "SUPER_PREF": 0.90,
    "PREF_NS":    0.95,
    "STD_NS":     1.00,
    "PREF_SM":    1.03,
    "STD_SM":     1.06,
}
_MORT_DURATION = {"1": 0.88, "2-5": 0.93, "6-10": 0.97, "11-15": 1.00, "11+": 1.00}
_MORT_BASE = 0.95


def mortality_true_factor(
    gender: str,
    smoker_status: str,
    risk_class: str,
    attained_age_band: str,
    duration_band: str,
) -> float:
    """True mortality A/E adjustment factor for one fitting cell."""
    age_adj = 0.90 + 0.06 * (_age_band_mid(attained_age_band) - 35.0) / 25.0  # 0.90 -> ~0.96
    return (
        _MORT_BASE
        * _MORT_GENDER.get(gender, 1.0)
        * _MORT_SMOKER.get(smoker_status, 1.0)
        * _MORT_RISK.get(risk_class, 1.0)
        * _MORT_DURATION.get(duration_band, 1.0)
        * age_adj
    )


# ---------------------------------------------------------------------------
# Lapse true A/E factor — log-linear in product x duration x jump band
# ---------------------------------------------------------------------------
_LAPSE_PRODUCT = {"TERM": 1.00, "WL": 0.97, "UL": 1.03, "ULSG": 0.95, "VUL": 1.01}
_LAPSE_DURATION = {"1": 1.06, "2-5": 1.00, "6-10": 0.95, "11-15": 0.93, "11+": 0.93}
_LAPSE_JUMP = {None: 1.00, "N/A": 1.00, "<=2x": 1.02, "2-3x": 1.04, "3-5x": 1.06}
_LAPSE_BASE = 1.00


def lapse_true_factor(
    product_code: str,
    duration_band: str,
    premium_jump_ratio_band: str | None = None,
) -> float:
    """True lapse A/E adjustment factor for one fitting cell."""
    return (
        _LAPSE_BASE
        * _LAPSE_PRODUCT.get(product_code, 1.0)
        * _LAPSE_DURATION.get(duration_band, 1.0)
        * _LAPSE_JUMP.get(premium_jump_ratio_band, 1.0)
    )


# ---------------------------------------------------------------------------
# CI incidence true A/E factor — reuses the generator's ci_age_factor ordering,
# compressed toward 1.0 so it reads as a residual A/E rather than a raw rate.
# ---------------------------------------------------------------------------
_CI_GENDER = {"M": 1.00, "F": 0.96}
_CI_BASE = 1.00

# Geometric-mean-normalise ci_age_factor over the bands in use, then compress.
_CI_RAW = {b: ci_age_factor(_age_band_mid(b)) for b in ATTAINED_AGE_BANDS}
_CI_GEOMEAN = math.exp(np.mean([math.log(v) for v in _CI_RAW.values()]))
_CI_COMPRESS = 0.10   # pull the (normalised) age signal close to 1.0


def ci_true_factor(attained_age_band: str, gender: str) -> float:
    """True CI-incidence A/E adjustment factor for one fitting cell."""
    raw = ci_age_factor(_age_band_mid(attained_age_band))
    normalised = raw / _CI_GEOMEAN
    age_adj = math.exp(_CI_COMPRESS * math.log(normalised))
    return _CI_BASE * _CI_GENDER.get(gender, 1.0) * age_adj


# ---------------------------------------------------------------------------
# Output-grain aggregation: the value the GLM must recover
# ---------------------------------------------------------------------------
# Config output-grain tokens differ from gold_ae_results column names.
GRAIN_TOKEN_TO_COLUMN = {
    "product": "product_code",
    "sex": "gender",
    "smoker": "smoker_status",
}


def output_grain_true_factors(
    cells: pd.DataFrame,
    output_grain: list[str],
    factor_col: str = "true_factor",
    expected_col: str = "expected",
) -> dict[tuple, float]:
    """Expected-weighted true factor at the output grain.

    The true factor at an output-grain cell is
    ``sum(expected * true_factor) / sum(expected)`` over the fitting cells in
    that group — identical in form to ``sum(predicted) / sum(reference)`` that
    the GLM aggregates, so the two are directly comparable.

    Args:
        cells:        fitting-grain cells with the grain columns, an expected
                      (reference) events column, and a per-cell true factor.
        output_grain: config grain tokens (e.g. ["product", "sex", ...]).
        factor_col:   column holding the per-cell true factor.
        expected_col: column holding the per-cell reference expected events.

    Returns:
        Dict keyed by the tuple of grain-column values, mapping to the
        expected-weighted true factor.
    """
    cols = [GRAIN_TOKEN_TO_COLUMN.get(tok, tok) for tok in output_grain]
    out: dict[tuple, float] = {}
    weighted = cells[expected_col] * cells[factor_col]
    grouped = cells.assign(_w=weighted).groupby(cols, dropna=False)
    for key, sub in grouped:
        key_tuple = key if isinstance(key, tuple) else (key,)
        denom = sub[expected_col].sum()
        out[key_tuple] = float(sub["_w"].sum() / denom) if denom > 0 else float("nan")
    return out
