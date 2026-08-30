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
| P2 | Assumption workflow v2 + materiality + legacy retirement | L | — |
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
