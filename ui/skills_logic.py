"""App-side assembly for the two AI Skills (Session 19).

The Skills themselves (``src/ai/skills/``) take a structured dict and never read
the database. This UI helper assembles those dicts from the Gold layer + the
Assumption Comparison page context (FR-3B-17), exactly as ``ai_comparison_logic``
does for the Phase 3a page. It lives under ``ui/`` (not ``src/ai/``) so it may
read Gold directly; all of its queries are **read-only and parameterised** (no
string-interpolated SQL), and ``src/ai/`` never imports it (FR-3A-07 preserved).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import duckdb

from src.utils.types import DecrementType, GLMFitResult, GBMFitResult
from src.ai.llm.client import load_llm_config, available_models
from ui.stats_helpers import credibility_z, get_run_method

#: Decrement -> the A/E (numerator, denominator) component columns in
#: gold_ae_results. A/E by segment must be re-aggregated as SUM(num)/SUM(den)
#: across the detail grain — the stored per-cell ``ae_*`` ratio cannot be summed
#: or averaged. Mirrors ``src/aggregation/aggregator.py::_RATIO_COMPONENTS``.
_DECREMENT_COMPONENTS = {
    DecrementType.MORTALITY: ("actual_deaths_count", "expected_deaths_count"),
    DecrementType.LAPSE: ("actual_lapses", "expected_lapses"),
    DecrementType.CI_INCIDENCE: ("actual_ci_claims", "expected_ci_claims"),
    DecrementType.SURRENDER: ("actual_surrenders", "expected_surrenders"),
}

#: Decrement -> the segment dimension surfaced in the memo's "by segment" table.
_DECREMENT_SEGMENT_DIM = {
    DecrementType.MORTALITY: "attained_age_band",
    DecrementType.LAPSE: "duration_band",
    DecrementType.CI_INCIDENCE: "attained_age_band",
    DecrementType.SURRENDER: "duration_band",  # surrender experience varies by duration
}

#: Decrement -> its exposure column in gold_ae_results (for the fact pack).
_DECREMENT_EXPOSURE = {
    DecrementType.MORTALITY: "exposure_count",
    DecrementType.LAPSE: "lapse_exposure_count",
    DecrementType.CI_INCIDENCE: "ci_exposure_count",
    DecrementType.SURRENDER: "surrender_exposure",
}


def available_skill_models(config_dir: Path) -> list[dict]:
    """Return the configured models for a Skill model selector (FR-3B-04/43).

    Greyed (``enabled=False``) when the provider API key is unset; the page shows
    them but the call surfaces a clear error if run without a key.
    """
    cfg = load_llm_config(Path(config_dir) / "llm_config.yaml")
    return available_models(cfg)


def _study_period(con, run_id: str) -> str:
    row = con.execute(
        "SELECT study_start_date, study_end_date FROM gold_study_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if not row or row[0] is None:
        return "N/A"
    return f"{str(row[0])[:10]} to {str(row[1])[:10]}"


def _study_years(con, run_id: str) -> list[int]:
    """Inclusive list of calendar years spanned by the study window.

    Carried in the memo input so the model may legitimately reference any year
    within the study period (e.g. 2020, 2022) and have it trace to the JSON.
    """
    row = con.execute(
        "SELECT study_start_date, study_end_date FROM gold_study_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return []
    try:
        start_year = int(str(row[0])[:4])
        end_year = int(str(row[1])[:4])
    except ValueError:
        return []
    if end_year < start_year:
        return []
    return list(range(start_year, end_year + 1))


def _ae_by_segment(
    con, run_id: str, product: str, decrement: DecrementType, limit: int = 20
) -> list[dict]:
    """A/E aggregated to **one row per segment** (e.g. per attained-age band).

    ``gold_ae_results`` stores rows only at the full detail grain (gender ×
    smoker × risk_class × band × duration × …). A by-segment view must therefore
    re-aggregate ``SUM(actual)/SUM(expected)`` across all other dimensions — not
    read raw detail rows (which would surface only one band's many sub-cells).
    Credibility Z is recomputed from the **aggregate** claim count (FR-1A-24),
    never the stored per-cell value. This mirrors how the A/E Explorer pages use
    ``aggregate_ae`` + ``stats_helpers``.
    """
    num_col, den_col = _DECREMENT_COMPONENTS[decrement]
    dim = _DECREMENT_SEGMENT_DIM[decrement]
    method = get_run_method(con, run_id)
    # Parameterised, read-only; column/dim names are fixed allowlisted identifiers
    # from the mapping tables above, never user input. ``illness_code IS NULL``
    # mirrors aggregator.aggregate_ae (avoids double-counting per-illness CI rows;
    # the per-band/aggregate CI claims live on the illness_code-NULL rows).
    sql = (
        f"SELECT {dim} AS segment, "  # noqa: S608 — fixed cols
        f"SUM({num_col}) AS actual, SUM({den_col}) AS expected "
        "FROM gold_ae_results "
        "WHERE study_run_id = ? AND product_code = ? "
        f"AND {dim} IS NOT NULL AND illness_code IS NULL "
        f"GROUP BY {dim} ORDER BY {dim}"
    )
    rows = con.execute(sql, [run_id, product]).fetchall()
    out = []
    for segment, actual, expected in rows[:limit]:
        actual = float(actual) if actual is not None else 0.0
        expected = float(expected) if expected is not None else 0.0
        ae_ratio = round(actual / expected, 4) if expected else None
        out.append({
            "segment": str(segment),
            "ae_ratio": ae_ratio,
            "credibility_z": round(float(credibility_z(actual, method=method)), 4),
        })
    return out


#: Below this aggregate credibility, a proposed factor is a degenerate sparse-cell
#: estimate (near-zero/exploding factor with an enormous CI) and is flagged
#: ``low_credibility`` in the fact pack rather than presented as a real assumption.
_LOW_CREDIBILITY_Z = 0.05


def _round_finite(value, ndigits: int = 4):
    """Round a finite float for display; return None for None/inf/nan."""
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, ndigits)


def _proposed_factors(
    con, run_id: str, product: str, decrement: DecrementType, limit: int = 60
) -> list[dict]:
    """Published **GLM** proposed adjustment factors for a product/decrement, if any.

    Reads the materialised ``gold_ai_proposed_factors`` (round-4 Gold table) so the
    commentary fact pack can ground a narrative on the *proposed* assumptions, not
    only the observed A/E — this is what let the chatbot's "commentary on the
    proposed assumptions" requests fail before (the fact pack had no proposals).
    Read-only and parameterised. Degenerate sparse-cell estimates (tiny factor with
    an enormous CI and ~zero credibility) are flagged ``low_credibility`` so the
    model can caveat them rather than quote a nonsensical value. Returns ``[]`` when
    the table is absent/empty (e.g. no models fit, or SURRENDER which is not modelled).
    """
    try:
        rows = con.execute(
            "SELECT sex, smoker, attained_age_band, duration_band, "
            "factor, ci_low, ci_high, credibility_z "
            "FROM gold_ai_proposed_factors "
            "WHERE run_id = ? AND product_code = ? AND decrement = ? "
            "AND model_type = 'GLM' "
            "ORDER BY attained_age_band, duration_band, sex, smoker LIMIT ?",
            [run_id, product, decrement.value, limit],
        ).fetchall()
    except duckdb.Error:
        return []
    out = []
    for sex, smoker, age_band, dur_band, factor, ci_low, ci_high, z in rows:
        z = float(z) if z is not None else 0.0
        grain = {}
        if sex is not None:
            grain["sex"] = str(sex)
        if smoker is not None:
            grain["smoker"] = str(smoker)
        if age_band is not None:
            grain["attained_age_band"] = str(age_band)
        if dur_band is not None:
            grain["duration_band"] = str(dur_band)
        out.append({
            "grain": grain,
            "proposed_factor": _round_finite(factor),
            "ci_low": _round_finite(ci_low),
            "ci_high": _round_finite(ci_high),
            "credibility_z": round(z, 4),
            "low_credibility": z < _LOW_CREDIBILITY_Z,
        })
    return out


def _tev_baseline(con) -> tuple[Optional[float], Optional[float]]:
    """Latest baseline TEV (sensitivity_id NULL) and its ΔTEV vs prior, if any."""
    row = con.execute(
        "SELECT total_tev, delta_tev_vs_prior FROM gold_tev_run_log "
        "WHERE sensitivity_id IS NULL AND status = 'COMPLETE' "
        "ORDER BY run_ts DESC NULLS LAST LIMIT 1"
    ).fetchone()
    if not row:
        return None, None
    base = float(row[0]) if row[0] is not None else None
    delta = float(row[1]) if row[1] is not None else None
    return base, delta


def assemble_memo_input(
    db_path: Path,
    run_id: str,
    decrement: DecrementType,
    product: str,
    *,
    glm: Optional[GLMFitResult] = None,
    gbm: Optional[GBMFitResult] = None,
    whatif_delta_tev: Optional[float] = None,
) -> dict:
    """Build the memo Skill's structured input from Gold + page context (FR-3B-17).

    All numbers are carried in display form so the memo can quote them verbatim
    and the traceability post-check accepts them.
    """
    decrement = DecrementType(decrement)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        period = _study_period(con, run_id)
        study_years = _study_years(con, run_id)
        segments = _ae_by_segment(con, run_id, product, decrement)
        tev_baseline, tev_delta = _tev_baseline(con)
    finally:
        con.close()

    # Top drivers: the segments with the largest deviation from 1.0, restricted to
    # cells that carry some experience (credibility_z > 0) — a zero-experience band
    # (A/E 0.0, Z 0.0) is not a "driver of deviation" and was previously surfaced
    # spuriously (e.g. the 15-19 / 20-24 Term bands).
    drivers = sorted(
        (s for s in segments
         if s["ae_ratio"] is not None and s.get("credibility_z", 0) > 0),
        key=lambda s: abs(s["ae_ratio"] - 1.0),
        reverse=True,
    )[:3]
    top_drivers = [d["segment"] for d in drivers]

    glm_factors = []
    if glm is not None and getattr(glm, "factors", None):
        for fc in glm.factors:
            glm_factors.append({
                "grain": fc.grain_key,
                "proposed_factor": round(float(fc.factor), 4),
                "ci_low": round(float(fc.ci_low), 4),
                "ci_high": round(float(fc.ci_high), 4),
            })

    return {
        "product": product,
        "decrement": decrement.value,
        "study_period": period,
        "study_years": study_years,
        "ae_by_segment": segments,
        "prior_assumption": 1.0,
        "proposed_glm_factors": glm_factors,
        "tev_baseline": round(tev_baseline, 0) if tev_baseline is not None else None,
        "delta_tev_vs_prior": (
            round(whatif_delta_tev, 0) if whatif_delta_tev is not None
            else (round(tev_delta, 0) if tev_delta is not None else None)
        ),
        "top_drivers": top_drivers,
        "exclusions": [],
        "run_id": run_id,
    }


def _overall_ae(con, run_id: str, product: str, decrement: DecrementType) -> Optional[dict]:
    """Aggregate (ratio-of-sums) A/E for one product × decrement, with context.

    A/E = ``SUM(actual)/SUM(expected)`` across the detail grain (never a raw
    ``ae_*`` cell). Credibility Z is from the **aggregate** claim count (FR-1A-24).
    ``illness_code IS NULL`` for mortality/lapse/surrender; ``IS NOT NULL`` for CI.
    Returns ``None`` when the product has no experience for this decrement.
    Column/dim names are fixed allowlisted identifiers (never user input).
    """
    num, den = _DECREMENT_COMPONENTS[decrement]
    exp = _DECREMENT_EXPOSURE[decrement]
    ci_filter = "IS NOT NULL" if decrement is DecrementType.CI_INCIDENCE else "IS NULL"
    method = get_run_method(con, run_id)
    sql = (  # noqa: S608 — fixed identifiers, parameterised run_id/product
        f"SELECT SUM({num}) AS actual, SUM({den}) AS expected, SUM({exp}) AS exposure "
        "FROM gold_ae_results "
        f"WHERE study_run_id = ? AND product_code = ? AND illness_code {ci_filter}"
    )
    row = con.execute(sql, [run_id, product]).fetchone()
    if not row:
        return None
    actual = float(row[0]) if row[0] is not None else 0.0
    expected = float(row[1]) if row[1] is not None else 0.0
    exposure = float(row[2]) if row[2] is not None else 0.0
    if expected <= 0 and actual <= 0:
        return None
    return {
        "actual": int(round(actual)),
        "expected": round(expected, 4),
        "exposure": round(exposure, 4),
        "ae_ratio": round(actual / expected, 4) if expected else None,
        "credibility_z": round(float(credibility_z(actual, method=method)), 4),
    }


def assemble_commentary_facts(db_path: Path, run_id: str) -> Optional[dict]:
    """Build the AI Analyst's run-wide commentary **fact pack** (round 3).

    Mirrors the memo Skill's ``assemble_memo_input`` but spans every product ×
    decrement in the run, so the chatbot can narrate over real, display-rounded
    figures rather than the one query it could fit before. Numbers are pre-rounded
    so the LLM can quote them verbatim and the traceability post-check accepts them.
    Read-only and parameterised; lives in the UI layer so the pipeline stays DB-free
    (FR-3B-25). Returns ``None`` when ``run_id`` is falsy.
    """
    if not run_id:
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        method = get_run_method(con, run_id)
        period = _study_period(con, run_id)
        study_years = _study_years(con, run_id)
        products = [
            r[0] for r in con.execute(
                "SELECT DISTINCT product_code FROM gold_ae_results "
                "WHERE study_run_id = ? AND product_code IS NOT NULL "
                "ORDER BY product_code",
                [run_id],
            ).fetchall()
        ]
        tev_baseline, tev_delta = _tev_baseline(con)
        by_product = []
        for product in products:
            decrements = {}
            for dec in (
                DecrementType.MORTALITY, DecrementType.LAPSE,
                DecrementType.SURRENDER, DecrementType.CI_INCIDENCE,
            ):
                overall = _overall_ae(con, run_id, product, dec)
                if overall is None:
                    continue
                entry = {
                    "overall": overall,
                    "by_segment": _ae_by_segment(con, run_id, product, dec),
                }
                proposed = _proposed_factors(con, run_id, product, dec)
                if proposed:
                    entry["proposed_factors"] = proposed
                decrements[dec.value] = entry
            if decrements:
                by_product.append({"product": product, "decrements": decrements})
    finally:
        con.close()
    return {
        "run_id": run_id,
        "study_period": period,
        "study_years": study_years,
        "credibility_method": method,  # so the chatbot recomputes Z with the run's method
        "products": products,
        "tev_baseline": round(tev_baseline, 0) if tev_baseline is not None else None,
        "delta_tev_vs_prior": round(tev_delta, 0) if tev_delta is not None else None,
        "by_product": by_product,
    }


def _round4(x):
    """Round a numeric value to 4 dp for display; pass non-numerics through."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    return round(float(x), 4)


def assemble_shap_cell_input(shap_json: dict, grain_key: dict) -> Optional[dict]:
    """Build the SHAP Skill's input from a persisted SHAP-JSON for one grain cell.

    Returns the cell dict (decrement/product/grain_key + base_value/prediction/
    contributions), or ``None`` if no cell matches the grain. Numeric values
    (base value, prediction, per-feature SHAP contributions) are rounded to 4 dp
    so the explanation reads cleanly; the model quotes the rounded values and the
    traceability post-check verifies against this same rounded input.
    """
    target = tuple(sorted(grain_key.items()))
    for cell in shap_json.get("cells", []):
        if tuple(sorted(cell.get("grain_key", {}).items())) == target:
            contributions = [
                {**c, "shap_value": _round4(c.get("shap_value"))}
                for c in cell.get("contributions", [])
            ]
            return {
                "decrement": shap_json.get("decrement"),
                "product_code": shap_json.get("product_code"),
                "grain_key": cell.get("grain_key", {}),
                "base_value": _round4(cell.get("base_value")),
                "prediction": _round4(cell.get("prediction")),
                "contributions": contributions,
            }
    return None


def feature_map_for_decrement(feature_to_assumption: dict, decrement: DecrementType) -> dict:
    """Return the per-feature actuarial map for one decrement (FR-3A-39)."""
    return feature_to_assumption.get(DecrementType(decrement).value, {})


def memo_to_markdown_bytes(markdown: str) -> bytes:
    """UTF-8 bytes for a `.md` download (tag + footer preserved)."""
    return (markdown or "").encode("utf-8")


def _debug_dump(obj) -> str:  # pragma: no cover - convenience for manual UI debug
    return json.dumps(obj, indent=2, default=str)
