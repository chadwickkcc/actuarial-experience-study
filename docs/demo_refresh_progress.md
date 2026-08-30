# Demo Refresh — Build Progress & Handoff Log

**Purpose:** single source of truth for demo-refresh status so any fresh session can resume.
Read together with `demo_refresh_scope.md` (decision authority) and
`demo_refresh_prompts.md` (per-phase session blocks).

**Resume protocol for a fresh session:** read CLAUDE.md → this status board → the next phase's
block in `demo_refresh_prompts.md` → `demo_refresh_scope.md` for the design locks it cites.
One phase per session; gate green before proceeding; commit per phase.

**Gate:** `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`

---

## Status board

| Phase | Title | Size | Status |
|-------|-------|------|--------|
| P0 | Driver docs + baseline verification | S | ✅ COMPLETE (2026-08-30) |
| P1 | Groundwork: re-home lifecycle, cut monitors, recon fix | M | ✅ COMPLETE (2026-08-30) |
| P2 | Assumption workflow v2 + materiality + legacy retirement | L | ✅ COMPLETE (2026-08-30) |
| P3 | TEV purge | L | ✅ COMPLETE (2026-08-30) — eval re-lock pending owner |
| P4 | Data expansion + regeneration | L | ✅ COMPLETE (2026-08-30) |
| P5 | Fraud module | L | ✅ COMPLETE (2026-08-30) |
| P6 | Management commentary | L | ✅ COMPLETE (2026-08-30) |
| P7 | Slickness pass | M | ✅ COMPLETE (2026-08-30) |
| P8 | Docs, demo script, UAT | M | ✅ BUILD COMPLETE (2026-08-30) — owner dry-run + sign-off pending |

**Test baseline history:**
| Point | Suite |
|---|---|
| Pre-refresh (P0 baseline) | 1368 passed, 6 skipped |
| After P1 | 1369 passed, 6 skipped (+1 recon-scope test) |
| After P2 | 1356 passed, 15 skipped, 0 failed (net −13: legacy approvals/envelope-summary tests retired, +9 new materiality/guard tests; +9 skips are TEV tests skipping on the fresh no-TEV-runs DB — deleted in P3 — plus 1 empty-assumption-sets lifecycle-UI skip) |
| After P3 | 1212 passed, 7 skipped, 0 failed (−~150 TEV tests deleted; +2 TEV-absence standing guards; skips = 6 pre-existing baseline + 1 empty-assumption-sets lifecycle-UI) |
| After P4 | 1217 passed, 7 skipped, 0 failed (+5 story-lock tests; ~30 volume/band/pinned-value tests made config-/live-driven) |
| After P5 | 1234 passed, 7 skipped, 0 failed (+17 fraud tests) |
| After P6 | 1253 passed, 7 skipped, 0 failed (+19 commentary tests) |
| After P7 | 1258 passed, 7 skipped, 0 failed (+5 UI guards; +1 home test updated) |
| After P8 (FINAL) | **1259 passed, 6 skipped, 0 failed** (lifecycle-UI skip cleared by the seeded workflow; the 6 skips are the original pre-refresh baseline skips) |

**Owner checkpoints:** P3 eval re-lock (**REQUESTED 2026-08-30** — golden 36→30
[G027–G032 removed], adversarial A007 retargeted to `gold_ai_proposed_factors`;
headers record "RE-LOCK PENDING") · P8 UAT sign-off (pending).

---

## Environment notes

- Interpreter: uv-managed `.venv`, Python 3.12. Run everything via `.venv/bin/python`.
- Live demo DB: `data/experience_study.duckdb` (~94 MB pre-refresh; rebuilt fresh at every
  DDL/generator phase per the live-DB rebuild rule — reset → `scripts/_uat_rerun.py` →
  `scripts/_uat_ai_fit.py`).
- Git: repo on `main`; one commit per phase.

---

## P0 — Driver docs — COMPLETE (2026-08-30)

- Approved plan captured from the planning session (owner approved 2026-08-30); decisions D1–D4
  recorded in `demo_refresh_scope.md` §2.
- Authored `demo_refresh_scope.md`, `demo_refresh_prompts.md`, this progress doc.
- CLAUDE.md updated: demo-refresh section + supersession pointers (rules 1–2 defer to the scope
  doc for changed schemas/interfaces).
- Baseline gate verified: **1368 passed, 6 skipped** (no API keys).

**Next session: P1** (see `demo_refresh_prompts.md` → P1).

---

## P1 — Groundwork — COMPLETE (2026-08-30)

**Re-home (verbatim moves, no logic change):**
- `src/tev/assumption_set.py` → `src/assumptions/assumption_set.py`;
  `src/tev/workflow.py` → `src/assumptions/workflow.py` (git mv, history preserved).
- `_deep_copy_assumption_set` moved out of `src/tev/sensitivities.py` into
  `src/assumptions/assumption_set.py` as public `deep_copy_assumption_set`;
  `sensitivities.py` + `ui/ai_comparison_logic.py` import it (alias keeps call sites).
- All importers rewritten (`src/governance/{lineage,workflow}.py`, 4 stage pages,
  `ui/ai_comparison_logic.py`, remaining TEV engine modules, scripts, ~25 test files).
  Path-string references in docstrings/tests fixed too. Zero `src.tev.assumption_set` /
  `src.tev.workflow` references remain.
- `src/tev/` now holds ONLY the pure TEV engine (tev_core, model_points, sensitivities,
  impact_matrix, envelope, products/) — P3 deletes the directory wholesale.

**Page cuts (owner decision D1):**
- Deleted `ui/views/{08_ul_account_value,09_ulsg_shadow_account,10_annuity_surrender,`
  `11_glb_utilisation,12_vul_fund_value,14_ci_incidence_summary}.py`.
- `ui/app.py`: "3 · Product Monitors" nav group + the CI-summary entry removed.
- `ui/views/00_home.py`: Product Monitors graph node/edge + link cards removed
  (full home rework is P7).
- 14's one unique piece — the cross-product "CI A/E by Product" bar (credibility colouring +
  Poisson error bars) — merged into `06_ci_explorer.py` as a new section after the summary
  metrics (`_query_ci_by_product`); VUL added to the product filter. Everything else on 14
  duplicated 06 and was dropped.
- No tests referenced the deleted pages (verified by grep).

**Recon fix:** `src/exposure/engine.py::_run_reconciliation` now deletes by
`(study_run_id, product_code)` — previously each product wiped all others' rows so only the
last product survived. Locked by new `tests/test_recon_scope.py` (per-product rows survive;
re-run replaces only that product).

**DoD:** gate green **1369 passed, 6 skipped** (+1, no regressions); live Streamlit boot
smoke HTTP 200, no errors; monitors gone from nav.

**Next session: P2** (see `demo_refresh_prompts.md` → P2).

---

## P2 — Assumption workflow v2 — COMPLETE (2026-08-30)

**3-step IA (pages renamed via git mv):**
- `20_tev_stage1` → `ui/views/20_assumption_step1.py` — TEV prose out; 3-step progress
  strip; session keys `stage3_approved`/`s3_envelope_*` retired. (Still passes
  `tev_config.yaml` to the constructor — dies in P3.)
- `21_tev_stage2` → `ui/views/21_assumption_step2.py` — editor kept verbatim
  (3 multiplier tabs, `_validate_bounds` credibility hard-block, Restore-from-A/E,
  Adopt-AI-proposal, mandatory comment, SAVED logging); **Economic Parameters tab +
  ΔTEV sidebar deleted**; **Submit-for-sign-off extracted from old Stage 3**
  (PROPOSED → STAGE3_APPROVED, mandatory comment, SUBMITTED_S4 logging, status-gated)
  + iteration-history expander.
- `23_tev_stage4` → `ui/views/22_assumption_step3.py` — multi-level chain UI + AI memo
  kept; TEV metrics/TEV Impact Report/`delta_frac`/`legacy_ctx` deleted; set picker now
  lists STAGE3_APPROVED/APPROVED sets (session-state URL-gate replaced by the status
  gate); materiality metric displayed; sign-off history reads `gold_governance_signoffs`.
- `ui/views/22_tev_stage3.py` **deleted** (all TEV analysis; its only lifecycle piece —
  submit/refine — re-homed to Step 2). Nav group → "5 · Assumption Setting" (3 pages);
  home links updated.

**Materiality (FR-4-16 new basis):**
- `VersionDiff.delta_tev` → `materiality_value` = max |Δ multiplier| over changed cells
  (added/removed cell measured vs neutral 1.0); computed in `lineage.compare_versions`
  (its `_baseline_tev` helper deleted).
- New `governance/workflow.materiality_vs_prior_approved(set_id)` walks `parent_set_id`
  to the nearest APPROVED/SUPERSEDED ancestor → `compare_versions(...).materiality_value`;
  None (no approved ancestor) → full chain.
- `required_final_level(materiality_value, cfg)` keys off
  `materiality.max_multiplier_delta_threshold` (governance_config.yaml, default 0.05);
  `record_signoff(..., materiality_value=None)` **auto-computes** it for assumption sets
  when not supplied (tests may still pass it explicitly).
- Column rename `gold_governance_signoffs.delta_tev` → `materiality_value` (DDL +
  `audit._SIGNOFF_COLUMNS`, append/verify symmetric); `readiness.py` config assert,
  page 29 compare metric, compliance-pack `_signoff_rows` + template column updated.

**Legacy retirement:**
- `gold_assumption_approvals` dropped (DDL, migrations, `_VERIFIABLE_CHAINS`,
  `_ASSUMPTION_APPROVAL_COLUMNS`, unified-audit APPROVAL source, page-26 verify list,
  reset script). `_write_legacy_summary` + `record_governance_approval` deleted;
  `gold_governance_signoffs` is the sole formal record.
- `gold_workflow_iterations` TEV columns dropped (DDL + `log_workflow_iteration` +
  `get_workflow_iterations` + `_WORKFLOW_ITER_COLUMNS`).

**Tests:** `tests/test_workflow.py` rewritten (16, incl. retired-table guard + grep guard);
`tests/governance/test_workflow.py` → materiality basis (+4 new: materiality-from-diff,
auto-compute immaterial-completes-at-senior / material-requires-chief) = 38;
`test_identity_capture.py` rewritten for new pages; `test_propose_gate.py` → Step-1/2
labels + AppTests; lineage compare tests → materiality (+added-cell-from-neutral case);
audit-integrity/reporting/segregation/locked-set updated (kwarg + config key + retired
table). `test_assumption_comparison_apptest.py` memo-wiring ref → step 3 page.

**Live-DB rebuild (rule applied; DDL changed):** old DB backed up to scratchpad; fresh
`init_database` (29 tables — approvals gone, signoffs carry `materiality_value`) +
`scripts/_uat_rerun.py` (run `2e1c949b…`, COMPLETE, 4.5s, deaths=456, recon PASS —
**per-product recon rows now visible live: 6 products × 8 years**) +
`scripts/_uat_ai_fit.py` (8 registry rows, 146 proposed factors).
`scripts/uat_section*.py` needed no changes (study-run sign-offs only).

**DoD:** gate green **1356 passed, 15 skipped, 0 failed**; boot smoke HTTP 200.
Full lifecycle now demoable TEV-free: create → edit → submit → 3-level sign-off
(materiality-driven) → lock → lineage publish → compliance pack (Materiality column).

**Next session: P3** (see `demo_refresh_prompts.md` → P3). Note for P3: the +9 skips
(envelope ×7, MCP-TEV realdata ×1) disappear with the TEV test deletions; the
lifecycle-UI skip clears once a demo assumption set exists again (P4/P8 flows).

---

## P3 — TEV purge — COMPLETE (2026-08-30; eval re-lock pending owner)

**Deleted:** `src/tev/` (engine + products); `config/tev_config.yaml`;
`Reference Materials/tev-methodology-report.md` (RAG source); stale
`data/assumption_sets/*.yaml`; `scripts/_uat_tev_baseline.py` +
`migrate_envelope_schema.py`; tests `test_tev_engine`(52)/`test_envelope`(27)/
`test_model_points`(66)/`test_whatif_tev`(2) + 2 stress scripts; the TEV half of
`src/reporting/generator.py` (~770 lines, 14 helpers + 2 report generators); the
5 TEV dataclasses in `src/utils/types.py` (`ModelPointResult`, `TEVProductResult`,
`TEVRunResult`, `SensitivityGridResult`, `EnvelopeResult`).

**Assumption set slimmed:** the 9 economic scalars (`rdr`, earned rates, tax,
expense inflation, `rc_pct_reserve`, acquisition/maintenance expenses) removed from
the `AssumptionSet` dataclass, YAML serialisation, `create_assumption_set_from_ae_run`
(which no longer takes `tev_config_path`), `deep_copy_assumption_set`, the DB
INSERT, and the `gold_assumption_sets` DDL (5 columns dropped).
`lineage.create_version` lost `tev_config_path`.

**DDL:** `gold_model_points`, `gold_tev_run_log`, `gold_tev_results` (+indexes)
dropped; DDL list renamed `_GOLD_ASSUMPTION_DDL`. 26 tables total.

**AI surface:** allowlist loses `gold_tev_results` (19 cols) + `gold_model_points`;
MCP tools 6 → 4 (`query_ae_results`, `query_results`, `list_available_dimensions`,
`get_study_run_summary`), `TOOL_SCHEMA_VERSION` → **"3.0"**; TEV branch out of
`execute_via_mcp` + digest + refusal text; `ui/skills_logic` fact packs lose
`tev_baseline`/`delta_tev_vs_prior` (+`_tev_baseline` helper, `whatif_delta_tev`
param); memo → **7 components** (`memo.md` v2.0, "TEV Impact" removed);
`sql_generation.md` v2.0 / `synthesis_plan.md` v2.0 / `routing.md` v2.0 /
`commentary.md` v3.0 (TEV cards/wording out); few-shots 43 → **35**; page-15
what-if section + `run_whatif_tev`/`_prior_total_tev`/`build_whatif_assumption_set`
deleted (the last was app-dead after the section went).

**Eval sets (owner re-lock PENDING):** golden 36 → **30** (G027–G032 removed);
adversarial A007 retargeted (`DELETE FROM gold_ai_proposed_factors`); both headers
record the change + pending re-lock. Harness untouched; `--help`/smoke wired.

**Compliance pack:** `_supporting_reports` loses the TEV-impact link (A/E reports only).

**Tests:** ~150 TEV tests deleted; ~25 files edited (economic-kwarg fixture sweep,
MCP 4-tool surface + schema "3.0", StubMCP `tev=` → generic `extra=`/`query_results`,
digest fixture (digest-only figure now the aggregate credibility Z), retargeted
single-table-routing/UNION/negative-number tests onto `gold_ai_proposed_factors` /
`gold_inforce_reconciliation` (layered-defence coverage preserved, not deleted),
boundary allowlist-shape asserts TEV tables ABSENT, golden-coverage test asserts
`gold_tev_results` absent). +2 standing guards in `test_ai_architecture.py`
(`src/tev` stays deleted; no source/config references retired TEV artifacts).

**Live-DB rebuild:** fresh init (26 tables) + `_uat_rerun.py` (run `a79e520b…`,
COMPLETE, 4.5s) + `_uat_ai_fit.py` (8 models, 146 proposed factors).

**DoD:** gate green **1212 passed, 7 skipped, 0 failed**; eval CLI smoke wired;
Streamlit boot HTTP 200; AI Analyst on 4 MCP tools.

**Next session: P4** (see `demo_refresh_prompts.md` → P4).

---

## P4 — Data expansion + regeneration — COMPLETE (2026-08-30)

**Config-driven generation:** new `config/synthetic_data.yaml` (volumes, CI penetration +
incidence multiplier, story multipliers, entities, fraud-ring parameters) read by
`generators/common.py::GEN_CONFIG` at import. Volumes → **25,000 policies**
(TERM 8000, WL 7000, UL 2000 + ULSG 2000 + IUL 500, VUL 2000, DA 2200+1300). Seed 42
unchanged; `generate_all.py` validation asserts made config-driven.

**New identity fields (all 5 generators → CSV → bronze DDL → `field_mappings` → silver
DDL):** `agency_office_id` (OFF-001…040), `agent_id` (AGT-<office><1-8>), and on claim rows
(DEATH / CI_CLAIM): `claimant_id`, `hospital_id` (HOSP-001…060), `claim_region`
(state → 8-region map in `common.py`). `assign_claim_fields` stamps them post-generation.

**Planted stories (draw-side only, via `common.mortality_story_multiplier` /
`lapse_story_multiplier`):** verified in gold on run `b23edb78…`:
- Term+WL mortality A/E by year: 2020 0.603 → 2021 0.612 → 2022 0.754 → **2023 0.880**
  (strict rise, +0.27 over the window).
- Term+UL-family lapse A/E: 2016–21 ≈ 0.85–1.31, **2022 1.19, 2023 1.88** (spike).
- CI: **589 claims across all 10 illness codes**, aggregate CI A/E 1.23 (the ×3.5
  incidence multiplier applies to BOTH draws and the `ci_incidence` reference table, so
  it raises volume, not the ratio).

**Fraud ring (planted via `common.plant_fraud_ring`, parameters in config):** 14 Term
policies, issued 2021–23, office **OFF-013**, ghost hospital **HOSP-066** (outside the
60-facility roster), all CI-001 claims in policy year 1 (55–283 days), NV/SOUTHWEST,
faces 115–126k; 4 share claimant **CLM-424242**. Office first-policy-year claim counts:
OFF-013 = 15 vs median 1 (the P5 rule-4 signal).

**Design deviations from the scope doc (recorded there in-place):** CI claims stayed
**terminal** — the boost alone hits the volume target and same-claimant similarity spans
policies, so the non-terminal rework (exposure/A-E ripple) was dropped. Fraud rule 4 is
defined on **first-policy-year** claim concentration (total-claim share dilutes).

**Tests:** +5 permanent story-lock tests (`tests/test_planted_stories.py`: strict
mortality rise, lapse spike ≥115% of the 2016–21 mean, CI ≥250/≥8 codes, ring
present incl. the ≥3×-median office check, identity fields in all 5 silver tables).
~30 reshuffle-broken tests fixed: volume asserts now import generator constants,
acceptance A/E bands widened for the planted-story data (documented), the UL
dynamic-lapse acceptance test pinned to `policy_year = 3` (holds the duration mix
constant), the 3 chatbot realdata tests now query live WL aggregates instead of
pinned 0.5718/232 values, VUL CI penetration asserts vs the configured target.

**Live DB:** fresh rebuild — run `b23edb78…` (COMPLETE, 8.9s, deaths 1206, recon PASS ×6
products), AI fit: 8 models fitted, 332 proposed factors.

**DoD:** gate green **1217 passed, 7 skipped, 0 failed**; story asserts green; boot smoke
HTTP 200.

**Next session: P5** (see `demo_refresh_prompts.md` → P5).

---

## P5 — Fraud module — COMPLETE (2026-08-30)

**Engine (`src/fraud/`, mirrors the DQ pattern):** `rules.py` — claims frame =
DEATH/CI_CLAIM events (`silver_policy_events` carries claim_amount + illness_code)
joined per Silver policy table (premium + office/agent/claimant/hospital/region;
DA joins on `contract_id`, UL double-load de-duplicated) + the 6 pure rule
functions (FR-RULE-01..06) in `ALL_RULES`; `runner.py` — `load_fraud_config`
(loud validation), `run_fraud_scan` (weighted composite, flag ≥ threshold,
persists via parameterized inserts + config sha256 stamp).
`config/fraud_config.yaml` holds every weight/threshold/high-risk list.
Rule 4 = **first-policy-year** claim concentration ≥ max(3× office median, 5).

**Tables (DDL in `db_init.py`, 29 total):** `gold_fraud_run_summary` (aggregates;
`run_by` excluded from allowlist), `gold_fraud_scores` + `gold_fraud_flags`
(claim-level, OFF-allowlist).

**UI `ui/views/17_fraud_monitor.py` (+ `ui/fraud_logic.py`):** propose-gated
"Run fraud scan", headline metrics, indicator-hit bar + score histogram with
threshold line, office/hospital/region concentration tables, flagged-claims
drill-down (per-claim rule evidence) + CSV export, **AI narrative** (model
dropdown, AI-DRAFT banner, .md export). Nav: added to "4 · AI Assistance"
(P7 moves it to Risk & Fraud).

**AI narrative (`src/ai/skills/fraud_narrative.py` + `config/prompts/skills/
fraud_narrative.md` v1.0):** generate-then-verify over
`ui/fraud_logic.assemble_fraud_facts` — **aggregates + institutional ids only**
(office/hospital/region; never policy_id/claimant_id; run ids excluded from the
traceable set); block-not-repair on any untraceable number; empty-body guard;
`skills.fraud_narrative` call params in `llm_config.yaml`.

**Chatbot surface:** `gold_fraud_run_summary` allowlisted (aggregate columns
only) + added to `_EXTRA_QUERYABLE_TABLES` (sync test green), schema card in
`sql_generation.md` v2.1, +2 few-shots (35 → 37).

**Live scan on the demo DB:** 1,808 claims scored, **32 flagged**; the ring tops
the list — the 4 shared-claimant claims score **1.20** (= hand-sum of the five
fired weights), the other 10 ring claims 0.85; OFF-013 / HOSP-066 are the top
flagged-office/-hospital concentrations.

**Tests (+17, `tests/test_fraud.py`):** per-rule fire/null fixtures ×7, config
validation ×4, ring realdata ×3 (≥4 OFF-013 flags; OFF-013/HOSP-066 top; the
shared-claimant cluster's composite equals the config-weight hand-calc and is
the scan max), fact-pack PII guard (no policy/claimant ids, no CLM-/TRM-
strings), narrative clean/blocked/empty via stub provider. PII guard lists in
`test_data_surface.py` extended (`run_by`, `claimant_id`, the 2 claim-level
fraud tables). Deferred: investigator-override workflow (DEFERRED_FOLLOWUPS at
P8).

**DoD:** gate green **1234 passed, 7 skipped, 0 failed**; boot smoke HTTP 200;
demo DB ships flagged.

**Next session: P6** (see `demo_refresh_prompts.md` → P6).

---

## P6 — Management commentary — COMPLETE (2026-08-30)

**Analytics (`src/analysis/commentary.py` + `config/commentary_config.yaml`):**
- `compute_yoy_movement` — per-calendar-year ratio-of-sums A/E + Δ vs prior
  (portfolio or per product).
- `attribute_drivers` — **exact** decomposition: `contribution_s = A_{s,t}/E_t −
  A_{s,t−1}/E_{t−1}` sums precisely to ΔA/E (locked to 1e-9 on live data);
  dimensions per decrement from config (mortality → age band + gender; lapse →
  product line + policy year; CI/surrender → product line); identifier
  interpolation guarded by an internal allowed-dimension set.
- `classify_trends` — `numpy.polyfit` slope over `trend.window_years` (3),
  improving/worsening/stable via `stable_slope_threshold` (0.02);
  `insufficient_data` under the window.
- `justification_metrics` — overall A/E, aggregate credibility Z, **cred_wtd_ae**
  (Z·A/E + (1−Z)·1.0), BE gap vs the current 1.0 multiplier, GLM proposed-factor
  cell count + range.
- `movement_legs` — per-product recon movement (enabled by the P1 recon fix).

**Fact-pack v2 (`assemble_commentary_facts` additive keys):** `yoy` (per
decrement, all years; `top_drivers` per configured dimension for the last two
transitions, top-3 by |contribution|), `trends` (portfolio per decrement +
per-product mortality/lapse), `justification` (products with GLM proposals),
`movement` (last 2 years). ~62 KB on the live run. Live: ALL-mortality and
ALL-lapse classify **worsening** (slopes +0.0848 / +0.21) — the planted stories.

**Skill (`src/ai/skills/management_commentary.py` + prompt v1.0):** four exact
`##` sections — YoY Movement & Key Drivers · Experience Trends · **Proposed
Management Actions** (recommendations explicitly allowed here, unlike chat) ·
Assumption Justification; AI-DRAFT banner; block-not-repair; empty-body guard;
`skills.management_commentary` params in `llm_config.yaml`. Chat
`commentary.md` → v3.1 (may cite `yoy`/`trends` figures; still no
recommendations in chat).

**UI `ui/views/18_management_commentary.py`:** run+decrement selectors, latest-A/E
+ trend-badge + slope metrics, A/E-by-year line with fitted trend overlay, YoY
table, **driver waterfall** (per movement-year × dimension, exact-sum caption),
portfolio trend table, justification expander, "Draft management commentary
(AI)" with model dropdown + .md export. Nav: under "2 · Experience Results"
(P7 finalises grouping).

**Tests (+19, `tests/test_commentary_analytics.py`):** attribution hand-calc
(2-segment fixture: M +0.35 / F +0.05 = Δ 0.40; absent-segment counts from
zero; exact-sum), trend edges (monotone up/down, flat→stable, 2 years→
insufficient), config validation ×3, realdata planted-story locks (mortality
worsening; 2022/23 top lapse movers; live exact-sum ×2 dims; fact-pack-v2
shape), skill clean/blocked/empty via stub provider.

**DoD:** gate green **1253 passed, 7 skipped, 0 failed**; boot smoke HTTP 200;
analytics match the planted stories; actions section present; export works.

**Next session: P7** (see `demo_refresh_prompts.md` → P7).

---

## P7 — Slickness pass — COMPLETE (2026-08-30)

**Nav → 5 storyline groups / 20 pages:** Overview (Home, Run Study, Data
Quality, Study Run Log) · Experience Results (Exposure, Mortality, Lapse, CI,
Product Comparison, Management Commentary) · Risk & Fraud (Fraud Monitor) ·
Assumptions & AI (AI Assumption Proposals, AI Analyst, Steps 1–3, Versioning &
Lineage) · Governance (Study Run Sign-Off, Dashboard, Audit & Integrity).

**Home rebuilt (`00_home.py`):** demo-facing copy, six-stage flow graphic
(commentary + fraud in, TEV gone), three-column guided tour with page links
(AppTest-safe `_link` fallback), "Where the AI fits" / "Where governance fits"
cards, All-pages reference expander.

**Shared theme (`ui/theme.py`):** `page_setup()` (wide layout + titled tab)
replaced 18 duplicate `st.set_page_config` calls; registers a default Plotly
template (colour-blind-safe palette, unified hover, consistent fonts/margins).

**Client-visible polish:** run-log Working/Chief Actuary reports now have
`st.download_button`s; every rendered FR-/NFR- requirement-ID string swept
(docstrings/comments retained; `FR-RULE-nn` fraud ids deliberately allowed);
page-13 stale "All Five Products" title fixed and the internal NFR-C-07
root-cause block replaced with client-appropriate directionality copy; page-15
GLM/GBM `st.write(dict)` dumps → tidy dataframes.

**Tests (+5, `tests/test_ui_slickness.py`):** every view registered exactly
once (no orphans/dupes), the 5 nav groups present, run-log download buttons
present, AST-based requirement-ID leak guard over rendered strings, and a
no-bare-`st.set_page_config` guard. `test_home_apptest` updated to the new
sections.

**DoD:** gate green **1258 passed, 7 skipped, 0 failed**; boot smoke HTTP 200.

**Next session: P8** (see `demo_refresh_prompts.md` → P8).

---

## P8 — Docs, demo script, UAT — BUILD COMPLETE (2026-08-30); owner sign-off pending

**Docs refreshed:** `README.md` + `USER_GUIDE.md` de-TEV'd and re-pitched
(AI-enabled demo feature set); dated **supersession notes** stamped on all four
legacy specs (v4_0 + v3_0 in `docs/`, v3_0_1 + v2_0_1 in `docs_archive/`);
CLAUDE.md finalised (project blurb, governing-doc pointers fixed to the real
file locations, refresh section → BUILD COMPLETE); `DEFERRED_FOLLOWUPS.md`
gains **FU-7** (fraud investigator-override workflow; optional fraud/commentary
goldens; the pre-existing `anti_selection_flag` quirk; lifecycle-skip note).

**Demo script:** `docs/demo_walkthrough.md` — nine-beat, 20–30 min script
(login → run → DQ → mortality story → lapse story → CI → commentary+waterfall →
fraud-ring reveal → AI proposals/analyst → 3-step assumption workflow with role
switching → lineage → run sign-off → audit verify → compliance pack), every
beat carrying its expected seed-42 figure + a quick-reference table.

**UAT:** `docs/demo_refresh_uat.md` — pre-flight, 5 sections of per-step
expected values, owner checkpoints (eval re-lock; optional live eval; dry-run),
defect log + sign-off table.

**Workflow seeding (new, closes three harness preconditions):**
`scripts/_uat_seed_workflow.py` drives one assumption set through
create (a.analyst) → save/submit → junior/senior/chief sign-offs → APPROVED on
the live DB (idempotent; skips when an APPROVED set exists). `_uat_rerun.py`
now also seeds `gold_users` (a fresh headless rebuild previously shipped an
empty user table — the app only seeds on first page load). Rebuild sequence is
now reset → rerun → ai_fit → seed_workflow (scope §4 updated).

**Verification:** all five governance harnesses PASS (s2 6/6, s3_3 7/7,
s3_7 5/5, s4_4 4/4, s5_6 8/8); boot smoke HTTP 200; final gate
**1259 passed, 6 skipped, 0 failed** — the empty-assumption-set skip cleared;
the remaining 6 skips are the pre-refresh baseline set (RPU/ETT ×2, missing
alt reference tables ×4).

**Remaining owner items (tracked in the UAT doc §6):**
1. Eval-set re-lock (golden 30 / adversarial 12 — headers say RE-LOCK PENDING).
2. Live demo dry-run via `docs/demo_walkthrough.md` + UAT sign-off.
3. Optional: live eval baseline with API keys; optional fraud/commentary goldens (FU-7).
