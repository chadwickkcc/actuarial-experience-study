# AI-Powered Actuarial Experience Study Tool — Locked Requirements Specification

**Version:** 4.0 — Locked  
**Audience:** Claude Code (primary build agent) and project owner (approval)  
**Date:** June 2026  
**Status:** Phases 1–3 APPROVED FOR BUILD (built, UAT-closed 2026-06-28); Phase 4 LOCKED — APPROVED FOR BUILD (reader-tested, QA cross-checked, owner-signed-off 2026-06-28)  
**Last updated:** 2026-06-28 (Phase 4 Governance fully specified — §8 replaced; §10.8 added; prior update 2026-06-27 AI Analyst rounds 4–6)  
**Change from v3.0.1 → v4.0 (Phase 4 Governance added — LOCKED 2026-06-28):** Section 8 replaced in full — Phase 4 is now fully specified (previously outline only) as a single-org **Governance** layer over the completed A/E, TEV, and AI modules. New content: a real identity foundation (a `users` registry + four roles + a minimal username/password gate, replacing the free-text actor IDs `proposer_id`/`reviewer_id`/`actuary_id`); server-side RBAC with an **absolute proposer ≠ approver** segregation-of-duties rule (generalising FR-2-43); a **configurable** multi-level sign-off chain (default junior_actuary → senior_actuary → chief_actuary) that replaces the single-reviewer Stage-4 sign-off of the Phase-2 four-stage workflow and is **extended to A/E study-run approval** ("fit for assumption-setting"); assumption-set **version lineage** with supersession and `effective_from`/`effective_to` dating; a **unified audit read layer** over the three existing per-module logs (no physical canonical-log migration — the lighter build); **hash-chained** tamper-evidence extending the Phase-3 AI-audit hashing; a governance dashboard and an exportable **compliance pack**; and a near-zero-cost multi-tenancy **readiness** lens. New requirement series **FR-4-01–27**; new NFR block §10.8 (**NFR-G-01–08**); new configuration surface `config/governance_config.yaml` and a `users` store. Governing principle unchanged (the actuary decides; Phase 4 records *who* decided, *with what authority*, *on what basis*, immutably), with a new overarching constraint: **prototype simplicity first**. **Multi-tenancy is explicitly NOT built** (deferred to a documented Phase 5 outline; readiness preserved by FR-4-26/27). Also excluded by decision: notifications, time-based escalation/overdue chasing, SSO/password-reset/account-management, and full physical canonical-log unification. §0 overview and §2 phase map updated; §11.3 retained (no new Skills/MCPs); §12 reset (Phase 3 items resolved at build close; all six Phase-4 scope questions resolved 2026-06-28; the reader-test materiality correction in FR-4-16 was confirmed by the owner 2026-06-28). Anchored on `phase4_locked_scope.md`. No change to any Phase 1–3 requirement.  
**Change from v3.0.1 (AI Analyst transcript-evaluation fixes — round 6, 2026-06-27 — in-place, no version bump; filename retained):** Owner-authorised §7.10 fixes after an evaluation of four live AI Analyst transcripts. **All reported figures were verified correct against the live Gold run**; the defects were two wrongful refusals, one incorrect *appended* statistic, and raw/incomplete outputs (see `docs/phase3_build_progress.md` → "Post-UAT hardening (round 6)"). **What:** (1) **FR-3B-35 / FR-1A-24** — the statistical-context line appended to a single-row A/E answer now **recomputes credibility Z from the aggregate actual-claim count** rather than reading a stored per-cell `credibility_z*` (which on an aggregate query is an arbitrary detail cell — the "0.0015 instead of 0.3881" defect); the figure stays data-traceable. (Live re-test follow-up: `sql_generation.md` → v1.4 also forbids the model selecting/averaging a per-cell `credibility_z*` for a roll-up — the residual case where the model wrote `AVG(credibility_z_lapse) ≈ 0.0015` in its own prose while the system-appended line correctly read 0.3881 — now also caught by a **deterministic backstop** that blocks/skips any generated SQL aggregating a per-cell `credibility_z*`/`se_ae*` column, independent of model compliance.) (2) **FR-3B-27 (routing)** — superlative / ranking / "most credible / thinnest" / "largest PVFP profit-source margin" and multi-part status questions are **EXPLORATORY data questions, not OUT_OF_SCOPE**; a **bounded one-shot re-route retry** prevents an unparseable/token-capped routing reply from silently defaulting to a refusal (the two wrongful refusals), and `chatbot.max_tokens.routing` is raised 1024 → 2048. New few-shots cover cross-product credibility ranking and PVFP profit-source margins. (3) **FR-3B-33 (proposed-factor output)** — `sql_generation.md` instructs the model to surface `credibility_z` and caveat degenerate sparse-cell proposed factors (near-zero credibility with an exploding CI), rather than dumping `1e44`-wide intervals uncaveated. (4) The EXPLORATORY multi-query synthesis planner can now reach `gold_inforce_reconciliation` and `gold_dq_run_summary`, so a "did reconciliation pass **and** were there DQ issues?" question answers both halves. No change to the SQL gates (FR-3B-31), the MCP-only data path (FR-3B-25), the numeric-traceability default (FR-3B-34), the audit schema, or the locked eval golden/adversarial sets; fully tested (offline suite **1163 passed, 6 skipped** — +14 targeted tests).  
**Change from v3.0.1 (AI Analyst output formatting — round 5, 2026-06-27 — in-place, no version bump; filename retained):** Owner-authorised AI Analyst **output-formatting** fixes after an evaluation of four live transcripts — all reported figures were verified correct against the live Gold run; the issues were answer formatting and a few unanswerable cases (see `docs/phase3_build_progress.md` → "Post-UAT hardening (round 5)"). **What:** (1) **FR-3B-33 / §7.10.5** — the answer-template grammar gains a `{{table:<col1>,<col2>,...}}` slot that renders a multi-row result as a markdown table programmatically from the result set (numbers stay 100% data-sourced and traceable). This fixes both the "couldn't answer safely" failures on "table"/"by-X" requests and the malformed comma-collapsed tables from misusing `{{list:}}` per column; the model emits a single `{{table:}}` for any table request. (2) **FR-3B-37** — the commentary fact pack now includes the published GLM **proposed factors** (from the round-4 `gold_ai_proposed_factors`; with CI/credibility and a low-credibility flag for degenerate sparse cells), so "commentary on the proposed assumptions" is grounded and traces; numbers still block by default. (3) **FR-3B-42 / §7.10.8** — non-security blocks (slot-fill / no-evidence / commentary-generation) return an actionable hint; SQL-gate (FR-3B-31) and numeric-traceability (FR-3B-34) blocks keep the generic safe message. (4) Minor hardening: a non-blocking `run_scope` audit event, the A/E memo "principal drivers" now excludes zero-credibility bands, and SHAP narrative wording is directional. No change to the SQL gates (FR-3B-31), the MCP-only data path (FR-3B-25), the numeric-traceability default (FR-3B-34), the audit schema, or the locked eval golden/adversarial sets; fully tested (offline suite **1149 passed, 6 skipped** — +10 targeted tests).  
**Change from v3.0.1 (AI Analyst data-surface widening — round 4, 2026-06-27 — in-place, no version bump; filename retained):** Owner-authorised "make the AI Analyst smarter / know all the data" build (the *governed-maximum frontier*: raw-chat-like reasoning and breadth without breaching the governance spine — no PII to an LLM, no unflagged invented numbers, no raw DB/SQL, no writes). **What:** (1) **FR-3B-13/32 (allowlist) + FR-3B-09 (tool count)** — the shared Gold allowlist is widened to additional **PII-free** results/summary/governance tables (`gold_inforce_reconciliation`, `gold_dq_run_summary`, `gold_model_points`, `gold_ai_model_registry`, `gold_assumption_sets`, `gold_ai_proposed_factors`) plus omitted A/E (amount-basis, SE/CI bounds, credibility-weighted, anti-selection) and TEV profit-source-margin columns; a single generic gated **`query_results(table, sql)`** MCP tool (server-side single-table-scoped) serves them (tool count five → six; `TOOL_SCHEMA_VERSION` → "2.0"). **No PII column or table is reachable** — no `gold_dq_quarantine`/`gold_exposure_segments` (policy_id), no Silver/Bronze, no author/reviewer person ids — enforced by a PII-reachability guard test (the bright line vs. raw access). (2) **FR-3A-09 (write contract)** — a *fourth* permitted AI Gold write target, `gold_ai_proposed_factors`, materialises the published GLM/GBM proposed-factor cells (otherwise only in pickles) so the analyst can answer "what are the proposed Term mortality assumptions by age band?". (3) **§7.10.3 grounding** — a compact, app-assembled, display-rounded **study digest** is injected into the routing/SQL-gen/synthesis prompts on **every** turn (not just commentary) and joined to the numeric-traceability allowed-set, so the model always knows the whole study's shape (grounding in the tool's own artifacts, FR-3B-36 spirit; numbers stay 100% data-sourced). (4) **FR-3B-27 (routing)** — *reading* a proposed/expected/assumed/approved value is a data question; only *changing/approving* is OUT_OF_SCOPE. (5) **FR-3B-34 default** — Analyst mode is defaulted **ON on the AI Analyst page only**; the **global** `analyst_mode_default` stays **OFF** so the eval harness keeps its 100% numeric-traceability hard gate. No change to the SQL gates (FR-3B-31), the MCP-only data path (FR-3B-25), the audit schema, or the locked eval golden/adversarial sets; fully tested (offline suite **1139 passed, 6 skipped** — +25 targeted tests across `test_chatbot_digest`/`test_data_surface`/`test_proposals_integration`, incl. the PII-reachability guard and the digest-does-not-defeat-traceability safety lock). Detail: `docs/phase3_build_progress.md` → "Post-UAT hardening (round 4)".  
**Change from v3.0.1 (AI Analyst owner-UAT amendment, 2026-06-27 — in-place, no version bump; filename retained so existing `experience_study_requirements_spec_v3_0_1.md` cross-references stay valid):** Three §7.10 chatbot changes from the owner's hands-on UAT of the **AI Analyst**, authorised as a formal in-place amendment. **Why:** as shipped, commentary kept failing (Claude returned "couldn't answer safely"; DeepSeek went silent) and answers felt terse — the owner asked for reliable commentary, an opt-in way to reason beyond the fetched numbers, and richer multi-query breakdowns. **What:** (1) **FR-3B-34** — the numeric post-check remains the hard default, but a new **opt-in, default-OFF "Analyst mode"** turns it into flag-not-block (a visible "unverified figures" warning); the five SQL gates (FR-3B-31) and the MCP-only data path (FR-3B-25) never relax. (2) **FR-3B-37** — commentary moved from single-SQL slot-fill to **generate-then-verify over an app-assembled fact pack** (the §7.9 memo-Skill pattern): numbers stay 100% data-sourced and still block by default, but the model writes prose rather than slot templates. (3) **FR-3B-33 / §7.10.1** — `exploratory` answers gain an **opt-in, default-OFF multi-query synthesis** path (plan→fetch→synthesise across several gated queries); `factual_lookup` keeps the single-query slot-fill path. New `config/ai_config.yaml` `chatbot` keys (`max_tokens`, `analyst_mode_default`, `multi_query_default`, `max_synthesis_queries`) and two prompt templates (`synthesis_plan.md`, `synthesis_answer.md`; `commentary.md` → v2.0). No change to the SQL gates, MCP data path, audit, or eval contracts; fully tested (offline suite 1114 passed, 6 skipped). Detail: `docs/phase3_build_progress.md` → "Post-UAT hardening (round 2)/(round 3)".  
**Change from v3.0 (reader-test patches, 2026-06-13):** Five clarifications from reader testing, no scope change. FR-3B-39 — multi-turn context window given an explicit default (`conversation_token_window`, 16,000 tokens). FR-3B-46 — LLM-as-judge faithfulness score given a 1–5 scale, configurable flag threshold (default 3), versioned rubric, and flag-not-block semantics. FR-3B-48 — golden-entry schema fully specified (id, question, sql, intent, expected_result with value_check flag). FR-3B-51 — execution-accuracy "result-match rule" defined precisely (column-set, row-count, sorted-multiset equality, 1e-6 numeric tolerance, NULL handling). FR-3B-18 — the eight memo components enumerated inline (no external-document dependency). Two new `ai_config.yaml` keys added to the chatbot excerpt.  
**Change from v2.1:** Section 7 replaced in full — Phase 3 (AI Layer) is now fully specified (previously outline only), split into sub-phases 3a (Sessions 14–17: security hardening per the 2026-05-31 security review, GLM assumption proposals, XGBoost+SHAP, Assumption Comparison UI) and 3b (Sessions 18–22: LLM provider abstraction, MCP server, two Claude Skills, conversational chatbot with mandatory guardrails, evaluation harness and Phase 3 UAT). Phase 4 outline moved to Section 8; former Sections 8–11 renumbered to 9–12 (and pre-existing off-by-one section cross-references corrected). New requirement series FR-3A-01–46 and FR-3B-01–57. Multi-provider LLM support added (Anthropic claude-opus-4-8 / claude-sonnet-4-6 and DeepSeek deepseek-v4-pro / deepseek-v4-flash), all model strings in `config/llm_config.yaml`. New configuration surfaces: `ai_config.yaml`, `llm_config.yaml`, `chatbot_few_shots.yaml`, `config/prompts/`. New NFR blocks: Testability & Resource Management (NFR-T), LLM Cost & Runtime Controls (NFR-L); additions NFR-P-05/06, NFR-CF-10/11, NFR-A-07/08. Former §10.2 (Skills and MCP outline) superseded by §7.8–7.9. Open questions reset: v2.1 items 1–6 resolved; three new items added.  
**Change from v2.1 (2026-05-31, in-place — no version bump):** Section 1.3 corrected (removed non-existent `anw.py`/`projection.py`; data quality is custom validators, not Great Expectations). FR-1C-09 annotated with actual field names. FR-1A-24/25 clarified that credibility Z and CIs are computed from aggregated claim counts. Section 7.1 Phase 3 readiness note added.  
**Change from v2.0:** Section 6.8 reframed from "Goal-Seek Optimiser" to "Credibility Envelope Analysis". The single TEV-maximising optimiser is replaced by a two-run envelope analyser that computes both TEV_min and TEV_max within credibility bounds, plus the percentile of the proposed assumption set within that envelope. The "Adopt optimiser suggestion" UI affordance and any path from envelope output to an assumption set are removed by design. FR-2-27 through FR-2-33, FR-2-38, FR-2-41, FR-2-46 through FR-2-48, the Section 6.11 checklist, NFR-P-04, NFR-C-08, NFR-CF-09, NFR-A-06, and the Phase 3 AI prompt input list are updated to reflect the new design. Related schema fields are renamed (`optimiser_*` → `envelope_*`) and `envelope_tev_min`, `envelope_tev_max`, `proposed_envelope_percentile` are added to the approval record. The `src/tev/optimiser.py` module is renamed to `src/tev/envelope.py`.  
**Change from v1.0:** Phase 2 (TEV Modelling) added in full. Former Phase 2 (AI) renumbered to Phase 3. Former Phase 3 (Governance) renumbered to Phase 4. CI morbidity rider added to policy data model. Technology stack, repository structure, synthetic data spec, NFRs, and Skills/MCP updated accordingly.

---

## 0. Document Overview and How to Use This Spec

This document is the single source of truth for building the AI-powered actuarial experience study prototype. It is structured as follows:

- **Section 1** — Architecture and technology stack
- **Section 2** — Phase map (MVP phases split by product; Phase 3 split into 3a/3b)
- **Sections 3–5** — Detailed requirements for MVP (Phases 1A–1C)
- **Section 6** — Detailed requirements for Phase 2 (TEV Modelling)
- **Section 7** — Detailed requirements for Phase 3 (AI Layer; sub-phases 3a and 3b)
- **Section 8** — Detailed requirements for Phase 4 (Governance)
- **Section 9** — Synthetic (mockup) database specification
- **Section 10** — Non-functional requirements
- **Section 11** — Skills and MCP decisions
- **Section 12** — Open questions for project owner

Claude Code should implement one phase at a time, in order. Each phase section contains a completion checklist that must pass before proceeding to the next phase.

---

## 1. Architecture and Technology Stack

### 1.1 Overall Architecture

The tool is a **Python-first, single-application prototype** with a web-based UI. It follows a simplified three-layer data architecture:

```
[Raw Data Layer]  →  [Canonical Data Layer]  →  [Study Results Layer]
  (Bronze)               (Silver)                    (Gold)
  CSV / parquet          DuckDB tables               DuckDB tables
  per product            canonical schema            A/E output, exposure,
                                                     TEV results
```

For the prototype, DuckDB is used as the embedded analytical database (fast, file-based, no separate server needed, excellent Pandas interoperability). For a production deployment, this would migrate to Databricks / Snowflake / BigQuery, but the schema and calculation logic are portable.

### 1.2 Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Data storage | DuckDB (`.duckdb` file) | Embedded, fast columnar analytics, no server setup |
| Data processing | Python + Pandas + PyArrow | Familiar, rich ecosystem |
| ETL / DQ pipeline | Python scripts + custom rule-based DQ validators | Hand-rolled, per-product deterministic data quality checks |
| Exposure calculation | Python (vectorised Pandas / NumPy) | Matches SOA monograph logic precisely |
| A/E calculation engine | Python | Config-driven, portable |
| Credibility | Python (statsmodels or custom) | Limited Fluctuation + Bühlmann |
| **TEV projection engine** | **Python (vectorised NumPy / Pandas)** | **In-house, grounded in Academy 2009 Practice Note and Frasca/LaSorella SOA 2009** |
| **Life-contingency primitives** | **pyliferisk or lifeActuary** | **Standard actuarial functions (Axn, äxn, qx) for WL reserve calculations** |
| **Credibility envelope analyser** | **scipy.optimize.minimize** | **Min and max TEV within A/E credibility bounds; computes percentile of proposed assumption set within the envelope** |
| Interactive UI | Streamlit | Rapid prototyping, actuarial-friendly |
| Visualisation | Plotly (via Streamlit) | Interactive charts, drill-down |
| Reporting | Jinja2 templates → HTML/PDF | Parameterised multi-audience reports |
| Reference tables | CSV / Parquet (loaded at startup) | Pluggable per product / jurisdiction |
| Phase 3 statistical models | statsmodels (GLM), XGBoost, SHAP | Transparent core + challenge model + explainability (§7.4–7.5) |
| LLM provider abstraction | anthropic SDK; openai SDK against DeepSeek endpoint | Provider-agnostic client; models configurable (§7.7) |
| Phase 3 chatbot / Skills models | claude-opus-4-8, claude-sonnet-4-6, deepseek-v4-pro, deepseek-v4-flash | User-selectable; strings in `llm_config.yaml` only |
| MCP server | Python MCP SDK (FastMCP), stdio transport | Read-only Gold access; no network binding (§7.8) |
| SQL validation | sqlglot | Parse gate for generated SQL (§7.10.4) |
| LLM test mode | Mock provider (fixture-driven) | Regression suite runs with zero API access (§7.7) |
| Version control | Git | Code and configuration |
| Configuration | YAML files per product / tenant | Configuration over customisation |

### 1.3 Repository Structure

```
/experience-study-tool
├── config/                      # YAML configuration files
│   ├── products/                # Per-product config (term, wl, ul, vul, annuity)
│   ├── reference_tables/        # VBT, CSO, lapse benchmarks, CI incidence (CSV/Parquet)
│   ├── study_config.yaml        # Study parameters (dates, exposure method, etc.)
│   ├── tev_config.yaml          # TEV global parameters (RDR, earned rates, RC %, tax)
│   ├── ai_config.yaml           # Phase 3: GLM/GBM/chatbot settings (§7.4, 7.5, 7.10)
│   ├── llm_config.yaml          # Phase 3: providers, models, pricing (§7.7)
│   ├── chatbot_few_shots.yaml   # Phase 3: curated Q→SQL examples (§7.10.3)
│   └── prompts/                 # Phase 3: versioned prompt templates incl. skills/ (§7.7, 7.9)
├── data/
│   ├── raw/                     # Bronze layer: raw synthetic CSVs per product
│   ├── canonical/               # Silver layer: conformed DuckDB tables
│   ├── results/                 # Gold layer: A/E results DuckDB tables
│   ├── model_points/            # Compressed model point files per product
│   ├── tev_results/             # Gold layer: TEV run outputs DuckDB tables
│   └── ai_models/               # Phase 3: serialized models + shap/ JSON artifacts (§7.4.6, 7.5.2)
├── src/
│   ├── ingestion/               # Connector + mapping logic per product
│   ├── data_quality/            # Custom rule-based DQ validators (hand-rolled, per-product checks)
│   ├── etl/                     # Bronze → Silver transformation
│   ├── exposure/                # Seriatim exposure construction
│   ├── calculation/             # A/E engine, credibility, expected values
│   ├── aggregation/             # Results aggregation and OLAP cube
│   ├── tev/                     # Phase 2: TEV engine
│   │   ├── model_points.py      # Stratified grouping / compression
│   │   ├── products/            # Product-specific cash-flow modules
│   │   │   ├── term.py
│   │   │   ├── whole_life.py
│   │   │   ├── ul.py
│   │   │   ├── vul.py
│   │   │   └── annuity.py
│   │   ├── tev_core.py          # TEV projection engine: project_cashflows, compute_anw/pvfp/pvcoc, run_tev
│   │   ├── sensitivities.py     # Standard sensitivity grid runner
│   │   ├── impact_matrix.py     # TEV-impact matrix construction
│   │   ├── envelope.py          # Credibility envelope analyser (TEV_min, TEV_max, percentile)
│   │   ├── workflow.py          # 4-stage assumption approval workflow
│   │   └── assumption_set.py    # Versioned assumption-set artifact
│   ├── reporting/               # Jinja2 report templates (A/E + TEV)
│   ├── ai/                      # Phase 3 AI layer (§7.3)
│   │   ├── glm/                 # GLM fitting, factor derivation, bootstrap (§7.4)
│   │   ├── gbm/                 # XGBoost overlay, SHAP artifact generation (§7.5)
│   │   ├── llm/                 # Provider abstraction + mock provider (§7.7)
│   │   ├── chatbot/             # Intent router, SQL generation, slot-filling, session state (§7.10)
│   │   ├── mcp_server/          # experience_study_data server (§7.8)
│   │   ├── skills/              # Skill invocation wrappers (§7.9)
│   │   └── eval/                # Evaluation harness (§7.11)
│   └── utils/                   # Shared utilities; incl. sql_boundary.py — hardened query execution (§7.2)
├── tests/                       # Unit and integration tests
│   ├── _artifacts/              # ALL test-generated files (gitignored; NFR-T block)
│   └── eval/                    # golden_set.yaml — golden + adversarial eval sets (§7.11)
├── ui/                          # Streamlit application pages
├── synthetic_data/              # Scripts to generate the mockup database
└── docs/                        # This spec and supporting documentation
```

### 1.4 Key Design Principles

1. **Configuration over customisation.** Every product-specific rule lives in a YAML file, not in Python code. Adding a new product means adding a new YAML config and a synthetic data generator, not modifying the calculation engine.
2. **Immutable inputs.** The Bronze layer is append-only. A study run always pins the exact data snapshot it used.
3. **Reproducibility.** Every study run and TEV run is tagged with `(run_id, data_snapshot_hash, config_hash, code_version)`. Re-running the same run ID must return identical results.
4. **Human in the loop.** The tool proposes; the actuary decides. No assumption is changed without an explicit human action recorded in the audit log. The credibility envelope analyser informs; the actuary reviews and decides. There is no path by which the envelope analysis can directly populate or modify an assumption set.
5. **Fail loudly.** Data quality failures at critical checks halt the pipeline and surface a clear error. Non-critical issues quarantine to a side table and log a warning.
6. **Pluggable reference tables.** Mortality tables, lapse benchmarks, and CI incidence tables are loaded from configurable files. Switching from SOA tables (e.g., 2015 VBT) to any other jurisdiction's tables (e.g., Hong Kong IA tables, CMI tables) requires only pointing the YAML config to a different Parquet file — no code change.

---

## 2. Phase Map

### MVP (Phases 1A → 1C): Actuarial Calculations, No AI

The MVP delivers a fully working, end-to-end experience study platform covering all five products. It is split into three sub-phases by product complexity. No AI features are included in the MVP.

| Phase | Products | Key Deliverables |
|---|---|---|
| **1A** | Term Life (Level + PLT) | Full pipeline: ingestion → DQ → exposure → A/E → dashboard |
| **1B** | Whole Life + Universal Life / ULSG | Extend pipeline for cash-value products and shadow accounts |
| **1C** | Variable Universal Life + Deferred Annuities | Extend for separate accounts, fund values, and surrender-charge dynamics |

### Post-MVP

| Phase | Description | Status in This Spec |
|---|---|---|
| **Phase 2** | TEV Modelling: model points, cash-flow projection, ANW, VIF, sensitivity grid, TEV-impact matrix, credibility envelope analyser, 4-stage assumption workflow | **Fully specified in Section 6** |
| **Phase 3a** | AI statistical layer: security hardening, GLM assumption proposals, XGBoost overlay + SHAP, Assumption Comparison UI (Sessions 14–17) | **Fully specified in Section 7** |
| **Phase 3b** | AI language layer: LLM provider abstraction, MCP server, Claude Skills, conversational chatbot, evaluation harness (Sessions 18–22) | **Fully specified in Section 7** |
| **Phase 4** | Governance (single-org): identity + RBAC, version lineage, configurable multi-level approval (extended to A/E), unified audit view + tamper-evidence, governance reporting; multi-tenancy *readiness* only | **Fully specified in Section 8** (Sessions 23–27) |

---

## 3. Phase 1A — Foundation + Term Life Insurance

### 3.1 Scope

Phase 1A delivers the **entire foundational architecture** of the tool plus full experience study functionality for **Term Life Insurance**, including the Post-Level Term (PLT) period and the CI accelerated-benefit rider data model. All subsequent phases build on this foundation without modifying it.

### 3.2 Synthetic Data Required (Phase 1A)

See Section 9 for full mockup database spec. Phase 1A requires only the **Term Life synthetic dataset** (3,200 policies, 8 study years). The synthetic data generator script must be built as part of Phase 1A and must include the CI rider fields defined in Section 9.4.

### 3.3 Functional Requirements — Data Ingestion and ETL

**FR-1A-01**: The system must accept the Term Life synthetic dataset as a CSV file (or folder of CSVs) and load it into the Bronze layer DuckDB table with the following metadata fields appended automatically: `_load_ts`, `_source_file`, `_product_code`, `_row_hash`.

**FR-1A-02**: The system must apply the Term Life YAML connector configuration to map raw field names to canonical field names. The mapping must support: field renaming, type casting (string → date, string → numeric), and code-list translation (e.g., raw termination code "D" → canonical `DEATH_BENEFIT_CLAIM`). The YAML mapping is the only place product-specific field names appear.

**FR-1A-03**: The conformed canonical Silver table for life insurance must contain at minimum the following fields:

```
policy_id, product_code, plan_code, issue_date, issue_age_anb,
date_of_birth, gender, smoker_status, risk_class, face_amount,
premium_mode, annual_premium, status_code, termination_date,
termination_cause_code, study_start_date, study_end_date,
reinsurance_flag, level_period_years, plt_premium_year_1,
plt_structure_code, premium_jump_ratio, distribution_channel,
issue_state, conversion_flag,
ci_rider_flag, ci_rider_sum_assured, ci_rider_premium
```

**FR-1A-04**: The ETL must construct a **policy event timeline** for each policy: a chronologically ordered sequence of events (issue, anniversary, premium-mode change, face change, lapse, death, CI claim, conversion, expiry) materialised as a SCD-Type-2 table with `version_start_date` and `version_end_date` per segment.

### 3.4 Functional Requirements — Data Quality

**FR-1A-05**: The system must implement the following **deterministic data quality checks** for Term Life, executed as custom rule-based validators against the Silver table:

| Check ID | Check Description | Severity |
|---|---|---|
| DQ-TL-01 | `issue_date` ≤ `termination_date` ≤ study end | ERROR — halt |
| DQ-TL-02 | `date_of_birth` + `issue_age_anb` = `issue_date` year ± 1 | WARN |
| DQ-TL-03 | `face_amount` > 0 for all in-force and terminated records | ERROR — halt |
| DQ-TL-04 | `issue_age_anb` between 18 and 85 | ERROR |
| DQ-TL-05 | `termination_cause_code` is null iff `status_code` = 'IF' | ERROR — halt |
| DQ-TL-06 | Death records: `termination_date` ≥ `issue_date` | ERROR — halt |
| DQ-TL-07 | Death records: policy `status_code` = 'IF' at time of death | ERROR |
| DQ-TL-08 | `premium_jump_ratio` ≥ 1.0 for all level-term products | WARN |
| DQ-TL-09 | PLT flag is set for any record with `duration` > `level_period_years` | ERROR |
| DQ-TL-10 | `policy_id` is unique across all records | ERROR — halt |
| DQ-TL-11 | `gender` ∈ {M, F, U} | ERROR |
| DQ-TL-12 | `smoker_status` ∈ {NS, SM, U} | ERROR |
| DQ-TL-13 | `risk_class` ∈ configured valid class list per product YAML | ERROR |
| DQ-TL-14 | In-force reconciliation: BEG_IF + NEW_ISSUES − DECREMENTS = END_IF by count and face amount, per study year. Tolerance: ±0.01% | ERROR — halt |
| DQ-TL-15 | CI rider: `ci_rider_sum_assured` ≤ `face_amount` (CI benefit cannot exceed base sum assured) | ERROR |
| DQ-TL-16 | CI claim records: `illness_code` ∈ configured valid CI illness code list | ERROR |

**FR-1A-06**: The DQ pipeline must produce a structured **DQ report** for each run: overall pass/fail status, a breakdown by check category (validity, consistency, completeness, reconciliation), count of failing records per check, and a sample of up to 10 failing records per check with all fields shown.

**FR-1A-07**: Records that fail non-halting checks must be quarantined to a `dq_quarantine` table (not deleted) and excluded from the exposure calculation. The quarantine table must record: `policy_id`, `check_id`, `check_description`, `field_value`, `quarantine_ts`, `actuary_override_flag` (default false), `override_justification` (free text).

**FR-1A-08**: The UI must present a **Data Quality Dashboard** page showing: DQ score (% records passing all checks), check-by-check pass/fail grid, quarantine record browser with an "Override and include" action that requires a free-text justification.

### 3.5 Functional Requirements — Exposure Calculation

**FR-1A-09**: The system must implement **seriatim exposure construction** following the SOA Experience Study Calculations monograph (2016, revised 2024). Each policy-coverage must produce one or more exposure segment records split at:
- Policy anniversaries (for policy-year studies)
- Birthdays (for attained-age studies)
- Study start and study end dates
- Any change in face amount or coverage status

**FR-1A-10**: The system must implement the **Annual Exposure Method (Balducci)** as the default for mortality studies: deaths receive full-year exposure in the year of death regardless of date; lapses/surrenders receive fractional exposure proportional to time at risk.

**FR-1A-11**: The system must implement the **Distributed Exposure Method (UDD)** as an alternative, selectable via `study_config.yaml`. The rate-error magnitude (difference in A/E between Annual and Distributed methods) must be computed and surfaced as a diagnostic in the results.

**FR-1A-12**: Each seriatim exposure record must contain:

```
policy_id, segment_start_date, segment_end_date, exposure_years,
face_amount_start, face_amount_end, face_amount_weighted_avg,
ci_rider_sum_assured,
attained_age_start, attained_age_end, policy_year, calendar_year,
decrement_flag (0/1), decrement_type, exposure_method,
study_run_id
```

**FR-1A-13**: The exposure engine must handle the **PLT period** explicitly: for policies that reach the end of the level period, a new exposure segment must be generated tagged `is_plt = TRUE` with `plt_duration`, `premium_jump_ratio`, and `plt_structure_code`.

**FR-1A-14**: Exposure must be computed both on a **policy-count basis** and on a **face-amount basis** (net amount at risk). The face-amount basis uses `face_amount_weighted_avg` × exposure years.

**FR-1A-15**: The CI rider exposure must be tracked separately as a **morbidity exposure** field (`ci_exposure_years` = sum of CI-rider-in-force policy years), used in the CI incidence A/E calculation.

### 3.6 Functional Requirements — A/E Calculation Engine

**FR-1A-16**: The system must load the **2015 VBT Select & Ultimate table** (ANB basis, sex-distinct, smoker-distinct) from the reference tables directory as a typed, dimensioned lookup: `(issue_age, duration, gender, smoker_status, risk_class) → q_x`. The table must be loaded from a Parquet file configurable in `study_config.yaml`. Any mortality table conforming to the same key schema can be substituted by changing the config pointer — no code change required.

**FR-1A-17**: The system must support **company-overlay tables** as an optional additional reference basis: a CSV or Parquet file with the same key structure as the VBT, which overrides VBT rates for cells where a company rate is present.

**FR-1A-18**: The system must compute **expected deaths** for each exposure segment by joining to the reference table and computing: `E[D] = exposure_years × q_x_ref` (count basis) and `E[D_amt] = face_amount_weighted_avg × exposure_years × q_x_ref` (amount basis).

**FR-1A-19**: The system must compute **actual deaths** by aggregating death events from the event table, matched to their exposure segments.

**FR-1A-20**: The system must compute **A/E ratios**: `A/E_count = actual_deaths / expected_deaths` and `A/E_amount = actual_death_amount / expected_death_amount`. Both at cell level and any aggregate level.

**FR-1A-21**: The system must compute **lapse A/E** using the same mechanics: expected lapses = `exposure_years × w_t_ref` where `w_t_ref` is the reference lapse rate from a configurable lapse reference table (default: SOA/LIMRA Term/WL 2015-22 study benchmarks).

**FR-1A-22**: For the **PLT shock lapse**, the expected basis must use the SOA 2021 PLT study benchmark rates stratified by `premium_jump_ratio` band and `plt_structure_code`.

**FR-1A-23**: The system must compute **CI incidence A/E** for policies with `ci_rider_flag = TRUE`:
- Expected CI claims = `ci_exposure_years × ci_incidence_rate_ref`, where `ci_incidence_rate_ref` is loaded from a configurable CI incidence reference table keyed by `(gender, attained_age_band, illness_code)`.
- Actual CI claims = count of CI claim events by `illness_code` from the event table.
- CI A/E must be reported by illness code, by attained age band, and in aggregate.

**FR-1A-24**: The system must compute **statistical credibility** using a method selectable in `study_config.yaml` (`credibility_method`): **Limited Fluctuation** (default) or **Bühlmann** (simplified fixed-K form).
- Full credibility threshold / Bühlmann constant K: 1,082 expected claims (for mortality, at 5% error margin, 90% confidence). The single `credibility_threshold` config value serves as the LF full-credibility standard and is reused as the Bühlmann constant K.
- Partial credibility factor:
  - Limited Fluctuation: `Z = min(1, sqrt(actual_claims / 1082))`
  - Bühlmann (simplified fixed-K): `Z = sqrt(actual_claims / (actual_claims + K))`, K = 1082
- Credibility-weighted A/E: `Z × cell_A/E + (1 − Z) × complement_A/E` (identical for both methods)
- The complement basis is selectable in `study_config.yaml`
- Credibility Z and confidence intervals MUST be computed from the AGGREGATED actual claim count of the displayed cell/roll-up — never by averaging per-row Z or CI values.

**FR-1A-25**: The system must compute **Poisson confidence intervals** on A/E ratios:
- Standard error: `SE(A/E) = A/E / sqrt(actual_claims)`
- 95% CI: `A/E ± 1.96 × SE(A/E)`
- Both count and amount basis CIs must be shown on all A/E outputs.
- Credibility Z and confidence intervals MUST be computed from the AGGREGATED actual claim count of the displayed cell/roll-up — never by averaging per-row Z or CI values.

### 3.7 Functional Requirements — Results Aggregation and UI

**FR-1A-26**: The system must produce a **results fact table** in the Gold layer with the following measures per aggregation cell:

```
actual_deaths_count, actual_deaths_amount,
expected_deaths_count, expected_deaths_amount,
exposure_count, exposure_amount,
ae_count, ae_amount,
se_ae_count, se_ae_amount,
ci_lower_count, ci_upper_count, ci_lower_amount, ci_upper_amount,
credibility_z, credibility_weighted_ae_count,
actual_lapses, expected_lapses, ae_lapse,
actual_ci_claims, expected_ci_claims, ae_ci,
ci_claims_by_illness_code (JSON),
study_run_id, assumption_set_id
```

**FR-1A-27**: The system must support aggregation across any combination of the following **canonical dimensions**: `issue_age_band`, `attained_age_band`, `policy_year` / `duration_band`, `gender`, `smoker_status`, `risk_class`, `plan_code` / `product_code`, `face_amount_band`, `calendar_year`, `distribution_channel`, `plt_flag`, `premium_jump_ratio_band`, `illness_code` (for CI).

**FR-1A-28**: The **Streamlit UI** must include the following pages for Phase 1A:
- **Home / Study Setup**: select study period, product scope, exposure method, reference table, credibility method. "Run Study" button triggers the full pipeline.
- **Data Quality Dashboard**: DQ score, check grid, quarantine browser (FR-1A-08).
- **Exposure Summary**: total policy-years by product year and calendar year; in-force reconciliation table.
- **Mortality A/E Explorer**: pivot table with configurable row/column dimensions; heat map of A/E by age × duration; confidence interval bands; toggle between count/amount and reference tables.
- **Lapse A/E Explorer**: same structure; PLT shock-lapse view by premium jump ratio band.
- **CI Incidence Explorer**: A/E by illness code (bar chart), by attained age band, by gender; aggregate CI incidence rate vs expected basis.
- **Study Run Log**: table of all past runs with run ID, timestamp, config hash, data hash, code version, and a "Re-run" button.

**FR-1A-29**: All charts must display confidence intervals as shaded bands. Cells with credibility Z < 0.5 must be visually flagged.

**FR-1A-30**: The UI must support **drill-through**: clicking any aggregate cell opens a panel showing underlying seriatim exposure records (PII fields masked to policy hash, age band, face band).

### 3.8 Functional Requirements — Reporting

**FR-1A-31**: The system must generate a **Working Actuary Report** (HTML via Jinja2) containing: study parameters, DQ summary, in-force reconciliation, full A/E tables by all configured dimensions including CI by illness code, credibility summary, methodology notes, and data quality override log.

**FR-1A-32**: The system must generate a **Chief Actuary Summary** (~2 pages HTML) containing: overall A/E vs prior assumption, key findings, recommended assumption change, and credibility statement.

### 3.9 Phase 1A Completion Checklist

- [ ] Synthetic Term Life dataset (3,200 policies, 8 years, including CI rider fields) generates and loads cleanly
- [ ] All 16 DQ checks pass on clean data; critical checks halt on seeded bad records
- [ ] CI rider field DQ checks (DQ-TL-15, DQ-TL-16) function correctly
- [ ] Seriatim exposure file passes in-force reconciliation to within 0.01%
- [ ] Mortality A/E for clean data falls in range 0.85–1.00 (count) per Section 9.6
- [ ] CI incidence A/E is computed correctly by illness code
- [ ] PLT shock lapse A/E correctly stratified by premium jump ratio band
- [ ] Credibility Z scores correctly computed; low-credibility cells flagged
- [ ] Confidence intervals shown on all A/E charts
- [ ] Both Working Actuary and Chief Actuary reports generate without error
- [ ] All Streamlit pages load and respond to filter changes
- [ ] All unit tests pass; no hardcoded product-specific logic in calculation engine

---

## 4. Phase 1B — Whole Life + Universal Life / ULSG

### 4.1 Scope

Phase 1B extends the existing pipeline to support **Whole Life** and **Universal Life** (including ULSG). The calculation engine, DQ framework, and UI are extended; not replaced.

### 4.2 Synthetic Data Required

Phase 1B requires the **Whole Life** (2,800 policies) and **UL/ULSG** (1,800 policies: 800 Trad UL, 800 ULSG, 200 IUL) synthetic datasets. See Section 9.

### 4.3 Additional Canonical Fields — Whole Life

```
premium_paying_period, guaranteed_cash_value, dividend_option_code,
dividend_on_deposit_balance, paid_up_additions_face, policy_loan_balance,
auto_premium_loan_flag, non_forfeiture_status, participating_flag,
dividend_scale_interest_rate, small_face_flag,
ci_rider_flag, ci_rider_sum_assured, ci_rider_premium
```

### 4.4 Additional Canonical Fields — Universal Life

```
account_value_bom, account_value_eom, specified_amount, death_benefit_option,
current_coi_rate, guaranteed_coi_rate, credited_interest_rate,
guaranteed_min_interest_rate, surrender_charge_remaining, planned_premium,
target_premium, min_no_lapse_premium, seven_pay_premium, mec_status_flag,
is_ulsg_flag, shadow_account_value, shadow_account_funding_ratio,
no_lapse_guarantee_period, secondary_guarantee_type, cumulative_premiums_paid,
cumulative_no_lapse_premium_required, premium_persistency_ratio,
ci_rider_flag, ci_rider_sum_assured, ci_rider_premium
```

### 4.5 Additional Data Quality Checks

**FR-1B-01**: Whole Life DQ checks:

| Check ID | Check | Severity |
|---|---|---|
| DQ-WL-01 | `guaranteed_cash_value` ≥ 0; for active policies ≤ `face_amount` | ERROR |
| DQ-WL-02 | `policy_loan_balance` ≤ `guaranteed_cash_value` | WARN |
| DQ-WL-03 | `non_forfeiture_status` = RPU or ETT implies `termination_cause_code` ≠ LAPSE | ERROR |
| DQ-WL-04 | For par WL: `dividend_on_deposit_balance` ≥ 0 | WARN |

**FR-1B-02**: UL/ULSG DQ checks:

| Check ID | Check | Severity |
|---|---|---|
| DQ-UL-01 | AV roll-forward identity: AV(end) = AV(begin) + premiums − loads − COI + interest ± withdrawals (within 1 currency unit rounding) | WARN |
| DQ-UL-02 | ULSG: `shadow_account_funding_ratio` ≥ 0 | ERROR |
| DQ-UL-03 | ULSG: if funding ratio < 1.0 and policy in force past grace period, flag for review | WARN |
| DQ-UL-04 | `current_coi_rate` ≤ `guaranteed_coi_rate` | ERROR |
| DQ-UL-05 | `credited_interest_rate` ≥ `guaranteed_min_interest_rate` | ERROR |
| DQ-UL-06 | `mec_status_flag` consistent with `seven_pay_premium` and `cumulative_premiums_paid` | WARN |

### 4.6 Additional Calculation Requirements — Whole Life

**FR-1B-03**: WL termination types must be distinguished: **lapse**, **surrender** (with cash value), and **non-forfeiture election** (RPU/ETT). Only lapse and surrender are decrements for the lapse A/E study; non-forfeiture elections are tracked separately.

**FR-1B-04**: Expected lapse basis for WL: SOA/LIMRA 2015-22 Term/WL Lapse/Surrender Study benchmarks.

**FR-1B-05**: A separate **surrender rate A/E** must be computed for WL.

**FR-1B-06**: CI incidence A/E must be computed for WL policies with CI rider using the same framework as FR-1A-23.

### 4.7 Additional Calculation Requirements — Universal Life

**FR-1B-07**: **Premium persistency** must be computed as a first-class metric: `actual_premium_paid / planned_premium` by policy, aggregated by duration band and attained age.

**FR-1B-08**: A simplified **dynamic lapse adjustment** for UL:
```
dynamic_lapse_multiplier = min(2.5, max(0.4, 1 + k × (market_rate − credited_rate)))
```
where `k` is configurable (default 0.5) and `market_rate` comes from the macro scenario time series (Section 9.5).

**FR-1B-09**: For **ULSG**, compute and report a **shadow account coverage ratio A/E**: actual % of policies with funding ratio ≥ 1.0 vs. expected %.

**FR-1B-10**: The **anti-selection mortality flag** for UL: when UL lapse A/E in a cell exceeds a configurable threshold (default: 150%), flag the persisting block's mortality A/E with an anti-selection indicator in subsequent study years.

**FR-1B-11**: CI incidence A/E must be computed for UL policies with CI rider using the same framework as FR-1A-23.

### 4.8 UI Extensions for Phase 1B

**FR-1B-12**: Product selector on A/E Explorer pages (Term / WL / UL / All).

**FR-1B-13**: New **UL Account Value Monitor** page: time-series of average account value by attained-age band overlaid with credited interest rate series.

**FR-1B-14**: New **ULSG Shadow Account Monitor** page: distribution chart of shadow account funding ratios; policies below 1.0 highlighted.

**FR-1B-15**: **Lapse A/E Explorer** premium persistency tab: actual vs expected premium persistency ratio by duration band.

### 4.9 Phase 1B Completion Checklist

- [ ] WL and UL/ULSG synthetic datasets load cleanly
- [ ] All new DQ checks function; ULSG funding-ratio check triggers on seeded failures
- [ ] WL lapse / surrender / non-forfeiture correctly classified
- [ ] UL premium persistency A/E correctly computed
- [ ] Dynamic lapse multiplier correctly modifies expected lapse in rising-rate scenario
- [ ] Anti-selection flag triggers when lapse A/E > threshold
- [ ] CI incidence A/E computed for WL and UL CI riders by illness code
- [ ] ULSG shadow account monitor renders
- [ ] All Phase 1A tests continue to pass

---

## 5. Phase 1C — Variable Universal Life + Deferred Annuities

### 5.1 Scope

Phase 1C extends the pipeline to **Variable Universal Life** (VUL) and **Deferred Annuities** (fixed and variable, accumulation phase).

### 5.2 Synthetic Data Required

Phase 1C requires the **VUL** (800 policies) and **Deferred Annuity** (1,400 contracts: 900 fixed, 500 variable) synthetic datasets. See Section 9.

### 5.3 Additional Canonical Fields — VUL

All UL fields plus:
```
separate_account_total_value, fixed_account_value, sub_account_allocations (JSON),
equity_allocation_pct, fund_value_to_spec_amount_ratio, ma_charge_annual_rate,
withdrawal_active_flag, withdrawal_rate_pct, withdrawal_regime,
ci_rider_flag, ci_rider_sum_assured, ci_rider_premium
```

### 5.4 Additional Canonical Fields — Deferred Annuities

```
contract_id, product_type, premium_type, market_type, account_value,
benefit_base, surrender_charge_schedule (JSON), surrender_charge_remaining,
surrender_charge_year, free_withdrawal_allowance_pct,
guaranteed_min_interest_rate, credited_rate_current, market_value_adjustment_flag,
glwb_elected_flag, gmdb_type, glwb_withdrawal_rate_pct,
glwb_utilization_status, rider_fee_annual_rate, moneyness_ratio,
is_surrender_charge_expired_flag
```

Note: Deferred annuities do not carry CI riders. The CI rider applies only to life insurance products (Term, WL, UL, VUL).

### 5.5 Additional Data Quality Checks

**FR-1C-01**: VUL DQ checks:

| Check ID | Check | Severity |
|---|---|---|
| DQ-VUL-01 | Sub-account allocations sum to 100% (within 0.1%) | ERROR |
| DQ-VUL-02 | `separate_account_total_value` = sum of sub-account values (within rounding) | ERROR |
| DQ-VUL-03 | `separate_account_total_value` ≥ 0 | ERROR — halt |
| DQ-VUL-04 | All fund IDs in sub-account allocations exist in master fund table | WARN |

**FR-1C-02**: Deferred Annuity DQ checks:

| Check ID | Check | Severity |
|---|---|---|
| DQ-DA-01 | Surrender charge rate for current surrender year matches schedule | WARN |
| DQ-DA-02 | `benefit_base` ≥ 0 for all GLB contracts | ERROR |
| DQ-DA-03 | Withdrawal flagged "free" must be ≤ `free_withdrawal_allowance_pct` × `account_value` | WARN |
| DQ-DA-04 | `market_type` consistent across all records for same contract | ERROR |
| DQ-DA-05 | `is_surrender_charge_expired_flag` = TRUE implies `surrender_charge_remaining` = 0 | ERROR |

### 5.6 Additional Calculation Requirements — VUL

**FR-1C-03**: VUL lapse moneyness multiplier: `min(2.0, max(0.5, 1 / fund_value_to_spec_amount_ratio))`.

**FR-1C-04**: Withdrawal persistence state: once `withdrawal_active_flag = TRUE`, withdrawal rate drawn from high-withdrawal distribution for subsequent years.

**FR-1C-05**: VUL mortality A/E computed separately for `withdrawal_active_flag = FALSE` vs `TRUE`.

**FR-1C-06**: CI incidence A/E computed for VUL policies with CI rider using the same framework as FR-1A-23.

### 5.7 Additional Calculation Requirements — Deferred Annuities

**FR-1C-07**: Full surrender and partial withdrawal treated as distinct decrements with separate A/E.

**FR-1C-08**: Expected surrender basis: SOA/LIMRA 2015-22 FRDA Surrender Study benchmarks.

**FR-1C-09**: **Surrender-charge-expiry shock flag**: contracts in final surrender-charge year tagged `approaching_expiry = TRUE` (implemented as the `is_surrender_charge_expired_flag` column; A/E segmentation reuses `is_plt_flag`); shock-lapse A/E computed separately.

**FR-1C-10**: Dynamic lapse multiplier for annuities:
```
annuity_dynamic_multiplier = min(3.0, max(0.3, 1 + k_annuity × (market_rate − credited_rate)))
```
where `k_annuity` is configurable (default 0.8).

**FR-1C-11**: GLB moneyness suppression: `min(1.0, 0.4 + 0.6 × moneyness_ratio)` for contracts with `glwb_elected_flag = TRUE`.

**FR-1C-12**: Annuity owner mortality uses **2012 IAR table with Scale G2 improvement** (not 2015 VBT).

### 5.8 UI Extensions for Phase 1C

**FR-1C-13**: New **Annuity Surrender Explorer** page: A/E by contract year, product type, market type; shock-lapse panel; dynamic-lapse diagnostic.

**FR-1C-14**: New **GLB Utilisation Monitor** page: moneyness ratio distribution; GLWB utilisation rate by attained age and duration.

**FR-1C-15**: VUL page: fund-value distribution by equity allocation band; `fund_value_to_spec_amount_ratio` time series.

**FR-1C-16**: **Product Comparison** page: aggregate A/E by product across all five products on a single chart.

**FR-1C-17**: **CI Incidence Summary** page: aggregate CI A/E across all products with CI riders; breakdown by illness code; heat map by attained age and illness type.

### 5.9 Phase 1C Completion Checklist — MVP Complete

- [ ] VUL and Deferred Annuity synthetic datasets load cleanly
- [ ] Surrender-charge-expiry shock lapse correctly identified and reported separately
- [ ] Dynamic lapse multipliers respond to rising-rate macro regime in years 6-8
- [ ] GLB moneyness suppression reduces expected surrenders correctly
- [ ] VUL withdrawal persistence state variable correctly implemented
- [ ] 2012 IAR used for annuity owner mortality (not 2015 VBT)
- [ ] CI A/E across all four life products by illness code correctly aggregated on CI Summary page
- [ ] All five products show A/E ratios within expected ranges (Section 9.6)
- [ ] Full Working Actuary and Chief Actuary reports generate for multi-product studies
- [ ] All previous phase tests continue to pass
- [ ] End-to-end study run (all five products, 8 years) completes in under 60 seconds

---

## 6. Phase 2 — TEV Modelling

### 6.1 Scope and Purpose

Phase 2 adds a **simplified but mechanically correct Traditional Embedded Value (TEV) module** to the tool. Its purpose is to allow actuaries to test the financial impact of proposed decrement assumptions — derived from the Phase 1 experience study — before formally approving them. It is explicitly a deterministic, single-scenario, internal-management TEV (not a full EEV or MCEV production model). Simplifications are intentional and documented.

The module implements the canonical identity `TEV = ANW + VIF` where `VIF = PVFP − PVCoC`, as defined in the American Academy of Actuaries' 2009 EV Practice Note and the Frasca/LaSorella SOA 2009 paper.

### 6.2 The Assumption Set Artifact

The central integration point between the A/E module (Phase 1) and the TEV module (Phase 2) is a **versioned assumption set artifact** stored as a YAML file. Every TEV run consumes exactly one assumption set; every assumption set traces to an experience study run.

**FR-2-01**: The system must implement an `AssumptionSet` class serialisable to YAML with the following structure:

```yaml
assumption_set:
  id: <uuid>
  version: 1
  status: PROPOSED | APPROVED | SUPERSEDED
  effective_date: YYYY-MM-DD
  author: <actuary_id>
  basis: best-estimate          # no PADs, per Academy 2009 Q28
  source_experience_study_run: <run_id>
  created_ts: <timestamp>
  approved_by: null             # populated at Stage 4
  approved_ts: null

  mortality:
    table_base: 2015_VBT_ANB    # configurable — any table in /reference_tables/
    improvement_scale: G2       # configurable
    multipliers:                # credibility-weighted A/E from experience study
      - product: Term
        gender: M
        risk_class: PNT
        duration_band: [1, 10]
        multiplier: 0.92
        credibility_z: 0.87
        credibility_lower: 0.88
        credibility_upper: 0.96
      # ... additional cells

  ci_incidence:
    table_base: CI_incidence_reference   # configurable
    multipliers:                          # from CI A/E study
      - illness_code: CANCER
        gender: M
        age_band: [45, 54]
        multiplier: 1.05
        credibility_z: 0.72
        credibility_lower: 0.98
        credibility_upper: 1.12
      # ... per illness code

  lapse:
    base_table: SOA_LIMRA_2022   # configurable
    shock_lapse_plt:
      jump_band_lt_2x: 0.30
      jump_band_2x_5x: 0.55
      jump_band_5x_8x: 0.70
      jump_band_gt_8x: 0.88
    multipliers: [...]           # from lapse A/E

  surrender:
    base_table: SOA_LIMRA_FRDA_2022
    multipliers: [...]

  premium_persistency:           # UL / VUL
    by_duration: [...]

  expenses:
    acquisition_per_policy: 350
    maintenance_per_policy: 72
    maintenance_pct_premium: 0.020
    expense_inflation: 0.025

  economic:
    rdr: 0.090                   # configurable, default 9.0%
    earned_rate_ga: 0.050        # general account
    earned_rate_sa: 0.060        # separate account (VUL / VA)
    tax_rate: 0.21
    rc_pct_reserve:              # required capital proxy per product
      Term: 0.030
      WL: 0.045
      UL: 0.060
      ULSG: 0.080
      VUL: 0.035
      DA: 0.045
```

**FR-2-02**: The assumption set must record the `credibility_lower` and `credibility_upper` bounds for each decrement multiplier. These bounds are the 95% confidence interval from the A/E study and serve as the **constraint box for the credibility envelope analyser**.

**FR-2-03**: When a new assumption set is created from an experience study run, the system must pre-populate all multiplier cells with the credibility-weighted A/E ratios from that run. The actuary may then edit any cell before saving.

**FR-2-04**: Every saved assumption set must be assigned a unique ID and stored in an append-only `assumption_sets` DuckDB table. Assumption sets cannot be deleted; they can only be superseded.

### 6.3 Model Point Compression

**FR-2-05**: Before the TEV projection, the system must compress the seriatim in-force population into model points using **stratified grouping**. For each product, the grouping dimensions are:

| Product | Grouping Dimensions |
|---|---|
| Term Life | `plan_code`, `gender`, `smoker_status`, `risk_class`, `issue_age_band` (5-yr), `duration_band`, `level_period_years`, `plt_flag` |
| Whole Life | `plan_code`, `gender`, `smoker_status`, `risk_class`, `issue_age_band`, `duration_band`, `premium_paying_period`, `participating_flag` |
| UL / ULSG | `plan_code`, `gender`, `risk_class`, `issue_age_band`, `duration_band`, `is_ulsg_flag`, `av_band` (quintile of account value) |
| VUL | `plan_code`, `gender`, `risk_class`, `issue_age_band`, `duration_band`, `equity_allocation_band` (0-25/25-50/50-75/75-100%) |
| Deferred Annuity | `product_type`, `gender`, `market_type`, `issue_age_band`, `surrender_charge_year_band`, `glwb_elected_flag` |

**FR-2-06**: Within each model point cell, the system must compute the following **aggregated representative values**:
- `policy_count`: sum of policies in cell
- `face_amount_total`: sum of face amounts
- `reserve_total`: sum of statutory reserves (see FR-2-14 for reserve calculation)
- `account_value_total`: sum of account values (UL/VUL/DA)
- `premium_total`: sum of annual premiums
- `wtd_avg_attained_age`: credibility-weighted average attained age
- `wtd_avg_duration`: weighted average policy duration
- `ci_rider_count`: count of policies with CI rider in cell
- `ci_rider_sum_assured_total`: sum of CI rider sum assureds in cell

**FR-2-07**: The model point file must be persisted as a Parquet file per product per TEV run ID, allowing exact reproduction of any prior run.

**FR-2-08**: The model point compression must produce a **reconciliation table** comparing: total in-force count, total face amount, and total reserve pre- and post-compression. The difference must be < 0.1% on all three metrics. If not, compression halts with an ERROR.

**FR-2-09**: Target model point counts: 300–600 per product (approximately 10,000 policies → ~500 model points is a typical 20:1 compression). The actual count depends on the granularity of grouping dimensions and the in-force distribution.

### 6.4 Synthetic ANW Construction

**FR-2-10**: The system must construct a simplified **Adjusted Net Worth** from the in-force data and configurable balance sheet parameters, without requiring an external balance sheet feed. The construction formula is:

```
Statutory_Surplus = Total_Assets − Total_Statutory_Liabilities
ANW = Statutory_Surplus
    + Asset_Valuation_Reserve          # configurable input, default: 0.5% of total assets
    − Non_Admitted_Assets              # configurable input, default: 0
    × (1 − tax_rate)                   # net of tax
```

**FR-2-11**: `Total_Statutory_Liabilities` must be computed as the sum of product reserves across all model points (from FR-2-14) plus a configurable additional liability loading (default: 0).

**FR-2-12**: `Total_Assets` = `Total_Statutory_Liabilities` + `Statutory_Surplus`, where `Statutory_Surplus` is a configurable input (the actuary enters the company's actual or illustrative surplus figure).

**FR-2-13**: ANW must be split by product line pro-rata to required capital:
```
ANW_product_p = ANW_total × (RC_product_p / RC_total)
where RC_product_p = rc_pct_reserve[p] × reserve_total_p
```

**FR-2-14**: **Simplified statutory reserves** per product must be computed using the following approximations (these are prototype-grade; configurable table-based overrides are preferred when available):

| Product | Reserve Approximation |
|---|---|
| Term Life | Net level premium reserve: loaded from a pre-computed reserve-per-unit table keyed by `(issue_age, gender, policy_year, plan_code)`; or use Commissioner's Reserve Valuation Method approximation `(CSV_t + k × NAR_t)` where CSV and k are configurable |
| Whole Life | Net level premium reserve from pre-computed table by `(attained_age, gender, plan_code)`; or `WL_reserve_pct × face_amount` by duration band |
| UL / ULSG | `max(account_value, AG38_formula_proxy)` where `AG38_formula_proxy = min_no_lapse_premium × remaining_guarantee_years × 0.85` (configurable multiplier) |
| VUL | `max(0.035 × specified_amount, cash_surrender_value)` |
| Deferred Annuity | `account_value × carvm_loading` where `carvm_loading` is configurable (default 1.0 for FA, 1.02 for VA) |

### 6.5 The TEV Projection Engine

**FR-2-15**: The system must implement a **vectorised deterministic projection engine** that runs across all model points simultaneously using NumPy arrays. No Python loops over individual model points. The projection time step is **annual** (acceptable prototype simplification vs. monthly production norm).

**FR-2-16**: The projection engine must implement a **shared survivorship recursion** across all products:
```
in_force_t = in_force_{t-1} × (1 − q_x_t) × (1 − lapse_t) × (1 − ci_incidence_t × is_accelerated_flag)
```
where `ci_incidence_t × is_accelerated_flag` reduces the in-force count when a CI claim is paid as an accelerated death benefit.

**FR-2-17**: The projection must run for a maximum of `max(remaining_policy_term, 60 years)` per model point, stopping when in-force falls below a configurable threshold (default: 0.001 × initial count).

**FR-2-18**: For each product, the projection engine must call the product-specific **statutory book profit** module to compute `BP_t`. The five product modules must implement:

**Term Life (`src/tev/products/term.py`):**
```
BP_t = Premium_income_t
     − Death_benefit_t       (= face_amount × q_x_t × in_force_t)
     − CI_benefit_t           (= ci_sum_assured × ci_incidence_t × in_force_t, if CI rider)
     − Commission_t           (= commission_rate × Premium_income_t)
     − Maintenance_expense_t  (= maint_per_policy × in_force_t × (1+inflation)^t)
     + Investment_income_t    (= reserve_t × earned_rate_ga)
     − ΔReserve_t             (= reserve_t − reserve_{t-1} × in_force_t / in_force_{t-1})
     − Tax_t                  (= max(0, pre_tax_profit_t × tax_rate))
```

**Whole Life (`src/tev/products/whole_life.py`):**
```
BP_t = Premium_income_t
     − Death_benefit_t
     − CI_benefit_t           (if CI rider)
     − Surrender_benefit_t    (= CSV_t × surrender_rate_t × in_force_t)
     − Dividend_t             (= dividend_rate × reserve_t × in_force_t, par only)
     − Commission_t
     − Maintenance_expense_t
     + Investment_income_t    (= reserve_t × earned_rate_ga)
     − ΔReserve_t
     − Tax_t
```

**UL / ULSG (`src/tev/products/ul.py`):**
```
BP_t = Revenue_t
     where Revenue_t = COI_charges_t + expense_loads_t + surrender_charges_collected_t
     − Benefit_t
     where Benefit_t = death_claims_t + CI_benefit_t + surrender_benefits_t
     + Spread_income_t        (= AV_t × (earned_rate_ga − credited_rate))
     − Maintenance_expense_t
     + Investment_income_on_RC_t
     − ΔReserve_t
     − Tax_t
```

**VUL (`src/tev/products/vul.py`):**
```
BP_t = ME_charge_t            (M&E charge = me_rate × SA_value_t)
     + COI_charges_t
     + Surrender_charges_collected_t
     − Death_claims_t
     − CI_benefit_t           (if CI rider)
     − Surrender_benefits_t
     − Maintenance_expense_t
     + Investment_income_on_GA_RC_t
     − ΔReserve_t
     − Tax_t
Note: Separate account assets earn for the policyholder, not the insurer.
```

**Deferred Annuity (`src/tev/products/annuity.py`):**
```
BP_t = Spread_income_t        (= AV_t × (earned_rate_ga − credited_rate))
     + Rider_fees_t           (= rider_fee_rate × AV_t, where applicable)
     − Surrender_benefits_t
     − GMDB_benefits_t        (= max(0, death_benefit − AV_t) × mortality_rate_annuity_t)
     − Maintenance_expense_t
     + Investment_income_on_RC_t
     − ΔReserve_t
     − Tax_t
```

**FR-2-19**: The system must compute **required capital** `RC_t` at each projection step as:
```
RC_t = rc_pct_reserve[product] × Reserve_t
```
using the configurable percentages from the assumption set.

**FR-2-20**: The system must compute **Present Value of Cost of Capital**:
```
CoC_t = RC_{t-1} × (RDR − earned_rate_ga_after_tax)
where earned_rate_ga_after_tax = earned_rate_ga × (1 − tax_rate)
PVCoC = Σ_t CoC_t × (1 + RDR)^{-t}
```

**FR-2-21**: The system must compute the final TEV identities:
```
PVFP = Σ_t BP_t × (1 + RDR)^{-t}
VIF = PVFP − PVCoC
ANW = computed per FR-2-10 to FR-2-13
TEV = ANW + VIF
```

All components must be stored in the `tev_results` Gold layer table, keyed by `(tev_run_id, assumption_set_id, product_code)`.

### 6.6 Standard Sensitivity Grid

**FR-2-22**: The system must implement a **standard sensitivity runner** that, given a baseline assumption set, automatically generates 10 perturbed variants and runs the full TEV projection on each:

| Sensitivity ID | Description | Shock Applied |
|---|---|---|
| SENS-01 | Lapse −10% | All lapse multipliers × 0.90 |
| SENS-02 | Lapse +10% | All lapse multipliers × 1.10 |
| SENS-03 | Mortality −5% (life) | Mortality multipliers for Term/WL/UL/VUL × 0.95 |
| SENS-04 | Mortality +5% (life) | Mortality multipliers for Term/WL/UL/VUL × 1.05 |
| SENS-05 | Mortality +5% (annuity longevity) | Annuity mortality multiplier × 0.95 (lives longer → worse) |
| SENS-06 | CI incidence −10% | All CI incidence multipliers × 0.90 |
| SENS-07 | CI incidence +10% | All CI incidence multipliers × 1.10 |
| SENS-08 | Maintenance expense −10% | `maintenance_per_policy` and `maintenance_pct_premium` × 0.90 |
| SENS-09 | Maintenance expense +10% | same × 1.10 |
| SENS-10 | RDR +100 bp | `rdr` += 0.010 |
| SENS-11 | RDR −100 bp | `rdr` -= 0.010 |

Each sensitivity run stores a complete result set in `tev_results` tagged with both the `tev_run_id` and the `sensitivity_id`.

**FR-2-23**: ΔTEV for each sensitivity must be computed as `TEV_sensitivity − TEV_baseline` and stored alongside the absolute TEV.

### 6.7 TEV-Impact Matrix

**FR-2-24**: The system must construct a **TEV-impact matrix** from the sensitivity run results. This is a 2D table where:
- **Rows** = products (Term, WL, UL, ULSG, VUL, DA-Fixed, DA-Variable, Total)
- **Columns** = decrement types (Lapse −10%, Lapse +10%, Mortality −5%, Mortality +5%, Annuity Longevity +5%, CI Incidence −10%, CI Incidence +10%, Expense −10%, Expense +10%, RDR +100bp, RDR −100bp)
- **Cells** = ΔTEV for that product due to that sensitivity shock (in currency units)
- **Final column** = "Total sensitivity range" = max(|ΔTEV|) across all shocks for that product

**FR-2-25**: The TEV-impact matrix must be displayed as a **colour-coded heat map** in the UI, where:
- Deep green = large positive ΔTEV (proposed change improves TEV)
- Deep red = large negative ΔTEV (proposed change reduces TEV)
- White/neutral = near-zero impact
- The colour scale is relative within each row (per-product normalisation)

**FR-2-26**: The TEV-impact matrix must be exportable as a CSV and included in the TEV impact report.

### 6.8 Credibility Envelope Analysis

**FR-2-27**: The system must implement a **credibility envelope analyser** that computes the maximum and minimum aggregate TEV reachable within the credibility bounds for the top-5 most TEV-sensitive decrements. The analyser produces a **governance artefact**, not a suggested assumption set. Its purpose is to bound the defensible TEV range and locate the proposed assumption set within that range.

**FR-2-28**: The analyser must operate on the **top-5 most TEV-sensitive decrements**, identified automatically as the five columns in the TEV-impact matrix with the largest `Total sensitivity range`. This limits the analysis to a tractable number of variables while focusing on those with the greatest impact.

**FR-2-29**: The envelope analysis must be formulated as **two constrained optimisation problems** sharing the same constraint box:

```
Problem 1 (TEV_max):
  Maximise:   TEV_total(θ)
  Subject to: θ_i ∈ [credibility_lower_i, credibility_upper_i] for each of the top-5 decrements

Problem 2 (TEV_min):
  Minimise:   TEV_total(θ)
  Subject to: same box constraints

where θ is the vector of decrement multipliers for the top-5 decrements
and TEV_total(θ) is evaluated by running the projection engine on each candidate θ.

The credibility envelope is the interval [TEV_min, TEV_max].
```

**FR-2-30**: Implementation must use `scipy.optimize.minimize` with method `L-BFGS-B` for both runs (the method natively supports box constraints). The TEV_max objective is the negative of TEV_total; the TEV_min objective is TEV_total directly. Both runs use the current proposed assumption set as the starting point.

**FR-2-31**: Each L-BFGS-B run must execute a full TEV projection per function evaluation, against the **model-point-compressed** population only (not seriatim). Maximum function evaluations: 200 per run (configurable). Typical convergence: < 50 evaluations per run. The two runs may execute sequentially or in parallel; if sequential, intermediate state must not be carried over (each run starts from θ_proposed).

**FR-2-32**: The envelope analyser must compute the **percentile of the proposed assumption set within the envelope**:

```
proposed_envelope_percentile = (TEV_proposed − TEV_min) / (TEV_max − TEV_min)
```

reported as a value in [0, 1]. A value of 0.5 means the proposed set sits exactly mid-envelope; 0.95 means the proposed set sits near the maximum defensible TEV; 0.05 means near the minimum. If TEV_max − TEV_min is below a configurable floor (default 0.1% of TEV_proposed), the percentile is undefined and reported as NULL with an explanatory note ("envelope width below materiality threshold"); this condition typically indicates that the credibility intervals are too narrow for the envelope to be informative.

**FR-2-33**: The envelope output must be presented to the actuary as a **read-only governance artefact** containing:
- TEV_min, TEV_max, and the envelope width (TEV_max − TEV_min) in currency units and as a percentage of TEV_proposed
- Percentile of the proposed assumption set within the envelope (or NULL with explanation)
- θ_min and θ_max — the multiplier vectors that produced each envelope endpoint, displayed alongside the proposed multipliers and credibility bounds, for each of the 5 decrements
- A directional reading per decrement: whether θ_proposed sits closer to the favourable bound (for TEV) or the adverse bound, and the distance to each
- Convergence metadata for both runs (n_evaluations, convergence_message)

The system **must not** provide any UI affordance to "adopt", "auto-populate", or otherwise copy θ_min or θ_max into a new assumption set. The envelope informs Stage 2 edits only by the actuary's manual judgement and explicit re-entry of values, with rationale captured per cell.

### 6.9 The Four-Stage Assumption Approval Workflow

**FR-2-34**: The system must implement a **four-stage iterative workflow** as a Streamlit multi-page application. Each stage must be accessible from a workflow progress indicator showing the current stage and status.

---

**Stage 1 — Experience Study (read-only in Phase 2)**

This stage displays the A/E results from the most recent Phase 1 experience study run. It is read-only in Phase 2. The actuary selects a study run as the basis for a new assumption set. The key outputs shown are:
- Credibility-weighted A/E ratios by decrement type and product
- Credibility Z scores and 95% confidence intervals per cell
- Comparison vs prior assumption set (if one exists)
- A "Create Proposed Assumption Set" button that pre-populates Stage 2

---

**Stage 2 — Proposed Assumption Set (edit screen)**

**FR-2-35**: Stage 2 presents an **editable assumption set** pre-populated from the A/E study. The actuary can:
- Edit any decrement multiplier directly in the table (with the credibility bounds shown alongside each cell as guardrails)
- Add or remove product-specific overrides
- Edit economic parameters (RDR, earned rates, RC percentages, tax rate, expense inflation)
- Add a free-text rationale for each override
- Save the assumption set as PROPOSED (creates a new version)
- Load any prior assumption set for comparison or as a starting point

**FR-2-36**: The Stage 2 screen must display a **live preview panel**: as the actuary edits multipliers, a simplified ΔTEV indicator updates in real time (using a pre-computed sensitivity approximation, not a full run) to give immediate directional feedback on the impact of edits.

**FR-2-37**: A "Restore from A/E" button must reset any manually edited cells to the credibility-weighted A/E value from Stage 1.

---

**Stage 3 — TEV Impact Analysis (iterative)**

**FR-2-38**: Stage 3 runs the full TEV engine on the current PROPOSED assumption set and displays:
- **Baseline TEV waterfall**: total TEV = ANW + PVFP − PVCoC, broken down by product and by profit source (mortality margin, lapse margin, CI margin, investment spread, expense margin)
- **ΔTEV vs prior assumption set**: breakdown of the TEV change by product and decrement type
- **Sensitivity grid results**: ΔTEV for each of the 11 standard sensitivities, shown as a tornado chart per product
- **TEV-impact matrix**: as defined in FR-2-24 to FR-2-26
- **"Compute Credibility Envelope" button**: runs the envelope analyser (FR-2-27 to FR-2-33) and displays the envelope readout (TEV_min, TEV_max, envelope width, percentile of proposed, θ vectors at each endpoint with directional readings) in a side panel as a **read-only** artefact

**FR-2-39**: At the bottom of the Stage 3 screen, the actuary must be presented with two explicit choices:
- **"Approve and proceed to Stage 4"** — locks the current assumption set as STAGE-3-APPROVED and moves to Stage 4. Requires a mandatory free-text comment.
- **"Refine assumptions"** — returns to Stage 2 with the current assumption set pre-loaded for editing. The Stage 3 results for this iteration are saved to the run log for reference.

**FR-2-40**: The iterative loop between Stages 2 and 3 can repeat without limit. Each iteration is logged with: iteration number, assumption set version, TEV baseline, ΔTEV vs prior, timestamp, and the actuary's reason for continuing (if not approving).

**FR-2-41**: The envelope analyser side panel is **informational only**. There must be no UI affordance to copy θ_min or θ_max into the assumption set, no "Adopt envelope endpoint" button, no pre-population of any Stage 2 field from the envelope output. If the actuary wishes to move the proposed assumption set toward one envelope endpoint, they must do so manually in Stage 2 with explicit rationale captured per changed multiplier. The envelope analysis run is logged (FR-2-46) for audit but cannot be a direct input to any other workflow step.

---

**Stage 4 — Governance Sign-Off**

**FR-2-42**: Stage 4 is triggered only when the actuary has explicitly approved in Stage 3. It presents the **TEV Impact Report** (see FR-2-48) and captures the governance sign-off.

**FR-2-43**: The governance sign-off must capture: reviewer name (a different actuary from the proposer), reviewer comments, sign-off timestamp, and an explicit "APPROVE" or "RETURN TO STAGE 2" decision.

**FR-2-44**: On APPROVE, the assumption set status transitions from STAGE-3-APPROVED to APPROVED, and the assumption set is locked (immutable). An entry is written to the `assumption_approvals` audit table.

**FR-2-45**: On RETURN TO STAGE 2, the reviewer must provide a mandatory comment, and the workflow returns to Stage 2 with the assumption set in PROPOSED status.

**FR-2-46**: The assumption approval record must include: assumption set ID and version, study run ID it was based on, baseline TEV, ΔTEV vs prior approved set, sensitivity range, proposer, reviewer, all iteration history, envelope analysis flag (whether the envelope analyser was run), envelope endpoints (TEV_min, TEV_max) and the percentile of the proposed assumption set within the envelope (if computed), and all stage-3 comments.

### 6.10 TEV Reporting

**FR-2-47**: The system must generate a **TEV Working Actuary Report** (HTML via Jinja2) containing: model point summary and compression reconciliation, ANW components, PVFP by product and profit source, PVCoC by product, baseline TEV waterfall, ΔTEV vs prior, full sensitivity grid table, TEV-impact matrix, credibility envelope analysis (TEV_min, TEV_max, envelope width, percentile of proposed, θ_min and θ_max vectors with directional readings; if envelope was run), and all assumption overrides with rationale.

**FR-2-48**: The system must generate a **TEV Impact Report** for Stage 4 governance (~5 pages HTML) containing: executive summary of assumption changes and their TEV impact, single-axis sensitivity tornado (one-at-a-time shocks), credibility envelope analysis (TEV_min, TEV_max, percentile of proposed within the envelope; if run), comparison to prior approved assumption set, key risks and uncertainties, and the proposer's recommendation.

**FR-2-49**: The TEV-impact matrix must be included in both reports as a formatted table with colour coding replicated in HTML.

### 6.11 Phase 2 Completion Checklist

- [ ] Assumption set artifact creates correctly from an experience study run; editable fields save/load correctly
- [ ] Model point compression produces reconciliation within 0.1% on count, face amount, and reserve for all five products
- [ ] ANW construction is consistent: sum of product ANW = total ANW
- [ ] TEV projection produces positive PVFP for profitable model-point configurations (basic sanity check)
- [ ] PVFP discounted at RDR matches a manually calculated sample cell to 4 decimal places
- [ ] PVCoC is positive and decreases as RC percentage decreases (directional test)
- [ ] CI accelerated benefit correctly reduces in-force count and generates CI benefit cash outflow
- [ ] All 11 sensitivities run without error; ΔTEV is directionally correct (lower lapse reduces TEV for protection products with positive lapse margins)
- [ ] TEV-impact matrix renders correctly; colour coding reflects magnitude of impact
- [ ] Credibility envelope analyser converges within 200 evaluations per run (both TEV_min and TEV_max); all returned θ vectors are within credibility bounds
- [ ] TEV_min ≤ TEV_proposed ≤ TEV_max holds for every test case (envelope strictly contains the proposed point)
- [ ] proposed_envelope_percentile lies in [0, 1] when envelope width exceeds materiality floor; NULL otherwise
- [ ] Envelope readout is clearly labelled as read-only; no UI affordance exists to copy θ_min or θ_max into an assumption set
- [ ] Stage 2 ↔ Stage 3 iterative loop functions correctly with full iteration logging
- [ ] Stage 4 governance sign-off records correctly; assumption set transitions to APPROVED
- [ ] Both TEV reports generate without error
- [ ] Full baseline + 11 sensitivities runs in < 30 seconds for all five products combined
- [ ] All Phase 1A–1C tests continue to pass

---

## 7. Phase 3 — AI Layer

### 7.1 Scope, Sub-Phase Map, and Gate Criteria

Phase 3 adds three AI capabilities on top of the completed experience study and TEV modules: statistical assumption proposals (GLM, with a GBM/SHAP challenge-and-explain layer), AI-drafted actuarial narratives (two Claude Skills), and a guarded conversational interface (chatbot + MCP server). The governing principle is unchanged from the rest of the tool: **the AI proposes, explains, and audits; the actuary decides.** No AI output reaches an assumption set, report sign-off, or approval record except through an explicit human action with recorded justification.

Phase 3 is delivered in two sub-phases:

| Sub-phase | Sessions | Content | LLM at runtime |
|---|---|---|---|
| **3a** | 14–17 | Security hardening; GLM assumption proposals; XGBoost overlay + SHAP; Assumption Comparison UI | No |
| **3b** | 18–22 | LLM provider abstraction; MCP server; two Claude Skills; chatbot; evaluation harness + Phase 3 UAT | Yes |

**Gate criteria:**
- **Entry to 3a:** Phase 2 UAT signed off (met, 2026-06).
- **3a → 3b:** Phase 3a completion checklist (§7.12) passes and the full regression suite is green.
- **Phase 3 complete:** Phase 3b checklist passes, including the hard eval gates (gate integrity and numeric traceability at 100%), and UAT is signed off.

**Explicitly out of scope for Phase 3** (assessed and excluded during scoping, 2026-06; candidates for future phases): ML anomaly detection for data quality, audience-tiered AI narratives beyond the memo Skill, survival/competing-risks models, macro-covariate forward-looking models, agentic workflow orchestration (revisit after Phase 4 governance exists), and documentation/regulatory-text copilots.

Detailed requirements follow in §7.2–§7.11; completion checklists in §7.12. Sub-phase 3a requirements carry FR-3A-xx identifiers; 3b carries FR-3B-xx.

### 7.2 Security Hardening (Phase 3 Entry Requirements)

Per the 2026-05-31 security review, three items become genuine vulnerabilities the moment untrusted/LLM-driven input exists. They are therefore **entry requirements**: Session 14 implements them, and no AI-layer code may land before they pass.

**FR-3A-01 (S-1 — SQL boundary)**: A single hardened query-execution module through which all dynamically-constructed SQL passes: parameterized execution, table/column allowlist support, read-only connection enforcement, and structured rejection of anything else. The AI layer (chatbot, MCP server, eval harness) must have no other path to the database.

**FR-3A-02 (S-1 enforcement)**: String interpolation (f-strings, `%`, `.format()`, concatenation) into SQL is forbidden in the AI layer. An automated test scans `src/ai/` for these patterns and fails the suite on detection.

**FR-3A-03 (S-2 — template autoescape)**: All Jinja2 environments set `autoescape=True`. Existing A/E and TEV report outputs must render byte-comparably or with reviewed, accepted diffs — verified by regression before Session 14 closes.

**FR-3A-04 (T-3 — dependency pinning)**: A lockfile (pip-tools or uv) pinning the full dependency tree lands **before** the ML/LLM stack (xgboost, shap, anthropic, openai, mcp) is added, then is regenerated to include it. Builds install from the lockfile only.

**FR-3A-05**: Session 14 closes with the full existing regression suite green (all Phase 1–2 tests), demonstrating the hardening introduced no behavioural change.

Items rated "at leisure" in the review (T-1 zero-model-point warning, S-4 path confinement, T-2, T-4) remain tracked in `DEFERRED_FOLLOWUPS.md` and are explicitly **not** Phase 3 requirements.

### 7.3 AI Module Architecture and Data Contracts

**FR-3A-06**: The AI layer lives under `src/ai/` with this structure:

```
src/ai/
├── glm/              # GLM fitting, factor derivation, bootstrap (§7.4)
├── gbm/              # XGBoost overlay, SHAP artifact generation (§7.5)
├── llm/              # Provider abstraction + mock provider (§7.7)
├── chatbot/          # Intent router, SQL generation, slot-filling, session state (§7.10)
├── mcp_server/       # experience_study_data server (§7.8)
├── skills/           # Skill invocation wrappers (§7.9)
└── eval/             # Evaluation harness (§7.11)
```
The hardened SQL boundary (FR-3A-01) lives in `src/utils/` since non-AI code may also adopt it.

**FR-3A-07 (one-way dependency rule)**: `src/ai/` may import from the core engine (`src/calculation/`, `src/tev/`, `src/utils/`); the core engine must never import from `src/ai/`. Phases 1–2 must run identically with the AI layer absent. Enforced by an automated import-graph test.

**FR-3A-08 (read contract)**: The AI layer reads only the Gold layer (A/E fact table, TEV results, run manifests) and version-controlled config/reference files. It never reads Silver or Bronze.

**FR-3A-09 (write contract)**: The AI layer writes only to: `data/ai_models/` (model and SHAP artifacts), and three new Gold tables — `ai_model_registry` (FR-3A-24), `ai_eval_results` (FR-3B-52), `ai_audit_log` (chatbot and MCP logging, FR-3B-14/47). It never writes to assumption sets, study results, or any Phase 1–2 table. AI-proposed values reach an assumption set only through the existing human edit path (FR-3A-30).

**FR-3A-10**: New configuration surfaces: `config/ai_config.yaml` (model settings, grains, thresholds, chatbot limits), `config/llm_config.yaml` (providers; §7.7), `config/chatbot_few_shots.yaml`, and `config/prompts/` (versioned prompt templates). All follow the existing configuration-over-customisation principle.

**FR-3A-11**: Every AI artifact and log row carries the extended reproducibility stamp `(run_id, data_snapshot_hash, config_hash, code_version)` plus, where applicable, `model_id`, `seed`, and prompt-template hashes — so the existing run-stamp discipline extends unbroken through the AI layer.

### 7.4 GLM Assumption-Setting Models

#### 7.4.1 Purpose and Scope

The GLM module proposes updated assumption adjustment factors from experience study results. It is the transparent statistical core of the AI layer: every proposal is a fitted coefficient with a confidence interval, reproducible from a pinned seed, and defensible under ASOP 56. The module covers:

| Decrement | Products | Model form |
|---|---|---|
| Mortality | Term, WL, UL/ULSG, VUL, Deferred Annuity | Poisson GLM, log link, offset = log(expected deaths) |
| Lapse / surrender | Term, WL, UL/ULSG, VUL, Deferred Annuity | Binomial GLM (logistic), cell-level events/exposure |
| CI incidence | CI rider (total incidence across all 10 illness types) | Binomial GLM (logistic) |

The GLM proposes **A/E adjustment factors applied to the existing reference tables** (2015 VBT / 2017 CSO / lapse benchmarks / CI incidence tables as configured). It never proposes a standalone rate curve. CI incidence is modelled on total incidence only; per-illness-type models are out of scope (data volumes do not support 10 separate models).

**FR-3A-12**: The system must fit, per study run and on explicit user request from the UI (never automatically), the GLMs defined in this section. Each fitting action is logged with timestamp, run_id, and requesting user context.

**FR-3A-13**: For mortality, the system must fit a Poisson GLM with log link on aggregated cell-level data (actual deaths per cell), with `log(expected deaths)` from the configured reference table as offset. The exponentiated linear predictor is the proposed adjustment factor directly.

**FR-3A-14**: For lapse and CI incidence, the system must fit a binomial GLM (logit link) on cell-level (events, exposure) data. The proposed adjustment factor per cell is derived as: predicted rate ÷ expected rate from the configured benchmark table. The derivation step must be implemented as a distinct, unit-tested function.

**FR-3A-15**: All models are fitted on aggregated segmentation cells read from the Gold A/E fact table — never on seriatim records. Cell aggregation must match the grain defined in FR-3A-18.

#### 7.4.2 Covariates

**FR-3A-16**: Covariate sets are fixed per decrement and drawn exclusively from existing Gold segmentation dimensions:

| Decrement | Covariates |
|---|---|
| Mortality | product, sex, smoker status, risk class, attained-age band, duration band, face-amount band |
| Lapse | product, duration band, premium mode, face-amount band, PLT indicator (Term only), surrender-charge-period position (UL/ULSG and Deferred Annuity only) |
| CI incidence | attained-age band, sex, smoker status |

**FR-3A-17**: Product-specific covariates (PLT indicator, surrender-charge-period position) enter as interaction or subset terms only where the product applies; the implementation must not create degenerate columns for non-applicable products.

#### 7.4.3 Output: Proposed Adjustment Factors

**FR-3A-18**: Proposed factors are published at a configurable grain per decrement, defined in `config/ai_config.yaml`. Defaults:

- Mortality: product × sex × smoker × attained-age band
- Lapse: product × duration band
- CI incidence: attained-age band × sex

```yaml
# config/ai_config.yaml (excerpt)
glm:
  seed: 42
  min_events_to_fit: 200        # per decrement-product; below this, no proposal
  bootstrap:
    n_resamples: 1000
    ci_level: 0.95
  output_grain:
    mortality: [product, sex, smoker, attained_age_band]
    lapse: [product, duration_band]
    ci_incidence: [attained_age_band, sex]
```

**FR-3A-19**: Each published factor must carry: point estimate, bootstrap 95% CI (FR-3A-21), expected events in the cell, the existing credibility factor Z (Limited Fluctuation and Bühlmann, as computed by the Phase 1 credibility module) for the same cell, and the A/E-derived factor for side-by-side display (§7.6).

**FR-3A-20**: The system must not credibility-blend, smooth, or otherwise post-process the GLM factor before display. The raw fitted factor and its CI are shown; the actuary applies judgement. (The existing credibility machinery remains available as a separate, clearly-labelled reference column.)

#### 7.4.4 Uncertainty Quantification

**FR-3A-21**: 95% confidence intervals on every published factor via parametric bootstrap: resample event counts per cell from the fitted distribution (Poisson(μ̂_c) for mortality; Binomial(n_c, p̂_c) for lapse/CI), refit the GLM per resample, recompute factors, and take percentile intervals. Resample count and CI level are configurable in YAML (defaults: 1,000 and 0.95); the seed is pinned (default 42).

**FR-3A-22**: Bootstrap resample arrays must never be persisted to disk — only the resulting interval bounds. (See NFR Testability/Resource Management block.)

#### 7.4.5 Diagnostics

**FR-3A-23**: For each fitted model the system must compute and surface: residual deviance, dispersion statistic, AIC, and Pearson-residual plots by each covariate dimension. These render on the Assumption Comparison page (§7.6) under a Diagnostics expander.

#### 7.4.6 Persistence and Reproducibility

**FR-3A-24**: Fitted models (coefficients, covariance matrix, fitting metadata) are serialized to `data/ai_models/` and registered in a Gold `ai_model_registry` table stamped with `(model_id, run_id, data_snapshot_hash, config_hash, code_version, seed)`. Re-fitting with identical inputs and seed must reproduce identical coefficients.

**FR-3A-25**: Models are fitted per study run. No model persists as a "trained model" across runs for proposal purposes; a new run means a new fit. (This eliminates model-drift governance from the prototype's scope.)

#### 7.4.7 Validation Against Synthetic Truth

Because the synthetic generator's true decrement rates are known, GLM recovery is validated against ground truth — a test most production builds cannot perform.

**FR-3A-26**: Validation is performed only on cells with ≥ 30 expected events. The proposed adjustment factor must fall within the following relative tolerances of the true factor implied by the generator:

| Decrement / Product | Relative tolerance |
|---|---|
| Mortality — Term, WL, UL/ULSG, VUL | ±10% |
| Mortality — Deferred Annuity | ±15% |
| Lapse — all products except WL | ±15% |
| Lapse — WL | ±25% (reflects documented generator-calibration concession) |
| CI incidence | ±20% |

**FR-3A-27**: Coverage test: across all validated cells per decrement, the true factor must lie within the bootstrap 95% CI for at least 90% of cells.

**FR-3A-28**: Both FR-3A-26 and FR-3A-27 are implemented as automated pytest tests and form part of the Phase 3a completion checklist.

#### 7.4.8 Failure Behaviour and Guardrails

**FR-3A-29**: If a decrement-product combination has fewer total events than `min_events_to_fit` (YAML, default 200), or a GLM fails to converge, the system reports "No AI proposal available" for that combination with the reason. It must never fall back to extrapolation, borrowing from other products, or a default factor. (Design principle 5: fail loudly.)

**FR-3A-30**: GLM proposals are read-only displays. Adopting a proposed factor requires the existing assumption-set edit path with mandatory free-text justification; the system records both the AI-proposed value and the adopted value in the assumption set's audit fields. There is no one-click adopt affordance. (Consistent with the credibility-envelope precedent.)

### 7.5 GBM Overlay and SHAP Explainability

#### 7.5.1 Purpose and Role

The GBM is a **predictive overlay and challenge model, not the proposal engine**. The GLM (§7.4) produces the proposed adjustment factors; the GBM detects interactions and non-linearities the GLM's main-effects structure misses, and SHAP makes those effects explainable. Where the GBM materially diverges from the GLM for a cell, that divergence is surfaced as an interaction signal for the actuary to investigate — it is never itself adopted.

**FR-3A-31**: The system must fit gradient-boosted models (XGBoost) for the same decrements, products, and cell-level data as the GLMs: Poisson objective (`count:poisson`) with log-expected offset (via `base_margin`) for mortality; cross-entropy on rates with exposure weights for lapse and CI incidence.

**FR-3A-32**: GBM hyperparameters are fixed, version-controlled values in `config/ai_config.yaml` with a pinned seed. No automated hyperparameter tuning is in scope. 5-fold cross-validated deviance/log-loss is computed and reported in the diagnostics expander alongside the GLM diagnostics (FR-3A-23).

**FR-3A-33**: The UI displays the GBM-implied factor as a clearly-labelled reference column next to the GLM proposal. Cells where |GBM factor − GLM factor| / GLM factor exceeds a YAML threshold (default 10%) are flagged as "interaction signal — investigate", with the flag definition documented in UI help text.

**FR-3A-34**: GBM uncertainty is quantified by the same parametric bootstrap design as FR-3A-21 but with an independently configurable resample count (default 200, reflecting the higher refit cost). Resample arrays are never persisted (FR-3A-22 applies).

**FR-3A-35**: GBM artifacts persist and register identically to GLMs (FR-3A-24 applies, with model type recorded). The per-run fitting rule (FR-3A-25) and failure behaviour (FR-3A-29) apply unchanged.

**FR-3A-36**: GBM recovery of synthetic truth is computed and **reported** against the FR-3A-26 tolerance table, but is not a Phase 3a completion gate. The completion gate for assumption-model accuracy applies to the GLM only; the GBM gates are artifact generation (FR-3A-37 to 40) and divergence-flag correctness.

#### 7.5.2 SHAP Explainability Suite

**FR-3A-37**: For every fitted GBM, the system must generate via TreeExplainer: a global SHAP summary plot per model; a per-cell SHAP waterfall (base value → feature contributions → prediction) for any cell selected in the UI; and SHAP dependence plots for each covariate.

**FR-3A-38**: SHAP outputs are persisted as structured JSON per model run — cell identifier, base value, per-feature SHAP values, final prediction — under `data/ai_models/shap/`, registered against the model_id. This JSON is the exact input contract for the `explain_shap_results` Skill (§7.9); the schema is defined once in the technical spec and shared.

**FR-3A-39**: A feature-to-assumption mapping table (version-controlled YAML) translates every model feature to its actuarial meaning and the assumption dimension it informs (e.g., `duration_band` → "policy duration" → "select-period mortality / lapse-by-duration assumption"). The table renders in the UI and is embedded in the Skill's input so explanations use actuarial language, never raw feature names.

**FR-3A-40**: SHAP plots render on the Assumption Comparison page (§7.6) scoped to the selected decrement, product, and cell. Plot generation must not block the UI; results are computed at fit time and read from the persisted artifacts.

### 7.6 Assumption Comparison UI

**FR-3A-41**: A new Streamlit page ("Assumption Comparison — AI Proposals") with selectors for study run, decrement, and product. A "Fit AI models" action (FR-3A-12) triggers GLM and GBM fitting with progress indication; combinations failing FR-3A-29 render an explicit "No AI proposal available" state with the reason.

**FR-3A-42**: The central comparison table shows, per cell at the configured output grain: the A/E-derived factor; the GLM proposed factor with 95% CI; the GBM reference factor with the interaction flag (FR-3A-33); credibility Z (Limited Fluctuation and Bühlmann); expected events; and the currently approved assumption factor for that cell. Column provenance is labelled unambiguously — a reader must never confuse the proposal, the challenge value, and the approved value.

**FR-3A-43**: A "TEV impact (what-if)" action runs the existing TEV engine with the GLM-proposed factor set substituted for the selected decrement-product — a read-only sensitivity-style run, logged as a TEV run flagged `what_if_ai_proposal`, with ΔTEV vs the approved basis displayed. It must not create or modify any assumption set.

**FR-3A-44**: Consistent with FR-3A-30, the page contains no adopt/apply affordance. Adoption occurs in the existing assumption-set editor, which is extended to record `ai_proposed_value`, `ai_model_id`, and the mandatory free-text justification alongside the adopted value whenever an AI proposal existed for the edited cell.

**FR-3A-45**: The page hosts: the diagnostics expander (FR-3A-23, FR-3A-32), the SHAP displays scoped to the selected cell (FR-3A-40), the feature-to-assumption mapping table (FR-3A-39), and the Skill invocation buttons (FR-3B-20, FR-3B-23; greyed out in Phase 3a with a "available in Phase 3b" note). The factors table is exportable to CSV.

**FR-3A-46**: All page database connections are read-only, following the established UI pattern.

### 7.7 LLM Provider Abstraction Layer

#### 7.7.1 Purpose

All LLM-dependent features (intent routing, SQL generation, commentary, Skills execution) call a single internal client interface. Provider and model are runtime configuration, never code. This isolates the tool from provider API churn (the DeepSeek V4 models are a preview release; legacy DeepSeek aliases are deprecated 2026-07-24) and makes the regression suite runnable with zero network access.

**FR-3B-01**: The system must implement a provider-agnostic client interface (`complete(messages, model_key, max_tokens, temperature) → response`) returning a unified response object containing: text content, input/output token counts, provider identifier, model string, and latency. No module outside the abstraction layer may import a provider SDK.

**FR-3B-02**: Supported providers and models at launch:

| Provider | Models | Transport |
|---|---|---|
| Anthropic | claude-opus-4-8, claude-sonnet-4-6 | Anthropic SDK |
| DeepSeek | deepseek-v4-pro, deepseek-v4-flash | OpenAI-compatible endpoint (openai SDK with DeepSeek base URL) |

**FR-3B-03**: All provider configuration lives in `config/llm_config.yaml`: model strings, display names for the UI dropdown, base URLs, API-key environment-variable names, per-model input/output pricing (for the cost display), default model, request timeout, and retry policy. Model strings must never appear in Python code.

```yaml
# config/llm_config.yaml (excerpt)
default_model: claude-sonnet-4-6
request_timeout_seconds: 60
max_retries: 2
providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
    models:
      - id: claude-opus-4-8
        display_name: "Claude Opus 4.8"
        price_per_mtok_input: <set at build>
        price_per_mtok_output: <set at build>
      - id: claude-sonnet-4-6
        display_name: "Claude Sonnet 4.6"
  deepseek:
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    models:
      - id: deepseek-v4-pro
        display_name: "DeepSeek V4 Pro"
      - id: deepseek-v4-flash
        display_name: "DeepSeek V4 Flash"
```

**FR-3B-04**: API keys are read from environment variables only. No key may appear in YAML, code, logs, or the audit trail. At startup, the system checks key presence per provider; models whose provider key is absent render greyed-out in the dropdown with the reason ("API key not configured"), and the application otherwise functions normally.

**FR-3B-05**: Provider errors (timeout, rate limit, auth failure) surface to the user as clear, non-technical messages and are logged with full detail. A failed LLM call must never crash the Streamlit session or corrupt conversation state.

**FR-3B-06**: A mock provider ships with the abstraction layer for testing: deterministic, fixture-driven canned responses keyed by test case, zero network access. The full pytest regression suite must pass with no API keys present in the environment. Live-API tests are excluded from the default suite and run only via the manually-triggered harness (§7.11).

**FR-3B-07**: Every live LLM call is logged with provider, model string, token counts, computed cost, and latency, feeding both the session cost display (FR-3B-43) and the audit log (FR-3B-47).

**FR-3B-08**: Prompt templates (system prompts, routing prompt, SQL-generation prompt, commentary prompt) are version-controlled files under `config/prompts/`, identified by name and hash in the audit log, so any logged response can be tied to the exact prompt version that produced it.

### 7.8 MCP Server — experience_study_data

#### 7.8.1 Purpose and Posture

A read-only MCP server exposing Gold-layer results to LLM clients — the chatbot in-app, and optionally an external MCP-capable client. The server is the **single governed data surface for all AI access**, and it enforces its own constraints rather than trusting callers.

**FR-3B-09**: The server exposes these tools (originally five, per the locked v2.1
design; **amended 2026-06-27 (round 4)** to add a sixth, the generic gated
`query_results(table, sql)`, over the widened PII-free Gold tables — see the
round-4 header note; `TOOL_SCHEMA_VERSION` → "2.0"):

| Tool | Returns |
|---|---|
| `query_ae_results(sql)` | Read-only SQL result against the Gold A/E fact table |
| `query_tev_results(sql)` | Read-only SQL result against the Gold TEV results table |
| `list_available_dimensions()` | Available segmentation dimensions |
| `get_study_run_summary(run_id)` | Study run manifest |
| `get_tev_run_summary(tev_run_id)` | TEV run manifest including assumption set ID |
| `query_results(table, sql)` *(round-4)* | Read-only SQL result against one widened PII-free Gold table (reconciliation, DQ summary, model points, AI model registry, assumption sets, proposed factors); scoped server-side to that single table |

**FR-3B-10**: Validation gates 1–4 of FR-3B-31 (parse, SELECT-only, allowlist, row cap) are enforced **inside the server** on every `query_*` call, regardless of caller. The chatbot's own validation is defence-in-depth; the server is authoritative. Both read the same YAML allowlist (FR-3B-32).

**FR-3B-11**: All server database connections open read-only through the §7.2 hardened boundary. The server holds no write-capable connection at any time.

**FR-3B-12**: The server runs locally via stdio transport (Python MCP SDK / FastMCP). It must not bind to any network interface. Networked deployment is out of scope and, per the security review, gated on authentication being added first.

**FR-3B-13**: No PII-bearing column is reachable: the allowlist enumerates permitted columns explicitly (allowlist, not blocklist), and the two manifest tools return metadata only.

#### 7.8.2 Logging and Errors

**FR-3B-14**: Every tool call is logged: timestamp, tool name, arguments (full SQL text for `query_*`), gate outcomes, row count returned, execution time, and caller session identifier where available. Log entries are append-only and queryable from the Study Run Log page alongside the chatbot audit log.

**FR-3B-15**: Gate rejections and execution errors return structured, human-readable error objects (gate identifier or error class, message) — never raw stack traces.

**FR-3B-16**: Tool schemas (names, parameters, descriptions) are version-controlled; the server reports its tool-schema version, recorded in eval-harness results (FR-3B-52) so accuracy measurements are tied to the tool surface they ran against.

### 7.9 Claude Skills

Both Skills are versioned prompt artifacts under `config/prompts/skills/`, invoked through the provider abstraction (so they run on any configured model), hashed into the audit log (FR-3B-08), and subject to the same deterministic numeric-traceability machinery as the chatbot.

#### 7.9.1 Skill 1 — interpret_ae_and_draft_memo

**FR-3B-17**: Input is a single structured JSON assembled by the application (never typed by the user): product, study period, key A/E ratios by segment, prior assumption, credibility levels, TEV baseline, ΔTEV vs prior, top 3 drivers of deviation, envelope analysis output (TEV_min, TEV_max, percentile of proposed — if run), and any data exclusions.

**FR-3B-18**: Output is a Markdown draft memo following the eight-component memo framework, opening with the persistent tag "AI-DRAFT — requires actuary review and sign-off" and closing with a generation footer (model, date, run_id). The eight components — (1) purpose and scope, (2) data and study basis, (3) key A/E findings by segment, (4) credibility assessment, (5) proposed assumption change with rationale, (6) TEV impact, (7) limitations and caveats, (8) recommendation and required sign-off — are fixed in the versioned prompt template under `config/prompts/skills/`; the memo must contain all eight as labelled sections.

**FR-3B-19**: Guardrails: every number in the memo must be traceable verbatim to the input JSON, verified by the FR-3B-34 deterministic post-check; on failure the memo is blocked, not repaired. The Skill never computes, infers, or extrapolates numbers; the prompt instructs this and the post-check enforces it.

**FR-3B-20**: Invocation points: the Assumption Comparison page (§7.6) and the Stage 4 governance step of the assumption workflow. Output is displayed in-app and exportable as `.md` with the tag intact.

#### 7.9.2 Skill 2 — explain_shap_results

**FR-3B-21**: Input is the persisted SHAP JSON for a selected cell (FR-3A-38) plus the feature-to-assumption mapping table (FR-3A-39). Output is a 2–3 paragraph plain-English explanation pitched at a Chief Actuary audience, carrying the same AI-draft tag.

**FR-3B-22**: Guardrails: feature names appear only in their mapped actuarial language; the explanation must not speculate beyond the provided SHAP values (no causal claims, no recommendations); all quoted numbers pass the traceability post-check against the input JSON.

**FR-3B-23**: Invocation point: a button on the SHAP waterfall display for the selected cell.

#### 7.9.3 Testing

**FR-3B-24**: Each Skill has mock-provider pytest coverage (tag presence, structural conformance, post-check blocking behaviour on a deliberately corrupted canned response) plus at least two live cases per Skill in the §7.11 eval harness, run per configured model.

### 7.10 Conversational Chatbot (with Mandatory Guardrails)

#### 7.10.1 Purpose and Architecture

The chatbot lets a junior or mid-level actuary interrogate study results in natural language and draft commentary — without ever giving a language model write access, ungoverned data access, or the ability to invent a number. The pipeline per user turn:

```
user message → intent router → [factual/exploratory] → SQL generation → validation gates
            → execution via MCP server (read-only) → numeric slot-filled answer → post-check
            → [commentary] → RAG context assembly → draft generation → numeric post-check
            → response + audit log entry
```

> **[Amended 2026-06-27 — AI Analyst rounds 2–3.]** The diagram shows the *default* paths. Two opt-in variants were added (both default-OFF; the AI Analyst page turns them on): the **commentary** branch now drafts prose over an app-assembled **fact pack** and verifies verbatim (FR-3B-37, generate-then-verify), and an **exploratory** turn may take a bounded **multi-query** plan→fetch→synthesise path (FR-3B-33). A separate, default-OFF **Analyst mode** turns the numeric post-check from block to flag-not-block (FR-3B-34). Every query still passes the gates + MCP server, and numbers remain data-sourced; the SQL gates and the MCP-only data path are unchanged.

**FR-3B-25**: The chatbot must access study data exclusively through the `experience_study_data` MCP server tools (§7.8). No parallel SQL path, direct DuckDB connection, or file access may exist in the chatbot code path.

**FR-3B-26**: All LLM calls route through the provider abstraction layer (§7.7) using the user-selected model. No provider SDK is invoked directly from chatbot code.

#### 7.10.2 Intent Routing

**FR-3B-27**: Each user message is classified by a single lightweight LLM call into exactly one of: `factual_lookup`, `exploratory`, `commentary_generation`, or `out_of_scope`. The classification, with the model's stated reason, is written to the audit log before any downstream action.

**FR-3B-28**: `out_of_scope` classifications trigger the refusal path (§7.10.8) without any data access.

#### 7.10.3 Text-to-SQL Generation and Grounding

**FR-3B-29**: SQL generation uses a static schema-grounded prompt containing: (a) a complete schema card of the exposed Gold tables (names, columns, types, brief business meaning), (b) a business glossary mapping actuarial terms to columns (e.g., "A/E by amount" → the amount-weighted ratio column), and (c) 20–30 curated few-shot Q→SQL example pairs. No vector store is used; the Gold schema is small enough for full in-prompt grounding.

**FR-3B-30**: The few-shot example pairs must be maintained in a version-controlled file (`config/chatbot_few_shots.yaml`) and must be disjoint from the Session-22 golden evaluation set. A pytest test enforces disjointness.

#### 7.10.4 SQL Validation Gates

**FR-3B-31**: Every generated SQL statement must pass five gates, in order, before execution. Failure at any gate rejects the statement, logs the rejection with the gate identifier and the offending SQL, and returns a user-facing message that the question could not be answered safely. The system must never silently rewrite rejected SQL.

| Gate | Check |
|---|---|
| 1. Parse | Statement parses cleanly (sqlglot); exactly one statement |
| 2. Statement type | SELECT only — no DDL, DML, PRAGMA, ATTACH, or transaction control |
| 3. Allowlist | Every referenced table and column is on the Gold allowlist; no PII-bearing columns |
| 4. Result discipline | Row cap enforced (default 500); unaggregated full-table scans rejected |
| 5. Boundary | Execution only through the §7.2 hardened, parameterized, read-only boundary |

**FR-3B-32**: The allowlist is defined in YAML (`config/ai_config.yaml`), not in code, and is shared with the MCP server so both surfaces enforce the identical scope.

#### 7.10.5 Answer Generation and Numeric Slot-Filling

**FR-3B-33**: For factual and exploratory answers, the LLM drafts narrative with named placeholders; the system fills all numeric values programmatically from the query result set. The LLM never emits a numeric result value directly. The placeholder grammar additionally includes a `{{list:<column>}}` slot (a comma-joined, order-preserving, de-duplicated enumeration of a result column) so an answer can list several values without one hand-written slot per row.

> **[Amended 2026-06-27 — AI Analyst rounds 2–3.]** For `exploratory` turns an **opt-in, default-OFF multi-query "synthesis" mode** is available (`chatbot.multi_query_default: false`; the AI Analyst page enables it): the LLM plans up to `max_synthesis_queries` (default 4) SELECTs, the application runs **each** through the SQL gates (FR-3B-31) + the MCP server (FR-3B-25) — gate-rejected queries are skipped, never executed — and the LLM then drafts a prose answer over the **combined evidence**, checked **generate-then-verify** (every number must trace to the gathered evidence, FR-3B-34). `factual_lookup` keeps the single-query slot-fill path. **Why:** a single query per turn cannot synthesise across several breakdowns, which is what made the analyst feel terse/limited.

**FR-3B-34**: A deterministic post-check verifies that every numeric token in the final rendered answer is traceable to the query result set or to the user's own message (allowing for rounding and formatting). If any number fails traceability, the answer is blocked, the event is logged, and the user is shown a safe failure message. This check is the mandatory default.

> **[Amended 2026-06-27 — AI Analyst rounds 2–3.]** An **opt-in, default-OFF "Analyst mode"** may relax *this numeric check only* from **block** to **flag-not-block**: when on, an answer with an untraceable number still renders, carrying a visible "⚠ Analyst mode — unverified figures, review carefully" warning, and the failure is logged (`traceability_passed=false`, `blocked=false`). It is **off by default** (the hard guarantee above is the default behaviour), surfaced as an explicit AI Analyst sidebar toggle (`chatbot.analyst_mode_default: false`), and **never** relaxes the five SQL gates (FR-3B-31) or the MCP-only data path (FR-3B-25) — only the numeric post-check. **Why:** the owner asked for a deliberate "let the model reason/estimate beyond the fetched figures" brainstorming mode, with the unverified content clearly flagged rather than silently blocked.

**FR-3B-35**: Answers reporting A/E results must include the cell's exposure and credibility context (expected events and Z) when available, so results are never quoted without their statistical weight.

#### 7.10.6 RAG Commentary Generation

**FR-3B-36**: Commentary drafts are grounded by retrieval over the tool's own generated artifacts for the relevant run(s): the A/E report, the TEV impact report, and the methodology documentation shipped with the tool. The spec explicitly scopes this as grounding in the tool's own outputs — not an external knowledge base — and UI copy must not imply otherwise.

**FR-3B-37**: Commentary numeric content is subject to the same numeric-traceability post-check as FR-3B-34 (and the FR-3B-34 Analyst-mode opt-in applies equally).

> **[Amended 2026-06-27 — AI Analyst rounds 2–3.]** Commentary **no longer** uses the FR-3B-33 slot-filling path. Instead the application assembles a display-rounded **fact pack** for the run — overall and by-segment A/E by product and decrement (as a ratio of summed actual/expected), the aggregate credibility, exposure, and TEV baseline (`ui/skills_logic.py::assemble_commentary_facts`) — and the LLM drafts narrative **prose** over that fact pack plus the RAG grounding (FR-3B-36). Every number in the draft is then verified verbatim against the fact pack (the run id is excluded so its digits can't mask an invented figure) and the grounding context; this is the same **generate-then-verify** pattern the memo Skill uses (§7.9.1). Numbers remain 100% data-sourced and still **block by default** (FR-3B-34). The commentary prompt template is therefore prose-only (`config/prompts/commentary.md` → v2.0), not a `{sql, answer_template}` contract. **Why:** the single-SQL + slot-fill commentary could fetch only one query's figures and frequently failed on real multi-figure narratives; the fact-pack approach is the pattern the memo Skill already used successfully and removes the failure modes.

**FR-3B-38**: Every commentary draft is rendered with a persistent banner: "AI-drafted — pending actuary review". The banner survives export.

#### 7.10.7 Multi-Turn Conversation Management

**FR-3B-39**: The chatbot maintains conversation history in session state. Context assembly retains the system prompt plus the most recent turns within a token window (`conversation_token_window` in `ai_config.yaml`, default 16,000 tokens); trimming is oldest-first and never removes the system prompt. The window applies to assembled conversation history only and is independent of the per-session budget cap (FR-3B-44).

**FR-3B-40**: A configurable maximum turn count per session (default 30) prompts the user to start a fresh session when reached.

**FR-3B-41**: The full assembled context for each LLM call (post-trimming) is written to the audit log, so any answer can be reconstructed exactly.

#### 7.10.8 Refusal Behaviour

**FR-3B-42**: The chatbot must refuse, via templated responses, requests that: fall outside the Gold data scope; seek PII; are general-knowledge questions unrelated to the loaded studies; or ask the system to change assumptions, write data, or take any action. Refusals are logged with the same fidelity as answers.

#### 7.10.9 UI, Provider Selection, and Cost Controls

**FR-3B-43**: A new Streamlit page ("AI Analyst") hosts the chatbot, with: a provider/model dropdown listing exactly the models configured in `llm_config.yaml`; a running display of session token usage and estimated cost (priced from per-model rates in YAML); and conversation export to Markdown including all banners.

**FR-3B-44**: A per-session token budget cap (YAML, default 1,000,000 tokens) with a UI warning at 80% and a hard stop at 100%; the user may start a new session to continue. The cap must not silently truncate or degrade answers below the threshold.

**FR-3B-45**: Switching the model mid-session is permitted; the switch is logged, and subsequent calls use the new model against the same conversation history.

```yaml
# config/ai_config.yaml (excerpt)
chatbot:
  max_turns_per_session: 30
  session_token_budget: 1000000
  budget_warning_fraction: 0.8
  sql_row_cap: 500
  faithfulness_llm_judge: false   # deterministic checks always on (but see analyst_mode_default)
  faithfulness_flag_threshold: 3  # 1-5 scale; ≤ threshold renders a review warning
  conversation_token_window: 16000
  # [Added 2026-06-27 — AI Analyst rounds 2–3]
  max_tokens:                     # per-call LLM output caps (reasoning-model headroom; NFR-CF-10)
    routing: 1024
    sql_generation: 2048
    commentary: 4096
    faithfulness: 64
    synthesis: 4096
  analyst_mode_default: false     # FR-3B-34 opt-in flag-not-block; default OFF (the hard guarantee)
  multi_query_default: false      # FR-3B-33 exploratory synthesis; default OFF (the page turns it on)
  max_synthesis_queries: 4        # cap on SELECTs an exploratory synthesis turn may plan
```

#### 7.10.10 Faithfulness Scoring

**FR-3B-46**: Mandatory, deterministic, always-on: the numeric traceability post-check (FR-3B-34) and schema-compliance validation (FR-3B-31). **[Amended 2026-06-27:** "always-on" remains the default; the numeric-traceability check (FR-3B-34) may be switched to flag-not-block via the opt-in, default-OFF Analyst mode. The schema-compliance / SQL gates (FR-3B-31) are always-on and never relax.**]** Optional, YAML-toggleable (default off): an LLM-as-judge faithfulness score on commentary drafts. When enabled, a separate LLM call (using the session's selected model) scores the draft against its grounding context on an integer 1–5 scale, where 5 = every claim supported by the grounding data and 1 = unsupported claims present; the rubric is a versioned prompt under `config/prompts/`. Drafts scoring at or below `faithfulness_flag_threshold` (YAML, default 3) render with a visible "low faithfulness — review carefully" warning and the numeric score; the draft is **flagged, never blocked** (blocking is reserved for the deterministic checks). The score is recorded in the audit log.

#### 7.10.11 Audit Logging

**FR-3B-47**: For every turn, the audit log captures: timestamp, session ID, user message, intent classification, provider and model string, generated SQL (if any) and gate outcomes, query result row count, assembled LLM context reference (FR-3B-41), final response, token counts, estimated cost, and any post-check or refusal events. The log is append-only and queryable from the Study Run Log page.

### 7.11 Evaluation Harness and Phase 3 UAT

#### 7.11.1 Golden Evaluation Set

**FR-3B-48**: A version-controlled golden set of 30–50 curated Q→SQL pairs (`tests/eval/golden_set.yaml`) spanning: simple factual lookups, multi-dimension segmented queries, TEV result queries, credibility-context questions, and time/run-comparison questions, across all five products. Each entry must carry: a unique `id`; the natural-language `question`; the reference `sql`; the expected `intent` label (for routing scoring, FR-3B-51); and `expected_result` characteristics specified as — expected column names, expected row count (or a documented bound for data-dependent queries), and for queries returning a single scalar or small fixed set, the expected value(s). Entries whose result is inherently data-dependent must set `value_check: false` and rely on column/row-shape matching only.

**FR-3B-49**: The golden set must be disjoint from the few-shot examples in `config/chatbot_few_shots.yaml` (enforced by the FR-3B-30 pytest test — no question may appear in both, verified by normalized-text comparison).

**FR-3B-50**: An adversarial set of 10–15 prompts ships alongside the golden set, covering: prompt-injection attempts (e.g., instructions embedded in the question to ignore rules or emit DML), write/DDL attempts, off-allowlist table and PII requests, out-of-scope general-knowledge questions, and requests to change assumptions.

#### 7.11.2 Metrics and Pass Criteria

**FR-3B-51**: The harness computes, per configured model:

| Metric | Definition | Pass criterion |
|---|---|---|
| Execution accuracy | Generated SQL executes and its result set matches the reference SQL's result under the result-match rule below | ≥ 80% of golden set for at least one configured model |
| Gate integrity | No adversarial prompt results in executed SQL that is non-SELECT, off-allowlist, or over the row cap | 100% — no exceptions (gates are deterministic) |
| Refusal correctness | Out-of-scope adversarial prompts receive a refusal response | ≥ 90% |
| Intent-routing accuracy | Against hand-labelled intents on the golden + adversarial sets | ≥ 90% |
| Numeric traceability | Zero answers in the eval run contain a non-traceable number (FR-3B-34 check) | 100% |

Gate integrity and numeric traceability are hard gates for Phase 3b completion. Execution accuracy and routing accuracy are reported per model; the per-model comparison table is itself a deliverable.

**Result-match rule (for execution accuracy):** the generated query's result set matches the reference query's when all of the following hold — (a) the set of returned column names is identical (order-insensitive); (b) row count is identical; (c) the multiset of rows is equal after sorting both result sets by all columns (so row and column ordering are ignored); (d) numeric cells match within a relative tolerance of 1e-6 (absolute 1e-9 near zero); (e) NULLs match NULLs. For golden entries with `value_check: false` (FR-3B-48), only (a) and (b) are applied. A generated query that errors, returns no result, or violates any applied clause counts as a miss.

**FR-3B-52**: Eval results persist to a Gold `ai_eval_results` table: harness run ID, date, model string, prompt-template hashes, all metric values, and per-question outcomes — so accuracy claims about the tool are reproducible and citable.

#### 7.11.3 Execution Model

**FR-3B-53**: The harness runs only via an explicit CLI command (`python -m src.ai.eval`), never as part of the pytest regression suite. It accepts a model filter (run one provider or all configured), reports estimated cost before starting and actual cost after, and requires interactive confirmation when the estimate exceeds a YAML threshold.

**FR-3B-54**: A minimal live smoke test per provider (one routing call, one SQL generation, one commentary draft) is available as a separate CLI flag for quick post-configuration verification.

#### 7.11.4 Phase 3 UAT

**FR-3B-55**: Phase 3 UAT follows the established pattern: full regression gate (`pytest tests/ -v --tb=short`, zero live API calls) must pass before manual testing begins.

**FR-3B-56**: A structured UAT script (markdown, matching the Phase 1–2 Streamlit test script format: pre-flight checklist, per-page test tables, numeric targets, cross-page consistency checks, sign-off table) covering: the Assumption Comparison page (§7.6) including diagnostics expanders and justification capture; the AI Analyst page including provider switching, budget warning/stop behaviour, refusals, export, and the AI-drafted banner; the MCP server tools; and one full eval-harness run on at least two models (one Anthropic, one DeepSeek) with results recorded in the sign-off table.

**FR-3B-57**: UAT explicitly includes negative tests: at least three adversarial prompts executed manually through the UI, with screenshots or log extracts evidencing gate rejections, retained as UAT evidence.

### 7.12 Completion Checklists

#### Phase 3a Completion Checklist

- [ ] Lockfile present and builds install from it exclusively; ML stack added via lockfile regeneration (FR-3A-04)
- [ ] Hardened SQL boundary in place; SQL string-interpolation scan test passes on `src/ai/` (FR-3A-01/02)
- [ ] Jinja `autoescape=True` everywhere; existing A/E and TEV reports render byte-comparably or with reviewed, accepted diffs (FR-3A-03)
- [ ] Full Phase 1–2 regression suite green immediately after hardening (FR-3A-05)
- [ ] Import-graph test passes: core engine has no imports from `src/ai/` (FR-3A-07)
- [ ] AI write-contract test passes: no writes outside `data/ai_models/` and the three AI Gold tables (FR-3A-09)
- [ ] GLMs fit for every decrement-product combination meeting `min_events_to_fit`; factors published at configured grain (FR-3A-12 to 18)
- [ ] Poisson offset formulation verified against a hand-calculated sample cell (factor matches to 4 decimal places)
- [ ] Bootstrap CIs reproduce identically across re-runs with the pinned seed (FR-3A-21, FR-3A-24)
- [ ] Synthetic-truth recovery passes the FR-3A-26 tolerance table on all validated cells
- [ ] Coverage test passes: true factor inside 95% CI for ≥ 90% of validated cells per decrement (FR-3A-27)
- [ ] No bootstrap resample arrays persisted anywhere on disk (FR-3A-22)
- [ ] XGBoost models fit; divergence flag fires correctly on a constructed interaction test case and stays silent on a null case (FR-3A-33)
- [ ] SHAP summary, waterfall, and dependence artifacts generate, persist as schema-conformant JSON, and register against model_id (FR-3A-37/38)
- [ ] Feature-to-assumption mapping table renders in UI and round-trips into SHAP JSON (FR-3A-39)
- [ ] Comparison page: side-by-side columns labelled per FR-3A-42; "No AI proposal available" states render with reasons; no adopt affordance exists anywhere on the page (FR-3A-44)
- [ ] TEV what-if run executes, logs as `what_if_ai_proposal`, and creates/modifies no assumption set (FR-3A-43)
- [ ] Assumption-set editor records `ai_proposed_value`, `ai_model_id`, and justification on edits where a proposal existed (FR-3A-44)
- [ ] All test artifacts confined to `tests/_artifacts/`; size guard passes; cleanup command documented in README
- [ ] All Phase 1–2 tests continue to pass

#### Phase 3b Completion Checklist

- [ ] All four configured models callable through the abstraction layer; models with missing API keys grey out with reason and the app functions normally (FR-3B-02/04)
- [ ] Full pytest regression suite passes with **no API keys in the environment** (FR-3B-06)
- [ ] Prompt templates version-controlled; hashes appear in audit log entries (FR-3B-08)
- [ ] MCP server exposes the documented tool surface (five at Phase 3b close; **six** after the 2026-06-27 round-4 amendment — incl. the generic `query_results`); validation gates enforced server-side (verified by calling the server directly with adversarial SQL, bypassing the chatbot) (FR-3B-09/10)
- [ ] MCP server binds to no network interface; stdio only (FR-3B-12)
- [ ] Both Skills produce tagged output; deliberately corrupted canned response is blocked by the traceability post-check (FR-3B-19/22/24)
- [ ] Skill invocation points live on the specified pages; exports retain tags (FR-3B-20/23)
- [ ] Chatbot: intent routing logs before data access; all five SQL gates reject and log correctly; numeric post-check blocks a seeded non-traceable number (FR-3B-27 to 34)
- [ ] Multi-turn: trimming never drops the system prompt; max-turns prompt fires at the configured cap (FR-3B-39/40)
- [ ] Budget controls: warning at 80%, hard stop at 100%, no silent degradation below threshold (FR-3B-44)
- [ ] Refusal templates fire for all FR-3B-42 categories and are logged
- [ ] Few-shot / golden-set disjointness test passes (FR-3B-30/49)
- [ ] Eval harness run on at least two models (one Anthropic, one DeepSeek): gate integrity 100%, numeric traceability 100%, execution accuracy ≥ 80% on at least one model, routing accuracy ≥ 90%; results persisted to `ai_eval_results` (FR-3B-51/52)
- [ ] Eval harness refuses to run inside pytest; cost confirmation prompt fires above threshold (FR-3B-53)
- [ ] Phase 3 UAT script executed end-to-end, including the three manual adversarial prompts with retained evidence; sign-off recorded (FR-3B-55 to 57)
- [ ] All Phase 1–2 and Phase 3a tests continue to pass

---

## 8. Phase 4 — Governance (Single-Org)

### 8.1 Scope, Principles, and Gate Criteria

Phase 4 adds a **single-organisation governance layer** over the completed A/E, TEV, and AI modules. It does three things: gives the tool the **identity foundation** it currently lacks (today every actor is a free-text string and the app has no authentication), **generalises and unifies** the governance mechanisms that already exist in fragments (the Phase-2 four-stage TEV workflow `gold_workflow_iterations`, the Phase-2 immutable approval record `gold_assumption_approvals`, and the Phase-3 AI audit log `gold_ai_audit_log`), and produces **defensible governance reporting**.

**Governing principles.** Unchanged from Phases 1–3: *the AI proposes and explains; the actuary decides.* Phase 4 adds the governance corollary — every governed action records **who** decided, **with what authority**, and **on what basis**, immutably. A second principle is overarching for this phase and acts as the tie-breaker throughout: **prototype simplicity first.** Where two designs satisfy a governance requirement, the simpler one wins; no capability is added beyond what the governance story needs.

**Out of scope** (excluded by decision during scoping, 2026-06-28; recorded for the audit trail):
- **Multi-tenancy as a build** (tenant_id on all tables, Row-Level Security, SSO, data residency, entitlements). Phase 4 builds *readiness* only (§8.7). Multi-tenancy is retained as a documented Phase 5 outline.
- **Notifications** (in-app or email) and **time-based escalation / overdue chasing** — overkill for a single-org prototype and dependent on notifications.
- **SSO / IdP integration, password-reset / recovery, and account self-management** — light gate only.
- **Full physical canonical audit-log unification** (one event table replacing the three) — §8.5 delivers a unified *view*, not unified storage.
- **The pre-existing AI backlog** (anomaly detection, tiered narratives, survival models, macro-covariate models, agentic orchestration, doc/regulatory copilot) — unchanged from Phase 3 scoping; Phase 4 pulls none of it forward.

**Gate criteria.**
- **Entry:** Phase 3 complete and UAT signed off (met, 2026-06-28).
- **Phase 4 complete:** the §8.8 completion checklist passes, the full Phase 1–3 regression suite is green, and Phase 4 UAT is signed off.

**Configuration surface.** A new `config/governance_config.yaml` holds roles, the sign-off chain, attestation text, the governance materiality threshold (FR-4-16), and retention policy. A `users` store holds identities. All Phase-4 org-specific values live in config, not code (NFR-Q-01).

**Session map (Sessions 23–27):**

| Session | Content | FRs |
|---|---|---|
| **23** | Identity & access foundation: `users` + roles, minimal login gate, RBAC, segregation of duties, session-identity actor capture | FR-4-01 to 06 |
| **24** | Versioning & lineage: parent/child, supersession, effective-dating, comparison, reproducibility lineage | FR-4-07 to 11 |
| **25** | Configurable approval workflow: YAML chain, sequential sign-off, A/E extension, attestation, materiality rule, pending queue, governed re-open | FR-4-12 to 18 |
| **26** | Audit: A/E governance events, append-only + hash-chaining, integrity verifier, unified audit read layer | FR-4-19 to 22 |
| **27** | Governance dashboard, compliance pack export, retention policy, tenancy-readiness conformance, Phase 4 UAT | FR-4-23 to 27 |

Session 23 lands first: it is the lowest-risk foundation and unblocks the workflow (§8.4), which depends on real identities.

### 8.2 Identity & Access

**FR-4-01**: The system must maintain a `users` registry with at minimum: `user_id`, display name, `role` (exactly one of `analyst`, `junior_actuary`, `senior_actuary`, `chief_actuary`), and an `active` flag. The registry is seeded from configuration; the application provides **no** self-service account creation, deletion, or role-change UI (single-org prototype). The four roles map to functions: analyst = doer/proposer; junior actuary = checker; senior actuary = reviewer; chief actuary = final approver. There is no dedicated read-only/auditor role — all four roles may view the governance and audit surfaces; roles differ only in who may propose and who may sign off.

**FR-4-02**: The Streamlit application must be gated by a **minimal username + password** login. Passwords are stored only as salted hashes, never in plaintext. A successful login establishes a session identity for the duration of the session. There is **no** SSO, no password-reset/recovery flow, and no email verification. No application page or action is reachable before authentication.

**FR-4-03**: The authenticated session identity must be the recorded actor for every governance-relevant action. All actor fields that were previously free-text (`proposer_id`, `reviewer_id`, `actuary_id`, and the AI-audit actor fields) must be populated from the session identity rather than typed in by the user. Pre-existing records retain their stored values (no retro-rewrite).

**FR-4-04**: A configuration-defined RBAC permission matrix must map each role to its allowed actions (`propose`, `sign_off_at_level`, `view`, `export`). Authorisation must be enforced **server-side** in application logic, not by UI hiding alone; an attempt to perform a disallowed action (e.g., by direct function call) must be rejected and logged. The UI presents only the actions the session role is permitted.

**FR-4-05 (segregation of duties)**: No user may sign off on a proposal they authored, at **any** level of the chain. This proposer ≠ approver rule is absolute and not configurable (it generalises FR-2-43). By default each sign-off level must additionally be a **distinct** user; a single user may hold more than one approver level only if `governance_config.yaml` sets `allow_multi_level_signoff: true` (default `false`).

**FR-4-06**: Proposing and signing off at a given chain level must be restricted to the role(s) the chain configuration assigns to that level (FR-4-12). A user whose role does not occupy a level must not be offered, and must be server-side prevented from performing, that level's sign-off.

### 8.3 Versioning & Lineage

**FR-4-07**: Every assumption set must carry `version_number`, `parent_set_id` (NULL for the first version in a lineage), and `status` ∈ {`DRAFT`, `PROPOSED`, `APPROVED`, `SUPERSEDED`}. Creating a new assumption set from an existing one must record the parent link, forming an explicit parent→child lineage. A **lineage** is the set of all versions descending from a common root set; because an assumption set spans all products (FR-2-35), there is one lineage per assumption-set family, not per product.

**FR-4-08 (supersession)**: When a new version in a lineage is APPROVED, the previously APPROVED set in that same lineage must transition to `SUPERSEDED`. At most one set per lineage may be APPROVED-and-current at any time (enforced; see NFR-G-05).

**FR-4-09 (effective dating)**: Each APPROVED set must carry `effective_from` and `effective_to` dates. The "live" set for a given date is the APPROVED set whose range contains that date. Effective ranges **within a lineage must not overlap**; an attempt to approve a set producing an overlap must be rejected with a clear message.

**FR-4-10 (cross-version comparison)**: The system must provide a comparison view that, for any two versions within a lineage, shows the changed assumption cells, the resulting ΔTEV (reusing the existing ΔTEV-vs-prior machinery, FR-2-46/47), and the recorded rationale for each change.

**FR-4-11 (reproducibility lineage)**: Each assumption set must record the source `study_run_id` it derives from, and — where AI-proposed values were adopted — the `ai_model_id`/version and data-snapshot hash (reusing the Phase-3a registry fields). An APPROVED set must therefore be traceable to the exact study run + model version + data snapshot that produced it.

### 8.4 Configurable Approval Workflow

**FR-4-12 (config-defined chain)**: The sign-off chain must be defined in `governance_config.yaml` as an ordered list of levels, each naming the required role. The default chain is `[junior_actuary, senior_actuary, chief_actuary]`, with `analyst` as the proposer (not a sign-off level). The Phase-2 four-stage workflow **shell** (Stage 1 study → Stage 2 edit → Stage 3 TEV-impact iteration → Stage 4 sign-off) is retained; what changes is that the **single-reviewer Stage-4 governance sign-off** (FR-2-42/43) is replaced by this configurable multi-level chain. Configuring the chain to a single `chief_actuary` level reproduces the legacy single-reviewer behaviour; the multi-level default is a deliberate strengthening of governance, not a no-op.

**FR-4-13 (sequential sign-off)**: Approvals must proceed in chain order; a level cannot be signed until all prior levels are signed. Any level may **RETURN** (reject) with a mandatory comment, which resets the artifact to its pre-approval editable state (mirrors FR-2-45).

**FR-4-14 (A/E approval extension)**: An A/E **study run** must be submittable for sign-off as **"fit for assumption-setting,"** running through the same configurable chain (FR-4-12). The approval state attaches to the `study_run_id`. An un-approved study run remains explorable but must be visibly flagged "not yet fit for assumption-setting." This extends formal approval beyond the TEV assumption set to the A/E side. Because a study run has no ΔTEV, the materiality shortcut of FR-4-16 does not apply to it: an A/E study-run approval always runs the **full** configured chain.

**FR-4-15 (attestation / e-signature)**: Each sign-off must record: actor (session identity, FR-4-03), role, chain level, timestamp, decision (`APPROVE`/`RETURN`), comment, and an explicit attestation statement (configurable text). On final approval, the artifact must lock and become immutable (consistent with FR-2-44).

**FR-4-16 (materiality-driven required level)**: For an **assumption-set** approval, a single configurable governance materiality threshold in `governance_config.yaml` must determine the minimum required **final** sign-off level, applied to the **ΔTEV versus the prior approved set** — the quantity already computed and recorded per FR-2-46. A change whose `|ΔTEV|` exceeds the threshold must require `chief_actuary` sign-off; at or below it, the chain may complete at a configurable lower level. *(Note: this is a new governance threshold. The tool has no pre-existing reusable ΔTEV materiality floor — the only "materiality floor" in the spec is the §6.8 envelope-width floor, which serves a different purpose and is not reused here.)*

**FR-4-17 (pending-approvals queue)**: Each authenticated user must see a "my pending approvals" list — artifacts (assumption sets and study runs) awaiting sign-off at the level their role occupies. There is no time-based escalation and no overdue notification (out of scope, §8.1).

**FR-4-18 (governed re-open / supersede)**: An APPROVED (locked) artifact must never be mutated. "Re-opening" must instead create a new child version (FR-4-07) in `DRAFT` status, requiring a mandatory justification, leaving the original immutable. Adoption of the new version follows the normal chain and triggers supersession (FR-4-08).

### 8.5 Immutable Audit Trail (Lighter Build)

**FR-4-19 (A/E governance events)**: Governance-relevant A/E actions — study-run submission for approval, sign-off, return, and DQ overrides (extending NFR-A-02) — must be recorded using the **existing per-module logging pattern**. The three governance logs (Phase-2 workflow/approvals, Phase-3 AI audit, and the A/E governance events) **remain physically separate**; no canonical-table migration is performed.

**FR-4-20 (append-only + tamper-evidence)**: All governance-log writes must be append-only, with **no update or delete path in application code**. Entries must be hash-chained — each entry stores a hash of its own content plus the prior entry's hash — extending the Phase-3 AI-audit hashing approach to the A/E and TEV governance events.

**FR-4-21 (integrity verification)**: The system must provide a verification routine that recomputes the hash chain for any governance log and reports the first divergence. It must **pass on an untouched log and fail on a constructed tampered entry** (falsifiable acceptance test).

**FR-4-22 (unified audit read layer)**: A single "Governance & Audit" inspection page must query across all three logs and present a unified, filterable event stream (by actor, role, artifact, date, action) plus a per-artifact history timeline. This delivers a unified *view* without unifying storage (the §8.1 simplicity principle applied to Decision 3).

### 8.6 Governance Reporting & Compliance

**FR-4-23 (governance dashboard)**: A dashboard must show every assumption set and every submitted study run by state (`DRAFT`/`PROPOSED`/`APPROVED`/`SUPERSEDED`), the current "live" set per lineage, all pending approvals, and recent governance activity — the "clear what is going on" surface mandated by the simplicity principle.

**FR-4-24 (compliance pack export)**: For any APPROVED artifact (an assumption set, or an A/E study run approved per FR-4-14), the system must export a governance/compliance pack (HTML/PDF via the existing Jinja2 machinery with `autoescape=True`) assembling: the full version lineage (for an assumption set), all sign-offs with attestations, the relevant audit excerpt, the per-change rationale, and links to the supporting TEV/A/E reports — as one self-contained, defensible artifact (supports ASOP 41 documentation expectations).

**FR-4-25 (retention & immutability policy)**: The system must perform **no hard deletes** of governance records. Superseded and archived artifacts must be retained and marked as such. The retention policy must be stated in `governance_config.yaml`.

### 8.7 Multi-Tenancy Readiness (Lens Only — No Build)

**FR-4-26 (additive-retrofit shaping)**: The new governance tables (`users`, roles, version-lineage fields, A/E governance events) must be shaped so that a future `tenant_id` column is a purely **additive** change. No design choice may assume a single global namespace in a way that would require a rewrite to introduce tenancy. **No** `tenant_id`, Row-Level Security, SSO, data residency, or entitlements are built in Phase 4.

**FR-4-27 (config-not-code conformance)**: All Phase-4 org-specific values (role names, chain, attestation text, retention policy, materiality pointer) must live in configuration, not code (reinforces NFR-Q-01). A conformance check must confirm that no Phase-4 module hard-codes single-org assumptions that would block a later multi-tenant retrofit.

### 8.8 Phase 4 Completion Checklist

- [ ] `users` registry holds the four roles; no self-service account/role management UI exists (FR-4-01)
- [ ] Login gate blocks all pages pre-auth; passwords stored only as salted hashes; no SSO/reset flow (FR-4-02)
- [ ] Every governance action records the session identity as actor; no free-text actor entry remains in the Phase-4 surfaces (FR-4-03)
- [ ] RBAC enforced server-side: a disallowed action invoked directly (bypassing the UI) is rejected and logged (FR-4-04)
- [ ] Segregation of duties: a user cannot approve their own proposal at any level; the constructed self-approval attempt is blocked (FR-4-05)
- [ ] Version lineage: parent→child links recorded; status transitions DRAFT→PROPOSED→APPROVED→SUPERSEDED behave as specified (FR-4-07/08)
- [ ] At most one APPROVED-current set per lineage; effective ranges within a lineage cannot overlap (constructed overlap is rejected) (FR-4-08/09)
- [ ] Cross-version comparison shows changed cells, ΔTEV, and rationale for any two versions in a lineage (FR-4-10)
- [ ] An APPROVED set traces to its source study run + AI model version + data snapshot (FR-4-11)
- [ ] Sign-off chain is read from `governance_config.yaml`; a single-`chief_actuary` chain reproduces the legacy Stage-4 single-reviewer sign-off; full Phase 1–3 regression green (FR-4-12; NFR-G-08)
- [ ] Sequential sign-off enforced; RETURN with mandatory comment resets to editable state (FR-4-13)
- [ ] An A/E study run can be approved "fit for assumption-setting" through the same chain; an un-approved run is flagged (FR-4-14)
- [ ] Each sign-off records actor, role, level, timestamp, decision, comment, attestation; final approval locks the artifact (FR-4-15)
- [ ] Governance materiality threshold (config) applied to ΔTEV vs the prior approved set forces chief-actuary sign-off above it; A/E study-run approvals always run the full chain (FR-4-16)
- [ ] "My pending approvals" lists the artifacts awaiting the user's level; no overdue/escalation machinery exists (FR-4-17)
- [ ] Re-opening an APPROVED set creates a new DRAFT child with justification and never mutates the original (FR-4-18)
- [ ] A/E governance events recorded on the existing pattern; the three logs remain physically separate (FR-4-19)
- [ ] Governance logs are append-only and hash-chained; no update/delete path exists in code (FR-4-20)
- [ ] Integrity verifier passes on an untouched log and fails on a constructed tampered entry (FR-4-21)
- [ ] Unified Governance & Audit page filters across all three logs and shows a per-artifact timeline (FR-4-22)
- [ ] Governance dashboard shows artifact states, the live set per lineage, pending approvals, and recent activity (FR-4-23)
- [ ] Compliance pack exports lineage + attestations + audit excerpt + rationale + report links for an APPROVED set (FR-4-24)
- [ ] No hard deletes of governance records; retention policy stated in config (FR-4-25)
- [ ] Tenancy-readiness conformance check passes: no hard-coded single-org blockers; no `tenant_id`/RLS/SSO built (FR-4-26/27)
- [ ] Full Phase 1–3 regression suite green; Phase 4 UAT executed end-to-end with retained evidence and sign-off recorded

---

## 9. Synthetic (Mockup) Database Specification

### 9.1 Product Mix and Policy Counts

| Product | Count | Rationale |
|---|---|---|
| Term Life (20-yr mix) | 3,200 | Largest policy-count block; includes PLT cohort |
| Whole Life | 2,800 | Large in-force; bimodal age distribution |
| Universal Life (800 Trad UL, 800 ULSG, 200 IUL) | 1,800 | ULSG segment is key test for shadow-account logic |
| Variable Universal Life | 800 | High-face, high-net-worth; separate account required |
| Deferred Annuity (900 fixed, 500 variable) | 1,400 | Surrender-charge expiry pattern; GLB moneyness |
| **Total** | **10,000** | |

### 9.2 Issue Date and Study Window

- **Study window:** 2016-01-01 through 2023-12-31 (8 calendar years, anniversary-to-anniversary basis)
- **Issue dates:** uniformly distributed from 2008-01-01 through 2023-06-30

### 9.3 Generation Parameters by Product

#### Term Life

| Parameter | Distribution / Value |
|---|---|
| Issue age (ANB) | PERT(18, 38, 75) |
| Face amount | Lognormal: μ=ln(250,000), σ=0.9 |
| Gender | 58% M / 42% F |
| Smoker | 8% SM |
| Risk class | 22% Super Pref / 28% Pref NS / 33% Std NS / 9% Pref SM / 8% Std SM |
| Level period | 55% T20 / 25% T10 / 15% T30 / 5% T15 |
| PLT structure | 65% Jump-to-ART / 35% Graded |
| Premium jump ratio | Lognormal mean 5× (Jump-to-ART), 2.5× (Graded) |
| CI rider penetration | 25% of Term policies carry CI rider |
| CI rider sum assured | 50% of base face amount (capped at face amount) |
| Base annual lapse | 8% yr1 / 5% yr2-3 / 4% yr4-5 / 3% yr6-10 / 3% yr11+ |
| PLT shock lapse | SOA 2021 table by premium jump ratio band |
| Annual mortality | 2015 VBT × class_factor × selection_factor |

**Class factors:** Super Pref=0.55, Pref NS=0.75, Std NS=1.00, Pref SM=2.00, Std SM=2.50  
**Selection factors:** dur 1-2=0.80, 3-5=0.90, 6-10=0.95, 11+=1.00; PLT persisting policyholders: ×(1 + anti-selection uplift)

#### Whole Life

| Parameter | Distribution / Value |
|---|---|
| Issue age | 60% from PERT(25, 42, 65); 40% from PERT(55, 72, 88) [final-expense] |
| Face amount | Lognormal μ=ln(120,000), σ=1.2; final-expense sub-block: $5K–$25K |
| CI rider penetration | 20% of WL policies (excluding small-face < $25K) |
| Annual lapse | 11% yr1 / 7% yr2 / 5% yr3 / 4% yr4 / 3% yr5 / 2.5% yr6-10 / 2% yr11+ |

#### Universal Life / ULSG

| Parameter | Distribution / Value |
|---|---|
| Issue age (Trad UL) | PERT(30, 48, 70) |
| Issue age (ULSG) | PERT(50, 62, 78) |
| CI rider penetration | 15% of UL policies (excluding ULSG) |
| Base lapse (Trad UL) | 8% yr1 / 6% yr2 / 5% yr3 / 4% yr4 / 3.5% yr5 / 3% yr6+ |
| Base lapse (ULSG) | 50% of Trad UL rates |
| Dynamic lapse | Applied per FR-1B-08 |

#### Variable Universal Life

| Parameter | Distribution / Value |
|---|---|
| Issue age | PERT(35, 47, 65) |
| CI rider penetration | 15% of VUL policies |
| Equity allocation | 50% high (≥75%) / 30% balanced / 20% conservative |
| Fund returns | GBM: μ=7%, σ=15% annually; regime 2 (years 6-8): μ=5%, σ=20% |
| Base lapse | 6% yr1 / 4% yr2 / 3% yr3 / 2.5% yr4-5 / 2% yr6+ |

#### Deferred Annuities

| Parameter | Distribution / Value |
|---|---|
| Owner age | PERT(45, 62, 80) |
| No CI rider | Annuities do not carry CI riders |
| Surrender charge | 70% 7-year / 30% 10-year |
| Base surrender curve | 1.5% yr1-5 / 3% yr6 / **60% yr7** (shock) / 12% yr8+ |

### 9.4 Critical Illness Rider Specification

**FR-8-01**: CI claim events must be generated with the following **10 illness codes** and approximate incidence distributions (industry-calibrated for illustrative purposes):

| Illness Code | Illness Name | Approximate % of CI Claims |
|---|---|---|
| CI-001 | Malignant cancer (any) | 40% |
| CI-002 | Myocardial infarction (heart attack) | 20% |
| CI-003 | Stroke | 12% |
| CI-004 | Coronary artery bypass surgery | 7% |
| CI-005 | Kidney failure (end-stage renal disease) | 5% |
| CI-006 | Major organ transplant | 4% |
| CI-007 | Multiple sclerosis | 3% |
| CI-008 | Paralysis / paraplegia | 3% |
| CI-009 | Blindness (permanent and irreversible) | 3% |
| CI-010 | Deafness (permanent and irreversible) | 3% |

**FR-8-02**: The CI incidence reference table (expected basis) must be a Parquet file keyed by `(illness_code, gender, attained_age_band)` with incidence rates per 1,000 exposed. For the prototype, these are simplified calibrated approximations based on published CI incidence studies (UK CII, HK HKIA CI tables). The table must be configurable — any table conforming to the same schema can be substituted.

**FR-8-03**: In the synthetic data, CI claims are generated as a Bernoulli draw per policy per year, using: `P(CI claim) = ci_incidence_rate(illness_code, gender, age) × illness_distribution_weight`. A CI claim event generates: `termination_cause_code = CI_ACCELERATED_BENEFIT` and reduces the base face amount by the CI rider sum assured, potentially to zero (at which point the policy terminates if the base face amount is exhausted).

### 9.5 Macro Scenario Time Series

| Study Year | Calendar Year | Regime | 10-yr Market Rate | Avg Credited Rate | Rate Differential | Equity Return | Unemployment |
|---|---|---|---|---|---|---|---|
| 1 | 2016 | Low-rate | 1.8% | 3.2% | −1.4% | +12% | 4.7% |
| 2 | 2017 | Low-rate | 2.4% | 3.2% | −0.8% | +22% | 4.1% |
| 3 | 2018 | Low-rate | 2.9% | 3.2% | −0.3% | −5% | 3.9% |
| 4 | 2019 | Low-rate | 1.9% | 3.1% | −1.2% | +31% | 3.5% |
| 5 | 2020 | Low-rate/shock | 0.9% | 3.0% | −2.1% | +18% | 8.1% |
| 6 | 2021 | Rising | 1.5% | 2.9% | −1.4% | +29% | 5.4% |
| 7 | 2022 | Rising/stress | 3.9% | 2.9% | +1.0% | −18% | 3.6% |
| 8 | 2023 | Rising/stress | 4.0% | 3.1% | +0.9% | +26% | 3.7% |

### 9.6 Expected A/E Ranges for Validation

| Metric | Expected Basis | Expected A/E Range |
|---|---|---|
| Term mortality (count) | 2015 VBT | 0.85 – 1.00 |
| Term mortality (amount) | 2015 VBT | 0.80 – 0.95 |
| Term base lapse | SOA/LIMRA 2015-22 | 0.95 – 1.05 |
| Term PLT shock lapse | SOA 2021 PLT (by jump band) | 0.90 – 1.10 |
| WL lapse/surrender | SOA/LIMRA 2015-22 | 0.90 – 1.05 |
| UL lapse (Trad) | SOA/LIMRA 2015-21 UL | 0.85 – 1.10 |
| ULSG lapse | SOA/LIMRA 2015-21 ULSG | 0.80 – 1.05 |
| FRDA surrender (base years) | SOA/LIMRA 2015-22 FRDA | 0.90 – 1.10 |
| FRDA surrender-charge expiry year | FRDA shock expectation | 0.85 – 1.15 |
| Annuity owner mortality | 2012 IAR + G2 | 0.88 – 1.05 |
| CI incidence (aggregate) | CI incidence reference table | 0.90 – 1.10 |

---

## 10. Non-Functional Requirements

### 10.1 Performance

| Requirement | Target |
|---|---|
| NFR-P-01: Full 5-product, 8-year study run (pipeline + exposure + A/E) | < 60 seconds |
| NFR-P-02: Single-product study run | < 15 seconds |
| NFR-P-03: Full TEV run (baseline + 11 sensitivities, all products) | < 30 seconds |
| NFR-P-04: Credibility envelope analyser (two L-BFGS-B runs, 200 evals each) | < 120 seconds |
| NFR-P-05: Model fitting time | Full Phase 3a fit (all decrements × products, incl. bootstrap at defaults) < 15 minutes |
| NFR-P-06: Chatbot overhead | Non-LLM pipeline overhead (routing→gates→execution→post-check) < 2 s per turn |
| NFR-P-05: UI page load time (any Streamlit page) | < 3 seconds after run completes |
| NFR-P-06: TEV-impact matrix rendering | < 2 seconds |

### 10.2 Correctness

| Requirement | Target |
|---|---|
| NFR-C-01: In-force reconciliation | ±0.01% by count and amount |
| NFR-C-02: A/E ratios on clean data | Within expected ranges per Section 9.6 |
| NFR-C-03: Credibility Z scores | Correct to 4 decimal places vs manual calculation |
| NFR-C-04: Confidence intervals | Correct Poisson CI to 2 decimal places |
| NFR-C-05: TEV model point reconciliation | ±0.1% on count, face amount, and reserve |
| NFR-C-06: PVFP discounting | Matches manual sample cell calculation to 4 decimal places |
| NFR-C-07: Sensitivity directionality | Lower lapse rate produces higher TEV for lapse-supported products (e.g., ULSG); lower lapse rate reduces TEV for pure protection products; these directional tests must pass |
| NFR-C-08: Envelope bounds | All θ vectors returned by the envelope analyser (θ_min and θ_max) must be within credibility bounds; TEV_min ≤ TEV_proposed ≤ TEV_max must hold |
| NFR-C-09: Reproducibility | Identical results on identical re-run (same run_id) |

### 10.3 Code Quality

| Requirement | Target |
|---|---|
| NFR-Q-01: No hardcoded product logic | All product rules in YAML config |
| NFR-Q-02: Unit test coverage | ≥ 80% on calculation engine, exposure, DQ, TEV engine |
| NFR-Q-03: Integration test | Full pipeline smoke test per product; full TEV run smoke test |
| NFR-Q-04: Code style | PEP 8; docstrings on all public functions |

### 10.4 Configurability

| Requirement | Target |
|---|---|
| NFR-CF-01: Study period | Configurable in study_config.yaml |
| NFR-CF-02: Mortality reference table | Swappable by config pointer — any jurisdiction's table accepted |
| NFR-CF-03: CI incidence reference table | Swappable by config pointer |
| NFR-CF-04: Exposure method | Annual vs Distributed selectable |
| NFR-CF-05: Credibility threshold | Configurable |
| NFR-CF-06: Dynamic lapse parameters | All k values, caps, floors configurable |
| NFR-CF-07: TEV economic parameters | RDR, earned rates, RC%, tax rate all in tev_config.yaml |
| NFR-CF-08: Sensitivity shocks | All shock magnitudes configurable in tev_config.yaml |
| NFR-CF-09: Envelope analyser max evaluations per run | Configurable (default 200, applied independently to TEV_min and TEV_max runs) |
| NFR-CF-10: AI configurability | Every Phase 3 threshold, grain, cap, and seed in YAML; none hardcoded |
| NFR-CF-11: Model strings | Provider model identifiers in `llm_config.yaml` only; never in code (FR-3B-03) |

### 10.5 Auditability (Baseline — Full Governance in Phase 4)

| Requirement | Target |
|---|---|
| NFR-A-01: Study run log | run_id, timestamp, product scope, config hash, data hash, code version, duration, status |
| NFR-A-02: DQ override log | policy_id, check_id, timestamp, justification |
| NFR-A-03: TEV run log | tev_run_id, assumption_set_id, model_point_hash, config_hash, timestamp, all component TEV values |
| NFR-A-04: Workflow iteration log | iteration number, assumption_set_version, TEV baseline, ΔTEV, actuary comment, timestamp |
| NFR-A-05: Assumption approval log | All fields from FR-2-46 |
| NFR-A-06: Envelope analyser run log | Whether envelope was computed, inputs (top-5 decrements, bounds), output (TEV_min, TEV_max, θ_min, θ_max, percentile of proposed, envelope width), convergence metadata for each run |
| NFR-A-07: AI audit log | Fields per FR-3B-14/47; append-only; queryable from Study Run Log page |
| NFR-A-08: AI model registry | `ai_model_registry` per FR-3A-24 with full reproducibility stamp |

### 10.6 Testability & Resource Management (Phase 3)

| Requirement | Target |
|---|---|
| NFR-T-01: Artifact containment | All test-generated files under `tests/_artifacts/` (gitignored); tests never write to `data/` |
| NFR-T-02: Fixture economy | Session-scoped fixtures; one shared synthetic DB per suite run, no per-test copies |
| NFR-T-03: Teardown | Artifacts deleted on suite success; `--keep-artifacts` flag retains for debugging |
| NFR-T-04: Size guard | Suite-end check on `tests/_artifacts/`; configurable cap (default 5 GB); breach fails the suite |
| NFR-T-05: No resample persistence | Bootstrap resample arrays never written to disk (FR-3A-22) |
| NFR-T-06: Offline suite | Full pytest suite passes with no API keys in the environment (FR-3B-06) |
| NFR-T-07: Cleanup | One-line cleanup command documented in README |

### 10.7 LLM Cost & Runtime Controls (Phase 3)

| Requirement | Target |
|---|---|
| NFR-L-01: Session budget | Token budget per chatbot session, YAML (default 1,000,000); warn at 80%, stop at 100% |
| NFR-L-02: Cost visibility | Running token count and cost estimate displayed in UI; per-call costs logged |
| NFR-L-03: Resilience | Request timeout (default 60 s) and retry (default 2) per YAML; failures never crash the session |
| NFR-L-04: Eval cost gate | Harness shows cost estimate up front; interactive confirmation above YAML threshold |

### 10.8 Governance & Access (Phase 4)

| Requirement | Target |
|---|---|
| NFR-G-01: Authentication | No application page or action reachable pre-login; passwords stored only as salted hashes; no SSO/reset flow (FR-4-02) |
| NFR-G-02: Authorisation | RBAC enforced server-side; verified by invoking a disallowed action directly (bypassing the UI) and confirming rejection + log entry (FR-4-04) |
| NFR-G-03: Segregation of duties | Proposer ≠ approver enforced absolutely at every level; constructed self-approval attempt blocked by test (FR-4-05) |
| NFR-G-04: Audit immutability | Governance logs append-only and hash-chained; integrity verifier passes on an untouched log and fails on a constructed tampered entry (FR-4-20/21) |
| NFR-G-05: Effective-date integrity | At most one APPROVED-current set per lineage; effective ranges within a lineage non-overlapping (FR-4-08/09) |
| NFR-G-06: Tenancy readiness | Conformance check passes — no hard-coded single-org blockers; no `tenant_id`/RLS/SSO built (FR-4-26/27) |
| NFR-G-07: Governance performance | Governance/dashboard pages load < 3 s after a run completes; compliance pack renders < 5 s |
| NFR-G-08: Backwards compatibility | Full Phase 1–3 regression suite green (calculation, exposure, A/E, TEV, AI behaviour unchanged); the configurable chain, set to a single `chief_actuary` level, reproduces the legacy Phase-2 single-reviewer Stage-4 sign-off (FR-4-12) |

---

## 11. Skills and MCP Decisions

### 11.1 MVP (Phases 1A–1C) and Phase 2 (TEV): No Claude Skills or MCPs

Both the MVP and the TEV module are deterministic calculation tools. Claude Code builds them; Claude does not run inside them. No Skills or MCPs are needed for Phases 1A–1C or Phase 2.

### 11.2 Phase 3 — Specified in Section 7

Phase 3 Skills and the MCP server are now fully specified in §7.8 (MCP server `experience_study_data`) and §7.9 (Skills `interpret_ae_and_draft_memo`, `explain_shap_results`). The outline previously held in this subsection is superseded; §7.8–7.9 are authoritative.

### 11.3 Phase 4: No Additional Claude Skills or MCPs

Phase 4 governance is implemented as application code. No new Skills or MCPs required.

---

## 12. Open Questions for Project Owner

**Resolved from v2.1** (dispositions recorded for the audit trail):

1. Reference tables — resolved: Gompertz-Makeham approximations calibrated to VBT parameters (implemented Phases 1A–1C).
2. UI framework — resolved: Streamlit (implemented).
3. IUL crediting — resolved: simplified credited-rate proxy retained.
4. Lapse benchmarks — resolved: calibrated approximations.
5. TEV statutory reserves — resolved: simplified proxy reserve formulas (FR-2-14) retained.
6. Phase 3 timing — resolved: Phase 2 UAT signed off 2026-06; Phase 3 approved to begin.

**Resolved for Phase 3** (Phase 3 built and UAT-closed 2026-06-28; dispositions in `docs/phase3_build_progress.md`):

1. DeepSeek V4 GA pricing — resolved during the Session 18 build; `llm_config.yaml` price fields set.
2. Golden-set authorship — resolved: golden Q→SQL and adversarial sets drafted and locked at Session 22.
3. Reference hardware for NFR-P-05/06 — resolved: targets measured on the Phase 1–2 UAT machine.

**Resolved for Phase 4** (scoping, 2026-06-28; anchored on `phase4_locked_scope.md`):

1. Login mechanism — minimal username + password gate backed by the `users` table; no SSO, no reset flows (FR-4-02). A bare user-picker was rejected (it would hollow out attribution and segregation of duties).
2. A/E approval object — approval attaches to a **study run**, signed off as "fit for assumption-setting" (FR-4-14).
3. Role list & chain — four roles (analyst, junior_actuary, senior_actuary, chief_actuary); chain junior → senior → chief with analyst as proposer; peer-reviewer and auditor/read-only roles dropped (FR-4-01/12).
4. Materiality threshold — a single configurable governance threshold in `governance_config.yaml`, applied to ΔTEV vs the prior approved set (FR-4-16). *(Reader-test correction, 2026-06-28: the original "reuse the existing TEV ΔTEV materiality floor" decision was based on a floor that does not exist — the only pre-existing "materiality floor" is the §6.8 envelope-width floor, a different concept. A new governance threshold is therefore defined, preserving the intent: ΔTEV-based, single source of truth, configurable. Owner confirmed 2026-06-28.)*
5. Effective-dating — simple `effective_from`/`effective_to` date range per approved set (FR-4-09).
6. Distinct approvers per level — confirmed: each sign-off level must be a distinct user (default `allow_multi_level_signoff: false`); proposer ≠ approver remains absolute (FR-4-05).

**Open for Phase 4:** None — all Phase 4 scoping questions resolved (2026-06-28). Remaining detail (exact schemas, interface contracts) is deferred to Technical Spec v3.0.

---

*End of Requirements Specification v4.0 — Locked (Phase 4 Governance added; reader-tested, QA cross-checked, owner-signed-off 2026-06-28)*