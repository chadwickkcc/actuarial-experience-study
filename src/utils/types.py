"""Shared data types for the actuarial experience study tool."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
import pandas as pd


class ProductCode(str, Enum):
    TERM    = "TERM"
    WL      = "WL"
    UL      = "UL"
    ULSG    = "ULSG"
    IUL     = "IUL"
    VUL     = "VUL"
    DA      = "DA"          # deferred annuity (generic)
    DA_FIXED  = "DA_FIXED"
    DA_FIA    = "DA_FIA"
    DA_VA     = "DA_VA"


class ExposureMethod(str, Enum):
    ANNUAL      = "ANNUAL"
    DISTRIBUTED = "DISTRIBUTED"


class CredibilityMethod(str, Enum):
    LIMITED_FLUCTUATION = "LF"
    BUHLMANN            = "BUHLMANN"


class StudyRunStatus(str, Enum):
    RUNNING  = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED   = "FAILED"


class AssumptionSetStatus(str, Enum):
    DRAFT           = "DRAFT"            # Phase 4 lineage: a newly created version (FR-4-07)
    PROPOSED        = "PROPOSED"
    STAGE3_APPROVED = "STAGE3_APPROVED"
    APPROVED        = "APPROVED"
    SUPERSEDED      = "SUPERSEDED"


@dataclass
class StudyConfig:
    study_start_date:       date
    study_end_date:         date
    product_codes:          list[str]
    exposure_method:        ExposureMethod
    mortality_table_path:   str
    lapse_table_path:       str
    ci_table_path:          str
    credibility_method:     CredibilityMethod
    credibility_threshold:  float = 1082.0      # for mortality
    overlay_table_path:     Optional[str] = None


@dataclass
class ETLResult:
    run_id:             str
    product_code:       str
    records_ingested:   int
    records_conformed:  int
    error_count:        int
    warnings:           list[str]
    success:            bool
    duration_sec:       float


@dataclass
class DQCheckResult:
    check_id:       str
    description:    str
    severity:       str         # "ERROR" or "WARN"
    passed:         bool
    fail_count:     int
    sample_records: list[dict]  # up to 10 failing records


@dataclass
class DQResult:
    dq_run_id:          str
    study_run_id:       str
    product_code:       str
    total_records:      int
    records_passed:     int
    records_quarantined: int
    critical_failure:   bool
    dq_score_pct:       float
    check_results:      list[DQCheckResult]
    success:            bool


@dataclass
class ExposureResult:
    run_id:                 str
    product_code:           str
    total_segments:         int
    total_exposure_years:   float
    total_face_amount:      float
    recon_passes:           bool
    recon_diff_count:       int
    recon_diff_amount_pct:  float
    duration_sec:           float


@dataclass
class AEResult:
    run_id:             str
    products_included:  list[str]
    total_exposure:     float
    total_deaths:       int
    total_ae_count:     float       # aggregate A/E by count
    total_ae_amount:    float       # aggregate A/E by amount
    total_ci_claims:    int
    total_ae_ci:        float
    results_df:         pd.DataFrame  # the gold_ae_results records for this run
    duration_sec:       float


@dataclass
class ModelPointResult:
    tev_run_id:             str
    product_code:           str
    seriatim_count:         int
    model_point_count:      int
    compression_ratio:      float
    recon_count_diff_pct:   float   # must be < 0.1%
    recon_face_diff_pct:    float   # must be < 0.1%
    recon_reserve_diff_pct: float   # must be < 0.1%
    model_points_df:        pd.DataFrame


@dataclass
class TEVProductResult:
    product_code:       str
    anw:                float
    pvfp:               float
    pvcoc:              float
    vif:                float
    tev:                float
    pvfp_by_source:     dict[str, float]
    projection_years:   int


@dataclass
class TEVRunResult:
    tev_run_id:         str
    assumption_set_id:  str
    sensitivity_id:     Optional[str]   # None for baseline
    product_results:    list[TEVProductResult]
    total_anw:          float
    total_pvfp:         float
    total_pvcoc:        float
    total_vif:          float
    total_tev:          float
    delta_tev:          Optional[float]
    duration_sec:       float


@dataclass
class SensitivityGridResult:
    baseline_run_id:            str
    assumption_set_id:          str
    sensitivity_results:        list[TEVRunResult]  # one per SENS-01..SENS-11
    impact_matrix_df:           pd.DataFrame
    # impact_matrix_df shape:
    #   index = product_codes + ["TOTAL"]
    #   columns = sensitivity_ids
    #   values = delta_tev


@dataclass
class EnvelopeResult:
    success:                    bool
    assumption_set_id:          str
    top5_decrements:            list[str]
    proposed_tev:               float
    tev_min:                    float
    tev_max:                    float
    envelope_width_abs:         float   # tev_max - tev_min
    envelope_width_pct:         float   # envelope_width_abs / proposed_tev
    proposed_envelope_percentile: Optional[float]  # None if width below materiality floor
    percentile_undefined_reason: Optional[str]
    theta_proposed:             dict[str, float]   # decrement_key -> identity theta (1.0 or 0.0)
    theta_min:                  dict[str, float]   # theta values producing tev_min
    theta_max:                  dict[str, float]   # theta values producing tev_max
    credibility_bounds:         dict[str, tuple[float, float]]
    n_evaluations_min:          int
    n_evaluations_max:          int
    convergence_message_min:    str
    convergence_message_max:    str
    envelope_yaml_path:         str     # read-only audit artefact; NOT an AssumptionSet


# ============================================================================
# Phase 3 — AI layer shared types (Tech Spec v2.0.1 §E.1)
# ----------------------------------------------------------------------------
# Appended in Session 14 (security hardening). Only the types the SQL boundary
# needs now are added here; the remaining §E.1 types (FactorCell, GLMFitResult,
# GBMFitResult, ValidationResult, LLMResponse, TraceabilityResult,
# ChatTurnResult, and the DecrementType/AIModelType/IntentLabel enums) land in
# Sessions 15–20 as their consumers are built. No existing type is modified.
# ============================================================================


class SQLGateOutcome(str, Enum):
    """Outcome of the hardened SQL boundary's validation gates (FR-3B-31)."""
    PASS              = "PASS"
    REJECT_PARSE      = "REJECT_PARSE"
    REJECT_NOT_SELECT = "REJECT_NOT_SELECT"
    REJECT_ALLOWLIST  = "REJECT_ALLOWLIST"
    REJECT_ROWCAP     = "REJECT_ROWCAP"
    REJECT_BOUNDARY   = "REJECT_BOUNDARY"


@dataclass
class SQLValidationResult:
    """Result of validating a candidate SELECT against the boundary gates.

    Rejected user SQL is *returned* as one of these (never raised); exceptions
    are reserved for misuse of the boundary API itself (SQLBoundaryError).
    """
    outcome:       SQLGateOutcome
    sql:           str                    # normalized/expanded SQL on PASS; original otherwise
    gate_failed:   Optional[str] = None   # gate identifier on reject (e.g. "gate_3_allowlist")
    detail:        Optional[str] = None


# ----------------------------------------------------------------------------
# Phase 3 — GLM assumption-engine types (Session 15; Tech Spec v2.0.1 §E.1)
# ----------------------------------------------------------------------------
# Consumed by src/ai/glm/. FactorCell realises FR-3A-19; GLMFitResult realises
# FR-3A-12/24; ValidationResult realises FR-3A-26/27. (AIModelType also tags the
# GBM models that land in Session 16.)


class DecrementType(str, Enum):
    """Decrement types. MORTALITY/LAPSE/CI_INCIDENCE are modelled by the AI layer
    (FR-3A-12). SURRENDER is **experience/memo-only** — it is reported in A/E
    results and can be drafted into a memo, but is never fit by the GLM/GBM
    engine (no GLM config / `_MEASURES` entry; guarded in `fit_models`)."""
    MORTALITY    = "MORTALITY"
    LAPSE        = "LAPSE"
    CI_INCIDENCE = "CI_INCIDENCE"
    SURRENDER    = "SURRENDER"


class AIModelType(str, Enum):
    """Statistical-model family recorded in gold_ai_model_registry (§D.1)."""
    GLM = "GLM"
    GBM = "GBM"


@dataclass
class FactorCell:
    """One published adjustment factor at the configured output grain (FR-3A-19)."""
    grain_key:         dict[str, str]   # e.g. {"product": "WL", "duration_band": "6-10"}
    factor:            float            # proposed A/E adjustment factor
    ci_low:            float            # bootstrap 95% CI lower
    ci_high:           float            # bootstrap 95% CI upper
    expected_events:   float
    credibility_z:     float            # decrement-appropriate Z from Phase 1 gold_ae_results
    ae_derived_factor: float            # for side-by-side display


@dataclass
class GLMFitResult:
    """Outcome of fitting one GLM for a decrement-product (FR-3A-12/24)."""
    model_id:         str
    run_id:           str
    decrement:        DecrementType
    product_code:     str
    converged:        bool
    n_cells:          int
    deviance:         float
    dispersion:       float
    aic:              float
    factors:          list[FactorCell]
    diagnostics_path: str              # serialized residual-by-dimension artifacts
    seed:             int
    message:          Optional[str] = None   # populated when converged is False


@dataclass
class GBMFitResult:
    """Outcome of fitting one GBM (XGBoost) challenge model (FR-3A-31/33; §E.1).

    The GBM is the challenge/explain overlay, never the proposal engine: its
    ``factors`` are a reference column comparable to the GLM's, and
    ``divergence_flags`` carry the cells where it materially disagrees with the
    GLM (FR-3A-33). Unlike ``GLMFitResult`` there is no ``converged``/``message``
    field (§E.1): a sub-threshold or failed fit returns ``factors=[]`` with
    ``cv_metric_value=NaN`` and empty ``divergence_flags`` (FR-3A-29).
    """
    model_id:         str
    run_id:           str
    decrement:        DecrementType
    product_code:     str
    n_cells:          int
    cv_metric_name:   str              # "deviance" (mortality) / "logloss" (lapse, CI)
    cv_metric_value:  float            # 5-fold CV test-mean (FR-3A-32); NaN if unavailable
    factors:          list[FactorCell]
    divergence_flags: list[dict]       # cells where |GBM-GLM|/GLM > threshold (FR-3A-33)
    shap_json_path:   str              # → SHAP-JSON (§D.6); set at registration
    seed:             int


@dataclass
class ValidationResult:
    """Synthetic-truth recovery check for one decrement-product (FR-3A-26/27)."""
    decrement:        DecrementType
    product_code:     str
    cells_validated:  int
    cells_within_tol: int
    tolerance_pct:    float
    coverage_pct:     float            # share of cells with truth inside 95% CI
    passed:           bool


# ----------------------------------------------------------------------------
# Phase 3b — LLM provider-abstraction type (Session 18; Tech Spec v2.0.1 §E.1)
# ----------------------------------------------------------------------------
# The single unified response object returned by every provider's complete()
# (FR-3B-01). No module outside src/ai/llm/ constructs one directly. Token
# counts and latency feed the session cost display (FR-3B-43) and audit log
# (FR-3B-47); ``provider``/``model`` identify the exact call.


@dataclass
class LLMResponse:
    """Unified, provider-agnostic completion result (FR-3B-01)."""
    text:           str
    input_tokens:   int
    output_tokens:  int
    provider:       str                # "anthropic" | "deepseek" | "mock"
    model:          str                # the resolved model string actually called
    latency_ms:     float
    stop_reason:    Optional[str] = None


# ----------------------------------------------------------------------------
# Phase 3b — numeric-traceability type (Session 19; Tech Spec v2.0.1 §E.1/§E.7)
# ----------------------------------------------------------------------------
# Result of the mandatory deterministic post-check (FR-3B-34). Pulled forward in
# Session 19 because the two Skills (memo, SHAP) need the numeric guard before
# the Session-20 chatbot is built; Session 20 consumes the same module unchanged.


@dataclass
class TraceabilityResult:
    """Outcome of the numeric post-check on a rendered answer (FR-3B-19/22/34)."""
    passed:           bool
    untraceable_nums: list[str]        # numeric tokens that failed to trace


# ----------------------------------------------------------------------------
# Phase 3b — conversational-chatbot types (Session 20; Tech Spec v2.0.1 §E.1)
# ----------------------------------------------------------------------------
# Consumed by src/ai/chatbot/. IntentLabel realises FR-3B-27 (one of four routes);
# ChatTurnResult realises FR-3B-47 (the per-turn record the pipeline returns and
# the Session-21 audit log persists). No existing type is modified.


class IntentLabel(str, Enum):
    """The single route assigned to each user message by the router (FR-3B-27).

    ``out_of_scope`` short-circuits to the templated refusal path with no data
    access (FR-3B-28/42).
    """
    FACTUAL_LOOKUP        = "FACTUAL_LOOKUP"
    EXPLORATORY           = "EXPLORATORY"
    COMMENTARY_GENERATION = "COMMENTARY_GENERATION"
    OUT_OF_SCOPE          = "OUT_OF_SCOPE"


@dataclass
class ChatTurnResult:
    """The outcome of one chatbot turn (FR-3B-47).

    ``blocked`` is True when a validation gate, an unresolved numeric slot, or the
    mandatory traceability post-check stopped the answer (block-not-repair,
    FR-3B-34); ``block_reason`` names which. ``llm_response`` is the routing call's
    response (the per-turn LLM attribution for the cost display / audit log); it is
    ``None`` only when the turn short-circuited before any LLM call (e.g. a budget
    hard-stop or max-turns prompt).
    """
    session_id:        str
    intent:            Optional[IntentLabel]
    response_text:     str
    sql:               Optional[str]
    sql_outcome:       Optional[SQLGateOutcome]
    result_row_count:  Optional[int]
    traceability:      Optional[TraceabilityResult]
    llm_response:      Optional[LLMResponse]
    blocked:           bool
    block_reason:      Optional[str] = None


# ============================================================
# Phase 4 — Governance shared types (Tech Spec v3.0 §H.1)
# ------------------------------------------------------------
# Identity, RBAC, and approval-chain value objects. Defined together so the
# Session-23 identity layer (Role/User/ChainLevel) and the Session-24/25
# lineage + workflow engine (ArtifactType/Decision/SignoffRecord) share one
# source of truth. Governance is additive application code outside src/ai/.
# ============================================================

class Role(str, Enum):
    ANALYST        = "analyst"          # doer / proposer
    JUNIOR_ACTUARY = "junior_actuary"   # checker
    SENIOR_ACTUARY = "senior_actuary"   # reviewer
    CHIEF_ACTUARY  = "chief_actuary"    # final approver


class ArtifactType(str, Enum):
    ASSUMPTION_SET = "ASSUMPTION_SET"
    STUDY_RUN      = "STUDY_RUN"


class Decision(str, Enum):
    APPROVE = "APPROVE"
    RETURN  = "RETURN"


@dataclass(frozen=True)
class User:
    """An identity from the gold_users registry (FR-4-01)."""
    user_id:      str
    username:     str
    display_name: str
    role:         Role
    active:       bool


@dataclass(frozen=True)
class ChainLevel:
    """One level of a configured sign-off chain (FR-4-12)."""
    level:         int      # 1-based position in the chain
    required_role: Role


@dataclass(frozen=True)
class SignoffRecord:
    """One chain-level sign-off action on an artifact (FR-4-15)."""
    signoff_id:       str
    artifact_type:    ArtifactType
    artifact_id:      str
    artifact_version: Optional[int]   # assumption-set version; None for a study run
    chain_level:      int
    actor:            User
    decision:         Decision
    comment:          str
    attestation_text: str
    signoff_ts:       datetime


@dataclass(frozen=True)
class VersionDiff:
    """Cell-level diff between two assumption-set versions (FR-4-10; §H.5).

    ``delta_tev`` is ``tev_b - tev_a`` from each set's latest baseline TEV run;
    it is ``float('nan')`` when either version has no baseline TEV run.
    """
    changed_cells:     list[dict]          # {decrement, dimension, old, new, rationale}
    delta_tev:         float
    rationale_by_cell: dict[str, str]


@dataclass(frozen=True)
class IntegrityResult:
    """Outcome of verifying one hash-chained governance log (FR-4-21; §H.7).

    ``first_divergence_seq`` is the ``seq`` of the first row whose recomputed
    hash chain diverges from what is stored (broken linkage or tampered business
    column); ``None`` when the chain is intact. ``rows_checked`` counts the hashed
    rows examined (a log with no hashed rows verifies as ``ok=True`` / 0).
    """
    table:                str
    ok:                   bool
    first_divergence_seq: Optional[int]
    rows_checked:         int


@dataclass(frozen=True)
class AuditFilter:
    """Filter for the unified governance-audit read layer (FR-4-22; §H.7).

    All dimensions optional (``AuditFilter()`` = unfiltered). ``date_from`` /
    ``date_to`` are inclusive date bounds applied to each event's timestamp.
    """
    actor_user_id: Optional[str]  = None
    role:          Optional[Role] = None
    artifact_id:   Optional[str]  = None
    date_from:     Optional[date] = None
    date_to:       Optional[date] = None
    action:        Optional[str]  = None
