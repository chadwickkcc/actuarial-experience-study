# Phase 3 — Claude Code Session Prompts (Sessions 14–22)

**Companion documents:** Requirements Spec v3.0.1, Technical Spec v2.0.1 (both in the project; authoritative — these prompts point, they do not restate).
**How to use:** Run one session at a time, in order. Open both specs alongside Claude Code. Paste one session block. Do not start a session until the prior session's regression gate is green. Where a session contains a **STOP — OWNER INPUT** checkpoint, Claude Code must halt and request your sign-off before proceeding past that point.

**Global conventions (apply to every session):**
- Regression gate command: `pytest tests/ -v --tb=short`, run with **no LLM API keys in the environment** (MockProvider only). Must be green before the session is "done" and before the next session starts.
- All test artifacts under `tests/_artifacts/` (gitignored); never write to `data/` from tests. (NFR-T block, Tech Spec §F.4.)
- Every AI artifact/log row carries the reproducibility stamp (FR-3A-11).
- The AI layer is strictly additive: it lives under `src/ai/` with the structure in FR-3A-06; the core engine must never import from `src/ai/` (FR-3A-07 one-way rule, import-graph tested); Phases 1–2 must run identically with the AI layer absent. AI reads only the Gold layer + config/reference files (FR-3A-08 read contract); AI writes only to `data/ai_models/` and the three AI Gold tables (FR-3A-09 write contract) — never to assumption sets or Phase 1–2 tables.
- Configuration over code: every threshold, grain, cap, seed, model string in the YAML surfaces of FR-3A-10 (`ai_config.yaml`, `llm_config.yaml`, `chatbot_few_shots.yaml`, `config/prompts/`); none hardcoded (Tech Spec §F.1/§F.2).
- Do not implement anything scoped to a later session. Respect each block's **Out of scope** fence.

---

## Session 14 — Security Hardening (Phase 3a entry)

**Goal:** Implement the three security-review items that gate all AI-layer code, changing no Phase 1–2 behaviour.

**Preconditions:** Full Phase 1–2 regression suite green (679 tests + 6 documented skips).

**Spec references:** FR-3A-01..05 (Req §7.2); Tech Spec §E.2 (`src/utils/sql_boundary.py`), §F.4 (interpolation scan test).

**Build this:**
- `src/utils/sql_boundary.py` per §E.2: `load_allowlist`, `validate_select` (gates 1–4, pure, no DB), `execute_safe_select` (gates 1–4 then read-only execution = gate 5), `SQLBoundaryError`. Rejected user SQL is *returned* as `SQLValidationResult`, never raised; exceptions are for boundary misuse only. Implement the `SELECT *` expansion-to-allowlisted-subset decision exactly as §E.2 gate 3 specifies.
- Set `autoescape=True` on all Jinja2 environments (FR-3A-03).
- Introduce a lockfile (pip-tools or uv) pinning the full current dependency tree (FR-3A-04). **Do not** add the ML/LLM stack yet — that lands in later sessions, after which the lockfile is regenerated.
- Establish the additive-layer architecture now (so it is tested from the outset): create the empty `src/ai/` package skeleton per FR-3A-06 (glm/, gbm/, llm/, chatbot/, mcp_server/, skills/, eval/); the SQL boundary lives in `src/utils/` (FR-3A-01). Record the read/write contracts (FR-3A-08/09) and config-surface principle (FR-3A-10) as the constraints the architecture tests below enforce.

**Author this:** none (no prompt templates or fixtures this session).

**Out of scope:** any `src/ai/` module; any LLM dependency; the allowlist *content* beyond what `load_allowlist` needs structurally (the populated allowlist YAML is finalised in Session 20 config, though the loader and schema land here).

**Tests to write:**
- `validate_select` unit tests for each gate: clean SELECT passes; multi-statement, DDL/DML/PRAGMA/ATTACH/transaction-control all reject with the correct `SQLGateOutcome`; off-allowlist table and column reject; missing-LIMIT non-aggregated scan rejects; `SELECT *` expands to the allowlisted subset.
- `execute_safe_select` opens a read-only connection; a write attempt raises `SQLBoundaryError`.
- `test_no_sql_string_interpolation` (§F.4): scans `src/ai/` for f-string/`%`/`.format()`/concatenation into SQL; passes now (directory empty) and remains a standing guard.
- Jinja autoescape: existing A/E and TEV reports render byte-comparably, or with reviewed/accepted diffs (FR-3A-03).
- Import-graph test: the core engine (`src/calculation/`, `src/tev/`, etc.) has no imports from `src/ai/` (FR-3A-07); a standing guard that holds as later sessions populate `src/ai/`.
- Write-contract scaffold test (FR-3A-09): no AI-layer write path targets anything outside `data/ai_models/` and the three AI Gold tables. (Asserts trivially now with an empty layer; grows as modules land.)

**Definition of done (subset of §7.12 Phase 3a):**
- [ ] Lockfile present; builds install from it exclusively.
- [ ] SQL boundary in place; interpolation scan passes on `src/ai/`.
- [ ] `autoescape=True` everywhere; reports render byte-comparably or with accepted diffs.
- [ ] `src/ai/` skeleton in place (FR-3A-06); import-graph test green (FR-3A-07); write-contract scaffold test green (FR-3A-09).
- [ ] Full Phase 1–2 regression suite green immediately after hardening (FR-3A-05).

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 15 — GLM Assumption Engine (Phase 3a)

**Goal:** Fit GLMs that propose A/E adjustment factors with bootstrap CIs, and prove they recover the synthetic generator's known true rates within the spec tolerance table.

**Preconditions:** Session 14 gate green.

**Spec references:** FR-3A-12..30 (Req §7.4); Tech Spec §E.1 (Phase 3 types), §E.3 (`src/ai/glm/`), §F.1 (`glm:` config block), §D.1 (`gold_ai_model_registry`).

**Build this:**
- Extend `src/utils/types.py` with the Phase 3 GLM types from §E.1: `DecrementType`, `AIModelType`, `FactorCell`, `GLMFitResult`, `ValidationResult`.
- `src/ai/glm/fit.py`: `load_cells` (reads Gold A/E fact table only, via boundary; benchmark-rate provenance per §E.3), `fit_glm` (Poisson+offset for mortality FR-3A-13; binomial logit for lapse/CI FR-3A-14), `derive_factor` (distinct, unit-tested; lapse/CI factor = predicted ÷ benchmark, FR-3A-14).
- `src/ai/glm/bootstrap.py`: `bootstrap_cis` — parametric bootstrap, determinism-first (master seed → per-resample child seeds; order-independent, FR-3A-21); resample arrays in memory only, never persisted (FR-3A-22).
- `src/ai/glm/validate.py`: `validate_against_truth` (FR-3A-26/27). If the Phase 1 synthetic generator does not already expose its true decrement factors at the validation grain, add that accessor (read-only) so validation compares against ground truth rather than re-deriving it.
- `gold_ai_model_registry` table (§D.1) + GLM model serialization to `data/ai_models/glm/` (FR-3A-24).
- Guardrail (FR-3A-29): below `min_events_to_fit` or non-convergence → "No AI proposal available" with reason; never extrapolate. Read-only/no-blend posture (FR-3A-20, FR-3A-30).

**Author this:**
- The `glm:` block of `config/ai_config.yaml` per §F.1, including the explicit per-product/decrement **tolerance table** (mortality ±10% / annuity ±15% / lapse ±15% / WL lapse ±25% / CI ±20%), `min_events_to_fit: 200`, bootstrap defaults (1000, seed 42), output grains, covariate lists.
- Synthetic-truth validation fixtures: a session-scoped small synthetic dataset (200–400 policies/product) with known true factors exposed for `validate_against_truth`.

**Out of scope:** GBM, SHAP (Session 16); any UI (Session 17); any LLM code.

**Tests to write:**
- `derive_factor` unit tests incl. `benchmark_rate == 0 → NaN`, cell excluded.
- Poisson offset: hand-calculated sample cell matches `fit_glm` factor to 4 decimals.
- Determinism: fit twice with same seed → identical coefficients (FR-3A-24); bootstrap order-independence.
- Synthetic-truth recovery passes the §F.1 tolerance table on all cells ≥30 expected events (FR-3A-26).
- Coverage: true factor inside 95% CI for ≥90% of validated cells per decrement (FR-3A-27).
- No resample arrays on disk after a fit (FR-3A-22).
- Guardrail: a sub-threshold decrement returns the "no proposal" state, not a number (FR-3A-29).

**Definition of done (subset of §7.12 Phase 3a):**
- [ ] GLMs fit for every qualifying decrement-product; factors at configured grain with CIs.
- [ ] Tolerance-table and coverage tests pass.
- [ ] Poisson hand-calc check passes; determinism holds.
- [ ] Registry rows written with full reproducibility stamp; no resample persistence.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 16 — GBM Overlay + SHAP (Phase 3a)

**Goal:** Add the XGBoost challenge model, flag where it diverges from the GLM, and generate the SHAP artifacts that feed the explainability Skill — all reported, none adopted.

**Preconditions:** Session 15 gate green.

**Spec references:** FR-3A-31..40 (Req §7.5); Tech Spec §E.1 (`GBMFitResult`), §E.4 (`src/ai/gbm/`), §D.6 (SHAP-JSON schema), §D.1/§D.5 (registry + artifact layout), §F.1 (`gbm:` config block).

**Build this:**
- Extend `src/utils/types.py` with `GBMFitResult` (§E.1).
- `src/ai/gbm/fit.py`: `fit_gbm` via the XGBoost **core API** (`xgboost.train`, not the sklearn wrapper), `base_margin = log(expected)` for `count:poisson` mortality, exposure-weighted `binary:logistic` for lapse/CI; reuse `derive_factor` from `src/ai/glm/fit.py` so factors are derived identically (FR-3A-31). Divergence flags where `|gbm−glm|/glm > divergence_threshold` (FR-3A-33). 5-fold CV metric recorded (FR-3A-32). Same loud-failure guardrail as the GLM (FR-3A-29). GBM bootstrap reuses the §E.3 pattern at its own resample count (FR-3A-34); no resample persistence.
- `src/ai/gbm/explain.py`: `generate_shap_artifacts` via TreeExplainer at fit time (never runtime, FR-3A-38); persist one schema-conformant SHAP-JSON per model to `data/ai_models/shap/` validated against the §D.6 schema; register against `model_id`.
- GBM serialization to `data/ai_models/gbm/` + registry rows (§D.1/§D.5).

**Author this:**
- The `gbm:` block of `config/ai_config.yaml` per §F.1: fixed hyperparameters (`max_depth: 3`, `n_estimators: 200`, `learning_rate: 0.05`, `min_child_weight: 10`, `gamma: 1.0`, `reg_lambda: 2.0`), `seed: 42`, `nthread: 1`, `divergence_threshold: 0.10`, bootstrap `n_resamples: 200`. No tuning logic (FR-3A-32).
- `src/ai/gbm/shap_schema.json` — the formal JSON Schema for §D.6, with `schema_version`.
- `config/feature_to_assumption.yaml` — the full per-decrement feature→actuarial-term mapping (FR-3A-39); the example in §D.6 is the pattern, complete it for every covariate.

**Out of scope:** any UI (Session 17); any LLM code; the `explain_shap_results` Skill itself (Session 19) — this session produces only the SHAP-JSON it will consume.

**Tests to write:**
- GBM Poisson offset via `base_margin`: hand-checked sample cell consistent with the GLM offset treatment.
- Divergence flag fires on a **constructed** GLM/GBM-disagreement cell **and stays silent on a null (agreement) case** (FR-3A-33).
- SHAP-JSON validates against `shap_schema.json`; additivity holds (`base_value + Σ shap ≈ prediction`, 1e-6); every `feature_to_assumption` key appears in `feature_names` (FR-3A-39).
- GBM truth-recovery is **computed and reported** but is **not** a completion gate (FR-3A-36) — assert it is recorded, not gated.
- Determinism: same seed → identical booster; no resample arrays on disk.

**Definition of done (subset of §7.12 Phase 3a):**
- [ ] XGBoost models fit; divergence flag correct on constructed + null cases.
- [ ] SHAP artifacts generate, persist as schema-conformant JSON, register against `model_id`.
- [ ] Feature-to-assumption mapping complete and round-trips into SHAP-JSON.
- [ ] GBM truth-recovery reported (not gated); determinism holds; no resample persistence.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 17 — Assumption Comparison UI (Phase 3a close)

**Goal:** Surface GLM proposals, GBM challenge, SHAP, and a read-only TEV what-if on one Streamlit page — with no adopt affordance anywhere — and close Phase 3a.

**Preconditions:** Session 16 gate green.

**Spec references:** FR-3A-41..46 (Req §7.6); consumes §E.1/§E.3/§E.4 outputs; §7.12 Phase 3a checklist.

**Build this:**
- New Streamlit page "Assumption Comparison — AI Proposals" (FR-3A-41): selectors for study run, decrement, product; "Fit AI models" action triggering GLM+GBM with progress; "No AI proposal available" states with reasons (FR-3A-29).
- Comparison table (FR-3A-42): per cell at output grain — A/E-derived factor; GLM factor + 95% CI; GBM reference factor + interaction flag; credibility Z (the decrement-appropriate column); expected events; currently-approved factor. Columns labelled so proposal / challenge / approved are never confusable.
- "TEV impact (what-if)" action (FR-3A-43): runs the existing TEV engine with the GLM-proposed factor set substituted, logged as a TEV run flagged `what_if_ai_proposal`; displays ΔTEV vs approved basis; creates/modifies no assumption set.
- Diagnostics expander (GLM + GBM), SHAP displays scoped to the selected cell, feature-to-assumption table; Skill buttons present but greyed with "available in Phase 3b" (FR-3A-45). No adopt/apply affordance anywhere (FR-3A-44). All connections read-only (FR-3A-46). CSV export of the factors table.
- Assumption-set editor extension (FR-3A-30 adoption path): records `ai_proposed_value` and `ai_model_id` alongside the adopted value + existing justification when an edited cell had an AI proposal. (DDL: the §D.4 `ALTER TABLE gold_assumption_sets` additions land here.)

**Author this:**
- A UAT test-script section for this page in your established Streamlit test-script format (pre-flight checklist, per-control test table, numeric targets, cross-page consistency checks). Accumulate it into the Phase 3 UAT script.

**Out of scope:** any LLM code; the Skill *implementations* (greyed buttons only); chatbot.

**Tests to write:**
- What-if run creates no assumption set and is correctly flagged (FR-3A-43).
- Editor records `ai_proposed_value` + `ai_model_id` on an AI-originated edit (FR-3A-30).
- Page asserts no adopt affordance is wired (FR-3A-44); connections read-only.

**Definition of done — Phase 3a complete (full §7.12 Phase 3a checklist):**
- [ ] Comparison page columns labelled per FR-3A-42; "no proposal" states render; no adopt affordance exists.
- [ ] What-if logs as `what_if_ai_proposal`, touches no assumption set.
- [ ] Editor records AI provenance fields.
- [ ] UAT script section for the page produced.
- [ ] **Gate to 3b:** full Phase 3a checklist passes and the entire regression suite (Phases 1–2 + 3a) is green.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 18 — LLM Provider Abstraction + MCP Server (Phase 3b)

**Goal:** Build the provider-agnostic LLM client (four models, mock for tests) and the read-only MCP data server — the deterministic infrastructure the chatbot will sit on. No LLM runtime behaviour is exercised in the suite yet.

**Preconditions:** Phase 3a complete (Session 17 gate + full Phase 3a checklist green).

**Spec references:** FR-3B-01..16 (Req §7.7–7.8); Tech Spec §E.1 (`LLMResponse`, `SQLValidationResult`), §E.5 (`src/ai/llm/`), §E.6 (`src/ai/mcp_server/`), §D.1–D.3 (AI Gold tables), §F.2 (`llm_config.yaml`).

**Build this:**
- `src/ai/llm/`: `base.py` (`LLMProvider` Protocol + `complete`), `anthropic_provider.py`, `deepseek_provider.py` (OpenAI-compatible via `openai` SDK + DeepSeek base URL), `mock_provider.py` (deterministic, fixture-keyed, zero network), `client.py` (`load_llm_config`, `available_models`, `complete` dispatch). Non-streaming. API keys from env vars only; missing-key models grey out with reason (FR-3B-04). No provider SDK imported outside this package (FR-3B-01).
- `src/ai/mcp_server/server.py`: FastMCP, stdio only, no network binding (FR-3B-12). The five tools (§E.6): `query_ae_results`, `query_tev_results`, `list_available_dimensions`, `get_study_run_summary`, `get_tev_run_summary`. All `query_*` route through `execute_safe_select` so gates 1–5 run server-side regardless of caller (FR-3B-10); structured error objects, never stack traces (FR-3B-15). No write-capable connection (FR-3B-11). Tool-schema version constant (FR-3B-16).
- The three AI Gold tables (§D.1–D.3): `gold_ai_model_registry` already exists from Session 15 — confirm; add `gold_ai_eval_results` and `gold_ai_audit_log`.

**Author this:**
- `config/llm_config.yaml` per §F.2: the four models with display names, the OpenAI-compatible DeepSeek route, env-var names, timeout/retry.
- MockProvider fixtures under `tests/fixtures/llm/` keyed by hash of (messages, model); document the hashing/normalization scheme.

> **STOP — OWNER INPUT (DeepSeek GA pricing, §12.1):** the `price_per_mtok_input/output` fields are `<set at build>`. Before marking this session done, request the confirmed per-model pricing from the owner and fill them. Until provided, leave the placeholders and flag that the cost display (FR-3B-43) and eval cost gate (NFR-L-04) compute approximate/zero figures. Do not invent prices.

**Out of scope:** intent routing, SQL generation, the chatbot pipeline (Session 20); Skills (Session 19); any prompt templates.

**Tests to write:**
- `complete` dispatches to the right provider per model_key; MockProvider returns deterministic `LLMResponse` with token counts.
- Missing API key → model greyed with reason; app still functions (FR-3B-04).
- **Full suite passes with no API keys present** (FR-3B-06 / NFR-T-06).
- MCP server: each `query_*` rejects adversarial SQL **called directly on the server** (bypassing any chatbot) — non-SELECT, off-allowlist, Silver-table, over-cap — proving server-side enforcement (FR-3B-10). Metadata tools return no row data. Server binds no network interface (FR-3B-12).

**Definition of done (subset of §7.12 Phase 3b):**
- [ ] Four models callable via the abstraction; missing-key greying works; app functions normally.
- [ ] Suite passes with no API keys in the environment.
- [ ] MCP server exposes exactly five tools; gates enforced server-side (verified by direct adversarial calls); stdio only.
- [ ] DeepSeek pricing obtained from owner and filled (or placeholders flagged pending).

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 19 — Claude Skills (Phase 3b)

**Goal:** Build the two prompt-artifact Skills (memo, SHAP explanation), each running on any configured model and blocking — never repairing — on a failed numeric-traceability check.

**Preconditions:** Session 18 gate green.

**Spec references:** FR-3B-17..24 (Req §7.9); Tech Spec §E.8 (`src/ai/skills/`); reuses §E.7 `verify_traceability`; consumes §D.6 SHAP-JSON. (Terminology: "Skill" = prompt-artifact pattern, runs on any provider — §E.8 note.)

**Build this:**
- `src/ai/skills/memo.py`: `interpret_ae_and_draft_memo(memo_input, cfg, model_key)` — app-assembled JSON input (FR-3B-17); eight labelled components (FR-3B-18); AI-DRAFT tag + footer; every number traced verbatim to `memo_input` via `verify_traceability`; block-not-repair (FR-3B-19).
- `src/ai/skills/shap_explain.py`: `explain_shap_results(shap_cell_json, feature_to_assumption, cfg, model_key)` — features only in mapped actuarial language, no causal claims/recommendations, numbers traced to the SHAP JSON, block-not-repair (FR-3B-22).
- Wire the two Skill buttons live on the Assumption Comparison page (un-grey them from Session 17): memo also reachable from the Stage-4 governance step (FR-3B-20); SHAP from the waterfall display (FR-3B-23).

**Author this:**
- `config/prompts/skills/memo.md` — the eight-component memo template (purpose/scope, data basis, A/E findings, credibility, proposed change + rationale, TEV impact, limitations, recommendation + sign-off), numbers as named placeholders only.
- `config/prompts/skills/shap_explain.md` — the SHAP narrative template.
- Both versioned; hashes logged (FR-3B-08).

**Out of scope:** chatbot; RAG commentary; eval harness.

**Tests to write (MockProvider only):**
- Memo: tag present; all eight components present; a deliberately corrupted canned response with an untraceable number is **blocked** (FR-3B-19/24).
- SHAP: explanation uses only mapped actuarial terms (no raw feature names); corrupted-number response blocked (FR-3B-22).
- Both Skills run via the abstraction (provider-agnostic).

**Definition of done (subset of §7.12 Phase 3b):**
- [ ] Both Skills produce tagged output; corrupted response blocked by traceability post-check.
- [ ] Invocation points live on the specified pages/steps; exports retain tags.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 20 — Chatbot Core + Guardrails (Phase 3b)

**Goal:** Build the seven-stage chatbot pipeline with all SQL gates and the mandatory numeric-traceability post-check — the heart of the guarded conversational interface.

**Preconditions:** Session 19 gate green.

**Spec references:** FR-3B-25..35, 39–45 (Req §7.10); Tech Spec §E.1 (`ChatTurnResult`, `TraceabilityResult`, etc.), §E.7 (`src/ai/chatbot/`), §F.3 (few-shots + prompts), §F.1 (`chatbot:` config incl. allowlist).

**Build this:**
- `src/ai/chatbot/session.py` (`SessionState`), `pipeline.py` (the stages `classify_intent`, `generate_sql`, `validate_sql`, `execute_via_mcp`, `fill_numeric_slots`, `assemble_response` + orchestrator `handle_turn`), `traceability.py` (`verify_traceability`), `context.py` (`trim_history`; `assemble_rag_context` stub for Session 21).
- Data access exclusively via the MCP client (FR-3B-25); never a direct DB connection. Intent routing logged before any data access (FR-3B-27); OUT_OF_SCOPE → refusal (FR-3B-28/42). Five SQL gates enforced (chatbot pre-check + authoritative server, FR-3B-31); never silently rewrite rejected SQL. Numeric slot-filling with the fixed placeholder grammar (§E.7); mandatory traceability post-check, block-not-repair (FR-3B-34). A/E answers carry exposure + credibility context (FR-3B-35). Multi-turn trimming keeps system prompt (FR-3B-39); max-turns cap (FR-3B-40); budget warn-80%/stop-100% (FR-3B-44); model switchable mid-session (FR-3B-45).

**Author this:**
- The populated `allowlist:` block in `config/ai_config.yaml` (§F.1) — Gold tables → permitted columns, no PII. Finalises the structure the Session-14 loader consumed.
- `config/prompts/routing.md`, `config/prompts/sql_generation.md` (schema card + business glossary inline).
- `config/chatbot_few_shots.yaml` — 20–30 curated Q→SQL pairs, **disjoint from the golden set** (FR-3B-30; the disjointness test lands in Session 22 but author with that constraint in mind).

**Out of scope:** RAG commentary, faithfulness judge, AI Analyst page (Session 21); eval harness (Session 22).

**Tests to write (MockProvider):**
- Intent classification logged before data access; OUT_OF_SCOPE and write/assumption-change requests → templated refusal, logged (FR-3B-42).
- All five gates reject+log correctly; a rejected statement is never rewritten.
- `fill_numeric_slots` parses exactly the §E.7 grammar; unresolved placeholder → blocked.
- A seeded non-traceable number → answer blocked (FR-3B-34).
- Trimming never drops the system prompt; max-turns prompt fires; budget warn/stop fire at thresholds (FR-3B-39/40/44).

**Definition of done (subset of §7.12 Phase 3b):**
- [ ] Routing→gates→execution→slot-fill→post-check pipeline works; refusals fire and log.
- [ ] Numeric post-check blocks a seeded bad number; rejected SQL never rewritten.
- [ ] Multi-turn + budget controls behave per spec.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 21 — RAG Commentary + Audit + AI Analyst Page (Phase 3b)

**Goal:** Add grounded commentary generation, the optional faithfulness judge, full per-turn audit logging, and the Streamlit AI Analyst page.

**Preconditions:** Session 20 gate green.

**Spec references:** FR-3B-36..38, 46–47 (Req §7.10–7.11); Tech Spec §E.7 (`assemble_rag_context`, audit write), §D.3 (`gold_ai_audit_log`), §F.1 (`chatbot:` faithfulness keys).

**Build this:**
- `assemble_rag_context` (FR-3B-36): grounding = the tool's own generated artifacts for the run(s) — A/E report, TEV impact report, methodology docs; not an external KB. Commentary numbers subject to the same slot-fill + traceability regime (FR-3B-37); persistent "AI-drafted — pending actuary review" banner surviving export (FR-3B-38).
- Optional faithfulness judge (FR-3B-46): off by default; when on, 1–5 score via `faithfulness_judge.md`, drafts at/below `faithfulness_flag_threshold` rendered with a review warning; flag-not-block; score logged.
- Audit logging to `gold_ai_audit_log` (§D.3, hashes-plus-dynamic-parts per the FR-3B-41 reconciliation): every turn writes the full field set (FR-3B-47); queryable from the Study Run Log page (NFR-A-07).
- AI Analyst Streamlit page (FR-3B-43): provider/model dropdown (configured models only), running token + cost display, conversation export to Markdown with banners.

**Author this:**
- `config/prompts/commentary.md`, `config/prompts/faithfulness_judge.md` (1–5 rubric). Versioned; hashes logged.
- A UAT test-script section for the AI Analyst page (provider switching, budget warn/stop, refusals, export, AI-draft banner). Accumulate into the Phase 3 UAT script.

**Out of scope:** eval harness + golden/adversarial sets (Session 22).

**Tests to write (MockProvider):**
- Commentary grounded only in the tool's own artifacts; banner present and survives export (FR-3B-38).
- Faithfulness judge off by default; when enabled, low score flags (not blocks) and is logged (FR-3B-46).
- Audit row written per turn with the full field set; reconstructable per the §D.3 deterministic-reconstruction note (FR-3B-47/41).
- Model switch mid-session logged; subsequent calls use the new model (FR-3B-45).

**Definition of done (subset of §7.12 Phase 3b):**
- [ ] Commentary grounded + banner-tagged; faithfulness option behaves per spec.
- [ ] Per-turn audit logging complete and queryable.
- [ ] AI Analyst page: dropdown, cost display, export with banners.
- [ ] UAT script section for the page produced.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present.

---

## Session 22 — Evaluation Harness + Phase 3 UAT (Phase 3b close)

**Goal:** Build the CLI eval harness with the hard gates, draft and lock the golden/adversarial sets, and run end-to-end Phase 3 UAT — closing Phase 3.

**Preconditions:** Session 21 gate green.

**Spec references:** FR-3B-48..57 (Req §7.11); Tech Spec §E.9 (`src/ai/eval/`), §F.5 (eval-set formats); §7.12 Phase 3b checklist.

**Build this:**
- `src/ai/eval/runner.py` (`run_eval`, `EvalMetrics`), `result_match.py` (`results_match` — the §F.5/FR-3B-51 rule: column-set, row-count, sorted-multiset, 1e-6 tol, NULL handling, honoring `value_check`), `__main__.py` (CLI: `--models`, `--smoke`; cost estimate up front; interactive confirm above `eval_cost_confirm_threshold`; non-zero exit if a hard gate fails). Never importable into pytest (FR-3B-53). Results persist to `gold_ai_eval_results` with prompt-template + tool-schema hashes (FR-3B-52).
- Hard gates: gate integrity = 100%, numeric traceability = 100% (FR-3B-51). Reported: execution accuracy (≥80% on ≥1 model), routing accuracy (≥90%), refusal correctness.

**Author this:**
- `tests/eval/golden_set.yaml` — 30–50 Q→SQL pairs across all five products and the five query classes (§F.5 format: id, question, intent, sql, expected_result + value_check).
- `tests/eval/adversarial_set.yaml` — 10–15 prompts (injection, write/DDL, off-allowlist, PII, out-of-scope, assumption-change), each with `expect: gate_reject|refusal`.
- The few-shot/golden disjointness test (FR-3B-30/49).
- The complete Phase 3 UAT script (assembling the Session-17 and Session-21 page sections + the harness run), in your established Streamlit test-script format with pre-flight, per-page tables, numeric targets, cross-page consistency, and a sign-off table.

> **STOP — OWNER INPUT (golden-set lock, §12.2):** after drafting `golden_set.yaml` and `adversarial_set.yaml`, request owner review and **lock** before running the evaluation baseline. Do not treat a self-drafted set as the locked baseline.

**Out of scope:** nothing further — this closes Phase 3.

**Tests to write:**
- `results_match` honors all five clauses with `value_check: true`, and columns+row-count only with `false`.
- Few-shot/golden disjointness passes (FR-3B-30/49).
- Harness refuses to run inside pytest (FR-3B-53); cost-confirm prompt fires above threshold.

**Definition of done — Phase 3 complete (full §7.12 Phase 3b checklist):**
- [ ] Golden + adversarial sets drafted **and owner-locked**.
- [ ] Eval run on ≥2 models (one Anthropic, one DeepSeek): gate integrity 100%, numeric traceability 100%, execution ≥80% on ≥1 model, routing ≥90%; results persisted.
- [ ] Harness barred from pytest; cost confirmation fires.
- [ ] Phase 3 UAT script executed end-to-end incl. ≥3 manual adversarial prompts with retained evidence; sign-off recorded.
- [ ] All Phase 1–2 and Phase 3a tests still pass.

**Regression gate:** `pytest tests/ -v --tb=short` green, no API keys present. (Live eval runs are a separate, manually-triggered, owner-confirmed step — not part of this gate.)

---

*End of Phase 3 Claude Code Session Prompts (Sessions 14–22).*
