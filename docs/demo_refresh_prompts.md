# Demo Refresh — Session Prompts (Phases P1–P8)

**Companions:** `demo_refresh_scope.md` (decision authority — read first),
`demo_refresh_progress.md` (status board — read before every session).
**How to use:** one phase per session, in order. Do not start a phase until the prior phase's
gate is green and its progress entry is written. Where a block contains **STOP — OWNER INPUT**,
halt and request sign-off.

**Global conventions (every session):**
- Gate: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short` green.
- Live-DB rebuild rule, governance-spine invariants, seed 42, config-over-code, delete over
  dead code — per `demo_refresh_scope.md` §4.
- TDD for new logic; verification before completion; commit per phase on `main`; update
  `demo_refresh_progress.md` before ending the session.

---

## P1 — Groundwork: re-home lifecycle, cut monitor pages, recon fix (M)

**Goal:** shrink the tree and break the `src/tev/` lifecycle coupling with zero behaviour change.

**Build:**
- Move verbatim (no logic change): `src/tev/assumption_set.py` → `src/assumptions/assumption_set.py`;
  `src/tev/workflow.py` → `src/assumptions/workflow.py`; `sensitivities._deep_copy_assumption_set`
  → `src/assumptions/assumption_set.deep_copy_assumption_set`. Update ALL importers
  (`src/governance/lineage.py`, `src/governance/workflow.py`, `ui/ai_comparison_logic.py`,
  the 4 stage pages, remaining `src/tev/` engine modules, scripts, ~30 test files). No shims.
- Delete `ui/views/{08,09,10,11,12}_*.py` + nav entries (Product Monitors group disappears).
  Grep `tests/` + `scripts/` for the filenames first.
- Merge `14_ci_incidence_summary.py` into `06_ci_explorer.py` as a tab; delete 14 + nav entry.
- Fix recon delete-scope bug: `src/exposure/engine.py` `_run_reconciliation` deletes by
  `(study_run_id, product_code)`.

**Out of scope:** TEV logic changes, schema changes, workflow/materiality changes, styling.

**Tests:** import updates across affected files; delete monitor-page tests; adjust CI-explorer
page test for the merged tab; NEW: per-product recon rows survive a multi-product run.

**DoD:** gate green; `grep -rn "from src.tev" src/governance ui` shows no lifecycle imports;
app boots; monitors gone from nav; recon holds all products after a rerun.

---

## P2 — Assumption workflow v2: 3-step IA + materiality + legacy retirement (L)

**Goal:** rebuild the assumption lifecycle TEV-free (Step 1 Select basis → Step 2 Edit & submit
→ Step 3 Sign off); replace materiality; retire the legacy approvals table. TEV engine still
exists (unreferenced by the workflow) — deleted in P3.

**Build:** per scope §3.2–3.4:
- Pages: `20_tev_stage1` → `20_assumption_step1` (drop tev_config/TEV prose; keep run-picker,
  propose gate, resume). `21_tev_stage2` → `21_assumption_step2` (keep editor tabs,
  `_validate_bounds`, Restore-from-A/E, Adopt-AI-proposal, mandatory comment; drop Economic
  Parameters tab + ΔTEV sidebar; ADD Submit-for-sign-off + refine loop + iteration history
  extracted from `22_tev_stage3.py:666-800`). `23_tev_stage4` → `22_assumption_step3` (chain UI
  + AI memo survive; drop TEV metrics/TEV Impact Report/`delta_frac`). Delete `22_tev_stage3.py`.
  Inventory `st.session_state` keys across the 4 pages first (`s3_*` TEV keys die).
- Materiality: `required_final_level` → max |Δ multiplier| via `compare_versions.changed_cells`;
  config key rename (`max_multiplier_delta_threshold: 0.05`); `gold_governance_signoffs.delta_tev`
  → `materiality_value` (DDL + `_SIGNOFF_COLUMNS`); `VersionDiff` rename; page 29 render;
  `readiness.py` asserts.
- Retire `gold_assumption_approvals` + `_write_legacy_summary` + `record_governance_approval`;
  drop TEV columns from `gold_workflow_iterations` + `log_workflow_iteration`.
- Update `scripts/uat_section*.py` harnesses. End: live-DB rebuild.

**Out of scope:** `src/tev/` engine deletion; AI-layer/report/eval changes; economic fields on
`gold_assumption_sets` (die in P3).

**Tests:** rewrite `tests/governance/test_workflow.py` (23 `delta_tev=` call-sites + 5
materiality tests → multiplier metric), lineage/identity-capture/propose-gate/audit-integrity/
stage AppTests, `tests/test_workflow.py`; NEW: materiality-from-diff units (two-version lineage
→ known max delta; root → None → full chain); Step-2 submit path; grep-guard nothing touches
`gold_assumption_approvals`.

**DoD:** gate green; lifecycle demoable end-to-end on rebuilt DB (create → edit → submit →
3-level sign-off → lock → publish → compliance pack shows materiality); locks + segregation hold.

---

## P3 — TEV purge (L)

**Goal:** delete every remaining TEV artifact.

**Build:** per scope §3.5 + §3.9:
- Delete `src/tev/`; drop tev_config read + 9 economic fields from
  `create_assumption_set_from_ae_run` + dataclass; `lineage.create_version` drops
  `tev_config_path`; delete `config/tev_config.yaml`.
- DDL: drop `gold_model_points`, `gold_tev_run_log`, `gold_tev_results` + indexes; drop the 5
  economic columns from `gold_assumption_sets`.
- AI surface: allowlists (`gold_tev_results`, `gold_model_points`) out; RAG ref to
  `Reference Materials/tev-methodology-report.md` out (delete file); MCP TEV impls +
  registrations out (6→4 tools; `TOOL_SCHEMA_VERSION` → "3.0"); mcp_client methods; pipeline
  TEV routing branch + digest `tev_baseline` + refusal text; `ui/skills_logic._tev_baseline` +
  fact-pack TEV keys; `memo.md` component 6 out (8→7); TEV cards out of
  sql_generation/synthesis_plan/routing/commentary prompts (version bumps); few-shots 43→35;
  page-15 what-if section + `run_whatif_tev`/`_prior_total_tev` out.
- Eval: golden G027–G032 out (36→30); adversarial A007 retargeted; update lock headers.
- Reports: delete `src/reporting/generator.py` TEV half (~566–1340); compliance pack drops TEV
  link + ΔTEV column. Scripts: delete `_uat_tev_baseline.py`, `migrate_envelope_schema.py`;
  purge reset list. End: live-DB rebuild.

> **STOP — OWNER INPUT:** after trimming `tests/eval/golden_set.yaml` + `adversarial_set.yaml`,
> request owner review and **re-lock** before closing the phase.

**Out of scope:** generator/data changes; new capability; nav redesign beyond removing dead
entries.

**Tests:** delete `test_tev_engine`, `test_envelope`, `test_model_points`, `test_whatif_tev`,
2 stress scripts; edit ~20 light files (`chatbot_helpers.StubMCP`, digest, data_surface,
eval_sets, memo headers `_EIGHT_HEADERS`→7, mcp tool counts…); NEW grep-guard: no source
references `gold_tev_|gold_model_points|tev_config`; `src/tev/` absent.

**DoD:** gate green (record new baseline — expect ≈ −150–200 tests); eval smoke wiring green;
AI Analyst answers on rebuilt DB with 4 tools; compliance pack renders.

---

## P4 — Data expansion + regeneration (L)

**Goal:** one-shot generator upgrade (25k policies, fraud fields, CI overhaul, planted stories)
+ the one regeneration. This phase owns ALL seed-reshuffle test-expectation updates.

**Build:** per scope §3.7:
- `config/synthetic_data.yaml` (volumes, CI penetration/incidence, story + ring + entity
  params); generators read it; seed 42.
- New fields (policy: `agency_office_id`, `agent_id`; claim: `claimant_id`, `hospital_id`,
  `claim_region`; `ci_*` non-terminal claim columns) through generators → CSV →
  `config/products/*.yaml` mappings → silver DDL → `_build_policy_events`.
- Non-terminal CI (never terminates; exposure unaffected; recon closes).
- Planted stories draw-side only (mortality ×1.08/1.16/1.25 2021-23 Term+WL; lapse ×1.4
  2022-23 Term+UL family; ring OFF-013/HOSP-066/CLM-424242). Tune till story asserts pass.
- Regenerate CSVs → fresh demo DB (reset + `_uat_rerun.py` + `_uat_ai_fit.py`). Confirm
  demo-friendly runtime at 25k.

**Out of scope:** fraud engine (P5); commentary analytics (P6); UI beyond tolerating new columns.

**Tests:** update reshuffle-broken expectations (prefer invariants: recon closes, exposure > 0,
A/E finite); verify GLM truth fixtures independent; NEW permanent story-lock tests (scope §3.7);
NEW ETL field-mapping/column tests.

**DoD:** gate green on regenerated DB; story asserts green; per-product recon populated;
volumes/event counts recorded in progress doc.

---

## P5 — Fraud module (L)

**Goal:** rule-based fraud scoring + flagged-claims UI + aggregates-only AI narrative.

**Build:** per scope §3.6:
- `src/fraud/rules.py` (6 rules, `_ALL_RULES` registry, DQ-check pattern; reads silver via
  parameterized SQL) + `src/fraud/runner.py` (`run_fraud_scan`, composite score, writes tables).
- `config/fraud_config.yaml` (thresholds, weights, flag threshold, high-risk lists, similarity
  tolerances; config hash stamped).
- 3 tables in `db_init.py` (scope §3.6 schemas).
- `ui/views/17_fraud_monitor.py`: RBAC-gated scan, score distribution + rule-hit +
  office/hospital/region concentration charts, flagged-claims drill-down, CSV export.
- Skill `draft_fraud_narrative` + `config/prompts/skills/fraud_narrative.md`
  (generate-then-verify; fact pack aggregates + institutional ids only). Allowlist + schema
  card + 2–3 few-shots for `gold_fraud_run_summary` ONLY.
- End: live-DB rebuild + one scan so the demo DB ships flagged.

**Out of scope:** investigator-override workflow (defer); ML scoring; commentary changes.

**Tests:** per-rule positive/null fixtures; composite hand-calc; config validation (bad config
fails loudly); ring realdata test (≥4 OFF-013 flags; OFF-013/HOSP-066 top concentrations);
PII-reachability guard extended (fraud claim tables unreachable; fact pack has no
policy_id/claimant_id); narrative MockProvider + traceability block; allowlist round-trip.

**DoD:** gate green; ring visibly surfaced on live DB; narrative drafts offline-testable;
PII guard green.

---

## P6 — Management commentary (L)

**Goal:** deterministic movement/trend/justification analytics + fact-pack v2 + management
commentary skill with proposed actions.

**Build:** per scope §3.8:
- `src/analysis/commentary.py` (`compute_yoy_movement`, `attribute_drivers` — contributions sum
  exactly to ΔA/E, exposure-weighted, dims per decrement from config; `classify_trends` —
  3-yr polyfit slope + YAML thresholds; `justification_metrics`; movement legs).
- `config/commentary_config.yaml`.
- Fact-pack v2: additive `yoy`/`trends`/`justification`/`movement` keys on
  `assemble_commentary_facts`; all numbers pre-computed.
- Skill `management_commentary` + `config/prompts/skills/management_commentary.md`
  (YoY w/ drivers; trend classification; proposed management actions — recommendations allowed,
  AI-DRAFT banner; assumption justification). Chat `commentary.md` → v3.0 (may cite yoy/trends;
  chat still gives no recommendations).
- `ui/views/18_management_commentary.py`: run selector, driver waterfall + trend badges + YoY
  tables, Draft button (model dropdown, banner, .md export).

**Out of scope:** memo skill changes (stays 7 components); eval golden additions (owner, P8);
fraud content.

**Tests:** attribution hand-calc (2-segment fixture, exact sum); trend edges (flat/monotone/
insufficient years); fact-pack shape + digits-from-pack; skill MockProvider + traceability +
banner-in-export; planted-story realdata (Term/WL mortality classified worsening; 2022–23 lapse
spike top driver period); config validation.

**DoD:** gate green; page demoable; analytics match planted stories; narrative cites only pack
numbers; actions section present; export works.

---

## P7 — Slickness pass (M)

**Goal:** client-ready surface.

**Build:** per scope §3.10:
- Nav → 5 groups / 19 pages; home page rework (flow graphic: TEV out, commentary + fraud in;
  demo copy; quick links).
- `ui/theme.py` (palette + `page_setup()` replacing 23 duplicate `st.set_page_config`;
  consistent Plotly template).
- Run-log report buttons get `st.download_button`; requirement-ID (FR-/NFR-) sweep from
  client-visible strings; fix page-13 stale title + NFR-C-07 internal text block; tame raw
  dataframe/json dumps on 15/26/27.

**Out of scope:** engine/schema/prompt changes; new features.

**Tests:** AppTest smokes (home + nav registration — every view registered exactly once);
download-button presence; requirement-ID grep-guard.

**DoD:** gate green; all 19 pages render error-free on live DB; boot smoke.

---

## P8 — Docs, demo script, UAT (M)

**Goal:** close the refresh documented and rehearsed.

**Build:**
- Refresh `README.md` + `USER_GUIDE.md`; finalize `demo_refresh_scope.md` + CLAUDE.md; dated
  supersession header notes on the 4 legacy specs; update `DEFERRED_FOLLOWUPS.md`.
- `docs/demo_walkthrough.md`: scripted 20–30 min demo, each beat with exact clicks + expected
  seed-42 numbers (login → run study → DQ → mortality story → lapse story → CI → product
  comparison → management commentary → fraud ring + narrative → AI Analyst → assumption steps
  1–3 → lineage/publish → study-run sign-off → audit verify → compliance pack).
- `docs/demo_refresh_uat.md` (+ sign-off table).

> **STOP — OWNER INPUT:** eval re-lock confirmation (+ optional fraud/commentary goldens);
> owner live dry-run + sign-off.

**Out of scope:** code changes except dry-run defects (each with a regression test, logged).

**Tests:** full gate; `scripts/uat_section*.py` harnesses pass; headless AppTest walk of the
demo beats where automatable.

**DoD:** gate green; owner sign-off recorded; progress doc closed out.
