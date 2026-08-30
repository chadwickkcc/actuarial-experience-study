"""Deterministic YoY-movement / driver / trend / justification analytics.

All figures the management-commentary Skill may narrate are pre-computed here
(the prompts forbid model arithmetic). Reads ``gold_ae_results`` /
``gold_inforce_reconciliation`` / ``gold_ai_proposed_factors`` via plain
parameterized read-only DuckDB. Dimension names are validated against a fixed
internal map (never user input) before being interpolated as identifiers.

Driver attribution (exact decomposition): for consecutive calendar years
t-1 → t, the aggregate ratio is ``AE_t = Σ_s A_{s,t} / E_t`` with
``E_t = Σ_s E_{s,t}``. Each segment's contribution is::

    contribution_s = A_{s,t} / E_t  −  A_{s,t−1} / E_{t−1}

which sums over segments to exactly ``AE_t − AE_{t−1}`` (each year's terms sum
to that year's aggregate ratio). A segment's contribution therefore blends its
claim experience and its exposure-mix shift — the standard "share of the
aggregate ratio" view.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import yaml

DEFAULT_COMMENTARY_CONFIG = Path("config/commentary_config.yaml")

# Decrement -> (actual column, expected column, illness_code predicate).
_DECREMENT_COLS: dict[str, tuple[str, str, str]] = {
    "MORTALITY": ("actual_deaths_count", "expected_deaths_count", "illness_code IS NULL"),
    "LAPSE": ("actual_lapses", "expected_lapses", "illness_code IS NULL"),
    "CI_INCIDENCE": ("actual_ci_claims", "expected_ci_claims", "illness_code IS NOT NULL"),
    "SURRENDER": ("actual_surrenders", "expected_surrenders", "illness_code IS NULL"),
}

# The only dimension identifiers attribution may group by (guards the
# identifier interpolation — dimension names never come from user input).
_ALLOWED_DIMENSIONS = {
    "product_code", "attained_age_band", "gender", "policy_year",
    "duration_band", "smoker_status", "risk_class",
}


def load_commentary_config(config_path: Path = DEFAULT_COMMENTARY_CONFIG) -> dict:
    """Load and validate config/commentary_config.yaml (loud on a bad config)."""
    with Path(config_path).open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    dims = cfg.get("attribution_dimensions")
    if not isinstance(dims, dict) or not dims:
        raise ValueError(f"{config_path}: 'attribution_dimensions' missing")
    for dec, dlist in dims.items():
        if dec not in _DECREMENT_COLS:
            raise ValueError(f"{config_path}: unknown decrement '{dec}'")
        for d in dlist or []:
            if d not in _ALLOWED_DIMENSIONS:
                raise ValueError(f"{config_path}: dimension '{d}' not permitted")
    trend = cfg.get("trend") or {}
    if "window_years" not in trend or "stable_slope_threshold" not in trend:
        raise ValueError(f"{config_path}: 'trend' block incomplete")
    return cfg


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


def compute_yoy_movement(
    db_path: Path,
    run_id: str,
    decrement: str,
    product: Optional[str] = None,
) -> list[dict]:
    """Per calendar year: actual, expected, A/E and the change vs prior year.

    Portfolio-wide when ``product`` is None, else scoped to one product.
    Ratios are ratio-of-sums (never averaged cells). Years with no expected
    experience are skipped.
    """
    actual_col, expected_col, illness_pred = _DECREMENT_COLS[decrement]
    sql = (
        f"SELECT calendar_year, SUM({actual_col}) AS a, SUM({expected_col}) AS e "
        f"FROM gold_ae_results WHERE study_run_id = ? AND {illness_pred} "
        f"AND calendar_year IS NOT NULL "
    )
    params: list = [run_id]
    if product is not None:
        sql += "AND product_code = ? "
        params.append(product)
    sql += "GROUP BY calendar_year ORDER BY calendar_year"

    con = _connect(db_path)
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    prior_ae: Optional[float] = None
    for year, a, e in rows:
        if not e:
            continue
        ae = float(a) / float(e)
        out.append({
            "year": int(year),
            "actual": int(a),
            "expected": round(float(e), 4),
            "ae": round(ae, 4),
            "delta_vs_prior": round(ae - prior_ae, 4) if prior_ae is not None else None,
        })
        prior_ae = ae
    return out


def attribute_drivers(
    db_path: Path,
    run_id: str,
    decrement: str,
    year: int,
    dimension: str,
    product: Optional[str] = None,
) -> dict:
    """Decompose the YoY A/E change into per-segment contributions on one dimension.

    Returns ``{year, prior_year, delta_ae, dimension, contributions:[{segment,
    contribution, actual, prior_actual}]}``. Contributions sum exactly to
    ``delta_ae`` (see module docstring). Raises ``ValueError`` for an
    unsupported dimension; returns ``delta_ae=None`` when either year has no
    expected experience.
    """
    if dimension not in _ALLOWED_DIMENSIONS:
        raise ValueError(f"dimension {dimension!r} not permitted")
    actual_col, expected_col, illness_pred = _DECREMENT_COLS[decrement]

    sql = (
        f"SELECT calendar_year, CAST({dimension} AS VARCHAR) AS segment, "
        f"SUM({actual_col}) AS a, SUM({expected_col}) AS e "
        f"FROM gold_ae_results WHERE study_run_id = ? AND {illness_pred} "
        f"AND calendar_year IN (?, ?) "
    )
    params: list = [run_id, year - 1, year]
    if product is not None:
        sql += "AND product_code = ? "
        params.append(product)
    sql += f"GROUP BY calendar_year, {dimension}"

    con = _connect(db_path)
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    by_year: dict[int, dict[str, tuple[float, float]]] = {year - 1: {}, year: {}}
    for yr, seg, a, e in rows:
        by_year[int(yr)][str(seg)] = (float(a or 0), float(e or 0))

    e_prior = sum(e for _, e in by_year[year - 1].values())
    e_curr = sum(e for _, e in by_year[year].values())
    if not e_prior or not e_curr:
        return {"year": year, "prior_year": year - 1, "delta_ae": None,
                "dimension": dimension, "contributions": []}

    ae_prior = sum(a for a, _ in by_year[year - 1].values()) / e_prior
    ae_curr = sum(a for a, _ in by_year[year].values()) / e_curr

    segments = sorted(set(by_year[year - 1]) | set(by_year[year]))
    contributions = []
    for seg in segments:
        a_prev = by_year[year - 1].get(seg, (0.0, 0.0))[0]
        a_curr = by_year[year].get(seg, (0.0, 0.0))[0]
        contrib = a_curr / e_curr - a_prev / e_prior
        contributions.append({
            "segment": seg,
            "contribution": round(contrib, 6),
            "actual": int(a_curr),
            "prior_actual": int(a_prev),
        })
    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return {
        "year": year,
        "prior_year": year - 1,
        "delta_ae": round(ae_curr - ae_prior, 6),
        "dimension": dimension,
        "contributions": contributions,
    }


def classify_trends(
    db_path: Path,
    run_id: str,
    decrement: str,
    product: Optional[str] = None,
    *,
    config_path: Path = DEFAULT_COMMENTARY_CONFIG,
) -> dict:
    """Classify the recent A/E trend as improving / worsening / stable.

    Fits a straight line (``numpy.polyfit`` degree 1) through the last
    ``trend.window_years`` calendar-year A/E points. A/E rising above the
    stable band = "worsening" (experience deteriorating vs expectation);
    falling = "improving". Fewer than the window's years of data →
    "insufficient_data".
    """
    cfg = load_commentary_config(config_path)
    window = int(cfg["trend"]["window_years"])
    threshold = float(cfg["trend"]["stable_slope_threshold"])

    yoy = compute_yoy_movement(db_path, run_id, decrement, product)
    if len(yoy) < window:
        return {"classification": "insufficient_data", "slope": None,
                "years_used": [r["year"] for r in yoy]}
    tail = yoy[-window:]
    years = np.array([r["year"] for r in tail], dtype=float)
    aes = np.array([r["ae"] for r in tail], dtype=float)
    slope = float(np.polyfit(years, aes, 1)[0])
    if abs(slope) <= threshold:
        cls = "stable"
    elif slope > 0:
        cls = "worsening"
    else:
        cls = "improving"
    return {
        "classification": cls,
        "slope": round(slope, 4),
        "years_used": [int(y) for y in years],
    }


def justification_metrics(
    db_path: Path,
    run_id: str,
    decrement: str,
    product: str,
) -> Optional[dict]:
    """Assumption-justification metrics for one product × decrement.

    ``overall_ae`` (ratio of sums), ``cred_wtd_ae`` (the credibility-weighted
    A/E, Z·A/E + (1−Z)·1 recomputed from the aggregate), and the published GLM
    proposal summary (cell count + factor range) when one exists. The
    best-estimate gap is ``overall_ae − 1.0`` (distance from the current
    neutral 1.0 multiplier). None when the combination has no experience.
    """
    from src.calculation.ae_engine import compute_credibility_z

    actual_col, expected_col, illness_pred = _DECREMENT_COLS[decrement]
    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT SUM({actual_col}), SUM({expected_col}) "
            f"FROM gold_ae_results WHERE study_run_id = ? AND product_code = ? "
            f"AND {illness_pred}",
            [run_id, product],
        ).fetchone()
        method_row = con.execute(
            "SELECT credibility_method FROM gold_study_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        proposed = con.execute(
            "SELECT COUNT(*), MIN(factor), MAX(factor) "
            "FROM gold_ai_proposed_factors "
            "WHERE run_id = ? AND product_code = ? AND decrement = ? "
            "AND model_type = 'GLM'",
            [run_id, product, decrement],
        ).fetchone()
    finally:
        con.close()

    if row is None or not row[1]:
        return None
    actual, expected = float(row[0] or 0), float(row[1])
    ae = actual / expected
    method = (method_row[0] if method_row and method_row[0] else "LF")
    z = float(compute_credibility_z(actual, method))
    out = {
        "product": product,
        "decrement": decrement,
        "overall_ae": round(ae, 4),
        "credibility_z": round(z, 4),
        "cred_wtd_ae": round(z * ae + (1 - z) * 1.0, 4),
        "current_multiplier": 1.0,
        "be_gap": round(ae - 1.0, 4),
    }
    if proposed and proposed[0]:
        out["proposed_cells"] = int(proposed[0])
        out["proposed_factor_min"] = round(float(proposed[1]), 4)
        out["proposed_factor_max"] = round(float(proposed[2]), 4)
    return out


def movement_legs(
    db_path: Path,
    run_id: str,
    years: Optional[list[int]] = None,
) -> list[dict]:
    """In-force movement legs per product × calendar year from reconciliation."""
    sql = (
        "SELECT product_code, calendar_year, beg_if_count, new_issues_count, "
        "deaths_count, lapses_count, surrenders_count, end_if_count, recon_passes "
        "FROM gold_inforce_reconciliation WHERE study_run_id = ? "
    )
    params: list = [run_id]
    if years:
        sql += f"AND calendar_year IN ({', '.join('?' for _ in years)}) "
        params.extend(years)
    sql += "ORDER BY product_code, calendar_year"
    con = _connect(db_path)
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    cols = ["product", "year", "beg_if", "new_issues", "deaths", "lapses",
            "surrenders", "end_if", "recon_passes"]
    return [dict(zip(cols, r)) for r in rows]
