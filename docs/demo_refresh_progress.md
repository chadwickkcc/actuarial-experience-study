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
| P3 | TEV purge | L | — |
| P4 | Data expansion + regeneration | L | — |
| P5 | Fraud module | L | — |
| P6 | Management commentary | L | — |
| P7 | Slickness pass | M | — |
| P8 | Docs, demo script, UAT | M | — |

**Test baseline history:**
| Point | Suite |
|---|---|
| Pre-refresh (P0 baseline) | 1368 passed, 6 skipped |
| After P1 | 1369 passed, 6 skipped (+1 recon-scope test) |
| After P2 | 1356 passed, 15 skipped, 0 failed (net −13: legacy approvals/envelope-summary tests retired, +9 new materiality/guard tests; +9 skips are TEV tests skipping on the fresh no-TEV-runs DB — deleted in P3 — plus 1 empty-assumption-sets lifecycle-UI skip) |

**Owner checkpoints:** P3 eval re-lock (pending) · P8 UAT sign-off (pending).

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
