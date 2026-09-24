# Experience Study Tool — Project Briefing

## What this project is
A Python-based, AI-enabled actuarial experience-study **client demo** for life
insurance covering 5 product families: Term Life, Whole Life, Universal Life
(UL/ULSG/IUL), Variable Universal Life (VUL), and Deferred Annuities. Core
surfaces: A/E results + credibility, YoY movement/driver/trend analytics with
AI-drafted management commentary, rule-based claims fraud screening with an
AI narrative, AI-proposed assumption factors + governed AI analyst, and a
multi-level assumption sign-off workflow with tamper-evident audit.
(TEV modelling existed in the original PoC and was removed in the 2026-08-30
demo refresh.)

## Where things stand
All build phases (1A–1C, 2, 3, 4), the 2026-08-30 demo refresh (P0–P8) and the
2026-08-31 adversarial review are complete. The current resume point, with open
owner items (eval-set re-lock, optional live eval baseline, browser walk of the
demo script) and verification commands:
@docs/adversarial_review_resume.md

## Governing documents
**Decision authority for everything the demo refresh changed:**
@docs/demo_refresh_scope.md

Read on demand (not auto-loaded — they are large):
- `docs/experience_study_requirements_spec_v4_0.md` + `docs/experience_study_technical_spec_v3_0.md`
  — the locked legacy specs (Phases 1–4). Valid for everything the refresh did not change.
- `docs/demo_refresh_progress.md` — refresh status board and per-phase handoff log.
- `docs/phase3_build_progress.md`, `docs/phase4_build_progress.md` — Phase 3 (AI layer) and
  Phase 4 (governance) build history, post-UAT hardening rounds and design decisions.
- `docs/adversarial_review_fixes.md` — fix log for the 2026-08-31 review.
- `docs/DEFERRED_FOLLOWUPS.md` — standing deferral register.
- `docs/demo_walkthrough.md`, `docs/demo_refresh_uat.md` — demo script and UAT with seed-42 figures.

## Key rules
1. All database schemas must exactly match the DDL in the technical spec (v3.0 Sections A, D, G).
   Do not add, rename, or remove columns. For schemas the refresh changed,
   `docs/demo_refresh_scope.md` §3.9 is the authority instead.
2. All module function signatures must exactly match the technical spec interfaces (v3.0 Sections
   B, E, H). For modules the refresh moved/changed (src/assumptions/, src/analysis/, src/fraud/,
   trimmed AI surface), `docs/demo_refresh_scope.md` §3 is the authority instead.
3. All product-specific logic goes in YAML config files under config/products/.
   The calculation engine must be product-agnostic Python.
4. The random seed for all synthetic data generation is 42.
5. Every function must have a docstring. PEP 8 style throughout.
6. DuckDB file lives at data/experience_study.duckdb.
7. Do not use SQLAlchemy or any ORM. All DB access uses the duckdb Python package directly.
8. The AI layer is strictly additive under src/ai/: the core engine never imports it; it reads
   only the Gold layer + config, and writes only to data/ai_models/ and the four AI Gold tables.
   ALL dynamically-built SQL in the AI layer goes through src/utils/sql_boundary.py — no
   string-interpolated SQL anywhere in src/ai/. Every threshold/seed/grain lives in config YAML.
9. Governance (src/governance/) is application code outside src/ai/ and uses the standard
   parameterized write path, not sql_boundary. Actors come from `current_user()`, never free text.

## Working environment
- Python 3.12 in a uv-managed `.venv`. Run everything via `.venv/bin/python`.
- Dependencies are pinned in `requirements.lock` (compiled from `requirements.in` with
  `uv pip compile requirements.in -o requirements.lock --generate-hashes --python-version 3.12`);
  install/sync from the lockfile only (`uv pip sync requirements.lock --python .venv/bin/python`).
- Stack: DuckDB, Pandas, NumPy, PyArrow, Streamlit, Plotly, Jinja2, PyYAML, SciPy, sqlglot,
  statsmodels, XGBoost, SHAP, anthropic/openai (LLM providers), mcp (FastMCP), pytest.
  Data quality is custom rule-based validators.
- Test gate (no API keys, MockProvider only):
  `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`
- Rebuild the demo DB: `scripts/reset_for_testing.py` (clears rows, then compacts the file) →
  `scripts/_uat_rerun.py` → `scripts/_uat_ai_fit.py` → `scripts/_uat_seed_workflow.py` →
  `scripts/_uat_seed_ai_activity.py`, then re-run the fraud scan. Source CSVs come from
  `synthetic_data/generate_all.py` (output in `synthetic_data/output/`, gitignored).
- Run the app: `.venv/bin/streamlit run ui/app.py`.
