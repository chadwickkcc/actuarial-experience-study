# Demo Refresh — Scope & Decision Authority

**Status:** ACTIVE (authored 2026-08-30, P0). This document is the **authoritative record of
every decision, schema and contract this demo refresh changes**. Where it conflicts with the
legacy specs (`experience_study_requirements_spec_v3_0_1.md` / `_v4_0.md`,
`experience_study_technical_spec_v2_0_1.md` / `_v3_0.md`), **this document supersedes them** —
the legacy specs remain valid for everything not named here. CLAUDE.md rules 1–2 (exact DDL /
exact interfaces) now point here for the schemas and interfaces changed by the refresh.

**Companion docs:** `demo_refresh_prompts.md` (per-phase session blocks),
`demo_refresh_progress.md` (status board + handoff log),
`~/.claude/plans/for-context-this-is-ancient-ocean.md` (approved plan, session-local copy).

---

## 1. Objective

Turn the completed PoC into a slick, client-ready demo for actuaries ("art of the possible"):

1. **TEV modelling removed entirely.** Kept: A/E engine + results/reports, AI-proposed
   assumptions, assumption-set editing/proposal, full governance (login/RBAC/multi-level
   sign-off/hash-chain audit/compliance pack).
2. **Management commentary capability** (new): YoY A/E movement with driver attribution,
   3-year improving/worsening trend classification, proposed management actions, justification
   of proposed assumptions (best-estimate alignment, credibility-weighted A/E).
3. **AI fraud detection** (new): 6 rule indicators + weighted composite score + LLM-drafted
   narrative (aggregates only to the LLM).
4. **Slickness**: fewer pages, clean nav, consistent look, demo walkthrough script.

## 2. Owner decisions (locked 2026-08-30)

| # | Decision |
|---|----------|
| D1 | Cut the 5 Product Monitor pages (`ui/views/08,09,10,11,12`) + merge `14_ci_incidence_summary` into `06_ci_explorer`. Keep `13_product_comparison`. |
| D2 | Fraud depth = rules + weighted composite score + AI narrative. **No ML anomaly overlay.** |
| D3 | Full dataset expansion + regeneration; fresh demo DB; prior run history discarded. Seed 42. |
| D4 | Keep full governance layer; polish UI only; materiality basis rewired off TEV. |

## 3. Design locks

### 3.1 Module layout
- `src/assumptions/` — assumption-set lifecycle: `assumption_set.py` (moved verbatim from
  `src/tev/assumption_set.py`, later slimmed of economic fields) + `workflow.py` (moved from
  `src/tev/workflow.py`) + `deep_copy_assumption_set` (from `src/tev/sensitivities.py`).
- `src/analysis/commentary.py` — deterministic commentary analytics (plain parameterized
  DuckDB, engine-side; NOT under `src/ai/`; importable by UI and reports).
- `src/fraud/` — `rules.py` + `runner.py`, mirroring the `src/data_quality/` pattern.
- `src/tev/` — **deleted entirely** by end of P3.

### 3.2 Governance materiality (replaces FR-4-16 ΔTEV basis)
- Basis: **max |Δ multiplier| vs prior approved version in the lineage**, computed from
  `lineage.compare_versions(...).changed_cells`. `None` (no prior approved version) → full chain
  (unchanged semantics).
- Config: `governance_config.yaml` `materiality.delta_tev_threshold` →
  `materiality.max_multiplier_delta_threshold: 0.05` (owner-tunable);
  `final_level_below_threshold` unchanged.
- Column: `gold_governance_signoffs.delta_tev` → `materiality_value` (DDL +
  `governance/audit._SIGNOFF_COLUMNS`; append/verify stay symmetric — one constants list).
  Safe because every DDL phase ends with a fresh live-DB rebuild (chains restart).
- `VersionDiff.delta_tev` → `materiality_value`; page 29 renders it; `readiness.py` config
  asserts updated.

### 3.3 Legacy approval table retired
`gold_assumption_approvals` (NOT-NULL TEV columns) + `_write_legacy_summary` +
`record_governance_approval` are **removed**; `gold_governance_signoffs` is the sole formal
record. `gold_workflow_iterations` loses `tev_baseline_run_id`, `total_tev`,
`delta_tev_vs_prior`, `envelope_run_flag`.

### 3.4 Assumption workflow v2 (3 steps)
- Step 1 `ui/views/20_assumption_step1.py` — pick COMPLETE run → create proposed set (RBAC
  propose gate, authenticated author, resume-existing).
- Step 2 `ui/views/21_assumption_step2.py` — the editor (3 multiplier tabs, credibility-bound
  hard-block `_validate_bounds`, Restore-from-A/E, Adopt-AI-proposal, mandatory comment,
  save → PROPOSED) **+ Submit for sign-off** (PROPOSED → STAGE3_APPROVED, extracted from old
  Stage 3) + refine loop + iteration history.
- Step 3 `ui/views/22_assumption_step3.py` — multi-level sign-off chain (attestation,
  segregation, APPROVE/RETURN, lock) + AI memo block.
- Status enum unchanged: DRAFT → PROPOSED → STAGE3_APPROVED → APPROVED → SUPERSEDED.
  (`STAGE3_APPROVED` label = "submitted for sign-off"; enum value kept to avoid churn.)

### 3.5 AI surface after TEV purge
- MCP tools 6 → 4 (`query_ae_results`, `query_results`, `list_available_dimensions`,
  `get_study_run_summary`); `TOOL_SCHEMA_VERSION` → "3.0".
- Allowlist: `gold_tev_results` + `gold_model_points` blocks removed. (P5 adds
  `gold_fraud_run_summary` only.)
- Memo = **7 components** (component "TEV Impact" deleted; management actions live in the new
  management-commentary skill, not the memo).
- Few-shots 43 → 35 (P3), then +2–3 fraud pairs (P5). Golden set 36 → 30 (G027–G032 removed);
  adversarial A007 retargeted to a surviving table. **Owner re-locks both sets at P3.**
- Prompts changed: `memo.md` (7 components), `commentary.md` (TEV out; later v3.0 cites
  yoy/trends, still no recommendations in chat), `sql_generation.md`, `synthesis_plan.md`,
  `routing.md` (TEV cards/wording out). New: `skills/fraud_narrative.md` (P5),
  `skills/management_commentary.md` (P6 — recommendations allowed, AI-DRAFT banner).

### 3.6 Fraud detection (P5)
Rules (ids FR-RULE-01..06), thresholds/weights in `config/fraud_config.yaml`:
1. `first_policy_year_claim` — claim event with `policy_year = 1`.
2. `claim_exceeds_premiums` — claim amount > cumulative premiums paid (≈ annual_premium ×
   policy_year; UL uses `cumulative_premiums_paid` where present).
3. `claim_above_materiality` — claim amount > per-product threshold.
4. `agency_office_concentration` — office count of **first-policy-year** claims ≥
   configured multiple of the office median (early-claim clustering — the sharp
   fraud signal; total-claim share dilutes under organic volume).
5. `similar_claims_same_claimant` — same `claimant_id` + same illness_code + amounts within
   configured tolerance.
6. `high_risk_region_or_hospital` — claim `hospital_id`/`claim_region` in configured lists.

Composite score = Σ(weight × hit), flag when ≥ `flag_threshold`. Config hash stamped on run.

Tables (added to `db_init.py`):
- `gold_fraud_run_summary(fraud_run_id PK, study_run_id, run_ts, run_by, config_hash,
  n_claims_scored, n_claims_flagged, composite_threshold, rule_hit_counts JSON,
  score_p50, score_p95, score_max)` — **only fraud table on the chatbot allowlist**.
- `gold_fraud_scores(fraud_run_id, claim_event_id, policy_id, product_code, event_type,
  event_date, claim_amount, agency_office_id, agent_id, claimant_id, hospital_id,
  claim_region, composite_score, n_rules_hit, flagged BOOLEAN,
  PK(fraud_run_id, claim_event_id))` — off-allowlist.
- `gold_fraud_flags(fraud_run_id, claim_event_id, rule_id, weight, evidence JSON,
  PK(fraud_run_id, claim_event_id, rule_id))` — off-allowlist.

**PII bright line:** fraud LLM fact pack carries aggregates + institutional entities
(office/hospital/region ids) only — never `policy_id` or `claimant_id` (regex guard test).
Claim-level fraud tables never enter the chatbot allowlist.

### 3.7 Synthetic data expansion (P4)
- New `config/synthetic_data.yaml` (volumes move out of module constants): TERM 8000, WL 7000,
  UL 2000 + ULSG 2000 + IUL 500, VUL 2000, DA 3500 = **25,000 policies**. Seed 42 unchanged.
- CI: rider penetration 0.45 (Term/WL), incidence ×3.5. **P4 build note (amends this
  section):** CI claims stayed **terminal** (as in the original generator) — the
  incidence/penetration boost alone delivers ~590 claims, and "similar claims from the
  same claimant" spans *policies* via the shared `claimant_id`, so the non-terminal
  rework (which would have rippled into exposure/A/E CI counting) was dropped as
  unnecessary. The `ci_*` claim columns were therefore not added; claim identity fields
  live on the policy row (single terminal claim).
- New fields — policy grain: `agency_office_id` (~40, `OFF-###`), `agent_id` (~8/office,
  `AGT-####`); claim grain: `claimant_id` (`CLM-######`), `hospital_id` (~60, `HOSP-###`),
  `claim_region` (small region list from state groupings). Flow: generators → CSV →
  `config/products/*.yaml` field_mappings → silver DDL → `_build_policy_events` (claimant/
  hospital/region on DEATH + CI_CLAIM events).
- **Planted stories (draw-side only — NEVER in reference tables, or they cancel out of A/E):**
  - Mortality deterioration: draw ×1.08 / ×1.16 / ×1.25 for calendar 2021/2022/2023, Term + WL.
  - Lapse spike: draw ×1.4 for calendar 2022–2023, Term + UL family.
  - Fraud ring (as built): rogue office `OFF-013`, ghost hospital `HOSP-066` (outside
    the 60-facility roster), repeat claimant `CLM-424242` (4 similar CI-001 claims,
    faces within ±5%), 14 Term ring policies issued 2021–23 in NV/SOUTHWEST, all with
    first-policy-year CI claims. OFF-013 first-year claim count ≈ 15 vs office median 1.
  - All magnitudes YAML-tunable; tuned in-phase until the story-lock tests pass.
- Story-lock tests (permanent): Term+WL mortality A/E strictly increasing 2021→2023 with
  2023 ≥ 2021 + 0.10; lapse A/E 2022–23 ≥ 115% of 2016–21 mean; total CI claims ≥ 250 across
  ≥ 8 illness codes; ≥ 4 claims share `CLM-424242`; `OFF-013` claim count ≥ 3× office median.

### 3.8 Commentary analytics (P6)
- `src/analysis/commentary.py`: `compute_yoy_movement` (per calendar year: A/E, actual,
  expected, Δ vs prior), `attribute_drivers` (per-segment contributions **summing exactly to
  ΔA/E**, exposure-weighted; dimensions per decrement from `config/commentary_config.yaml`:
  lapse/CI → product line; mortality/morbidity → age band + gender; lapse additionally policy
  year), `classify_trends` (3-yr slope via `numpy.polyfit`; improving/worsening/stable via
  YAML thresholds), `justification_metrics` (`credibility_wtd_ae` (existing unused column),
  proposed vs current multiplier, credibility Z, BE gap), movement legs from per-product
  `gold_inforce_reconciliation`.
- Fact-pack v2 — additive keys on `assemble_commentary_facts`: `yoy`, `trends`,
  `justification`, `movement`. All numbers pre-computed (prompts forbid model arithmetic).
- UI `ui/views/18_management_commentary.py`: deterministic tables/charts (driver waterfall,
  trend badges) + "Draft management commentary" (model dropdown, AI-DRAFT banner, .md export).

### 3.9 Database schema deltas (cumulative)
| Change | Phase |
|---|---|
| `gold_governance_signoffs.delta_tev` → `materiality_value` | P2 |
| Drop `gold_assumption_approvals` (table + writer + verify registry) | P2 |
| Drop TEV columns from `gold_workflow_iterations` | P2 |
| Drop `gold_model_points`, `gold_tev_run_log`, `gold_tev_results` (+indexes) | P3 |
| Drop `rdr`, `earned_rate_ga`, `earned_rate_sa`, `tax_rate`, `expense_inflation` from `gold_assumption_sets` | P3 |
| Silver policy tables + `silver_policy_events`: new fraud/identity columns | P4 |
| Add `gold_fraud_run_summary`, `gold_fraud_scores`, `gold_fraud_flags` | P5 |

### 3.10 Nav target (P7, tunable at build)
5 groups / 20 pages (as built; the 19 in the plan was indicative): **Overview** (Home, Run Study, Data Quality, Run Log) ·
**Experience Results** (Exposure, Mortality, Lapse, CI, Product Comparison, Management
Commentary) · **Risk & Fraud** (Fraud Monitor) · **Assumptions & AI** (Assumption Comparison,
AI Analyst, Steps 1–3, Lineage) · **Governance** (Study Run Sign-off, Dashboard, Audit).
Shared `ui/theme.py` (palette + `page_setup()`); requirement-ID (FR-/NFR-) strings removed
from client-visible copy (grep-guarded).

## 4. Global conventions

- **Gate** (every phase): `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY &&
  .venv/bin/python -m pytest tests/ -v --tb=short` green. Baseline at P0: **1368 passed,
  6 skipped**.
- **Live-DB rebuild rule:** any phase changing DDL or the generator ends with a headless
  rebuild (`scripts/reset_for_testing.py` → `scripts/_uat_rerun.py` → `scripts/_uat_ai_fit.py`
  → `scripts/_uat_seed_workflow.py`) so realdata tests + the running app stay coherent, hash
  chains restart fresh, and the DB ships with seeded users + one completed example
  assumption-set workflow (P8 addition — `_uat_rerun.py` also seeds `gold_users`).
- Governance spine never relaxed: MCP-only chatbot data path; AI dynamic SQL via
  `src/utils/sql_boundary.py`; traceability blocks-not-repairs; PII bright line; every
  threshold in YAML; seed 42; no new dependencies; delete over dead code.
- Streamlit boot smoke when `ui/` touched. Atomic commit(s) per phase on `main`.

## 5. Known live bugs fixed in-refresh
- `gold_inforce_reconciliation` delete-scope bug (`src/exposure/engine.py` deletes by
  `study_run_id` only → only last product's rows survive) — fixed P1 (movement legs needed by
  commentary).

## 6. Deferred (recorded in DEFERRED_FOLLOWUPS.md at P8)
- Fraud investigator-override workflow (DQ-style override trail).
- Optional fraud/commentary golden-set additions (owner decision at P8).
- `anti_selection_flag` computed but dropped before insert (pre-existing; harmless; out of
  demo scope).
