"""Shared statistics helpers for aggregate A/E credibility and confidence intervals.

Aggregate-level credibility Z and the 95% Poisson confidence interval MUST be
recomputed from the *summed* actual-claim count of the aggregate — they must never
be produced by averaging the per-cell values stored in ``gold_ae_results`` (doing so
collapses Z toward 0 and produces meaningless, sometimes negative, CI bounds).

Formulas (FR-1A-24, FR-1A-25):
    Limited Fluctuation Z = min(1, sqrt(actual_claims / 1082))
    Buhlmann (fixed-K) Z  = sqrt(actual_claims / (actual_claims + K)), K = 1082
    Poisson SE            = A/E / sqrt(actual_claims)
    95% CI                = A/E +/- 1.96 * SE     (lower bound floored at 0)
    Credibility-wtd A/E   = Z * A/E + (1 - Z) * complement
"""
import numpy as np

FULL_CREDIBILITY_CLAIMS = 1082.0  # Limited Fluctuation full-credibility standard (FR-1A-24)
_Z95 = 1.96


def credibility_z(actual_claims, method="LF", threshold=FULL_CREDIBILITY_CLAIMS):
    """Credibility Z from an *aggregate* claim count.

    LF (Limited Fluctuation):
        Z = min(1, sqrt(actual_claims / threshold))
    BUHLMANN (simplified fixed-K):
        Z = sqrt(actual_claims / (actual_claims + threshold))  (threshold reused as K)

    ``method`` is case-insensitive and defaults to "LF" (backward compatible).
    Accepts a scalar or array-like and returns the matching type. Z is 0 when
    there are no claims.
    """
    arr = np.asarray(actual_claims, dtype=float)
    clipped = np.clip(arr, 0.0, None)
    if (method or "LF").strip().upper() == "BUHLMANN":
        z = np.sqrt(clipped / (clipped + threshold))
    else:
        z = np.minimum(np.sqrt(clipped / threshold), 1.0)
    z = np.where(arr > 0, z, 0.0)
    return z if z.ndim else float(z)


def poisson_ci(ae_ratio, actual_claims, z_score=_Z95):
    """95% Poisson CI on an *aggregate* A/E ratio.

    SE = A/E / sqrt(actual_claims); CI = A/E +/- z_score * SE, lower floored at 0.
    Returns ``(lower, upper)`` as floats (scalar input) or arrays (array input).
    Bounds are NaN where there are no claims or the A/E ratio is undefined.
    """
    ae = np.asarray(ae_ratio, dtype=float)
    n = np.asarray(actual_claims, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        se = np.where((n > 0) & ~np.isnan(ae), ae / np.sqrt(n), np.nan)
    lower = np.clip(ae - z_score * se, 0.0, None)
    upper = ae + z_score * se
    if ae.ndim == 0:
        return float(lower), float(upper)
    return lower, upper


def credibility_weighted_ae(ae_ratio, z, complement=1.0):
    """Credibility-weighted A/E = Z * A/E + (1 - Z) * complement (FR-1A-24)."""
    ae = np.asarray(ae_ratio, dtype=float)
    zz = np.asarray(z, dtype=float)
    cw = zz * ae + (1.0 - zz) * complement
    cw = np.where(np.isnan(ae), np.nan, cw)
    return cw if cw.ndim else float(cw)


def get_run_method(con, run_id, default="LF"):
    """Return the credibility method selected for a study run.

    Reads ``gold_study_runs.credibility_method`` for ``run_id`` using the open
    DuckDB connection ``con``. Returns ``default`` ("LF") when the run is missing
    or the column is NULL, so callers can pass the result straight into
    ``credibility_z(..., method=...)``.
    """
    if run_id is None:
        return default
    row = con.execute(
        "SELECT credibility_method FROM gold_study_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if row is None or row[0] is None:
        return default
    return str(row[0])
