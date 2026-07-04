"""Credibility Envelope Analyser — Phase 2.

Implements Technical Specification v1.1 Section B.11:
    - run_envelope_analysis()    FR-2-27 to FR-2-33
    - identify_top5_decrements() FR-2-28
    - run_tev_fast()             inner-loop TEV evaluation (no DB writes)

The analyser computes the maximum and minimum aggregate TEV reachable within
the credibility bounds for the top-5 most TEV-sensitive decrements.  It is a
governance artefact only — it never creates or modifies an AssumptionSet.

ARCHITECTURAL INVARIANT:
    No code path in this module converts EnvelopeResult or the envelope YAML
    into an AssumptionSet.  The UI must display the envelope as read-only.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import minimize, Bounds

from src.utils.types import EnvelopeResult, AssumptionSetStatus
from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    load_assumption_set,
)
from src.tev.tev_core import (
    project_cashflows,
    compute_pvfp,
    compute_pvcoc,
)
from src.tev.impact_matrix import SENSITIVITY_ORDER


# ---------------------------------------------------------------------------
# Decrement key definitions (unchanged from optimiser.py)
# ---------------------------------------------------------------------------

#: Maps each decrement key to the sensitivity IDs that perturb it
DECREMENT_TO_SENSITIVITIES: dict[str, list[str]] = {
    "lapse":             ["SENS-01", "SENS-02"],
    "mortality_life":    ["SENS-03", "SENS-04"],
    "mortality_annuity": ["SENS-05"],
    "ci_incidence":      ["SENS-06", "SENS-07"],
    "expense":           ["SENS-08", "SENS-09"],
}

#: Life-insurance product codes (exclude DA family for mortality_life)
_LIFE_PRODUCTS = {"TERM", "WL", "UL", "ULSG", "IUL", "VUL"}

#: Deferred-annuity product codes (for mortality_annuity)
_DA_PRODUCTS = {"DA", "DA_FIXED", "DA_FIA", "DA_VA"}

#: Default projection horizon for the fast inner loop — overridden by tev_config.yaml
_FAST_PROJECTION_YEARS_DEFAULT = 25

#: Small relative tolerance for the tev_min <= proposed <= tev_max sanity check
_CONTAINMENT_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Public API — Section B.11
# ---------------------------------------------------------------------------

def identify_top5_decrements(
    impact_matrix_df: pd.DataFrame,
    assumption_set: AssumptionSet,
) -> list[str]:
    """Return the 5 decrement keys with the largest TEV sensitivity.

    Ranks decrement types by max(|ΔTEV|) across their paired sensitivities
    in the TOTAL row of the impact matrix.  Preference is given to decrements
    that have credibility bounds from the A/E study (lapse, mortality, CI);
    expense and rdr fill remaining slots if fewer than 5 A/E-backed decrements
    are available.

    Only includes a decrement if at least one of its paired sensitivity columns
    appears in the impact matrix (avoids KeyError on sparse grids).

    Args:
        impact_matrix_df: TEV-impact matrix from run_sensitivity_grid().
        assumption_set:   The base AssumptionSet (used to check which decrements
                          have credibility bounds).

    Returns:
        List of decrement key strings (up to 5), sorted most-impactful first.
    """
    if impact_matrix_df.empty:
        return []
    if "TOTAL" not in impact_matrix_df.index:
        total_row = impact_matrix_df.iloc[-1]
    else:
        total_row = impact_matrix_df.loc["TOTAL"]

    decrement_impact: dict[str, float] = {}
    for dec_key, sens_ids in DECREMENT_TO_SENSITIVITIES.items():
        available = [s for s in sens_ids if s in total_row.index]
        if not available:
            continue
        max_abs_delta = float(total_row[available].abs().max())
        decrement_impact[dec_key] = max_abs_delta

    ranked = sorted(decrement_impact.items(), key=lambda kv: kv[1], reverse=True)

    ae_backed = {"lapse", "mortality_life", "mortality_annuity", "ci_incidence"}
    selected: list[str] = []

    for dec_key, _ in ranked:
        if dec_key in ae_backed and _has_multipliers(assumption_set, dec_key):
            selected.append(dec_key)
        if len(selected) == 5:
            break

    if len(selected) < 5:
        for dec_key, _ in ranked:
            if dec_key not in selected:
                selected.append(dec_key)
            if len(selected) == 5:
                break

    return selected[:5]


def run_tev_fast(
    theta: np.ndarray,
    db_path: Path,
    base_assumption_set: AssumptionSet,
    top5_decrement_keys: list[str],
    model_points_cache: dict,
    fast_projection_years: int = _FAST_PROJECTION_YEARS_DEFAULT,
) -> float:
    """Lightweight TEV evaluation for the envelope inner loop.

    Applies theta (scaling/additive factors for the top-5 decrements) to a
    copy of the base assumption set, runs vectorised projections for all
    products using pre-loaded model points, and returns total TEV.

    Does NOT write to the database.  Projection horizon is controlled by
    fast_projection_years (default 25, configurable via tev_config.yaml).

    Args:
        theta:               1-D array of length len(top5_decrement_keys).
        db_path:             DuckDB path (kept for API compatibility).
        base_assumption_set: The starting AssumptionSet to modify.
        top5_decrement_keys: Ordered list matching the theta vector.
        model_points_cache:  Dict: product_code → pd.DataFrame; "__anw_total__" → float.
        fast_projection_years: Projection horizon.

    Returns:
        Scalar total TEV (ANW + sum of VIF across all products).
    """
    aset = _apply_theta(base_assumption_set, theta, top5_decrement_keys)

    rdr = aset.rdr
    tax_rate = aset.tax_rate
    earned_after_tax = aset.earned_rate_ga * (1.0 - tax_rate)

    anw_total: float = model_points_cache.get("__anw_total__", 0.0)

    total_vif = 0.0
    for product_code, mp_df in model_points_cache.items():
        if product_code.startswith("__"):
            continue
        if mp_df.empty:
            continue

        proj = project_cashflows(
            mp_df,
            aset,
            product_code,
            max_projection_years=fast_projection_years,
        )

        weights = proj["initial_in_force"]
        pvfp  = compute_pvfp(proj["bp"],  weights, rdr)
        pvcoc = compute_pvcoc(proj["rc"], weights, rdr, earned_after_tax)
        total_vif += pvfp - pvcoc

    return anw_total + total_vif


def run_envelope_analysis(
    db_path: Path,
    assumption_set_id: str,
    baseline_tev_run_id: str,
    impact_matrix_df: pd.DataFrame,
    max_evaluations: int = 200,
    width_materiality_floor_pct: float = 0.001,
) -> EnvelopeResult:
    """Compute the credibility envelope for aggregate TEV.

    Finds the maximum and minimum TEV reachable by varying the top-5 most
    TEV-sensitive decrements within their credibility bounds, and locates the
    proposed assumption set within that envelope as a percentile.

    Both L-BFGS-B runs share the same box constraints and the same pre-loaded
    model-point cache.  Neither run writes to the database or modifies any
    AssumptionSet.

    Args:
        db_path:                    DuckDB path.
        assumption_set_id:          Input assumption set UUID.
        baseline_tev_run_id:        Baseline TEV run UUID (provides proposed_tev).
        impact_matrix_df:           TEV-impact matrix from run_sensitivity_grid().
        max_evaluations:            Max objective evaluations per run (default 200).
        width_materiality_floor_pct: Below this fraction of proposed_tev the
                                    envelope width is immaterial; percentile → None.

    Returns:
        EnvelopeResult — read-only governance artefact.

    IMPORTANT: This function never creates or saves an AssumptionSet.  The
    caller (UI) must treat the result as read-only.  No code path anywhere in
    the system may convert theta_min or theta_max into an AssumptionSet.
    """
    import time
    t_start = time.time()

    # --- 1. Load assumption set and proposed TEV ---
    base_aset = load_assumption_set(assumption_set_id, db_path)

    _cfg_path = db_path.parent.parent / "config" / "tev_config.yaml"
    try:
        with open(_cfg_path) as _fh:
            _tev_cfg = yaml.safe_load(_fh)
        fast_years: int = int(_tev_cfg.get("fast_projection_years", _FAST_PROJECTION_YEARS_DEFAULT))
    except (FileNotFoundError, KeyError, TypeError):
        fast_years = _FAST_PROJECTION_YEARS_DEFAULT

    proposed_tev = _load_proposed_tev(db_path, baseline_tev_run_id)

    # --- 2. Identify top-5 decrements ---
    top5_keys = identify_top5_decrements(impact_matrix_df, base_aset)
    if not top5_keys:
        return _failure_result(assumption_set_id, proposed_tev, "No optimisable decrements identified.")

    # --- 3. Extract credibility bounds ---
    bounds_map: dict[str, tuple[float, float]] = {
        dk: _extract_bounds(base_aset, dk) for dk in top5_keys
    }

    lower_bounds = np.array([bounds_map[dk][0] for dk in top5_keys], dtype=np.float64)
    upper_bounds = np.array([bounds_map[dk][1] for dk in top5_keys], dtype=np.float64)

    # --- 4. Pre-load model points (shared by both runs) ---
    model_points_cache = _load_model_points_cache(db_path, base_aset)

    # theta_proposed: identity values (1.0 for multipliers, 0.0 for rdr)
    theta_proposed_arr = np.array([
        0.0 if dk == "rdr" else 1.0
        for dk in top5_keys
    ], dtype=np.float64)
    theta_proposed_arr = np.clip(theta_proposed_arr, lower_bounds, upper_bounds)

    scipy_bounds = Bounds(lb=lower_bounds, ub=upper_bounds)

    # --- 5. Run TEV_max (minimise –TEV) ---
    eval_count_max = [0]

    def obj_max(theta: np.ndarray) -> float:
        """Objective for TEV maximisation: minimise negative TEV."""
        eval_count_max[0] += 1
        return -run_tev_fast(theta, db_path, base_aset, top5_keys, model_points_cache, fast_years)

    result_max = minimize(
        fun=obj_max,
        x0=theta_proposed_arr.copy(),
        method="L-BFGS-B",
        bounds=scipy_bounds,
        options={"maxiter": max_evaluations, "maxfun": max_evaluations, "ftol": 1e-9, "gtol": 1e-6},
    )
    theta_max_arr = np.clip(result_max.x, lower_bounds, upper_bounds)
    tev_max = -float(result_max.fun)
    n_evals_max = eval_count_max[0]
    conv_msg_max = result_max.message if hasattr(result_max, "message") else str(result_max.get("message", ""))

    # --- 6. Run TEV_min (minimise +TEV) ---
    eval_count_min = [0]

    def obj_min(theta: np.ndarray) -> float:
        """Objective for TEV minimisation: minimise positive TEV."""
        eval_count_min[0] += 1
        return run_tev_fast(theta, db_path, base_aset, top5_keys, model_points_cache, fast_years)

    result_min = minimize(
        fun=obj_min,
        x0=theta_proposed_arr.copy(),
        method="L-BFGS-B",
        bounds=scipy_bounds,
        options={"maxiter": max_evaluations, "maxfun": max_evaluations, "ftol": 1e-9, "gtol": 1e-6},
    )
    theta_min_arr = np.clip(result_min.x, lower_bounds, upper_bounds)
    tev_min = float(result_min.fun)
    n_evals_min = eval_count_min[0]
    conv_msg_min = result_min.message if hasattr(result_min, "message") else str(result_min.get("message", ""))

    # --- 7. Sanity check: tev_min <= proposed_tev <= tev_max ---
    tol = _CONTAINMENT_TOLERANCE * max(abs(proposed_tev), 1.0)
    containment_ok = (tev_min - tol) <= proposed_tev <= (tev_max + tol)
    success = containment_ok

    if not containment_ok:
        diag = (
            f"Containment violated: tev_min={tev_min:,.0f} proposed={proposed_tev:,.0f} "
            f"tev_max={tev_max:,.0f}. Check model-point cache or credibility bounds."
        )
        conv_msg_min = diag
        conv_msg_max = diag
    else:
        # Clamp to avoid floating-point overshoot when reporting
        tev_min = min(tev_min, proposed_tev)
        tev_max = max(tev_max, proposed_tev)
        success = result_max.success and result_min.success

    # --- 8. Envelope width ---
    envelope_width_abs = tev_max - tev_min
    envelope_width_pct = envelope_width_abs / max(abs(proposed_tev), 1.0)

    # --- 9. Percentile ---
    if envelope_width_pct < width_materiality_floor_pct:
        percentile: Optional[float] = None
        percentile_reason: Optional[str] = "envelope width below materiality floor"
    else:
        denom = tev_max - tev_min
        percentile = (proposed_tev - tev_min) / denom if denom != 0 else 0.5
        percentile_reason = None

    # --- 10. Write audit YAML ---
    theta_proposed_dict = {dk: float(theta_proposed_arr[i]) for i, dk in enumerate(top5_keys)}
    theta_min_dict = {dk: float(theta_min_arr[i]) for i, dk in enumerate(top5_keys)}
    theta_max_dict = {dk: float(theta_max_arr[i]) for i, dk in enumerate(top5_keys)}

    yaml_path = _write_envelope_yaml(
        db_path=db_path,
        assumption_set_id=assumption_set_id,
        top5_keys=top5_keys,
        bounds_map=bounds_map,
        proposed_tev=proposed_tev,
        tev_min=tev_min,
        tev_max=tev_max,
        envelope_width_abs=envelope_width_abs,
        envelope_width_pct=envelope_width_pct,
        percentile=percentile,
        percentile_reason=percentile_reason,
        theta_proposed=theta_proposed_dict,
        theta_min=theta_min_dict,
        theta_max=theta_max_dict,
        n_evals_min=n_evals_min,
        n_evals_max=n_evals_max,
        conv_msg_min=conv_msg_min,
        conv_msg_max=conv_msg_max,
        success=success,
        duration_sec=time.time() - t_start,
    )

    # --- 11. Return EnvelopeResult ---
    return EnvelopeResult(
        success=success,
        assumption_set_id=assumption_set_id,
        top5_decrements=top5_keys,
        proposed_tev=proposed_tev,
        tev_min=tev_min,
        tev_max=tev_max,
        envelope_width_abs=envelope_width_abs,
        envelope_width_pct=envelope_width_pct,
        proposed_envelope_percentile=percentile,
        percentile_undefined_reason=percentile_reason,
        theta_proposed=theta_proposed_dict,
        theta_min=theta_min_dict,
        theta_max=theta_max_dict,
        credibility_bounds={dk: bounds_map[dk] for dk in top5_keys},
        n_evaluations_min=n_evals_min,
        n_evaluations_max=n_evals_max,
        convergence_message_min=conv_msg_min,
        convergence_message_max=conv_msg_max,
        envelope_yaml_path=yaml_path,
    )


# ---------------------------------------------------------------------------
# Internal helpers (private)
# ---------------------------------------------------------------------------

def _failure_result(assumption_set_id: str, proposed_tev: float, msg: str) -> EnvelopeResult:
    """Return a failed EnvelopeResult with zero-width envelope."""
    return EnvelopeResult(
        success=False,
        assumption_set_id=assumption_set_id,
        top5_decrements=[],
        proposed_tev=proposed_tev,
        tev_min=proposed_tev,
        tev_max=proposed_tev,
        envelope_width_abs=0.0,
        envelope_width_pct=0.0,
        proposed_envelope_percentile=None,
        percentile_undefined_reason=msg,
        theta_proposed={},
        theta_min={},
        theta_max={},
        credibility_bounds={},
        n_evaluations_min=0,
        n_evaluations_max=0,
        convergence_message_min=msg,
        convergence_message_max=msg,
        envelope_yaml_path="",
    )


def _has_multipliers(aset: AssumptionSet, decrement_key: str) -> bool:
    """Return True if the assumption set has any multiplier cells for this key."""
    mapping = {
        "lapse":             aset.lapse_multipliers,
        "mortality_life":    aset.mortality_multipliers,
        "mortality_annuity": aset.mortality_multipliers,
        "ci_incidence":      aset.ci_incidence_multipliers,
        "expense":           [],
        "rdr":               [],
    }
    return len(mapping.get(decrement_key, [])) > 0


def _extract_bounds(
    aset: AssumptionSet,
    decrement_key: str,
) -> tuple[float, float]:
    """Return (lower_theta, upper_theta) for the decrement's global scaling factor.

    For multiplier-based decrements, bounds are derived from the credibility
    intervals stored in the assumption set cells.  For expense and rdr, sensible
    defaults are used.

    Args:
        aset:          AssumptionSet with multiplier cells.
        decrement_key: One of the DECREMENT_TO_SENSITIVITIES keys.

    Returns:
        (lower_bound, upper_bound) for the theta variable.
    """
    if decrement_key == "expense":
        return (0.50, 1.50)

    if decrement_key == "rdr":
        return (-0.020, 0.020)

    if decrement_key == "lapse":
        mults = aset.lapse_multipliers
    elif decrement_key == "ci_incidence":
        mults = aset.ci_incidence_multipliers
    elif decrement_key == "mortality_life":
        mults = [m for m in aset.mortality_multipliers if m.product.upper() in _LIFE_PRODUCTS]
    elif decrement_key == "mortality_annuity":
        mults = [m for m in aset.mortality_multipliers if m.product.upper() in _DA_PRODUCTS]
    else:
        mults = []

    if not mults:
        return (0.70, 1.30)

    ratios_lo: list[float] = []
    ratios_hi: list[float] = []
    for m in mults:
        base = max(abs(m.multiplier), 0.01)
        ratios_lo.append(m.credibility_lower / base)
        ratios_hi.append(m.credibility_upper / base)

    lo = float(np.mean(ratios_lo))
    hi = float(np.mean(ratios_hi))

    lo = max(0.20, min(lo, 0.99))
    hi = max(lo + 0.02, min(hi, 4.00))

    return (lo, hi)


def _apply_theta(
    base_aset: AssumptionSet,
    theta: np.ndarray,
    top5_keys: list[str],
) -> AssumptionSet:
    """Return a modified (deep-copied) AssumptionSet with theta applied.

    For multiplier-based decrements: scale the multiplier field of each cell.
    For expense: scale maintenance costs.
    For rdr: add theta as an additive delta.

    Args:
        base_aset:  Base AssumptionSet (not mutated).
        theta:      Vector of scaling/delta values aligned to top5_keys.
        top5_keys:  Decrement key names matching theta indices.

    Returns:
        Modified AssumptionSet (in-memory, yaml_file_path="").
    """
    aset = _shallow_copy_aset(base_aset)

    for i, dk in enumerate(top5_keys):
        t = float(theta[i])

        if dk == "lapse":
            aset.lapse_multipliers = _scale_mults(base_aset.lapse_multipliers, t)

        elif dk == "mortality_life":
            aset.mortality_multipliers = [
                _scale_one_mult(m, t) if m.product.upper() in _LIFE_PRODUCTS else m
                for m in base_aset.mortality_multipliers
            ]

        elif dk == "mortality_annuity":
            aset.mortality_multipliers = [
                _scale_one_mult(m, t) if m.product.upper() in _DA_PRODUCTS else m
                for m in aset.mortality_multipliers
            ]

        elif dk == "ci_incidence":
            aset.ci_incidence_multipliers = _scale_mults(base_aset.ci_incidence_multipliers, t)

        elif dk == "expense":
            aset.maintenance_per_policy  = base_aset.maintenance_per_policy  * t
            aset.maintenance_pct_premium = base_aset.maintenance_pct_premium * t

        elif dk == "rdr":
            aset.rdr = base_aset.rdr + t

    return aset


def _scale_mults(mults: list[DecrementMultiplier], factor: float) -> list[DecrementMultiplier]:
    """Return new list with each cell's multiplier scaled by factor."""
    return [_scale_one_mult(m, factor) for m in mults]


def _scale_one_mult(m: DecrementMultiplier, factor: float) -> DecrementMultiplier:
    """Return a new DecrementMultiplier with multiplier × factor."""
    return DecrementMultiplier(
        product=m.product,
        gender=m.gender,
        risk_class=m.risk_class,
        duration_band=list(m.duration_band),
        multiplier=m.multiplier * factor,
        credibility_z=m.credibility_z,
        credibility_lower=m.credibility_lower,
        credibility_upper=m.credibility_upper,
        override_rationale=m.override_rationale,
    )


def _shallow_copy_aset(aset: AssumptionSet) -> AssumptionSet:
    """Shallow-copy an AssumptionSet; list fields must be replaced, not mutated."""
    return AssumptionSet(
        id=str(uuid.uuid4()),
        version=aset.version,
        status=aset.status,
        effective_date=aset.effective_date,
        author_id=aset.author_id,
        basis=aset.basis,
        source_study_run_id=aset.source_study_run_id,
        rdr=aset.rdr,
        earned_rate_ga=aset.earned_rate_ga,
        earned_rate_sa=aset.earned_rate_sa,
        tax_rate=aset.tax_rate,
        expense_inflation=aset.expense_inflation,
        rc_pct_reserve=dict(aset.rc_pct_reserve),
        acquisition_per_policy=aset.acquisition_per_policy,
        maintenance_per_policy=aset.maintenance_per_policy,
        maintenance_pct_premium=aset.maintenance_pct_premium,
        mortality_multipliers=list(aset.mortality_multipliers),
        lapse_multipliers=list(aset.lapse_multipliers),
        surrender_multipliers=list(aset.surrender_multipliers),
        ci_incidence_multipliers=list(aset.ci_incidence_multipliers),
        premium_persistency=list(aset.premium_persistency),
        shock_lapse_plt=dict(aset.shock_lapse_plt),
        yaml_file_path="",
    )


def _load_model_points_cache(db_path: Path, base_aset: AssumptionSet) -> dict:
    """Pre-load model points and ANW into an in-memory cache.

    Returns:
        Dict: product_code → pd.DataFrame; "__anw_total__" → float.
    """
    import duckdb

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

    cache: dict = {}
    if mp_all.empty:
        cache["__anw_total__"] = 0.0
        return cache

    for pc in mp_all["product_code"].unique():
        cache[pc] = mp_all[mp_all["product_code"] == pc].reset_index(drop=True)

    cache["__anw_total__"] = _compute_anw_fast(db_path, base_aset, mp_all)
    return cache


def _compute_anw_fast(db_path: Path, aset: AssumptionSet, mp_all: pd.DataFrame) -> float:
    """Compute total ANW from pre-loaded model points without an extra DB round-trip."""
    cfg_path = db_path.parent.parent / "config" / "tev_config.yaml"
    with open(cfg_path) as fh:
        tev_cfg = yaml.safe_load(fh)

    statutory_surplus = float(tev_cfg.get("statutory_surplus", 50_000_000.0))
    avr_pct = float(tev_cfg.get("avr_pct_of_reserve", 0.005))

    total_reserve = float(mp_all["reserve_total"].sum())
    avr = avr_pct * total_reserve
    return (statutory_surplus + avr) * (1.0 - aset.tax_rate)


def _load_proposed_tev(db_path: Path, baseline_tev_run_id: str) -> float:
    """Load proposed_tev from gold_tev_run_log for the given baseline run."""
    import duckdb

    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT total_tev FROM gold_tev_run_log WHERE tev_run_id = ?",
            [baseline_tev_run_id],
        ).fetchone()
    finally:
        con.close()

    if row and row[0] is not None:
        return float(row[0])
    return 0.0


def _write_envelope_yaml(
    db_path: Path,
    assumption_set_id: str,
    top5_keys: list[str],
    bounds_map: dict[str, tuple[float, float]],
    proposed_tev: float,
    tev_min: float,
    tev_max: float,
    envelope_width_abs: float,
    envelope_width_pct: float,
    percentile: Optional[float],
    percentile_reason: Optional[str],
    theta_proposed: dict[str, float],
    theta_min: dict[str, float],
    theta_max: dict[str, float],
    n_evals_min: int,
    n_evals_max: int,
    conv_msg_min: str,
    conv_msg_max: str,
    success: bool,
    duration_sec: float,
) -> str:
    """Write a read-only audit YAML for this envelope run.

    Written to reports/envelope_<assumption_set_id[:8]>_<timestamp>.yaml.
    This file is for audit and reporting only.  It must NOT be imported as
    an AssumptionSet anywhere in the codebase.

    Returns:
        Absolute path string of the written YAML file.
    """
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = db_path.parent.parent / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"envelope_{assumption_set_id[:8]}_{ts}.yaml"

    data = {
        "envelope_audit": {
            "WARNING": (
                "CREDIBILITY ENVELOPE ANALYSIS — read-only audit artefact. "
                "This file was generated automatically and must NOT be loaded as "
                "an AssumptionSet.  It contains no assumption for adoption; it is "
                "a governance record only."
            ),
            "assumption_set_id": assumption_set_id,
            "generated_ts": datetime.utcnow().isoformat(),
            "success": success,
            "duration_sec": round(duration_sec, 2),
            "top5_decrements": top5_keys,
            "credibility_bounds": {
                dk: {"lower": round(lo, 6), "upper": round(hi, 6)}
                for dk, (lo, hi) in bounds_map.items()
            },
            "tev": {
                "proposed": round(proposed_tev, 2),
                "min": round(tev_min, 2),
                "max": round(tev_max, 2),
                "envelope_width_abs": round(envelope_width_abs, 2),
                "envelope_width_pct": round(envelope_width_pct, 6),
                "proposed_envelope_percentile": (
                    round(percentile, 6) if percentile is not None else None
                ),
                "percentile_undefined_reason": percentile_reason,
            },
            "theta_proposed": {dk: round(v, 6) for dk, v in theta_proposed.items()},
            "theta_min":      {dk: round(v, 6) for dk, v in theta_min.items()},
            "theta_max":      {dk: round(v, 6) for dk, v in theta_max.items()},
            "convergence": {
                "tev_max_run": {
                    "n_evaluations": n_evals_max,
                    "message": conv_msg_max,
                },
                "tev_min_run": {
                    "n_evaluations": n_evals_min,
                    "message": conv_msg_min,
                },
            },
        },
    }

    with open(output_path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

    return str(output_path)
