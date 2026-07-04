"""Pure orchestration for the Assumption Comparison page (Phase 3a, Session 17).

This module carries the non-Streamlit logic the page calls, so it can be unit
tested without importing Streamlit. It is strictly additive (CLAUDE.md rule #8):

  * It **reuses** the Session 15/16 GLM + GBM functions — it never reimplements
    any modelling.
  * It reads the persisted SHAP-JSON; it never recomputes SHAP at render time.
  * The TEV what-if (FR-3A-43) substitutes a GLM-proposed factor into an
    *in-memory* copy of the approved assumption set and runs the existing TEV
    engine. It creates or modifies **no** assumption set.

It is a UI helper (under ``ui/``), not part of ``src/ai/`` — so it may call the
core TEV engine and read the AI Gold registry. ``src/ai/`` still never imports
it (FR-3A-07 one-way rule is preserved).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from src.utils.types import DecrementType, GLMFitResult, GBMFitResult
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier, load_assumption_set
from src.tev.sensitivities import _deep_copy_assumption_set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AI_CONFIG_PATH = PROJECT_ROOT / "config" / "ai_config.yaml"
_FEATURE_MAP_PATH = PROJECT_ROOT / "config" / "feature_to_assumption.yaml"

#: The sensitivity_id used to flag a what-if TEV run (FR-3A-43). Reusing the
#: existing sensitivity_id column keeps the run queryable with no schema change.
WHAT_IF_SENSITIVITY_ID = "what_if_ai_proposal"

#: GLM grain-token -> AssumptionSet dimension. Mirrors glm.fit._GRAIN_TOKEN_TO_COLUMN
#: but maps onto DecrementMultiplier fields (which use ``gender``, not ``sex``).
_GRAIN_TOKEN_TO_DIM = {"product": "product", "sex": "gender", "smoker": "smoker_status"}

#: DecrementType -> the AssumptionSet multiplier-list attribute it perturbs.
_DECREMENT_TO_MULT_ATTR = {
    DecrementType.MORTALITY: "mortality_multipliers",
    DecrementType.LAPSE: "lapse_multipliers",
    DecrementType.CI_INCIDENCE: "ci_incidence_multipliers",
    DecrementType.SURRENDER: "surrender_multipliers",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_ai_config(path: Path = _AI_CONFIG_PATH) -> dict:
    """Return the parsed ``config/ai_config.yaml`` (FR-3A-10 / NFR-CF-10).

    Every grain, covariate set, threshold, and seed used by the fit lives here;
    none is hard-coded in the UI.
    """
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_feature_to_assumption(path: Path = _FEATURE_MAP_PATH) -> dict:
    """Return the per-decrement feature->actuarial-term map (FR-3A-39)."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("feature_to_assumption", {})


def _config_hash(path: Path = _AI_CONFIG_PATH) -> str:
    """SHA-256 of the AI config file — part of the model reproducibility stamp."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _data_snapshot_hash(db_path: Path, run_id: str) -> str:
    """The study run's data snapshot hash, or a fallback derived from run_id."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT data_snapshot_hash FROM gold_study_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
    finally:
        con.close()
    if row and row[0]:
        return str(row[0])
    return hashlib.sha256(run_id.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fitting (reuses Session 15/16 modules)
# ---------------------------------------------------------------------------

def fit_models(
    db_path: Path,
    run_id: str,
    decrement: DecrementType,
    product_code: str,
    *,
    register: bool = True,
    code_version: str = "0.17",
) -> dict:
    """Fit the GLM proposal and the GBM challenge for one decrement-product.

    Runs the exact reuse chain from Sessions 15/16:
      ``load_cells`` -> ``fit_glm`` -> ``bootstrap_cis`` -> ``register_glm_model``;
      then ``fit_gbm`` (with the GLM result for divergence flags) ->
      ``bootstrap_gbm_cis`` -> ``register_gbm_model`` (which also writes the
      SHAP-JSON via ``generate_shap_artifacts``).

    The loud-failure "No AI proposal available" state (FR-3A-29) is surfaced via
    the returned ``reasons`` dict whenever the GLM did not converge or the GBM
    produced no factors. Registration writes only to ``data/ai_models/`` and the
    AI Gold registry (FR-3A-09) — the page's own queries stay read-only.

    Returns:
        dict with keys ``glm`` (GLMFitResult|None), ``gbm`` (GBMFitResult|None),
        ``shap_json_path`` (str|""), and ``reasons`` (dict of decrement->message).
    """
    # Imported lazily so the module imports cleanly even if the ML stack is absent.
    from src.ai.glm.fit import load_cells, fit_glm
    from src.ai.glm.bootstrap import bootstrap_cis
    from src.ai.glm.registry import register_glm_model
    from src.ai.gbm.fit import fit_gbm, bootstrap_gbm_cis, register_gbm_model

    decrement = DecrementType(decrement)

    # SURRENDER is experience/memo-only: it has no GLM/GBM config (`_MEASURES`,
    # covariates, output_grain, tolerance), so short-circuit before any config or
    # DB access and surface the standard no-proposal state. The memo still works
    # (it reads surrender A/E straight from the Gold layer).
    if decrement == DecrementType.SURRENDER:
        return {
            "glm": None, "gbm": None, "shap_json_path": "",
            "reasons": {"surrender": "Surrender is experience/memo-only — no GLM/GBM proposal."},
        }

    cfg = load_ai_config()
    glm_cfg = cfg["glm"]
    gbm_cfg = cfg["gbm"]
    dec_key = decrement.value.lower()  # MORTALITY -> "mortality" etc.

    covariates = glm_cfg["covariates"][dec_key]
    output_grain = glm_cfg["output_grain"][dec_key]
    min_events = int(glm_cfg["min_events_to_fit"])
    glm_seed = int(glm_cfg["seed"])
    boot = glm_cfg["bootstrap"]

    snap = _data_snapshot_hash(db_path, run_id)
    cfg_hash = _config_hash()

    reasons: dict[str, str] = {}
    cells = load_cells(db_path, run_id, decrement, product_code)
    if cells.empty:
        reasons[dec_key] = "No A/E cells for this run/decrement/product."
        return {"glm": None, "gbm": None, "shap_json_path": "", "reasons": reasons}

    # ---- GLM proposal ----
    glm = fit_glm(cells, decrement, product_code, covariates, output_grain, min_events, glm_seed)
    if not glm.converged:
        reasons[dec_key] = glm.message or "No AI proposal available."
        return {"glm": glm, "gbm": None, "shap_json_path": "", "reasons": reasons}

    glm = bootstrap_cis(
        cells, decrement, product_code, covariates, output_grain, glm,
        n_resamples=int(boot["n_resamples"]), ci_level=float(boot["ci_level"]), seed=glm_seed,
    )
    if register:
        register_glm_model(
            glm, cells, covariates, output_grain, db_path,
            data_snapshot_hash=snap, config_hash=cfg_hash, code_version=code_version,
        )

    # ---- GBM challenge + SHAP ----
    gbm_boot = gbm_cfg["bootstrap"]
    gbm = fit_gbm(
        cells, decrement, product_code, covariates, output_grain,
        gbm_cfg["hyperparams"], glm, float(gbm_cfg["divergence_threshold"]),
        min_events, int(gbm_cfg["seed"]),
    )
    shap_json_path = ""
    if gbm.factors:
        gbm = bootstrap_gbm_cis(
            cells, decrement, product_code, covariates, output_grain,
            gbm_cfg["hyperparams"], gbm,
            n_resamples=int(gbm_boot["n_resamples"]), ci_level=float(gbm_boot["ci_level"]),
            seed=int(gbm_cfg["seed"]),
        )
        if register:
            register_gbm_model(
                gbm, cells, covariates, output_grain, gbm_cfg["hyperparams"], db_path,
                feature_to_assumption=load_feature_to_assumption(),
                data_snapshot_hash=snap, config_hash=cfg_hash, code_version=code_version,
            )
            shap_json_path = gbm.shap_json_path
    else:
        reasons[dec_key + "_gbm"] = "No GBM challenge available (insufficient cells)."

    return {"glm": glm, "gbm": gbm, "shap_json_path": shap_json_path, "reasons": reasons}


# ---------------------------------------------------------------------------
# Comparison table (FR-3A-42)
# ---------------------------------------------------------------------------

def _grain_id(grain_key: dict) -> tuple:
    """Order-independent identity of a grain key (mirrors gbm.fit._grain_id)."""
    return tuple(sorted(grain_key.items()))


def build_comparison_table(
    glm_result: GLMFitResult,
    gbm_result: Optional[GBMFitResult],
    approved_aset: Optional[AssumptionSet],
    decrement: DecrementType,
) -> pd.DataFrame:
    """Assemble the per-cell comparison table (FR-3A-42).

    One row per GLM output-grain cell with unambiguously labelled columns so the
    *proposal* (GLM), the *challenge* (GBM), and the *approved* basis are never
    confused:

      grain columns, ``ae_derived_factor``, ``glm_factor``, ``glm_ci_low``,
      ``glm_ci_high``, ``gbm_factor``, ``interaction_flag``, ``credibility_z``,
      ``expected_events``, ``approved_factor``.
    """
    decrement = DecrementType(decrement)
    gbm_by = {_grain_id(fc.grain_key): fc for fc in (gbm_result.factors if gbm_result else [])}
    diverged = {_grain_id(d["grain_key"]) for d in (gbm_result.divergence_flags if gbm_result else [])}

    rows: list[dict] = []
    for fc in glm_result.factors:
        gid = _grain_id(fc.grain_key)
        gbm_fc = gbm_by.get(gid)
        row = dict(fc.grain_key)  # grain key columns first
        row.update({
            "ae_derived_factor": fc.ae_derived_factor,
            "glm_factor": fc.factor,
            "glm_ci_low": fc.ci_low,
            "glm_ci_high": fc.ci_high,
            "gbm_factor": gbm_fc.factor if gbm_fc is not None else float("nan"),
            "interaction_flag": gid in diverged,
            "credibility_z": fc.credibility_z,
            "expected_events": fc.expected_events,
            "approved_factor": (
                lookup_approved_factor(approved_aset, decrement, fc.grain_key)
                if approved_aset is not None else None
            ),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def lookup_approved_factor(
    approved_aset: Optional[AssumptionSet],
    decrement: DecrementType,
    grain_key: dict,
) -> Optional[float]:
    """Best-effort lookup of the currently-approved factor for a grain cell.

    The GLM output grain (e.g. mortality = product x sex x smoker x
    attained_age_band) does not map one-to-one onto the assumption set's
    multiplier cells (product x gender x risk_class x duration_band). This
    returns the mean of the approved multipliers whose overlapping dims match
    the grain key, or ``None`` when nothing matches (rendered as "—"). It is a
    display aid only — never an input to any calculation.
    """
    if approved_aset is None:
        return None
    decrement = DecrementType(decrement)
    mults = getattr(approved_aset, _DECREMENT_TO_MULT_ATTR[decrement], [])
    if not mults:
        return None

    want_product = grain_key.get("product")
    want_gender = grain_key.get("sex")
    matched = [
        m.multiplier
        for m in mults
        if (want_product is None or m.product == want_product)
        and (want_gender is None or m.gender == want_gender)
    ]
    if not matched:
        return None
    return float(sum(matched) / len(matched))


# ---------------------------------------------------------------------------
# What-if assumption set (FR-3A-43) — in-memory only, never persisted
# ---------------------------------------------------------------------------

def build_whatif_assumption_set(
    baseline_aset: AssumptionSet,
    decrement: DecrementType,
    product_code: str,
    glm_result: GLMFitResult,
) -> AssumptionSet:
    """Return an in-memory copy of ``baseline_aset`` with the selected
    decrement-product multipliers moved toward the GLM proposal (FR-3A-43).

    The GLM factors live at the output grain, which is coarser than the
    multiplier cells, so each affected multiplier takes the GLM factor whose
    grain key matches on the dims they share (sex), falling back to the
    product-mean GLM factor. The result is a transparent "what if this product's
    <decrement> assumption moved to the AI-proposed level" run.

    This deep-copies (fresh UUID, ``yaml_file_path=""``) and **never** calls
    ``save_assumption_set`` — no assumption set is created or modified on disk
    or in the DB.
    """
    decrement = DecrementType(decrement)
    perturbed = _deep_copy_assumption_set(baseline_aset)
    attr = _DECREMENT_TO_MULT_ATTR[decrement]

    factors = glm_result.factors
    if not factors:
        return perturbed
    product_mean = float(sum(f.factor for f in factors) / len(factors))
    by_gender: dict[str, list[float]] = {}
    for f in factors:
        g = f.grain_key.get("sex")
        if g is not None:
            by_gender.setdefault(g, []).append(f.factor)

    def _proposed_for(m: DecrementMultiplier) -> float:
        vals = by_gender.get(m.gender)
        return float(sum(vals) / len(vals)) if vals else product_mean

    new_mults: list[DecrementMultiplier] = []
    for m in getattr(perturbed, attr):
        if m.product == product_code:
            new_mults.append(DecrementMultiplier(
                product=m.product, gender=m.gender, risk_class=m.risk_class,
                duration_band=list(m.duration_band), multiplier=_proposed_for(m),
                credibility_z=m.credibility_z, credibility_lower=m.credibility_lower,
                credibility_upper=m.credibility_upper,
                override_rationale="AI what-if (GLM proposal)",
            ))
        else:
            new_mults.append(m)
    setattr(perturbed, attr, new_mults)
    return perturbed


def run_whatif_tev(
    db_path: Path,
    approved_aset: AssumptionSet,
    whatif_aset: AssumptionSet,
    prior_tev_run_id: Optional[str] = None,
):
    """Run the TEV what-if and return ``(whatif_result, baseline_total_tev)``.

    Runs a baseline projection on the approved set, then the what-if projection
    on the in-memory perturbed set flagged ``sensitivity_id='what_if_ai_proposal'``
    (FR-3A-43), with the baseline as ``prior_tev_run_id`` so the engine fills in
    ΔTEV vs the approved basis. Both projections use the existing model points
    (the engine loads the latest build per product); neither creates or modifies
    any assumption set.
    """
    from src.tev.tev_core import run_tev

    if prior_tev_run_id is None:
        baseline = run_tev(
            db_path,
            assumption_set_id=approved_aset.id,
            assumption_set=approved_aset,
            tev_run_id=str(uuid.uuid4()),
        )
        prior_tev_run_id = baseline.tev_run_id
        baseline_total = baseline.total_tev
    else:
        baseline_total = _prior_total_tev(db_path, prior_tev_run_id)

    whatif = run_tev(
        db_path,
        assumption_set_id=approved_aset.id,
        assumption_set=whatif_aset,
        sensitivity_id=WHAT_IF_SENSITIVITY_ID,
        prior_tev_run_id=prior_tev_run_id,
        tev_run_id=str(uuid.uuid4()),
    )
    return whatif, baseline_total


def _prior_total_tev(db_path: Path, tev_run_id: str) -> Optional[float]:
    """Total TEV of a prior run from gold_tev_run_log (read-only)."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT total_tev FROM gold_tev_run_log WHERE tev_run_id = ?",
            [tev_run_id],
        ).fetchone()
    finally:
        con.close()
    return float(row[0]) if row and row[0] is not None else None


# ---------------------------------------------------------------------------
# Approved assumption set + SHAP reading (read-only helpers for the page)
# ---------------------------------------------------------------------------

def latest_approved_assumption_set(db_path: Path) -> Optional[AssumptionSet]:
    """Load the most recently approved AssumptionSet, or ``None`` if none exist."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT assumption_set_id FROM gold_assumption_sets "
            "WHERE status = 'APPROVED' ORDER BY approved_ts DESC NULLS LAST, created_ts DESC "
            "LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return load_assumption_set(row[0], db_path)


def load_shap_json(shap_json_path: str) -> Optional[dict]:
    """Read a persisted SHAP-JSON artifact (FR-3A-38); never recompute SHAP."""
    if not shap_json_path:
        return None
    p = Path(shap_json_path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def shap_cell_for_grain(shap_json: dict, grain_key: dict) -> Optional[dict]:
    """Return the SHAP cell whose grain_key matches, scoped for the waterfall."""
    target = _grain_id(grain_key)
    for cell in shap_json.get("cells", []):
        if _grain_id(cell.get("grain_key", {})) == target:
            return cell
    return None
