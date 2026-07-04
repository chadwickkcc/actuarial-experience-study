# Phase 3 (AI Layer) — Build Progress & Handoff Log

**Purpose:** single source of truth for what has been built across Phase 3
sessions, so any new Claude Code session can resume without losing context.
Read this together with `docs/phase3_claude_code_prompts.md` (the per-session
prompt blocks) and the authoritative specs (`docs/experience_study_requirements_spec_v3_0_1.md`,
`docs/experience_study_technical_spec_v2_0_1.md`).

**Regression gate (every session):** `pytest tests/ -v --tb=short` green with **no
LLM API keys** in the environment (MockProvider posture).

---

## Status board

| Session | Title | Status |
|--------|-------|--------|
| 14 | Security Hardening (Phase 3a entry) | ✅ COMPLETE |
| 15 | GLM Assumption Engine | ✅ COMPLETE |
| 16 | GBM Overlay + SHAP | ✅ COMPLETE |
| 17 | Assumption Comparison UI (closes 3a) | ✅ COMPLETE |
| 18 | LLM Provider Abstraction + MCP Server | ✅ COMPLETE |
| 19 | Claude Skills | ✅ COMPLETE |
| 20 | Chatbot Core + Guardrails | ✅ COMPLETE |
| 21 | RAG Commentary + Audit + AI Analyst Page | ✅ COMPLETE |
| 22 | Evaluation Harness + Phase 3 UAT (closes 3b) | ✅ COMPLETE — **Phase 3b / Phase 3 CLOSED** |

> **Post-build:** owner UAT (2026-06-25→27) drove four follow-on rounds — Skills bug fixes +
> SURRENDER memo decrement, then three rounds on the **AI Analyst** chatbot (round 2: figures/
> list-slot/routing/run-scope; round 3: provider-error handling, opt-in Analyst mode, fact-pack
> commentary, multi-query synthesis) and a **robustness pass** (+32 offline edge-case tests). Current
> suite **1114 passed, 6 skipped**. See the **"Post-UAT hardening"** sections below the Session-22
> section for the full list. NOTE: three round-2/3 changes were **formally amended into the locked
> specs in-place** (2026-06-27, owner-authorised — dated header notes on Req v3.0.1 + Tech v2.0.1;
> `docs/DEFERRED_FOLLOWUPS.md` FU-5 RESOLVED).

**Owner sign-off checkpoints:** **Session 18 — model pricing in `llm_config.yaml`
RESOLVED (2026-06-20):** at the owner's direction, the four `price_per_mtok_*`
pairs were filled from public list rates rather than left as placeholders —
Anthropic Opus 4.8 $5/$25 and Sonnet 4.6 $3/$15 (Anthropic pricing reference,
cached 2026-06-04); DeepSeek V4 Pro $0.435/$0.87 and Flash $0.14/$0.28 (published
V4 GA rates, 2026-06; Pro reflects the now-official ¼ price effective 2026-05-31).
Owner may override. **Session 22 — golden/adversarial set lock: RESOLVED
(locked 2026-06-20).** The owner reviewed `tests/eval/{golden_set,adversarial_set}.yaml`
(36 golden + 12 adversarial) and **locked them as the authoritative baseline** (§12.2);
their headers now record the lock. **Phase 3 UAT sign-off: RESOLVED — Phase 3
ACCEPTED & CLOSED (2026-06-28).** The owner ran the two owner-triggered steps
offline — the live eval baseline on ≥2 models (one Anthropic + one DeepSeek; hard
gates 100%, results in `gold_ai_eval_results`) and the ≥3 manual AI-Analyst
adversarial prompts — and **signed off Phase 3** on 2026-06-28. Claude Code executed
and recorded the offline portion the same day (full regression gate **1163 passed,
6 skipped**, no keys; the 44 eval-harness tests incl. `test_eval_realdata` against
the production Gold DB). Sign-off is recorded in `docs/phase3_uat_script.md` and the
aligned `docs/phase3_uat_runbook.md` (all boxes ticked; sign-off tables completed).
Sessions 14, 15, 16 and 17 had no owner checkpoint.

---

## Environment notes (important for the next session)

- **Interpreter (current, from Session 18 — 2026-06-20):** the working
  environment is a **uv-managed `.venv` on Python 3.12.13** at the project root.
  Run everything through it: **`.venv/bin/python -m pytest …`** (or
  `source .venv/bin/activate`). `requirements.lock` is compiled for 3.12 and the
  venv is installed from it. All code keeps `from __future__ import annotations`
  (harmless on 3.12).
  - **Why not the system Python?** The owner's "just updated Python" installed
    Homebrew **3.14.6** as `python3`, but the ML stack has **no 3.14 wheels**
    (`numba`/`llvmlite` top out at 3.12 at the pinned versions → `shap` won't
    install), so a 3.14 lockfile regen fails and the baseline can't run there.
    `/usr/bin/python3` is still 3.9.6 (the old deps), but it can't install
    `mcp`/FastMCP (needs ≥3.10). **Python 3.12 via uv is the chosen interpreter**
    (owner-confirmed); the system 3.14 is left untouched.
  - **Phase 0 outcome (no pin drift):** added `anthropic`/`openai`/`mcp` to
    `requirements.in`; regenerated the lockfile **against the existing lock**, so
    uv kept the ML-stack pins unchanged (numba 0.60.0, llvmlite 0.43.0, shap
    0.49.1, xgboost 2.1.4 — all have 3.12 wheels) and only added `mcp==1.28.0`,
    `anthropic==0.111.0`, `openai==2.43.0`. The **814 passed / 6 skipped**
    baseline reproduced exactly on 3.12 before any Session-18 code (so the
    "treat ML-pin drift as cleanup" risk never materialised).
  - **Migration command used:**
    `uv pip compile requirements.in -o requirements.lock --generate-hashes --python-version 3.12`
    then `uv pip install -r requirements.lock`.
- **`watchdog` added (dev convenience, 2026-06-21):** `watchdog>=4.0.0` added to
  `requirements.in`; lockfile regenerated (resolved `watchdog==6.0.0`) with **no
  ML-stack pin drift** (numba/llvmlite/shap/xgboost/statsmodels/streamlit/mcp/
  anthropic/openai all unchanged) and installed from the lock. Streamlit uses it
  for fast OS-level (FSEvents) file-change auto-reload instead of slow polling;
  it affects **nothing** in calculations, tests, or output. Note: the package
  exposes no top-level `__version__` — check it via
  `from importlib.metadata import version; version('watchdog')`, not
  `watchdog.__version__`.
- **Not a git repo.** `.gitignore` exists and is correct, but `git init` has not
  been run (owner decision). The lockfile/gitignore are ready for when it is.
- **Run tests with keys unset, via the venv:**
  `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`.
- **Production DB:** `data/experience_study.duckdb` (~258 MB) has one COMPLETE
  study run (`ed193b59-c5d6-48cd-b5e6-43d33464dff8`), used for report rendering.

---

## Session 14 — Security Hardening — COMPLETE

**Goal delivered:** the three security-review items + the additive-layer
architecture, with zero Phase 1–2 behavioural change.

### Files added
| Path | What |
|------|------|
| `src/utils/sql_boundary.py` | Hardened SQL boundary (Tech Spec §E.2): `load_allowlist`, `validate_select` (gates 1–4, pure), `execute_safe_select` (gate 5, read-only), `SQLBoundaryError`. **Only permitted DB path for the AI layer.** |
| `config/ai_config.yaml` | AI settings; only the `chatbot.allowlist` block is populated now (Gold tables → permitted columns, no PII). Content **finalised in Session 20**; structure is final. |
| `src/ai/__init__.py` + `glm/ gbm/ llm/ chatbot/ mcp_server/ skills/ eval/` `__init__.py` | Empty package skeleton (FR-3A-06); root `__init__` documents the FR-3A-07/08/09/02 contracts. |
| `requirements.in` / `requirements.lock` | uv-compiled, hash-pinned lockfile (FR-3A-04). `sqlglot` added (needed by the boundary); ML/LLM stack deferred to later sessions. |
| `.gitignore` | Ignores `tests/_artifacts/`, `data/ai_models/`, caches. |
| `README.md` | Install-from-lockfile + test/cleanup commands. |
| `tests/test_sql_boundary.py` | 38 tests: all five gates, `*` expansion, UNION/subquery bypass rejection, read-only enforcement, determinism. |
| `tests/test_ai_architecture.py` | 8 tests: no-SQL-interpolation scan, import-graph, write-contract scaffold, autoescape — each with a negative self-test proving the guard fires. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended Phase 3 types **`SQLGateOutcome`** + **`SQLValidationResult`** (only what the boundary needs now; the rest of §E.1 lands in Sessions 15–20). |
| `src/reporting/generator.py` | `_get_jinja_env()` → `autoescape=True` (FR-3A-03). |
| `tests/conftest.py` | Added `ARTIFACT_ROOT`, `SIZE_CAP_GB`, `--keep-artifacts` option, and the autouse `_artifact_guard` (size cap + cleanup, NFR-T-03/04). The `synthetic_db` fixture is **not yet built** — it lands in Session 15. |
| `CLAUDE.md` | Updated spec pointers to v3.0.1/v2.0.1, phase status, Phase 3 rule #8, tech stack. |

### Definition of done — all met
- [x] Lockfile present, hash-pinned; builds install from it exclusively.
- [x] SQL boundary per §E.2; interpolation scan passes on `src/ai/`.
- [x] `autoescape=True`; A/E reports verified byte-comparable modulo **accepted
      pure-escaping diffs** (data values like the products list and band labels
      `<=2x`/`>12x` are now correctly HTML-entity-escaped; they render
      identically in a browser — see "autoescape verification" below). No
      `| safe` markers were needed; templates inject no raw HTML.
- [x] `src/ai/` skeleton; import-graph green; write-contract scaffold green.
- [x] Full regression suite green: **756 passed, 6 skipped, no API keys**.

### Autoescape verification (how it was checked)
Rendered the working-actuary + chief-actuary reports before and after the
change; diffed ignoring the nondeterministic timestamp. Every changed block
unescapes back to the baseline exactly (pure HTML-entity escaping); static
template text is untouched. This is the "reviewed, accepted diffs" path of
FR-3A-03.

### Known limitations / deferred (not blockers)
- **Derived-table column aliases over-reject.** `SELECT x FROM (SELECT ae_count
  AS x FROM gold_ae_results) s` is rejected (REJECT_ALLOWLIST) because `x` is not
  an allowlisted column name. This is *conservative* (rejects a safe query, never
  leaks) — a precision limitation, not a security hole. CTEs are handled (their
  names are transparent); flat derived-table aliases are not. Revisit when the
  Session 20 SQL generator needs them.
- **Interpolation/write-contract scanners are keyword/heuristic-based** (gated on
  SQL keywords / `data/` path literals). They have negative self-tests proving
  they fire, but a determined obfuscation could evade them. Sufficient as a
  standing CI guard for tool-generated code.
- **`min_events_to_fit`, GLM/GBM/eval config blocks** are intentionally absent
  from `ai_config.yaml` — they belong to Sessions 15/16/22.

---

## Session 15 — GLM Assumption Engine — COMPLETE

**Goal delivered:** Poisson/binomial GLMs that propose A/E adjustment factors
with bootstrap 95% CIs, proven to recover the synthetic generator's known true
factors within the §F.1 tolerance table. Read-only, unblended, no adopt path
(FR-3A-20/29/30). Strictly additive; all Gold reads via the SQL boundary.

### Files added
| Path | What |
|------|------|
| `src/ai/glm/fit.py` | `derive_factor` (FR-3A-14), `load_cells` (static SELECT via `execute_safe_select`, run/product filtered in pandas), `fit_glm` (Poisson+log-offset mortality FR-3A-13; binomial-logit lapse/CI FR-3A-14; publishes at output grain FR-3A-18; loud-failure guardrail FR-3A-29). Internal `_fit_core`/`_fit_from_fitting_cells` shared with bootstrap + registry. |
| `src/ai/glm/bootstrap.py` | `bootstrap_cis` — parametric bootstrap, determinism-first (master→child seeds, order-independent FR-3A-21); resamples in memory only, never persisted (FR-3A-22 / NFR-T-05). |
| `src/ai/glm/validate.py` | `validate_against_truth` (FR-3A-26 tolerance, FR-3A-27 ≥90% CI coverage). |
| `src/ai/glm/registry.py` | `register_glm_model` (pickle results to `data/ai_models/glm/{model_id}.pkl`, diagnostics JSON, **static parameterized** INSERT into `gold_ai_model_registry` with the full reproducibility stamp, FR-3A-24); `load_glm_model`. |
| `synthetic_data/true_factors.py` | Read-only ground-truth accessor: per-cell true A/E factors (reuses generator risk-class + `ci_age_factor`) and `output_grain_true_factors` (expected-weighted). Shared by the fixture and `validate`. |
| `tests/test_glm_fit.py` | derive_factor edge cases; Poisson hand-calc (4 dp); determinism; guardrail; load_cells filtering/grain. |
| `tests/test_glm_bootstrap.py` | CI population; determinism; no-resample-persistence; no-proposal passthrough. |
| `tests/test_glm_validate.py` | Synthetic-truth recovery — the Phase 3a accuracy gate (mortality, lapse, CI). |
| `tests/test_glm_registry.py` | Registry row + stamp; pickle round-trip → identical coefficients. |
| `tests/test_glm_realdata.py` | Real-data smoke (skip-if-absent `prod_db`): fit + bootstrap on production Gold runs clean — guards the two real-data robustness fixes below. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended §E.1 GLM types: `DecrementType`, `AIModelType`, `FactorCell`, `GLMFitResult`, `ValidationResult`. |
| `src/utils/db_init.py` | New `_GOLD_AI_DDL` with `gold_ai_model_registry` (§D.1) + index; concatenated in `init_database()`. Session 18 extends this list with the other two AI tables. |
| `config/ai_config.yaml` | Appended the `glm:` block per §F.1 (seed 42, `min_events_to_fit: 200`, bootstrap 1000/0.95, output grains, covariates, full validation tolerance table). |
| `tests/conftest.py` | `synthetic_db` session fixture: synthesises `gold_ae_results` cells directly (the GLM's only input, FR-3A-15) with injected known factors and Poisson-drawn actuals (seed 42, independent detail/CI RNG streams); `glm_config` fixture. |
| `requirements.in` / `requirements.lock` | Added `statsmodels>=0.14.0`; lockfile regenerated with `uv … --generate-hashes` (pulls in `statsmodels==0.14.6`, `patsy`). |

### Definition of done — all met
- [x] GLMs fit for every qualifying decrement-product; factors published at the configured grain with bootstrap CIs.
- [x] §F.1 tolerance-table recovery + ≥90% CI coverage pass (FR-3A-26/27).
- [x] Poisson offset hand-calc matches to 4 dp; fit + bootstrap deterministic (FR-3A-24).
- [x] Registry rows written with full reproducibility stamp; pickle round-trips to identical coefficients; no resample arrays on disk.
- [x] Guardrail: sub-`min_events_to_fit` / non-convergence returns "No AI proposal available", never a number (FR-3A-29).
- [x] Session-14 guards still green (no-SQL-interpolation scan, import-graph, write-contract, autoescape).
- [x] Full regression suite green: **778 passed, 6 skipped, no API keys** (was 756/6 — +22 GLM tests, no regressions). Lockfile dry-run resolves the full tree (`uv pip install --dry-run`).

### Post-build hardening (validated against the real production Gold run)
After the synthetic gate passed, the engine was spot-checked against the
production DB (`data/experience_study.duckdb`, run `ed193b59…`) across all
products. This surfaced two real-data robustness bugs the synthetic fixture
could not — both fixed and now covered by `tests/test_glm_realdata.py` +
`test_glm_fit.test_fit_excludes_zero_expected_cells`:
1. **Zero-expected cells broke the Poisson fit.** Real Gold data has cells with
   `expected_deaths_count = 0` (23 in this run); `log(0)` poisoned the offset
   and WL mortality failed with "NaN in endog" (a *false* no-proposal despite
   ample events). Fix: `_fit_core` now drops cells with a non-positive
   offset/weight denominator before fitting — actuarially correct (a
   zero-expected cell has no reference basis).
2. **A degenerate bootstrap resample crashed the whole bootstrap.** A sparse
   resample could raise "NaN in weights" inside a refit; the loop only skipped
   non-converged refits, not raised ones. Fix: each refit is wrapped in
   try/except and a failed resample is dropped (standard bootstrap practice).
Confirmed afterwards: all converged real-data fits produce ordered, finite CIs;
sparse products correctly return "No AI proposal available" via the guardrail.
(Near-zero factors appear for output cells with no observed deaths — an honest
sparse-data estimate, shown with its CI, not a bug.)

### Design notes / known limitations (not blockers)
- **Fixture is direct cell synthesis, not a full Phase-1 pipeline run.** The GLM
  reads only `gold_ae_results` (FR-3A-15), so the fixture writes those cells
  directly with injected true factors — faster, self-contained, and an exact
  realisation of FR-3A-26/27. `true_factors.py` reuses the generator's
  risk-class/CI-age structure so the truth ties back to the generator's design.
- **`load_cells` reads the table under a large static LIMIT and filters
  run/product in pandas.** The §7.2 boundary has no bind parameters and rejects
  un-aggregated `GROUP BY` scans (gate 4 only passes single-row aggregates), so
  filtering by a dynamic `run_id` in SQL would require interpolation (forbidden).
  Reading allowlisted columns under a static `WHERE illness_code IS [NOT] NULL`
  + static `LIMIT` and filtering in-memory is the no-interpolation-safe path.
  Fine for the prototype; a future enhancement is bind-parameter support in the
  boundary.
- **Lapse (and CI) coverage is pooled across products in the validation test.**
  The lapse output grain (product × duration_band) is coarse — 4 cells per
  product is too few for a stable ≥90% coverage threshold — so the test pools
  TERM/WL/UL (FR-3A-27 is "per decrement"). Mortality (24 cells, non-saturated
  → strong shrinkage) passes single-product.
- **Saturated small-cell fits** make statsmodels emit benign
  `PerfectSeparation` / divide-by-zero (df_resid = 0) warnings; suppressed
  locally in `_fit_from_fitting_cells` since CIs come from the bootstrap, not
  model-based SEs, and the dispersion ratio is guarded.

---

## Session 16 — GBM Overlay + SHAP — COMPLETE

**Goal delivered:** the XGBoost challenge/explain overlay on top of the GLM. It
fits at the **same output grain on the same covariates** as the GLM (so the two
are directly comparable), flags cells where it materially diverges from the GLM
(FR-3A-33), and generates the SHAP-JSON the Session-19 `explain_shap_results`
Skill will consume — all **reported, none adopted** (the GBM is never a proposal
engine; its truth-recovery is reported, not gated, FR-3A-36). Strictly additive;
`src/ai/glm/` source was **not modified** — its internals are imported and reused
so the GBM and GLM factors are computed identically.

### Files added
| Path | What |
|------|------|
| `src/ai/gbm/fit.py` | `_fit_gbm_core`, `fit_gbm` (XGBoost **core API**: `base_margin=log(expected)` for `count:poisson` mortality; exposure-weighted `binary:logistic` for lapse/CI), `bootstrap_gbm_cis` (FR-3A-34, mirrors the GLM master→child-seed scheme), `_divergence_flags` (FR-3A-33), `register_gbm_model` + `load_gbm_model`. Reuses `_MEASURES`, `_used_covariates`, `_output_grain_columns`, `_aggregate_to_covariates`, `_factors_at_output_grain` from `src/ai/glm/fit.py`. |
| `src/ai/gbm/explain.py` | `generate_shap_artifacts` via `shap.TreeExplainer` at fit time (FR-3A-38); one-hot SHAP summed back to the parent covariate so `feature_names` are actuarial (FR-3A-39); emits + validates the §D.6 SHAP-JSON. `_validate_shap_json` (dependency-free: schema_version, mapping⊆features, additivity 1e-6, grain match). |
| `src/ai/gbm/shap_schema.json` | Formal §D.6 JSON-Schema document (`schema_version` 1.0; `supported_schema_versions`). |
| `config/feature_to_assumption.yaml` | Per-decrement covariate→{actuarial_term, assumption_dimension} map (FR-3A-39), covering every covariate. |
| `tests/test_gbm_fit.py` | offset hand-check, divergence fire/silent + no-GLM, guardrail, determinism (`save_raw` + predictions), bootstrap CIs + determinism + no-disk-persistence (8 tests). |
| `tests/test_gbm_explain.py` | SHAP-JSON validates + additivity + mapping⊆features + actuarial feature_names; `_validate_shap_json` accept/reject (2 tests). |
| `tests/test_gbm_validate.py` | GBM truth-recovery **reported, not gated** (FR-3A-36) — asserts a `ValidationResult` is produced, not `passed`. |
| `tests/test_gbm_registry.py` | registry row (`model_type='GBM'`, `cv_metric_*`, `shap_json_path`, GLM-stats NULL, stamp); booster JSON round-trip → identical predictions (2 tests). |
| `tests/test_gbm_realdata.py` | skip-if-absent `prod_db` smoke across 6 product/decrement combos (mortality TERM/WL, lapse TERM/WL/DA, CI TERM): clean proposal-or-no-proposal, finite CIs, SHAP generated. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended `GBMFitResult` (§E.1) — no `converged`/`message`; no-proposal = empty `factors` + NaN cv. |
| `config/ai_config.yaml` | Appended `gbm:` block (seed 42, `divergence_threshold: 0.10`, bootstrap 200/0.95, fixed hyperparams incl. `nthread: 1` and `cv_folds: 5` nested under `hyperparams` so the single dict flows to `fit_gbm`). `output_grain`/`covariates`/`validation` are **reused from the `glm:` block** — the GBM is the challenge column at the same grain. |
| `requirements.in` / `requirements.lock` | Added `xgboost>=2.0.0`, `shap>=0.44.0`; lockfile regenerated with `uv … --generate-hashes --python-version 3.9` (resolves `xgboost==2.1.4`, `shap==0.49.1`, + `numba`/`llvmlite`/`scikit-learn`/`slicer`/`cloudpickle`/`joblib`/`threadpoolctl`). |
| `tests/conftest.py` | Added session `gbm_config` and `feature_to_assumption_map` fixtures (mirror `glm_config`). |

### Definition of done — all met
- [x] XGBoost models fit via the core API; Poisson `base_margin=log(expected)` hand-check passes (predicted ≈ k·expected; factor ≈ k).
- [x] Divergence flag fires on a constructed disagreement **and stays silent on the null/agreement case** (FR-3A-33).
- [x] SHAP artifacts generate, persist as schema-conformant JSON (additivity within 1e-6), and register against `model_id`; `feature_names` are actuarial covariates, never raw one-hot columns (FR-3A-39).
- [x] Feature-to-assumption mapping complete and round-trips into the SHAP-JSON (`feature_to_assumption` ⊆ `feature_names`).
- [x] GBM truth-recovery **reported, not gated** (FR-3A-36); determinism holds (same seed → identical booster); no resample arrays on disk (FR-3A-22).
- [x] Registry rows: `model_type='GBM'`, `cv_metric_name/value` set, `shap_json_path` set, GLM-only stats NULL, full reproducibility stamp; booster (native XGBoost JSON) round-trips to identical predictions.
- [x] Loud-failure guardrail (FR-3A-29): sub-`min_events_to_fit` returns empty factors, never a number.
- [x] Session-14 guards still green (no-SQL-interpolation scan now covers `src/ai/gbm/`, import-graph, write-contract, autoescape); GBM uses no string-interpolated SQL (reads via the boundary; the registry INSERT is parameterized).
- [x] Full regression suite green: **797 passed, 6 skipped, no API keys** (was 778/6 — +19 GBM tests, no regressions). Lockfile dry-run resolves the full tree (`uv pip install --system --dry-run -r requirements.lock`).

### Real-data spot-check (validated against the production Gold run)
Ran `tests/test_gbm_realdata.py` against a copy of `data/experience_study.duckdb`
across products. It surfaced **one real-data robustness issue the synthetic
fixture could not** — XGBoost forbids `<`, `[`, `]` in `DMatrix` feature names,
and the real PLT `premium_jump_ratio_band` levels (`<=2x`, `>12x`) would have
raised. **Fix:** the `DMatrix` is built positionally (no `feature_names`); SHAP
names features positionally from the design-matrix columns instead, so nothing is
lost and the one-hot→covariate aggregation still works. Confirmed afterwards: all
products either produce finite, ordered factors/CIs with a schema-valid SHAP-JSON,
or return the empty-factors guardrail (e.g. annuity CI, sparse cells) — never a
crash.

### Design notes / known limitations (not blockers)
- **`GBMFitResult` has no `converged`/`message`** (matches §E.1 exactly, rule #2).
  A no-proposal is signalled by `factors == []` with `cv_metric_value = NaN`; the
  registry maps `converged = bool(factors)`.
- **CV is reported, never fatal (FR-3A-32).** With fewer fitting cells than folds
  (e.g. lapse's coarse product×duration grain — 4 cells < 5 folds), or if `xgb.cv`
  raises, `cv_metric_value` is `NaN` (stored as SQL NULL). The registry stamp test
  therefore uses mortality (many cells) to exercise a real CV value.
- **`generate_shap_artifacts` takes keyword-only `decrement`/`product_code`** in
  addition to the §E.4 positional contract, because the §D.6 JSON requires those
  fields and a `DMatrix`/design matrix does not carry them. Its second positional
  arg is the design-matrix DataFrame (the dense SHAP input) rather than a
  `DMatrix`, since `TreeExplainer.shap_values` consumes a matrix.
- **SHAP contributions are in the model's margin (link) space** (log for
  `count:poisson`, logit for `binary:logistic`); `prediction = base + Σshap` by
  construction, so additivity is exact.
- **The real-data smoke generates SHAP directly (not via `register_gbm_model`)**
  because the production DB copy predates the AI Gold tables; the registry INSERT
  is covered on the synthetic DB.

---

## Session 17 — Assumption Comparison UI — COMPLETE ✅ (Phase 3a CLOSED)

**Goal delivered:** the **Assumption Comparison — AI Proposals** Streamlit page
surfaces the GLM proposal, the GBM challenge, SHAP explainability, and a read-only
TEV what-if on one page — with **no adopt affordance anywhere on the page**
(FR-3A-44) — plus the Stage 2 editor extension that records AI provenance on
adoption (FR-3A-30). Strictly additive: it **reuses** the Session 15/16 functions
(no modelling reimplemented), reads persisted SHAP-JSON (never recomputes), and
keeps all of the page's own queries read-only. Closes Phase 3a.

### Files added
| Path | What |
|------|------|
| `ui/ai_comparison_logic.py` | Pure, import-safe (no Streamlit) orchestration the page calls: `load_ai_config`/`load_feature_to_assumption`, `fit_models` (reuse chain: `load_cells`→`fit_glm`→`bootstrap_cis`→`register_glm_model`; `fit_gbm`→`bootstrap_gbm_cis`→`register_gbm_model` incl. SHAP), `build_comparison_table` (FR-3A-42), `lookup_approved_factor`, `build_whatif_assumption_set` (in-memory only), `run_whatif_tev` (flags `sensitivity_id='what_if_ai_proposal'`, FR-3A-43), `latest_approved_assumption_set`, `load_shap_json`/`shap_cell_for_grain`. Under `ui/`, not `src/ai/`, so it may call the TEV engine + read the AI registry while `src/ai/` never imports it (FR-3A-07 preserved). |
| `ui/pages/15_assumption_comparison.py` | The page (FR-3A-41..46): run/decrement/product selectors + "Fit AI models"; "No AI proposal available" states with reasons (FR-3A-29); labelled comparison table + CSV; "TEV impact (what-if)" with ΔTEV vs approved; diagnostics expander (GLM + GBM); SHAP waterfall/global from persisted JSON scoped to a selected cell; `feature_to_assumption` table; greyed Skill buttons ("available in Phase 3b", FR-3A-45). No adopt affordance (FR-3A-44); page queries read-only (FR-3A-46). |
| `tests/test_ai_comparison_logic.py` | `build_comparison_table` shape/labels + interaction flag; no-GBM passthrough; `lookup_approved_factor`; `build_whatif_assumption_set` is in-memory + non-mutating (4 tests). |
| `tests/test_ai_provenance.py` | §D.4 columns present after init + init idempotent; `record_ai_provenance` sets the columns + raises on unknown id; `find_ai_proposal_for_set` returns latest GLM / None (5 tests). |
| `tests/test_whatif_tev.py` | what-if flags `what_if_ai_proposal`, passes the in-memory set, references the approved id, chains baseline as prior; build_whatif writes no `gold_assumption_sets` row (2 tests). |
| `tests/test_assumption_comparison_page.py` | source-scan guards: page wires no adopt/write path; every `duckdb.connect` is read-only; logic module imports without Streamlit (4 tests). |
| `tests/test_assumption_comparison_apptest.py` | headless Streamlit `AppTest` render smoke of the page's initial (no-fit) state — title, three selectors, fit button, info prompt, no exception; skipped when the local DuckDB is absent (1 test). |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/db_init.py` | §D.4 idempotent migration: `_ensure_column()` (checks `information_schema.columns`, then `ALTER TABLE ADD COLUMN` — DuckDB has no `ADD COLUMN IF NOT EXISTS`) applied in `init_database()` after the DDL, adding `gold_assumption_sets.ai_proposed_value DOUBLE` + `.ai_model_id VARCHAR(36)`. |
| `src/tev/assumption_set.py` | Added `record_ai_provenance()` (parameterized UPDATE of the §D.4 columns) and `find_ai_proposal_for_set()` (latest converged GLM from `gold_ai_model_registry`). Live in `src/tev/` (not `src/ai/`) — the sanctioned human edit path that writes the Phase 2 table (FR-3A-09/30). |
| `ui/pages/21_tev_stage2.py` | Minimal "Adopt AI proposal" expander (shown when `find_ai_proposal_for_set` returns a model for the set's source run): checkbox + adopted-value input; on save, `record_ai_provenance(...)` stamps set-level provenance using the existing save comment as justification. |
| `ui/app.py` | New nav group **"AI Proposals (Phase 3a)"** → the page. |

### Definition of done — all met
- [x] Comparison page columns labelled per FR-3A-42; "No AI proposal available" states render with reasons (real data: MORTALITY/TERM 84 events, DA lapse 0, CI/TERM 14 → no-proposal); no adopt affordance anywhere (guard test).
- [x] What-if logs as `what_if_ai_proposal`, touches no assumption set (real data: 1 flagged run logged; assumption sets before=after=1).
- [x] Editor records `ai_proposed_value` + `ai_model_id` on an AI-originated edit (`record_ai_provenance` + Stage 2 wiring + test).
- [x] §D.4 `ALTER TABLE gold_assumption_sets` additions land idempotently.
- [x] UAT script section produced (`docs/phase3_uat_script.md`).
- [x] Session 14 standing guards still green (no-SQL-interpolation scan now also over `ui/ai_comparison_logic.py`? — it lives under `ui/`, not `src/ai/`, and uses only parameterized SQL; import-graph, write-contract, autoescape all green).
- [x] **Gate to 3b:** full regression suite green — **814 passed, 6 skipped, no API keys** (was 797/6 → +17 Session-17 tests, no regressions).

### Real-data spot-check (production Gold copy, run `ed193b59…`)
Ran the page logic against a copy of `data/experience_study.duckdb` (which predates
the AI tables): `init_database` on the copy added `gold_ai_model_registry` + the
§D.4 columns, then drove `fit_models` → comparison table → SHAP-JSON → what-if:
- MORTALITY/WL: 58 GLM cells, 58 GBM cells, 41 interaction flags, finite ordered
  CIs, 58 SHAP cells.
- LAPSE/TERM & LAPSE/WL: 5 cells each, finite CIs, SHAP OK.
- Loud-failure guardrail correctly returned "No AI proposal available" for sparse
  combos (MORTALITY/TERM, DA lapse, CI/TERM).
- What-if MORTALITY/WL end-to-end: baseline TEV 173.4M → what-if 177.9M
  (ΔTEV +4.48M), `sensitivity_id='what_if_ai_proposal'`, exactly 1 flagged run
  logged, assumption-set count unchanged (no set created).

### Post-build review pass (correctness audit)
A verification pass after the initial build surfaced and fixed two issues:
1. **AI-provenance was wiped on a plain re-save (fixed).** `save_assumption_set`
   → `_insert_assumption_set_metadata` does `DELETE`+`INSERT` with a column list
   that omits the §D.4 AI columns, so a later non-adopting Stage 2 save silently
   reset `ai_proposed_value`/`ai_model_id` to NULL — a data-loss against FR-3A-30.
   Fixed: `_insert_assumption_set_metadata` now reads the two AI columns before the
   DELETE and re-applies them after the INSERT (guarded on column presence), so
   provenance is sticky across re-saves. Covered by
   `test_ai_provenance.test_provenance_survives_a_plain_resave`.
2. **Spot-check artifact leak (cleaned).** The first real-data spot-check ran
   `fit_models(register=True)` and reassigned the registry `_MODELS_DIR` module
   global — but `register_*` binds `models_dir` as a *def-time default*, so the
   reassignment had no effect and 12 model/SHAP/diagnostics files were written
   into the real `data/ai_models/` (their registry rows lived only in the deleted
   temp copy — orphaned). The real `data/experience_study.duckdb` was confirmed
   untouched (no `gold_ai_model_registry` table) and the orphans were removed.
   Lesson for future spot-checks: pass `register=False`, or pass an explicit
   `models_dir=`, rather than reassigning the module global.

Also added a headless `AppTest` render smoke (initial state) so the page's
Streamlit wiring is checked automatically, not just `py_compile`d.

### Design notes / known limitations (not blockers)
- **What-if substitution is uniform per product.** GLM factors live at the output
  grain (coarser than the multiplier cells), so `build_whatif_assumption_set`
  moves a product's selected-decrement multipliers to the GLM factor matched on
  the shared dim (sex), falling back to the product-mean — a transparent "what if
  this product's <decrement> moved to the AI-proposed level" run. Documented in
  the page caption.
- **`run_tev` resolves `config/tev_config.yaml` relative to `db_path.parent.parent`.**
  Correct in the real app (DB at `<project>/data/…`); a temp-copy spot-check must
  mirror that layout (`<tmp>/data/copy.duckdb` + `<tmp>/config/`).
- **`lookup_approved_factor`** is a best-effort display aid (mean of approved
  multipliers overlapping the grain), never an input to any calculation; shows
  blank when nothing matches or no APPROVED set exists.
- **Provenance is set-level** (`ai_proposed_value`, `ai_model_id` on the row) per
  §D.4; cell-level factors stay in the assumption YAML. The Stage 2 adopt confirm
  is intentionally minimal (checkbox + value); per-cell wiring is out of scope.
- **Model artifacts** from a `fit_models(register=True)` write to `data/ai_models/`
  + the registry (the sanctioned AI write locations, FR-3A-09); each fit is a new
  `model_id` (FR-3A-25). The page's own queries stay read-only; registration and
  the TEV engine manage their own connections.

---

## Session 18 — LLM Provider Abstraction + MCP Server — COMPLETE ✅ (Phase 3b STARTED, 2026-06-20)

**Goal delivered:** the deterministic infrastructure the chatbot (Session 20)
sits on — a provider-agnostic LLM client (four configured models + a mock
provider) and the read-only `experience_study_data` MCP server (five tools,
stdio, server-side gates) — plus the two remaining AI Gold tables. No LLM
*runtime* behaviour is exercised by the suite (MockProvider only). Strictly
additive under `src/ai/`; reads only Gold + config; the MCP server's dynamic SQL
goes through `src/utils/sql_boundary.py`; its metadata reads use parameterized
`?` queries — **no string-interpolated SQL anywhere** in the new code.

### Phase 0 (interpreter upgrade) — done first
Moved off the 3.9.6 system interpreter to a **uv-managed `.venv` on Python 3.12**
(see the updated Environment notes for the full rationale — system 3.14 has no
ML-stack wheels). Added `anthropic`/`openai`/`mcp` to `requirements.in`,
regenerated `requirements.lock` for 3.12 **with no ML-stack pin drift** (only
`mcp==1.28.0`, `anthropic==0.111.0`, `openai==2.43.0` added; numba/llvmlite/shap/
xgboost unchanged), installed, and **re-verified 814 passed / 6 skipped on 3.12**
before writing any Session-18 code.

### Files added
| Path | What |
|------|------|
| `src/ai/llm/base.py` | `LLMProvider` Protocol (`complete(...) -> LLMResponse`, non-streaming) + `LLMProviderError` (user-safe). No SDK import (FR-3B-01). |
| `src/ai/llm/anthropic_provider.py` | `AnthropicProvider` — **lazy** `import anthropic` inside `complete()`; bounded retry; translates usage → `LLMResponse`. |
| `src/ai/llm/deepseek_provider.py` | `DeepSeekProvider` — **lazy** `from openai import OpenAI` against the DeepSeek `base_url` (OpenAI-compatible, FR-3B-02); system prompt carried as first chat message. |
| `src/ai/llm/mock_provider.py` | `MockProvider` (deterministic, fixture-keyed, zero network, FR-3B-06) + `canonical_key(model, system, messages)` (sha256 of canonical JSON). In-memory `register()`, `{key}.json` fixtures, and a deterministic synthetic fallback. |
| `src/ai/llm/client.py` | `load_llm_config`, `available_models` (greys missing-key models, FR-3B-04), `resolve_provider` (pure dispatch), `build_provider` (key from env only), `complete` (resolve→dispatch; optional injected `provider=` for tests/chatbot). |
| `src/ai/mcp_server/server.py` | The five tools as explicit-keyword `*_impl` core fns + thin FastMCP closures via `build_server`; `serve()`/`run()` → stdio only; `TOOL_SCHEMA_VERSION`. `query_*` route through `execute_safe_select` with a **per-tool single-table allowlist**; metadata tools read manifests via parameterized read-only queries. |
| `config/llm_config.yaml` | §F.2: four models, DeepSeek OpenAI-compatible route, `api_key_env`s, timeout/retry, `default_model`. **Prices `"<set at build>"`** (STOP checkpoint). |
| `tests/fixtures/llm/README.md` | Documents the `(model, system, messages)`→sha256 keying + fixture format. |
| `tests/test_llm_client.py` (16) · `tests/test_mock_provider.py` (7) · `tests/test_llm_providers.py` (8) · `tests/test_mcp_server.py` (16) · `tests/test_mcp_server_protocol.py` (5) · `tests/test_ai_gold_tables.py` (2) · `tests/test_mcp_server_realdata.py` (6) | 60 new tests (43 initial + 17 from the post-build audit). |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended `LLMResponse` (§E.1) after `ValidationResult`. |
| `src/utils/db_init.py` | Appended `gold_ai_eval_results` (§D.2) + `gold_ai_audit_log` (§D.3) to `_GOLD_AI_DDL` (created, not written to this session). Added exported `DEFAULT_DB_PATH` constant and used it as `init_database`'s default. |
| `src/ai/llm/__init__.py`, `src/ai/mcp_server/__init__.py` | Export the public API (were empty Session-14 skeletons). |
| `requirements.in` / `requirements.lock` | Added the LLM/MCP stack; lockfile regenerated for 3.12 (see Phase 0). |

### Definition of done — all met
- [x] Four models callable via the abstraction; missing-key models grey out with reason and the app still functions (FR-3B-02/04).
- [x] Full suite passes with **no API keys present** via MockProvider (FR-3B-06).
- [x] MCP server exposes **exactly five tools**; gates enforced server-side (proven by calling tools directly with adversarial SQL — non-SELECT, off-allowlist, Silver-table, over-cap, multi-statement; AE tool rejects TEV-table SQL and vice versa) (FR-3B-09/10).
- [x] MCP server binds no network interface; **stdio only** (FR-3B-12).
- [x] The two AI Gold tables created (§D.2/§D.3); `gold_ai_model_registry` confirmed not duplicated; idempotent re-init.
- [x] Standing guards green: no-SQL-interpolation scan (now over `src/ai/llm/` + `src/ai/mcp_server/`), import-graph, write-contract, autoescape.
- [x] Real-data spot-check: all five tools end-to-end against run `ed193b59…` (`tests/test_mcp_server_realdata.py`, not skipped).
- [x] Full regression suite green: **874 passed, 6 skipped, no API keys** (was 814/6 — +60 Session-18 tests, no regressions; 857/6 at initial build, 874/6 after the post-build audit added +17).
- [x] **Model pricing filled (2026-06-20)** — owner directed sourcing from public rates; all four `price_per_mtok_*` pairs populated in `llm_config.yaml` (Anthropic Opus 4.8 $5/$25, Sonnet 4.6 $3/$15; DeepSeek V4 Pro $0.435/$0.87, Flash $0.14/$0.28), owner-overridable. Cost display (FR-3B-43) / eval cost gate (NFR-L-04) now compute real figures.

### Post-build hardening
- **Write-contract guard false positive (fixed).** The FR-3A-09 scanner flags any
  `data/…` string literal outside `data/ai_models/`; the MCP server's read-only
  default `Path("data/experience_study.duckdb")` tripped it. Fixed by exporting
  `DEFAULT_DB_PATH` from `src/utils/db_init.py` (an allowed location) and
  importing it — so `src/ai/` carries no bare `data/…` path literal. (Importing
  from `src/utils` is permitted; FR-3A-07 only forbids the *core* engine
  importing `src/ai/`.)

### Post-build correctness audit (2026-06-20, owner-requested)
A focused audit after the initial build (the build was suite-green but two
areas had thin coverage). One latent bug fixed, +17 tests added (857/6 → 874/6):
- **`AnthropicProvider` forwarded `temperature` → 400 on modern models (FIXED).**
  The provider always sent `temperature` to the Messages API, but `claude-opus-4-8`
  (a configured, user-selectable model) rejects sampling params with a 400
  (Opus 4.7+/Fable removed `temperature`/`top_p`/`top_k`). Mock-only Session 18
  never exercised a live call, so it was invisible until the chatbot (Session 20)
  would make one. Fixed: `AnthropicProvider` no longer forwards `temperature`
  (kept in the signature for interface uniformity; `_ = temperature` documents
  the deliberate non-use). The DeepSeek/OpenAI-compatible path **does** forward
  it (that endpoint accepts it). *Minor limitation:* if an older Anthropic model
  that accepts `temperature` is ever added to `llm_config.yaml`, its temperature
  would be silently dropped — acceptable for the configured 4.6/4.8 model set.
- **Provider modules had zero direct coverage (CLOSED).** `tests/test_llm_providers.py`
  (8) injects a fake SDK into `sys.modules` and covers, for both providers:
  success-path `LLMResponse` mapping, retry-then-`LLMProviderError`, missing-SDK
  degradation, and the request shape (Anthropic omits `temperature` + conditional
  `system`; DeepSeek forwards `temperature` + prepends `system` as the first chat
  message). No network, no keys.
- **MCP server only tested at the impl-function level (CLOSED).**
  `tests/test_mcp_server_protocol.py` (5) drives the tools through FastMCP's real
  `call_tool` dispatch (proving the registered closures are wired and results
  survive the tool layer) and asserts every tool result is **JSON-serializable**
  — DuckDB can yield numpy/Decimal/date types that `json.dumps` rejects, which
  would break the MCP transport; `_jsonable()` is confirmed to coerce them.
- **Client gaps closed.** `build_provider` returns the right concrete provider
  when a key is present (and raises without one); `load_llm_config` rejects a
  missing `providers` block; `available_models` surfaces the filled prices.
- **Verified, no change needed:** `FastMCP.run` signature accepts
  `transport="stdio"` (so `serve()` is correct at real runtime); the metadata
  read path opens a read-only connection; the no-interpolation / import-graph /
  write-contract / autoescape guards stay green; lockfile install is a no-op
  (`uv pip install --dry-run` → "no changes").

### Design notes / known limitations (not blockers)
- **`list_available_dimensions` is a bounded sample.** Distinct values come from a
  static `SELECT DISTINCT … FROM gold_ae_results LIMIT 500` (no interpolation,
  gate-4 compliant) with per-column uniqueness in pandas — so low-cardinality
  dimensions are fully enumerated but the list is a representative sample, not a
  guaranteed-exhaustive enumeration. It is a UI/agent hint, not a query result.
  (The literal `LIMIT 500` assumes the configured `chatbot.sql_row_cap` is ≥ 500;
  it is 500.)
- **Metadata tools bypass the allowlist deliberately.** `get_study_run_summary` /
  `get_tev_run_summary` read `gold_study_runs` / `gold_tev_run_log` (not on the
  chatbot allowlist) via a **read-only, parameterized, fixed-column** SELECT —
  the documented sanctioned metadata path (PII-free, no interpolation). The
  dynamic `query_*` tools remain the only allowlist-gated SQL surface.
- **Retry/timeout live on the provider instances** (constructed by `build_provider`
  from `request_timeout_seconds`/`max_retries`), since the `LLMProvider.complete`
  signature is fixed; `client.complete` accepts an injected `provider=` for the
  offline/mock path.
- **GROUP-BY-without-LIMIT is rejected by the boundary** (gate 4 treats it as a
  non-fully-aggregated scan). Callers must add a `LIMIT` or use a single-row
  aggregate — this is the existing Session-14 boundary contract, surfaced here.

### What Session 19 (Claude Skills) needs — preconditions (all satisfied)
- LLM abstraction present: `src.ai.llm.complete(cfg, model_key, …, provider=…)`
  runs any configured model and accepts an injected `MockProvider` for offline
  tests; `LLMResponse` carries text + token counts.
- `gold_ai_audit_log` exists (§D.3) for the Skills' audit rows.
- Reuse for Session 19: `src/ai/chatbot/traceability.py::verify_traceability`
  does **not** exist yet (it lands in Session 20) — but the Session-19 prompt
  block references it. **Heads-up for Session 19:** either implement
  `verify_traceability` as part of Session 19 (the Skills need the numeric
  post-check) or confirm the sequencing; the §E.8 contract imports it from
  `src.ai.chatbot.traceability`.
- Run tests via `.venv/bin/python -m pytest` with keys unset.

---

## Session 19 — Claude Skills — COMPLETE ✅ (2026-06-20)

**Goal delivered:** the two prompt-artifact **Skills** — an AI-drafted A/E memo
(`interpret_ae_and_draft_memo`) and a SHAP explanation (`explain_shap_results`) —
each running on **any** configured model via the §E.5 provider abstraction
(Anthropic *or* DeepSeek), each **blocking, never repairing** when a number fails
the deterministic numeric post-check (FR-3B-19/22). Strictly additive under
`src/ai/` (+ `config/prompts/` + `ui/` wiring); the Skills read no DB (all Gold
reads stay in `ui/` via parameterized read-only queries); no string-interpolated
SQL in `src/ai/`; all call params + model strings in YAML.

### Cross-session decision — `verify_traceability` / `TraceabilityResult` pulled forward
The §E.8 Skill contracts import `verify_traceability` (returning
`TraceabilityResult`) from `src.ai.chatbot.traceability`, but the §E.7 chatbot is
not built until Session 20. **Owner-confirmed:** the numeric post-check was pulled
forward — a small, deterministic, LLM-free slice of §E.7 — as
`src/ai/chatbot/traceability.py` + the `TraceabilityResult` type. **Session 20
consumes this module unchanged** (it is the canonical §E.7 traceability checker,
not a stub); Session 20 should build only the *rest* of `src/ai/chatbot/` around
it (`session.py`, `pipeline.py`, `context.py`).

### Files added
| Path | What |
|------|------|
| `src/ai/chatbot/traceability.py` | `verify_traceability(rendered, result_set, user_msg="", rel_tol=1e-6) -> TraceabilityResult` (§E.7/FR-3B-34). Recursive numeric extraction over the supporting data — actual numeric cells **and** numbers embedded in string values (so study periods / age-bands like `"45-54"` count) — handles both the Skills' nested `memo_input` dict (no `flatten()` needed) and the chatbot's `{columns, rows}` shape (forward-compatible with Session 20). A token traces if it matches an allowed value rounded to the token's display precision (rel tol 1e-6 / abs 1e-9). Strict (no percent/unit rescaling). |
| `src/ai/prompts.py` | `load_prompt_template(name) -> PromptTemplate(name, text, version, sha256, path)`. Introduces `config/prompts/`; version from a leading `<!-- version: X.Y -->` line; `sha256` over full file bytes (FR-3B-08). Shared home for the Session 20–21 prompts too. |
| `src/ai/skills/memo.py` | `interpret_ae_and_draft_memo(memo_input, cfg, model_key, *, provider=None)`. Eight **named** components via `config/prompts/skills/memo.md`; LLM body checked with `verify_traceability(result_set=memo_input)`; on pass the Skill **code-appends** the `AI-DRAFT` tag + footer (`model · date · run_id`) so the footer's date/run_id never risk a false block; block-not-repair on failure. Returns `{markdown, blocked, model, hashes}`. |
| `src/ai/skills/shap_explain.py` | `explain_shap_results(shap_cell_json, feature_to_assumption, cfg, model_key, *, provider=None)`. **Translates** every raw covariate to its `actuarial_term` *before* prompting (FR-3A-39/FR-3B-22 — the LLM never sees a raw feature name); numbers checked against the input cell; block-not-repair; tag + footer appended. |
| `config/prompts/skills/memo.md` · `shap_explain.md` | Versioned templates (`<!-- version: 1.0 -->`). Memo: eight named `##` components, numbers verbatim from input, no tag/footer (Skill wraps). SHAP: 2–3 paragraphs, actuarial terms only, no causal claims/recommendations. |
| `ui/skills_logic.py` | App-side assembler: `assemble_memo_input(...)` (FR-3B-17 fields from Gold via parameterized read-only queries + page context), `assemble_shap_cell_input(shap_json, grain_key)`, `available_skill_models(config_dir)`, `feature_map_for_decrement(...)`. Under `ui/` (not `src/ai/`) so it may read Gold; `src/ai/` never imports it. |
| `tests/test_traceability.py` (5) · `test_prompts_loader.py` (3) · `test_skill_memo.py` (3) · `test_skill_shap.py` (3) · `test_skills_realdata.py` (2, skip-if-absent `prod_db`) | 16 new tests; + 2 source-scan guards added to `test_assumption_comparison_apptest.py` = **+18**. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended `TraceabilityResult` (§E.1) after `LLMResponse`. |
| `config/llm_config.yaml` | Appended a `skills:` block (`memo`/`shap_explain` `max_tokens`+`temperature`) so a Skill needs only the one config dict; nothing hard-coded. |
| `ui/pages/15_assumption_comparison.py` | **Un-greyed** the two Skill buttons (live, `disabled` removed); added a model selectbox (`available_models`), assembled-input memo run, SHAP run on the selected grain cell, in-app render + `.md` download (tag intact), provider-error surfacing. Captures `selected_shap_grain` for the SHAP Skill. |
| `ui/pages/23_tev_stage4.py` | Added a **"Draft A/E memo (AI)"** section (product/decrement/model selectors) reachable from Stage-4 governance (FR-3B-20); render + `.md` download. |
| `tests/test_assumption_comparison_apptest.py` | +2 source-scan guards: Skill buttons live (no `disabled=True`, no "Available in Phase 3b"; both Skills wired) on page 15; memo Skill wired on Stage 4. |

### Definition of done — all met
- [x] Both Skills produce **tagged** output (`AI-DRAFT — requires actuary review and sign-off`) with a generation footer; the memo carries all eight labelled components.
- [x] A deliberately corrupted canned response (an untraceable number) is **blocked, not repaired** by the traceability post-check (memo + SHAP).
- [x] SHAP explanation uses only mapped actuarial terms — no raw feature names leak (translation-before-prompt).
- [x] Both Skills run **provider-agnostically** via the abstraction (exercised with `MockProvider` and an injected stub; no live API).
- [x] Skill invocation points live: Assumption Comparison page (memo + SHAP) un-greyed; memo also on the Stage-4 governance step (FR-3B-20). Exports retain the tag.
- [x] Prompt templates versioned; their hashes are surfaced in each Skill's `hashes` (FR-3B-08).
- [x] Standing guards green: no-SQL-interpolation scan (now over `src/ai/skills/` + `src/ai/chatbot/traceability.py` — these touch no SQL), import-graph, write-contract, autoescape.
- [x] Real-data spot-check: memo assembled from run `ed193b59…` (Gold copy, `init_database` first) + a SHAP cell; both Skills run end-to-end; block-not-repair fires on a corrupted number.
- [x] Full regression suite green: **911 passed, 6 skipped, no API keys** (was 874/6 — +37 Session-19 tests across the build + post-build audit, no regressions).

### Post-build correctness audit (2026-06-20, owner-requested)
A focused audit after the suite-green build found and fixed **two real
correctness defects** (both now covered by tests) and added +19 tests
(892/6 → 911/6):
1. **UUID digit-leakage into the traceability allowed-set (FIXED).** The memo
   passed the whole `memo_input` — including `run_id` — to `verify_traceability`;
   the recursive extractor pulls digit-runs out of strings, so a UUID like
   `ed193b59-…` silently added `193`, `59`, `48`, `33464`, … to the allowed
   numbers and could **mask an invented figure**. Fix: the memo excludes `run_id`
   (and the SHAP Skill excludes `model_id`) from the allowed-set; `run_id` is used
   only for the footer. The memo template also now instructs the model not to cite
   the run_id/identifiers in the body. Covered by
   `test_skill_memo.test_memo_run_id_digits_are_not_traceable`.
2. **Grain-key dimension names reached the LLM untranslated (FIXED, FR-3B-22).**
   The SHAP Skill translated *contribution* feature names to actuarial terms but
   sent the cell's `grain_key` (e.g. `{"duration_band": "6-10"}`) verbatim, so the
   raw model feature name `duration_band` still reached the model. Fix:
   `_translate_cell` now translates grain-key dimension names too (raw → actuarial
   term, or humanised fallback). Covered by
   `test_skill_shap.test_shap_raw_feature_names_never_reach_the_llm`.

Additional tests added in the audit (beyond the two fixes): traceability edge
cases (percent/currency/negative-with-sign-mismatch/hyphenated-band/empty-answer/
absent-number); prompt-loader missing-version → `ValueError`; memo prompt-wiring
capture (system = template, user = input JSON), model-agnostic identical body,
block-path still returns hashes+model, and the **real MockProvider keyed-fixture
path** (not just the deterministic fallback); SHAP raw-names-never-reach-the-LLM
(input boundary), unmapped-feature humanised fallback, block-path metadata; and a
new `tests/test_skills_logic.py` for the app-side assembler helpers
(`assemble_shap_cell_input` match/None/order-insensitive, `feature_map_for_decrement`,
`available_skill_models` greying).

### Design notes / known limitations (not blockers)
- **Skill output skips a slot-filling step.** §E.8 for Skills is generate-then-verify
  (no `fill_numeric_slots` — that grammar is the chatbot's, Session 20). The LLM
  copies numbers verbatim from the input; the post-check enforces it. So the
  `memo_input` numbers must be carried in **display form** (the assembler rounds
  to sensible precision); `verify_traceability` is intentionally strict (no
  percent/unit rescaling) — a deviation blocks rather than repairs.
- **Memo headers are named, not numbered.** Leading list digits (`1.`…`8.`) would
  be parsed as numeric tokens and could false-block, so the eight components use
  named `##` headers; the tag/footer are code-appended after the check.
- **No audit-log writes this session.** `gold_ai_audit_log` exists but full
  per-turn audit logging is Session 21; for now each Skill surfaces its prompt
  template `hashes` (FR-3B-08) in the return value only.
- **Tests use a zero-network stub provider for content control** (plus the real
  `MockProvider` for the dispatch/provider-agnostic check). A stub conforms to the
  `LLMProvider` Protocol and is injected via `complete(provider=...)` — equivalent
  to MockProvider for offline testing, but lets a test pin exact response text to
  assert the eight components / block path without precomputing a `canonical_key`.
- **`ui/skills_logic.py` uses f-string column identifiers** for the fixed,
  internal `_DECREMENT_AE_COLS`/`_DECREMENT_SEGMENT_DIM` mappings (column names
  can't be bind-parameterized); `run_id`/`product` are always parameterized. It is
  under `ui/` (outside the `src/ai/` interpolation-scan scope) and the
  interpolated tokens are never user input.

### What Session 20 (Chatbot Core + Guardrails) needs — preconditions
- **`verify_traceability` already exists** at `src/ai/chatbot/traceability.py`
  with `TraceabilityResult` in `src/utils/types.py` — Session 20 imports them
  as-is (do **not** rebuild). Build the rest of `src/ai/chatbot/`: `session.py`
  (`SessionState`), `pipeline.py` (stages + `handle_turn`), `context.py`
  (`trim_history`, `assemble_rag_context` stub).
- LLM abstraction (`src.ai.llm.complete`, `MockProvider`, `canonical_key`,
  `available_models`) and the MCP server (`src/ai/mcp_server/server.py`, five
  read-only tools) are in place. The chatbot reaches the DB **only** via the MCP
  client (FR-3B-25) — never a direct connection.
- `gold_ai_audit_log` (§D.3) exists for per-turn logging (Session 21 wires the
  full field set; Session 20 logs intent before data access).
- The prompt loader (`src/ai/prompts.py`) is ready for `routing.md` /
  `sql_generation.md` (author under `config/prompts/`, versioned).
- The `chatbot.allowlist` + `chatbot.sql_row_cap` blocks in `config/ai_config.yaml`
  are populated (Session 14/18); `config/chatbot_few_shots.yaml` is authored in
  Session 20, **disjoint from the Session-22 golden set**.
- Run tests via `.venv/bin/python -m pytest` with keys unset.

---

## Session 20 — Chatbot Core + Guardrails — COMPLETE ✅ (2026-06-20)

**Goal delivered:** the seven-stage **guarded conversational pipeline** — the
heart of the AI Analyst interface — built around the Session-19
`verify_traceability` post-check (consumed unchanged). A user message is routed,
turned into a single read-only `SELECT` + an answer template, validated through
all five SQL gates, executed **only** via the gated MCP server, slot-filled
programmatically, and post-checked for numeric traceability before anything
reaches the user. The LLM never writes data, never reaches the DB except through
the server, and never emits a number. Strictly additive under `src/ai/chatbot/`;
no string-interpolated SQL (table routing uses `sqlglot` parsing, not string
building); all caps/limits/model-strings in YAML.

### Cross-session note — `verify_traceability` reused unchanged
`src/ai/chatbot/traceability.py` + `TraceabilityResult` (pulled forward in
Session 19) were imported and reused **as-is** (FR-3B-34). Session 20 built only
the rest of `src/ai/chatbot/`.

### §E.7 reconciliation (owner-confirmed) — combined sql+template
The SQL-generation step returns one JSON object `{"sql", "answer_template"}` in a
single LLM call (`sql_generation.md`). `generate_sql(...) -> str` (the §E.7
signature) is preserved as a thin wrapper returning `plan["sql"]`; the internal
`generate_query_plan(...) -> dict` exposes both fields. `answer_template` uses the
fixed §E.7 slot grammar (`{{col:..}}`, `{{col:..[row]}}`, `{{agg:fn:..}}`), filled
programmatically by `fill_numeric_slots` from the MCP result set (FR-3B-33); the
post-check then passes cleanly because every number came from the result set.
This keeps the data path to one authored prompt (commentary.md is Session 21).

### Files added
| Path | What |
|------|------|
| `src/ai/chatbot/session.py` | `SessionState` (session_id, mutable `model_key` for mid-session switch FR-3B-45, turns, running tokens/cost); `model_prices`/`call_cost`/`record_call` cost helpers (prices from `llm_config.yaml`, FR-3B-43). |
| `src/ai/chatbot/context.py` | `trim_history` (keeps system-prompt budget + most-recent turns within `conversation_token_window`, oldest-first, never drops the system prompt, FR-3B-39); `assemble_rag_context` **stub** (full RAG = Session 21, FR-3B-36). |
| `src/ai/chatbot/mcp_client.py` | `InProcessMCPClient` binding `db_path`/`allowlist`/`row_cap` to the five server `*_impl` tools — the chatbot's **only** DB path (FR-3B-25); optional `on_call` hook emits `data_access` events for ordering tests. `MCPClient` Protocol for typing. |
| `src/ai/chatbot/pipeline.py` | The stages `classify_intent`, `generate_query_plan`/`generate_sql`, `validate_sql` (→ boundary gates 1-4), `execute_via_mcp` (routes AE/TEV by `sqlglot`-parsed table), `fill_numeric_slots`/`_resolve_slots` (+ `SlotFillError`), `assemble_response` (+ exposure/credibility context FR-3B-35); orchestrator `handle_turn`. |
| `config/prompts/routing.md` · `sql_generation.md` | Versioned (`<!-- version: 1.0 -->`) router (4-label strict contract) and schema-grounded SQL+template prompt (schema card + glossary + slot grammar + JSON contract). |
| `config/chatbot_few_shots.yaml` | 24 curated Q→SQL pairs across the five products + query classes, gate-compliant (LIMIT/aggregate), authored disjoint from the Session-22 golden set (FR-3B-30). |
| `tests/chatbot_helpers.py` | Shared offline helpers (not collected): `ScriptedProvider` (routes by system-prompt marker), `StubMCP`, config loaders, `routing_reply`/`sqlgen_reply`. |
| `tests/test_chatbot_{slots,intent,gates,traceability,multiturn,context,realdata}.py` | 34 new tests. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended `IntentLabel` (FR-3B-27) + `ChatTurnResult` (FR-3B-47); no existing type changed. |
| `config/ai_config.yaml` | Extended the `chatbot:` block with `max_turns_per_session: 30`, `conversation_token_window: 16000`, `session_token_budget: 1000000`, `budget_warning_fraction: 0.8`, and inert `faithfulness_*` scaffolding (Session 21). `sql_row_cap`/`allowlist` unchanged. |
| `src/ai/chatbot/__init__.py` | Exports the public pipeline API (was the Session-14 skeleton). |

### Definition of done — all met
- [x] Routing→gates→execution→slot-fill→post-check pipeline works; refusals fire (OUT_OF_SCOPE / write / assumption-change) and intent is logged **before** any data access (FR-3B-27/42).
- [x] All five SQL gates reject + record correctly; a rejected statement is **never** rewritten (its verbatim text is recorded); the MCP server re-enforces the gates independently (proven by calling a tool directly with a TEV-table / non-SELECT statement, FR-3B-10).
- [x] `fill_numeric_slots` parses **exactly** the §E.7 grammar; unresolved/malformed → `SlotFillError` → BLOCK (FR-3B-33).
- [x] A seeded non-traceable number → answer BLOCKED by the reused `verify_traceability` (block-not-repair, FR-3B-34).
- [x] Multi-turn: `trim_history` never drops the system prompt; max-turns prompt fires at the cap; budget warns at 80% and hard-stops at 100% with no LLM call / no silent degradation (FR-3B-39/40/44); model switchable mid-session (FR-3B-45).
- [x] A/E answers carry exposure + credibility-Z context (FR-3B-35).
- [x] Real-data spot-check (`tests/test_chatbot_realdata.py`, skip-if-absent `prod_db`): a factual turn runs end-to-end through `InProcessMCPClient` against run `ed193b59…` (gates pass, slot-fill works, traceability passes, intent-before-data ordering holds); an adversarial turn gate-rejects.
- [x] Standing guards green: no-SQL-interpolation scan (now over `src/ai/chatbot/`), import-graph, write-contract, autoescape.
- [x] Full regression suite green: **962 passed, 6 skipped, no API keys** (was 911/6 — +51 Session-20 tests across the build + post-build audit, no regressions).

### Post-build hardening
- **no-SQL-interpolation scan false trip (fixed).** The scanner's keyword list
  includes `VALUES` (case-insensitive); a `SlotFillError` f-string reading "no
  numeric **values** to aggregate" tripped it. Reworded to "no numeric data to
  aggregate in column" — the only f-string in the chatbot touching a flagged
  word; no behavioural change.

### Post-build correctness audit (2026-06-20, owner-requested)
A focused audit after the suite-green build. **No code defects were found** — the
guardrails behave as specified — and **+17 tests** were added
(`tests/test_chatbot_pipeline_extra.py`, 945/6 → 962/6) to close coverage gaps and
lock subtle behaviours:
- **TEV data path end-to-end** — a `gold_tev_results` question routes to the TEV
  tool and answers (the build's `handle_turn` tests were all A/E).
- **`execute_via_mcp` routing** — AE → AE tool, TEV → TEV tool; a no-table query
  and a query referencing **both** tables are rejected as `unroutable`.
- **Cross-table UNION injection vector** — a `UNION` over both allowlisted Gold
  tables **passes** the boundary (both tables are allowlisted) but is caught at
  the single-table routing layer (`unroutable`) and never executes; the per-table
  server tools would reject it too. Layered defence confirmed.
- **`_parse_plan` robustness** — fenced / embedded JSON parse; malformed or
  `sql`/`answer_template`-incomplete replies → `None` → safe failure.
- **Routing robustness** — an unparseable router reply defaults to `OUT_OF_SCOPE`
  (fail-safe); the refusal path still emits the `intent` audit event.
- **Cost accounting** — `record_call` computes the right USD from the
  `llm_config.yaml` per-model prices (Opus 4.8 1M+1M tok → $30); session totals
  accumulate across the two LLM calls of a data turn.
- **Model switch on the data path** — both the routing and SQL-gen calls use the
  switched `model_key` (the build test only covered the refusal/routing call).
- **Empty result** → `slot_fill_failed` (safe block, not a crash); **negative
  numbers** (e.g. `delta_tev`) fill and trace correctly.
- **No-direct-DB-connection guard** — `pipeline.py` / `session.py` / `context.py`
  import no `duckdb` and open no connection (FR-3B-25); data access is solely via
  the MCP client. Standing guard, grows with the layer.

### Design notes / known limitations (not blockers)
- **`handle_turn` signature.** Matches the §E.7 positional contract
  (`user_msg, state, cfg, mcp_client, allowlist`) with keyword-only extras
  (`chatbot_cfg`, `few_shots`, `provider`, `audit`, `prompts_dir`) — the same
  pattern Session 19 used to add `*, provider=` to the Skills. `cfg` is the
  parsed `llm_config.yaml` (drives `complete` + pricing); `chatbot_cfg` is the
  `ai_config.yaml` `chatbot` block (limits + row cap).
- **Verify runs on the FINAL assembled text.** `assemble_response` appends the
  exposure/credibility context (values from result cells) *before*
  `verify_traceability`, so the post-check covers the complete rendered answer
  (FR-3B-34 "final rendered answer") — a deliberate ordering refinement of the
  plan's fill→verify→assemble sketch.
- **Client-side aggregates stay traceable.** `_resolve_slots` returns the numbers
  it injected; `handle_turn` passes them to `verify_traceability` as `computed`
  values so a system-computed `{{agg:..}}` result is traceable to the data (it is
  a deterministic function of the result set), not flagged as model-invented.
- **AE/TEV routing is single-table.** `execute_via_mcp` routes by the one Gold
  table the SQL references; a query spanning both is treated as unroutable (and
  the single-table-scoped server tools would reject a cross-table join anyway).
- **Intent logging is a pluggable `audit` sink** (ordered event recorder). The
  full per-turn `gold_ai_audit_log` DB write is Session 21; Session 20 emits the
  `intent` event before any `data_access` event (FR-3B-27) and a `sql_validation`
  event, sufficient for the ordering guarantee and offline tests.

### What Session 21 (RAG Commentary + Audit + AI Analyst Page) needs — preconditions
- The pipeline seam is ready: `context.assemble_rag_context` is a documented stub
  to fill (FR-3B-36); the `COMMENTARY_GENERATION` route currently returns a
  number-free "pending" message and is where the grounded-draft path attaches.
  Commentary reuses the same `fill_numeric_slots` + `verify_traceability` regime
  (FR-3B-37) and must carry the "AI-drafted — pending actuary review" banner
  (FR-3B-38).
- Audit: `gold_ai_audit_log` (§D.3) exists; Session 20 already emits ordered
  `intent` / `sql_validation` events through the optional `audit` sink — Session
  21 wires the **full** per-turn field set to the DB (FR-3B-47) and the
  Study-Run-Log query view (NFR-A-07), plus the optional faithfulness judge
  (config keys `faithfulness_llm_judge` / `faithfulness_flag_threshold` are
  already present, inert).
- AI Analyst page: `available_models` (greys missing-key models), `SessionState`
  (running tokens/cost), and `handle_turn` are ready to drive the Streamlit page
  (FR-3B-43); `config/prompts/{commentary,faithfulness_judge}.md` are the two
  prompts Session 21 authors.
- Run tests via `.venv/bin/python -m pytest` with keys unset.

---

## Session 21 — RAG Commentary + Audit + AI Analyst Page — COMPLETE ✅ (2026-06-20)

**Goal delivered:** filled the four deliberate Session-20 seams — real RAG-grounded
commentary, the optional LLM-as-judge faithfulness score, full per-turn audit
logging to `gold_ai_audit_log`, and the **AI Analyst** Streamlit page. Strictly
additive under `src/ai/` (+ `config/prompts/`, `config/ai_config.yaml`, `ui/`); the
pipeline stays DB-free (FR-3B-25) — the only write is the sanctioned
`gold_ai_audit_log` INSERT (one of the three AI Gold tables). All commentary data
flows through the MCP client; no string-interpolated SQL in `src/ai/`.

### Design decisions (spec reconciliation)
- **Commentary numbers come via the MCP data path, grounded by RAG prose.**
  FR-3B-37 mandates the *same* `fill_numeric_slots` + `verify_traceability` regime
  as the data path, and FR-3B-25 forbids any DB path but the MCP client — so the
  commentary route reuses Session 20's machinery (commentary prompt returns the
  same `{sql, answer_template}` JSON → validate → MCP execute → slot-fill →
  traceability). The RAG grounding (the tool's *own* reports + methodology docs,
  FR-3B-36) is injected into the prompt for the qualitative prose **and** joined to
  the traceability allowed-set, so a figure quoted verbatim from the tool's own
  report doesn't false-block while an invented number still blocks.
- **RAG reads files, never the DB.** `assemble_rag_context` reads only the report
  HTML + methodology markdown handed to it (resolved by `resolve_rag_artifacts`
  from the `chatbot.rag` config); it strips HTML, bounds length, and degrades to
  methodology-only grounding when a run has no report (e.g. `ed193b59…`).
- **Audit row built inside `handle_turn`, written by an injected sink.**
  `handle_turn` is now a thin wrapper over `_run_turn(ctx, …)`: the inner function
  populates a per-turn `ctx` (tokens, cost, latency, intent reason, faithfulness,
  grounding refs) and the wrapper emits one `{"event":"turn", …§D.3 fields…}` event
  to the `audit` sink at the single tail. The Session-20 `intent` / `sql_validation`
  / `data_access` events and the `ChatTurnResult` shape are unchanged (no
  regression). The DB sink (`audit.make_db_audit_sink`) writes only on the `turn`
  event; `pipeline.py` opens no connection.
- **Faithfulness flags, never blocks.** Off by default (`faithfulness_llm_judge`);
  when on, a separate judge call scores the draft 1–5 against the grounding, a
  score ≤ `faithfulness_flag_threshold` appends a visible warning, and the score is
  logged — blocking stays reserved for the deterministic checks (FR-3B-46).

### Files added
| Path | What |
|------|------|
| `src/ai/chatbot/audit.py` | `write_audit_row` (static 26-`?` parameterized INSERT, `registry.py` pattern; imports `DEFAULT_DB_PATH` — no bare `data/` literal) + `make_db_audit_sink` (writes on the `turn` event only). The sanctioned `gold_ai_audit_log` write (FR-3B-47). |
| `config/prompts/commentary.md` · `faithfulness_judge.md` | Versioned (`<!-- version: 1.0 -->`) RAG-grounded commentary template (returns `{sql, answer_template}`, slots only, prose grounded in the appended context) and the 1–5 faithfulness rubric (single-integer output). |
| `ui/ai_analyst_logic.py` | App-side orchestration (mirrors `skills_logic.py`): config loaders, `available_analyst_models` (greying), `list_study_runs`, `resolve_rag_for_run`, `build_mcp_client`, `run_turn` (drives `handle_turn` with the DB audit sink + RAG), `export_conversation_markdown` (banners preserved). Read-only DB except the injected audit sink. |
| `ui/pages/16_ai_analyst.py` | The AI Analyst page (FR-3B-43): model dropdown, run selector, token/cost metrics + budget warn/stop, chat loop over `SessionState`, mid-session model switch, Markdown export. |
| `tests/test_rag_context.py` (6) · `test_chatbot_commentary.py` (6) · `test_chatbot_audit.py` (5) · `test_ai_analyst_logic.py` (5) · `test_ai_analyst_apptest.py` (2) | 24 new tests + 1 commentary case added to `test_chatbot_realdata.py`. |

### Files modified
| Path | Change |
|------|--------|
| `src/ai/chatbot/context.py` | Implemented `assemble_rag_context` (HTML-stripping, bounded, file-only) + `resolve_rag_artifacts`; kept `trim_history`. No `duckdb` import (FR-3B-25 guard). |
| `src/ai/chatbot/pipeline.py` | Added `_generate_commentary`/`generate_commentary_plan`, `_judge`/`score_faithfulness`, the real `_commentary_turn` path, `_accumulate`/`_build_audit_row`/`_prompt_template_hashes`; split `handle_turn` into `_run_turn` + an audit-emitting wrapper (with `rag_run_ids`/`rag_artifact_paths` kwargs). Removed the obsolete `_COMMENTARY_PENDING_TEXT`. |
| `src/ai/chatbot/__init__.py` | Exported the new commentary/faithfulness/audit/RAG-resolve helpers. |
| `config/ai_config.yaml` | Added the `chatbot.rag` block (reports_dir, methodology_docs, max_grounding_chars); the `faithfulness_*` keys are now consumed. |
| `ui/app.py` | New nav group **"AI Analyst (Phase 3b)"** → page 16. |
| `ui/pages/07_run_log.py` | Added a read-only **AI Activity Log** expander over `gold_ai_audit_log` (NFR-A-07). |
| `tests/chatbot_helpers.py` | `ScriptedProvider` now also routes commentary / faithfulness replies by system-prompt marker. |
| `tests/test_chatbot_intent.py` | Updated the obsolete commentary-stub test to assert the new grounded, banner-tagged draft. |

### Definition of done — all met
- [x] Commentary grounded in the tool's own artifacts; banner present and survives Markdown export (FR-3B-36/38).
- [x] Commentary numbers fill via slots + pass traceability; an invented number blocks (FR-3B-37/34).
- [x] Faithfulness judge off by default; when on, a low score flags-not-blocks and is logged (FR-3B-46).
- [x] Per-turn `gold_ai_audit_log` row written with the full §D.3 field set via static parameterized INSERT; reconstructable (prompt-template hashes + dynamic parts); model switch mid-session logged (FR-3B-47/41/45); queryable from the Study Run Log page (NFR-A-07).
- [x] AI Analyst page: configured-model dropdown (greys missing keys), running token/cost + budget warn/stop, export with banners (FR-3B-43).
- [x] Standing guards green (no-SQL-interpolation scan over `audit.py`/`context.py`/`pipeline.py`; import-graph; write-contract; Jinja autoescape; chatbot core opens no direct DB connection).
- [x] Real-data spot-check: commentary turn end-to-end against run `ed193b59…` (gates pass, slot-fill works, traceability passes, audit row written) + AI Analyst `AppTest` render smoke — both pass with the prod DB present.
- [x] Full regression suite green: **998 passed, 6 skipped, no API keys** (was 962/6 — +36 across the build + post-build audit, no regressions).

### Post-build correctness audit (2026-06-20, owner-requested)
A focused audit after the suite-green build. **No code defects were found** — the
guardrails behave as specified — and **+11 tests** were added to close coverage
gaps and lock subtle invariants:
- **Audit INSERT column alignment locked** — `test_insert_column_list_matches_columns_in_order`
  parses the column list out of `_INSERT_SQL` and asserts it equals `_COLUMNS`, and
  `test_full_row_roundtrips_every_column_in_alignment` writes a distinct value per
  column and reads each back — catching any silent off-by-one between the
  hand-written INSERT column list, `_COLUMNS`, and `_coerce` (a data-corruption
  class the count-only `assert` could not catch).
- **Commentary guard paths** — SQL gate rejection (non-SELECT → `REJECT_NOT_SELECT`;
  off-allowlist Silver table → `REJECT_ALLOWLIST`), slot-fill failure, and
  generation failure (unparseable reply) all **block** on the commentary route
  exactly as on the data path; the audit row for a commentary turn carries the
  `commentary.md` hash and the `retrieved_context_ref`.
- **Faithfulness robustness** — an unparseable judge reply yields `None` (no
  warning, not blocked, `faithfulness_score` NULL in the audit); the public
  `generate_commentary_plan` / `score_faithfulness` wrappers are unit-tested.
- **None-intent audit path** — a pre-routing budget hard-stop still writes a row
  (intent NULL, `blocked=True`, `model_string` falling back to the session model),
  and an OUT_OF_SCOPE refusal is logged with its intent.

### Design notes / known limitations (not blockers)
- **Commentary fetches its numbers from one SQL query** (the data-path mechanism),
  with the RAG report text covering qualitative claims and any figure quoted from
  the report (which joins the traceability allowed-set). A commentary spanning both
  A/E and TEV in one turn would need the model to pick the most relevant single
  query; broader multi-query commentary is out of scope for the prototype.
- **`gold_ai_audit_log` has no `study_run_id` FK** (per §D.3), so the Run-Log page
  shows recent AI activity globally (the grounded run is carried inside
  `retrieved_context_ref`), not filtered per study run — matches the spec's
  "queryable from the Study Run Log page alongside the chatbot audit log".
- **`audit.write_audit_row` uses `datetime.utcnow()`** to match the established
  `registry.py` convention (emits the same benign deprecation warning as the rest
  of the codebase); a project-wide switch to timezone-aware UTC is out of scope.
- **The faithfulness warning carries the score digit** in the rendered text; it is
  appended *after* the deterministic traceability check (a system annotation, not
  an LLM-emitted figure), so it does not affect the post-check.

### What Session 22 (Evaluation Harness + Phase 3 UAT) needs — preconditions
- The guarded pipeline is complete and audited: `handle_turn` returns a
  `ChatTurnResult` and writes a `gold_ai_audit_log` row per turn; the eval harness
  (`src/ai/eval/`) drives this same pipeline + the MCP server (which it also calls
  directly with adversarial SQL to prove server-side gates).
- Hard gates (gate integrity 100%, numeric traceability 100%) ride on the existing
  `verify_traceability` + the boundary gates; `results_match` (§F.5/FR-3B-51) is
  the new piece.
- Author `tests/eval/golden_set.yaml` (30–50 Q→SQL) + `adversarial_set.yaml`
  (10–15), **disjoint from `config/chatbot_few_shots.yaml`** (the disjointness test
  lands in Session 22); **STOP — OWNER INPUT** to lock the sets before the baseline.
- `gold_ai_eval_results` (§D.2) already exists (Session 18); the harness persists
  metrics there. The harness must refuse to run inside pytest (FR-3B-53).
- Assemble the Phase 3 UAT script from the Session-17 and Session-21 page sections
  in `docs/phase3_uat_script.md` + one eval run on ≥2 models.
- Run tests via `.venv/bin/python -m pytest` with keys unset.

---

## Session 22 — Evaluation Harness + Phase 3 UAT — COMPLETE ✅ (Phase 3b / Phase 3 CLOSED, 2026-06-20)

**Goal delivered:** the CLI **evaluation harness** that measures the guarded
chatbot against a locked golden Q→SQL set and an adversarial set, enforcing the two
hard gates (gate integrity = 100 %, numeric traceability = 100 %) and reporting
execution + routing + refusal accuracy per model; the locked-pending eval sets; the
`eval:` config; and the finalised Phase 3 UAT script. Strictly additive under
`src/ai/eval/`; it **drives the existing** `handle_turn` pipeline and MCP server and
**rebuilds nothing** (no change to the pipeline, MCP server, `verify_traceability`,
the SQL gates, or the slot grammar). No string-interpolated SQL in `src/ai/` — the
harness reads Gold only through the gated MCP client / boundary, and the
`gold_ai_eval_results` INSERT is a static `?`-placeholder statement (the
`audit.py`/`registry.py` pattern, importing `DEFAULT_DB_PATH`).

### Design decisions (spec reconciliation)
- **One gated data path for the result-match (FR-3B-25/51).** Execution accuracy
  needs the *generated* query's result set and the *reference* query's result set.
  Rather than open a second DB connection, `run_eval` materialises **both** through
  the same MCP client (`execute_via_mcp`) — the generated SQL from the
  `ChatTurnResult` and the reference SQL from the locked golden YAML — so the server
  re-enforces gates 1–5 on every query and `results_match` compares like-for-like.
- **Hard gates from observed pipeline behaviour, not hardcoded.**
  `numeric_traceability` counts any answer flagged non-traceable (block-reason
  `numeric_traceability`, or a failed `TraceabilityResult`); `gate_integrity` counts
  any turn that *executed* SQL (PASS + a row count) whose re-validation is not PASS
  — structurally 0 with the boundary intact, but a real function of behaviour so a
  future regression would surface. Both helpers (`has_untraceable_number`,
  `gate_integrity_violation`) are unit-tested directly with synthetic
  `ChatTurnResult`s.
- **Adversarial robustness to routing variation.** For an `expect: gate_reject`
  entry, `expect_ok = gate_rejected OR refused` — an injection that the router sends
  to OUT_OF_SCOPE (refusal) and one that reaches the gates and is rejected both
  count; `gate_integrity` only ever penalises *executed* disallowed SQL (which the
  boundary prevents).
- **`run_eval` signature extends §E.9 with keyword-only extras** (`provider`,
  `chatbot_cfg`, `few_shots`, `prompts_dir`, `db_path`, `persist`, `est_cost_usd`)
  — the same pattern Sessions 19/20 used. `provider=None` ⇒ the live model;
  tests inject a scripted, zero-network provider.

### Files added
| Path | What |
|------|------|
| `src/ai/eval/result_match.py` | `results_match(...)` — the FR-3B-51 rule: identical column-name set (order-insensitive), identical row count, sorted-multiset row equality, numeric tolerance (rel 1e-6 / abs 1e-9), NULLs-match-NULLs; `value_check: false` applies only column-set + row-count. `None` (errored/empty) ⇒ miss. Pure Python, no SQL. |
| `src/ai/eval/runner.py` | `EvalMetrics` dataclass + `run_eval` (drives `handle_turn` per golden Q; materialises reference + generated SQL via the MCP client; computes the five metrics) + the accounting helpers (`has_untraceable_number`, `gate_integrity_violation`, `is_refused`, `_execution_match`) + `persist_eval_metrics` (static `?`-placeholder INSERT into `gold_ai_eval_results`, 15 cols, prompt-template + tool-schema hashes, FR-3B-52) + `load_golden`/`load_adversarial`. |
| `src/ai/eval/__main__.py` | CLI (`python -m src.ai.eval`): `_assert_not_under_pytest` (FR-3B-53), `confirm_cost`/`estimate_cost` (NFR-L-04), `select_models`/`format_table`, `run_smoke` (FR-3B-54), `main` (per-model run, table print, non-zero exit on a hard-gate fail). |
| `tests/eval/golden_set.yaml` | 36 locked-pending Q→SQL entries across all five product families and the five query classes (factual / segmented / TEV / credibility-context / time). |
| `tests/eval/adversarial_set.yaml` | 12 locked-pending probes (injection, write/DDL, off-allowlist table, PII, out-of-scope, assumption-change), each `expect: gate_reject \| refusal`. |
| `tests/test_result_match.py` (15) · `test_eval_runner.py` (9) · `test_eval_cli.py` (7) · `test_eval_sets.py` (5) · `test_eval_realdata.py` (2, skip-if-absent `prod_db`) | **38** new tests. |

### Files modified
| Path | Change |
|------|--------|
| `config/ai_config.yaml` | Appended the `eval:` block (`eval_cost_confirm_threshold: 5.00`, NFR-L-04). |
| `src/ai/eval/__init__.py` | (Unchanged docstring; the package is now populated.) |
| `docs/phase3_uat_script.md` | Appended the Session-22 eval-harness section (automated mechanics table, owner-triggered live baseline table on ≥2 models, ≥3 manual adversarial prompts) + the **Phase 3 sign-off** table. |
| `CLAUDE.md` | Phase 3 marked **DONE** (build complete; live baseline + UAT owner-triggered); Session 22 note added. |

### Definition of done — all met
- [x] `results_match` honours all five clauses with `value_check: true`, and columns + row-count only with `false`; error/empty ⇒ miss (15 tests).
- [x] `run_eval` drives the real `handle_turn`, scores intent + execution (reference vs generated via the gated MCP path), and persists one row per (harness run × model) to `gold_ai_eval_results` (FR-3B-52); a seeded non-traceable number drops `numeric_traceability`; the `gate_integrity` accounting flags executed disallowed SQL.
- [x] Golden (36) + adversarial (12) sets authored in §F.5 format, gate-compliant (every reference SQL PASSes the boundary — `test_eval_sets.py`), spanning all five product families + the TEV class; **disjoint** from `chatbot_few_shots.yaml` (FR-3B-30/49).
- [x] CLI refuses to run inside pytest (FR-3B-53); cost-confirm prompt fires above the threshold (NFR-L-04); per-model table + non-zero exit on a hard-gate fail.
- [x] MCP gate proof independent of the chatbot (`test_eval_realdata.py`: a direct `query_ae_results_impl` DDL / Silver read → structured error, never executed).
- [x] Standing guards green: no-SQL-interpolation scan now over `src/ai/eval/`; import-graph; write-contract (`gold_ai_eval_results` is a permitted AI write target); Jinja autoescape.
- [x] Real-data spot-check: `run_eval` end-to-end against run `ed193b59…` (prod copy, `init_database` on the copy) — exec/gate/trace/route all 1.0, one eval row written.
- [x] Full regression suite green: **1042 passed, 6 skipped, no API keys** (was 998/6 — +44 Session-22 tests across the build + post-build audit, no regressions).

### Post-build correctness audit (2026-06-20, owner-requested)
A careful verification pass after the suite-green build found **one spec-compliance
defect** (fixed) and added **+6 tests** (1036/6 → 1042/6):
- **`EvalMetrics` carried three non-spec fields (FIXED).** The §E.9 dataclass is
  exactly seven fields (`model`, the five metrics, `per_question`); the build had
  added `n_golden`/`n_adversarial`/`actual_cost_usd` — an "invent an alternative
  return type" violation (rule #2). Those values are now parameters of
  `persist_eval_metrics`; the returned `EvalMetrics` matches §E.9 exactly.
- **`gold_ai_eval_results` INSERT alignment locked.**
  `test_eval_insert_column_list_matches_columns_in_order` parses the column list
  out of `_INSERT_EVAL_SQL` and asserts it equals `_EVAL_COLUMNS`;
  `test_eval_full_row_roundtrips_every_column_in_alignment` writes a distinct value
  per column and reads each back — catching any silent off-by-one between the
  hand-written INSERT, `_EVAL_COLUMNS`, and the values list (the same data-corruption
  class the Session-21 audit closed for `audit.py`).
- **Locked golden set validated against real data.**
  `test_locked_golden_sql_runs_and_returns_declared_columns` executes all 36
  reference queries against the production Gold schema and asserts each returns
  exactly its declared columns (and value_check entries return one row) — so an
  authoring bug in the locked set (a mistyped column/alias, or a query that errors
  on real data) can't silently score every model a miss. Confirmed all locked
  filters hit real data (AE product codes `TERM/WL/UL/ULSG/VUL/DA_FIXED/DA_VA`
  present; TEV has exactly 6 baseline rows, matching the declared `row_count: 6`).
- **Locked adversarial set scored end-to-end** through `run_eval`
  (`test_run_eval_over_locked_adversarial_set_holds_hard_gate`): gate integrity 1.0,
  refusal correctness 1.0, every probe's `expect` satisfied.
- **`results_match` on real MCP-materialised rows** with a column- and row-reordered
  but equivalent generated query (`test_execution_match_on_reordered_equivalent_query`).
- **CLI smoke wiring** offline (`test_run_smoke_offline_wiring`), plus a manual
  end-to-end check that `python -m src.ai.eval --help` is wired and the no-keys path
  degrades gracefully (all four models greyed, exit 0, no DB write / model call).
No further code defects found; standing guards stay green over `src/ai/eval/`.

### STOP — OWNER INPUT (RESOLVED — locked 2026-06-20, §12.2)
The owner reviewed `tests/eval/golden_set.yaml` + `tests/eval/adversarial_set.yaml`
(36 golden + 12 adversarial) and **locked them as the authoritative baseline** on
2026-06-20; the file headers record the lock. The **live eval baseline** (≥2 models,
one Anthropic + one DeepSeek) and the Phase 3 UAT sign-off remain owner-triggered and
are **not** part of the pytest gate (the offline harness mechanics are).

### Design notes / known limitations (not blockers)
- **Declared `expected_result.columns/row_count` are documentation.** The harness
  compares the model's generated result to the reference query's *executed* result
  (FR-3B-51), so for `value_check: false` entries the declared `row_count` is a
  documented bound, not the comparison basis (which is generated-vs-reference live).
- **`gate_integrity` is structurally 1.0 from the chatbot** (the boundary prevents
  disallowed SQL from executing); its accounting helper is unit-tested with a
  synthetic contradictory `ChatTurnResult`, and the MCP tools are also probed
  directly (`test_eval_realdata.py`) to prove the gate independent of the pipeline.
- **`run_eval` persists to the `db_path` it was given** (the prod/synthetic DB that
  already holds `gold_ai_eval_results`); the CLI defaults that to `DEFAULT_DB_PATH`.
- **`utcnow()` deprecation warning** matches the established `registry.py`/`audit.py`
  convention; a project-wide switch to timezone-aware UTC is out of scope.

---

## Post-UAT hardening — owner UAT of the Skills (2026-06-25 → 06-26)

Phase 3 build was CLOSED at Session 22; these are **bug fixes + one additive feature**
from the owner's hands-on UAT of the two Skills (A/E memo, SHAP explanation) on live
models. No phase reopened; all changes are additive/corrective and the offline regression
gate stayed green throughout. **Suite now 1056 passed, 6 skipped** (no keys; +14 over the
Session-22 1042/6 baseline). Each fix below has a regression test.

**1. API-key guidance (docs only).** Keys are env-var-only by design (FR-3B-04); there is
intentionally no in-app field. Added an "Enabling the AI features (API keys)" section to
`README.md` (which env var un-greys which models; `export …KEY=… && streamlit run ui/app.py`).
No code change.

**2. Empty memo/SHAP output → fail loudly (not a tag+footer-only file).** A model returning
empty content (DeepSeek V4 reasoning model exhausting `max_tokens` on reasoning) silently
produced a memo with only the AI-DRAFT tag + footer. Fixed at three layers:
`src/ai/llm/deepseek_provider.py` now raises `LLMProviderError` on empty content (with a
truncation hint when `finish_reason='length'`) instead of returning `""`; `src/ai/skills/
memo.py` + `shap_explain.py` **block** with a clear reason when the body is empty;
`config/llm_config.yaml` skill `max_tokens` raised (memo 2000→4096, shap 800→2048); pages
15/23 render the block reason and don't offer an empty download. +6 tests.

**3. Numeric-traceability false-block on age/duration bands + dates (real bug in the §E.7
checker).** `src/ai/chatbot/traceability.py` `_NUMBER_RE` parsed the hyphen in a band label
(`"25-29"`) / date (`"2023-12-31"`) as a **minus sign**, so the input extracted `25,-29`
while the model's prose (`25–29` en-dash, `25 to 29`) extracted `25,+29` → upper endpoints
never traced and the memo blocked. Fixed with a `(?<!\d)` look-behind so a hyphen between
digits is a **range separator** (both endpoints positive), while genuine leading negatives
(`-0.05`, `-4,480,000`) still parse negative. Benefits the chatbot too (same checker). +4
traceability tests (en-dash/em-dash/"to"/date-fragment cases; negative-sign cases preserved).

**4. Memo prompt hardening + `study_years`.** `config/prompts/skills/memo.md` → v1.2 (and
`shap_explain.md` → v1.1): quote decimals verbatim, never convert to percentages, no
numbers/years/dates not in the JSON, no external-event references (e.g. "COVID-19"), no
numbered lists. `ui/skills_logic.py::assemble_memo_input` now carries `study_years` (the
inclusive study-window year list) so in-window year references trace.

**5. A/E-by-segment was truncated, not aggregated (memo showed all-0.0 / all "25-29").**
`ui/skills_logic.py::_ae_by_segment` read raw detail rows (the full cross-product of other
dims) and `ORDER BY … LIMIT 12` surfaced only the youngest (zero-death) band. Rewrote it to
**aggregate** `SUM(actual)/SUM(expected)` by the segment dim and recompute credibility Z from
the aggregate count (`ui/stats_helpers.credibility_z` + `get_run_method`, FR-1A-24) — the
proven `aggregate_ae` convention incl. `illness_code IS NULL`. The **study data was always
correct** (verified read-only: WL mortality A/E ≈ 0.57, sensible by-band spread); only the
memo's assembly was wrong. Also rounded the SHAP cell numbers to 4 dp in
`assemble_shap_cell_input` for readability. +2 tests.

**6. SURRENDER memo decrement (additive feature).** Annuity/WL/UL **surrender** experience
couldn't be memo'd (no SURRENDER decrement). Added `SURRENDER` to `DecrementType`
(`src/utils/types.py`) as **experience/memo-only** — it never reaches the GLM/GBM engine:
`fit_models` short-circuits to the standard "no AI proposal" state (`ui/ai_comparison_logic.py`),
and the memo maps gained surrender entries (`_DECREMENT_COMPONENTS` → `actual/expected_surrenders`,
`_DECREMENT_SEGMENT_DIM` → `duration_band`). Offered on the Assumption Comparison page
("Surrender (memo only)") and auto on Stage 4 (`list(DecrementType)`). Surrender A/E is already
populated in Gold, so surrender memos work immediately. +2 tests.
  > **Spec note (RESOLVED 2026-06-26):** the owner authorised a **formal in-place amendment**
  > of the locked Tech Spec **v2.0.1** — §E.1 `DecrementType` now lists `SURRENDER` (annotated
  > memo/experience-only, not GLM/GBM-modelled) and a dated header change-note records it; the
  > filename is retained so `@docs/...v2_0_1.md` cross-refs stay valid. The §E.1 statement that
  > mortality/lapse/CI-incidence are "the three decrements the AI layer **models**" remains
  > accurate. Tracked as FU-4 (RESOLVED) in `docs/DEFERRED_FOLLOWUPS.md`.

**7. IUL lapse A/E was undefined (`expected_lapses = 0`).** `src/calculation/ae_engine.py`
normalised DA sub-types to "DA" for the lapse-benchmark join but had no IUL entry, and
`lapse_benchmarks.parquet` has no IUL rows → IUL's join missed → `lapse_rate` filled to 0.
Promoted the map to module-level `_LAPSE_PARENT` and added `"IUL": "UL"` so IUL borrows UL's
lapse basis (mirrors DA→DA; IUL is an indexed-UL variant). Minimal blast radius (only IUL
changes). +2 tests.
  > **Re-run caveat:** this affects the A/E **engine**, so it takes effect on the **next**
  > study run — the current `gold_ae_results` (run `07ec6a88…`) still shows IUL
  > expected-lapses 0 until the owner re-runs the study (UI "Run Study"). A distinct IUL
  > lapse basis (vs UL) can be added later as IUL rows in `lapse_benchmarks.parquet`.

**Files touched:** `README.md`, `src/ai/llm/deepseek_provider.py`,
`src/ai/skills/{memo,shap_explain}.py`, `src/ai/chatbot/traceability.py`,
`config/llm_config.yaml`, `config/prompts/skills/{memo,shap_explain}.md`,
`ui/skills_logic.py`, `ui/ai_comparison_logic.py`, `ui/pages/15_assumption_comparison.py`,
`src/utils/types.py`, `src/calculation/ae_engine.py`, and the matching tests
(`tests/test_{traceability,skill_memo,skill_shap,llm_providers,skills_logic,ae_engine_1b}.py`).

---

## Post-UAT hardening (round 2) — owner UAT of the AI Analyst chatbot (2026-06-26)

Owner UAT of the **AI Analyst** page (Session 21) found the chatbot effectively unfit for
purpose: it quoted **0** for every A/E figure, said only **DA_FIA** was covered, refused/failed
reasonable follow-ups, and the commentary path + faithfulness judge were undiscoverable. Root-
cause investigation (3 Explore agents + read-only DB queries) confirmed the **study data was
always sound** (the run `dd9784f2…` has 9 products incl. WL; true WL mortality A/E ≈ **0.5718** =
232/405.76) — the failures were **query-grain + slot + routing bugs**, the same class as the
earlier `_ae_by_segment` memo fix. Owner chose to fix **within the governed architecture** (no
agentic loosening) and to address all three UI gaps. All offline-gate-green; **suite now 1070
passed, 6 skipped** (+14 over the 1056 baseline). Each fix has a regression test.

**1. "All A/E = 0" — the core fix (ratio-of-sums aggregation).** `gold_ae_results` has **no
grand-total row**; every row is a detail cell (young age bands ≈ 0 expected/0 A/E), and the
SQL-gen prompt/few-shots never taught the model that **A/E = SUM(actual)/SUM(expected)** — so the
`{{col:ae_count}}` slot grabbed row[0], a near-zero cell. Rewrote `config/prompts/sql_generation.md`
→ **v1.1** (no grand-total row; aggregate as `SUM(actual)/NULLIF(SUM(expected),0)`; `illness_code
IS NULL` for mortality/lapse/surrender, `IS NOT NULL` for CI; always SUM the expected/exposure for
context) and added 6 aggregate/list few-shots to `config/chatbot_few_shots.yaml` (gate-compliant,
disjoint from the locked golden set). End-to-end on the prod DB the chatbot now answers "WL
mortality A/E is **0.5718** (232 actual vs 405.76 expected)".

**2. "Products = DA_FIA" — `{{list:}}` slot.** The slot grammar could surface only one value, so a
9-row product list collapsed to one. Added a `{{list:<column>}}` slot to `_resolve_slots`
(`src/ai/chatbot/pipeline.py`, comma-joined, order-preserving de-dup; numeric list values stay
traceable) and documented it in the prompt. "Which products are covered?" now lists all 9.

**3. Misleading row[0] context.** `assemble_response` appended exposure/credibility from `rows[0]`
even on multi-row detail sets ("credibility Z = 0", "exposure 2"). Now it appends the statistical
context **only for single-row** (aggregate/one-cell) results; multi-row answers rely on the
template's own SUM slots.

**4. Over-refusal + DeepSeek `llm_error`.** `config/prompts/routing.md` → **v1.1**: follow-ups /
brief continuations that reference prior results ("why is it that low?", "try", "I thought WL was
covered?") are data questions (FACTUAL/EXPLORATORY), not OUT_OF_SCOPE; PII/action/general refusals
unchanged. The router now also sees the last few turns (`_route` takes `history`). The DeepSeek
`llm_error` turns were the **routing call dying** on a hardcoded `_ROUTING_MAX_TOKENS = 256`
exhausted by V4-Pro reasoning tokens → empty content → error (same empty-output class as the memo
fix). Per-call token caps moved to `config/ai_config.yaml` `chatbot.max_tokens` (routing 1024,
sql_generation 1536, commentary 2048, faithfulness 64; NFR-CF-10) and the defaults raised to match.

**5. Run scoping made real.** The selected run reached the pipeline only as RAG grounding while the
UI claimed "scopes data". `_generate`/`_generate_commentary` now inject a run-scope instruction
(`study_run_id = '<id>'`) into the SQL-gen system prompt (`_render_run_scope`, keyword-free so the
no-interpolation guard isn't tripped — it's prose, the boundary still validates every query).

**6. UI discoverability.** `ui/pages/16_ai_analyst.py`: an "Example questions & commentary prompts"
expander with one-click buttons (incl. two that draft **commentary** → the AI-draft banner now
actually appears; the export was already banner-correct — the owner had simply never reached the
commentary path), and a sidebar **faithfulness toggle** (flag-not-block) threaded via
`ai_analyst_logic.run_turn(faithfulness=…)` → `chatbot_cfg.faithfulness_llm_judge`.

**Spec note (banner):** the "exported .md has no AI-draft banner" UAT item was **not a bug** — the
banner is reserved for commentary turns (FR-3B-38); the exported sessions were factual Q&A. Fix #6
makes commentary discoverable so the banner shows.

**Files touched:** `config/prompts/{sql_generation,routing}.md`, `config/chatbot_few_shots.yaml`,
`config/ai_config.yaml`, `src/ai/chatbot/pipeline.py`, `ui/ai_analyst_logic.py`,
`ui/pages/16_ai_analyst.py`, and tests (`tests/test_chatbot_aggregation.py` [new, 11],
`tests/test_chatbot_realdata.py` [+2], `tests/test_ai_analyst_logic.py` [+1],
`tests/test_ai_analyst_apptest.py` [+asserts], `tests/chatbot_helpers.py` [record max_tokens]).
**Not yet re-run by the owner on a live model** — the offline gate is green; a live AI Analyst
pass (real figures, commentary banner, DeepSeek routing no longer erroring) is the remaining
owner-triggered check.

---

## Post-UAT hardening (round 3) — reliable commentary, multi-query reasoning, Analyst mode (2026-06-26)

Round-2 fixed the figures; round-3 UAT of the **AI Analyst** showed commentary still failing
(DeepSeek **silence**, Claude "couldn't answer safely"), DeepSeek worse than Claude, and the
analyst still feeling "terse/stupid". Investigation confirmed the figures were never the issue
(true WL mortality A/E ≈ 0.5718, credibility Z ≈ 0.4631 from the **aggregate** count). Owner
decisions: rebuild commentary on the **fact-pack** pattern, add an **opt-in Analyst mode (default
OFF)**, and add a **multi-query** path for exploratory questions. SQL safety gates and the MCP-only
data path are unchanged; only the numeric post-check relaxes, and only in Analyst mode. **Suite
1082 passed, 6 skipped** (+26 over the 1056 baseline / +12 over round-2's 1070).

**A. DeepSeek "silence" = unhandled exception (FIXED).** Only the routing and faithfulness calls
caught `LLMProviderError`; the **SQL-gen and commentary-gen calls did not**, so a reasoning model
truncated to empty content raised through `handle_turn` and the page swallowed it (`st.error`, no
saved turn). Both calls are now wrapped → a saved `_blocked(..., "llm_error", _LLM_ERROR_TEXT)`.
Token caps raised in `chatbot.max_tokens` (commentary 4096, sql_generation 2048, routing 1024,
+`synthesis` 4096).

**B. Opt-in Analyst mode, default OFF (FR-3B-34 preserved).** `_apply_traceability(trace, body,
analyst_mode)`: OFF → blocks an untraceable number as before; ON → renders it with a visible
"⚠ Analyst mode — unverified figures" warning and logs `traceability_passed=false`. The five SQL
gates never relax. Config `chatbot.analyst_mode_default: false`; sidebar toggle on page 16.

**C. Commentary fact-pack overhaul (the real "commentary works" fix).** The single-SQL+slot-fill
commentary route is **replaced** by generate-then-verify over an app-assembled fact pack — exactly
the memo Skill's pattern. New `ui/skills_logic.py::assemble_commentary_facts(db_path, run_id)` spans
every product × decrement (overall A/E as SUM(actual)/SUM(expected), by-segment via `_ae_by_segment`,
**aggregate** credibility, exposure) + TEV baseline, display-rounded. `_commentary_turn` now hands
the pack + RAG to the model, which writes **prose** (no SQL/slots); every number is checked verbatim
against the pack (`run_id` excluded so its UUID digits can't mask an invented figure) + grounding.
`commentary.md` → **v2.0** (prose, numbers-verbatim, no JSON). This removes the
`commentary_generation_failed`/`slot_fill_failed`/one-query failure modes and the wrongly-averaged
credibility artifact ("0.0003" → 0.4631).

**D. Multi-query synthesis for EXPLORATORY (feature-flagged, default OFF; UI ON).** A bounded
**plan → fetch → synthesise** loop (`_synthesis_turn`): the planner returns up to
`max_synthesis_queries` (4) gated SELECTs (`synthesis_plan.md`); each runs through the boundary +
MCP server (gate-rejected ones skipped, never executed); the synthesiser drafts prose over the
combined evidence (`synthesis_answer.md`), generate-then-verify against it. `chatbot.multi_query_default:
false` keeps the eval harness/tests on the single-query path; the AI Analyst page's "Deep analysis"
toggle (default on) turns it on. Real-data smoke: "compare WL mortality and lapse by duration" plans
2 queries → 6 rows → traced synthesis.

**Layering/guards:** the pipeline stays DB-free (FR-3B-25) — the fact pack is assembled in the UI
layer and passed in; synthesis fetches only via the MCP client. No string-interpolated SQL in
`src/ai/` (the run-scope/synthesis prose carry no SQL keywords; the boundary validates every query).
`handle_turn` gained `commentary_facts`, `analyst_mode`, `multi_query` kwargs (all default-safe).

**Files touched:** `src/ai/chatbot/pipeline.py`, `ui/skills_logic.py` (`assemble_commentary_facts`),
`ui/ai_analyst_logic.py`, `ui/pages/16_ai_analyst.py`, `config/prompts/{commentary.md (v2.0),
synthesis_plan.md, synthesis_answer.md}`, `config/ai_config.yaml`, `tests/chatbot_helpers.py`, and
tests (`test_chatbot_{resilience,analyst_mode,synthesis}.py` [new], `test_chatbot_commentary.py`
[rewritten to the prose contract], `test_chatbot_{intent,realdata}.py` [updated],
`test_ai_analyst_{logic,apptest}.py` [+toggles]). **Live AI Analyst re-test remains owner-triggered.**

**Robustness hardening (2026-06-27).** After the round-1→3 changes, a coverage audit (existing
~110 chatbot tests) drove **+32 offline edge-case tests** across the three areas the owner cares
about — number computation/querying, LLM interaction, and response assembly/output — in three new
files: `tests/test_chatbot_robustness_{numbers,llm,output}.py`, plus 2 real-data fact-pack tests in
`tests/test_chatbot_realdata.py`. Coverage added: slot-grammar NULL/mixed/list edges
(`{{col:}}`→`N/A`, `{{agg:mean}}` over NULLs, `{{agg:sum}}` all-NULL→`slot_fill_failed`,
`{{agg:count}}` over NULLs, `{{list:}}` single/numeric+NULL); the **synthesis evidence guard** — an
invented cross-query total blocks `numeric_traceability` (the synthesiser can't compute its own
numbers), a zero-row query is handled, model-switch is honoured on the synthesis **plan+answer**,
commentary and **faithfulness** calls (previously only routing+sqlgen were covered); parser
fail-safety (`_parse_plan` truncated/non-string/fenced-with-prose→None, `_parse_query_plan`
list+dict+garbage, routing label variants→intent or OUT_OF_SCOPE, `_parse_score` embedded/`2/5`/
out-of-range); provider errors mid-synthesis and on the faithfulness judge are safe (no crash);
token/cost accumulation across a 3-call turn; `ChatTurnResult`/audit shape per path (synthesis
joins both SELECTs + records `synthesis_*` template hashes; commentary `sql=None`); Analyst-mode
flag audited as an unblocked traceability failure; export preserves the unverified/faithfulness
warnings; traceability formatting (billions-with-commas trace, string-cell numbers trace, and a
**documented** scientific-notation non-trace). **No source defect found** — the real-run fact pack
is clean (9 products × 4 decrements = 32 entries, all finite/4-dp-rounded, no None ratios, SURRENDER
present everywhere → no KeyError; the asymmetric `expected≤0,actual>0` `_overall_ae` edge is
unreachable on real data, left as a benign latent edge). **Suite 1114 passed, 6 skipped** (+32).

---

## Post-UAT hardening (round 4) — AI Analyst "make it smarter / know all the data" (2026-06-27)

Owner reviewed four live AI Analyst transcripts (uploaded) and judged the analyst still
"stupid/incompetent": it refused "what are the proposed Term mortality assumptions?", over-refused
"what can you do?", commentary quoted a fabricated "mean credibility … 0.0003", and cross-product
commentary failed. Investigation (3 Explore agents) showed **three** distinct causes — not just the
MCP: (A) the data surface was only `gold_ae_results` + `gold_tev_results`; (B) the numeric hard-block
made narrative brittle; (C) normal turns got **no** study context (only commentary got the fact pack),
so the model guessed. The owner chose the **"safe bundle + data-surface expansion"** build (the
governed-maximum frontier): get raw-chat-like reasoning/breadth **without** the catastrophic breaches
(no PII to an LLM, no unflagged invented numbers, no raw DB/SQL, no writes). Plan file:
`~/.claude/plans/context-the-uploaded-md-purring-cat.md`.

**What changed (all within the governed spine; PII bright line held):**
1. **Analyst mode ON by default *on the AI Analyst page only*** (`ui/pages/16_ai_analyst.py`) — the
   global `chatbot.analyst_mode_default` stays **OFF** so the eval harness keeps its 100% numeric-
   traceability gate. Multi-query "Deep analysis" already defaulted ON.
2. **Study digest in every turn** — `pipeline._render_digest` injects a compact, display-rounded
   "study at a glance" (per-product×decrement overall A/E + aggregate credibility + products + study
   period + baseline TEV; reuses `ui/skills_logic.assemble_commentary_facts`) into the routing /
   SQL-gen / synthesis system prompts, and joins the digest to the numeric-traceability allowed-set on
   the data paths. `handle_turn`/`_run_turn`/`_route`/`_generate`/`_synthesis_turn` gain a
   default-safe `study_digest` kwarg (None → inert, so eval/tests are unchanged); the UI passes the
   fact pack. So the analyst always "knows the whole study" and stops guessing.
3. **Routing over-refusal fix** (`routing.md` → v1.2) — *reading* a proposed/expected/assumed/approved
   value (or DQ/reconciliation) is a **data question**; only *changing/setting/approving* is
   OUT_OF_SCOPE. **Commentary credibility fix** (`commentary.md` → v2.1) — cite the fact pack's
   aggregate `overall.credibility_z`; never compute/average a "mean credibility across cells" (kills
   the 0.0003 artifact). **SQL-gen** (`sql_generation.md` → v1.2) — CI illness-code legend,
   top-N/ranking guidance, and schema cards for the widened tables; **+10 few-shots** (CI causes
   ranking, top-N, cross-product, proposed factors, reconciliation, DQ, registry, assumption sets),
   kept disjoint from the locked golden set.
4. **Data-surface widening (PII-free), FR-3B-09/13/32 + FR-3A-09 in-place amendments:** the
   `chatbot.allowlist` gains the omitted A/E columns (amount-basis deaths, SE/CI bounds,
   credibility-weighted A/E, anti-selection) + the TEV profit-source margins, and **six more Gold
   tables** — `gold_inforce_reconciliation`, `gold_dq_run_summary`, `gold_model_points`,
   `gold_ai_model_registry`, `gold_assumption_sets`, and a new `gold_ai_proposed_factors`. A single
   generic gated **`query_results(table, sql)`** MCP tool (server-side single-table-scoped; tool count
   5 → 6; `TOOL_SCHEMA_VERSION` → "2.0") serves them; `execute_via_mcp` routes a validated
   single-table SELECT to it (AE/TEV keep their dedicated tools). **No PII column or table is reachable**
   — no `gold_dq_quarantine`/`gold_exposure_segments` (policy_id), no Silver/Bronze, no author/reviewer
   person ids — enforced by a new PII-reachability guard test.
5. **Materialised proposals** (`gold_ai_proposed_factors`, the *fourth* permitted AI Gold write target)
   — `src/ai/proposals.py::write_proposed_factors` (static parameterized INSERT, the registry pattern)
   writes each published GLM/GBM `FactorCell` (grain dims + factor + CI + credibility) at
   registration; `register_glm_model`/`register_gbm_model` call it. This is the only way the per-cell
   proposed factor *values* (otherwise only in pickles) become SQL-queryable, so the analyst can answer
   "proposed Term mortality assumptions by age band". The write-contract guard's allowed-set is amended
   to include it. **(Re-run note:** the live `data/experience_study.duckdb` must be re-`init_database`d
   to create the new table, and AI models re-fit on the Assumption Comparison page to populate it.)

**Tests (25 new across 3 files):** `tests/test_chatbot_digest.py` (**7**: digest render/inject/traceable
on the factual **and** synthesis paths; the safety-lock that an invented number still blocks *with* the
digest present; control-blocks-without), `tests/test_data_surface.py` (**16**: queryable↔allowlist sync,
the generic `query_results` tool + its gate enforcement on the new tables, the **PII-reachability
guard**, pipeline routing incl. CTE-over-widened-table, the proposals writer + INSERT alignment +
replace-on-refit + lapse/CI grain mapping), and `tests/test_proposals_integration.py` (**2**:
`register_glm_model`/`register_gbm_model` actually materialise their factors with the right
`model_type`, readable through the gated MCP tool). Updated: `test_mcp_server` (6-tool surface, schema
"2.0"), `test_sql_boundary` (widened allowlist shape), `test_eval_hardening` (schema "2.0"),
`test_ai_architecture` (4th AI write target). **Suite 1139 passed, 6 skipped** (+25 net over the round-3
1114 baseline; no regressions). The locked golden/adversarial eval sets are **unchanged** (still
owner-locked; the adversarial PII/Silver probes still gate-reject). Live AI Analyst re-test (real
proposed-assumption answers; reliable commentary; cross-product synthesis) is owner-triggered.

> **Spec amendments (2026-06-27, owner-authorised, in-place — same pattern as rounds 2/3):** Req v3.0.1
> and Tech v2.0.1 carry dated header notes for FR-3B-09 (6th tool), FR-3B-13/32 (widened PII-free
> allowlist), FR-3A-09 (4th AI Gold write target `gold_ai_proposed_factors`), the §E.7 `study_digest`
> seam, and the page-level Analyst-mode default. The global default and the eval/audit/gate contracts
> are unchanged.

---

## Post-UAT hardening (round 5) — AI Analyst output evaluation & formatting fixes (2026-06-27)

Owner uploaded four live AI Analyst transcripts (Opus 4.8, Sonnet 4.6, DeepSeek V4 Pro/Flash) plus
two memos and two SHAP explanations and asked to (1) verify the figures, (2) explain the unanswered
questions, (3) fix what should be fixed. **Evaluation finding: the figures are 100% correct** — a
subagent revalidated 80+ figures (mortality/lapse/CI A/E, exposure-years full-period and 2020-only,
CI illness-code counts, all TEV totals/per-product) against the live DB (run
`ed697c0c-fac4-4405-9079-6cd30339cc77`) to full precision, with cross-model consistency (WL 0.5718,
Term 0.9368, UL lapse 0.9521 identical across sessions). The problems were **answer formatting and a
few unanswerable cases**, not data. All changes are additive/within the governed spine (SQL gates,
MCP-only data path, numeric-traceability default, audit schema, and locked eval sets unchanged).
**Suite 1149 passed, 6 skipped** (+10 over the round-4 1139 baseline).

**Fix 1 — deterministic `{{table:...}}` slot (the big one).** The single-query slot grammar had no
row-by-row table slot, so multi-row requests either **failed** → generic "couldn't answer safely"
("WL A/E across policy years", "TEV for Term") or the model misused `{{list:}}` per column so columns
collapsed into comma-joined cells (the garbled TEV report; the `", |"`-joined WL age table — seen on
both Sonnet and DeepSeek, so architectural). Added `{{table:<col1>,<col2>,...}}` to
`src/ai/chatbot/pipeline.py::_resolve_slots` (`_TABLE_RE` + `_table_repl`, applied before
list/col): it renders a markdown table (header + divider + one row per result row) from the result
set via the existing `_format_value`, appending every numeric cell to `injected` so the post-check
treats them as traceable. Unknown column → `SlotFillError`; empty result → "(no matching rows)".
Documented in `config/prompts/sql_generation.md` (use a single `{{table:}}` for any "table"/"by X"
request; never `{{list:}}` per column) + the existing aggregated by-segment few-shots. Numbers are
rendered programmatically, so no LLM transcription and no traceability false-blocks. +5 tests
(`test_chatbot_robustness_numbers.py`, `test_chatbot_aggregation.py` incl. an end-to-end render).

**Fix 2 — commentary over proposed factors.** `gold_ai_proposed_factors` is populated (136 rows, 6
models on run `ed697c0c`), but "commentary on the AI/GLM-proposed assumptions" routed to the
commentary path, whose fact pack (`ui/skills_logic.py::assemble_commentary_facts`) carried only
A/E/TEV → proposed-factor numbers failed traceability → `commentary_generation_failed`. Added
`_proposed_factors(...)` (read-only, parameterised; `model_type='GLM'`) and a `proposed_factors`
block per product/decrement in the fact pack (grain, factor, CI, credibility_z, `low_credibility`
flag); `commentary.md` → v2.2 explains how to cite them. No pipeline change — `_commentary_turn`
already verifies against the fact pack. +1 real-data test.

**Fix 3 — friendlier non-security block messages.** `_SAFE_FAILURE_TEXT` was returned for every
non-security block. Added tailored hints for `slot_fill_failed` (→ "ask for it as a table … or turn
on Deep analysis"), `synthesis_no_evidence`, and `commentary_generation_failed`; **kept generic** for
SQL-gate rejections, MCP gate errors, and `numeric_traceability` (security/guardrail surface
unchanged). +1 assertion.

**Fix 4 — lower-priority hardening.** (a) Run-scope guard: a non-blocking `run_scope` audit **event**
(no DB-schema change) records whether an active-run A/E query honoured the `study_run_id` filter;
full server-side enforcement remains deferred (only one COMPLETE run exists). (b) Memo "principal
drivers" (`assemble_memo_input`) now excludes zero-credibility/zero-experience bands (the spurious
15-19/20-24 Term drivers). (c) `shap_explain.md` → v1.2: describe contributions directionally
("raises/lowers the modelled adjustment"), not literal margin-space sign ("below zero"). (d) Degenerate
sparse-cell GLM factors (e.g. `5.95e-29`, ci_high `5.1e+44`, Z 0.0) are flagged `low_credibility` in
the fact pack so they're caveated, not quoted as real assumptions. +2 real-data tests.

> **Spec amendments (2026-06-27, owner-authorised, in-place — same rounds-2/3 pattern):** Tech v2.0.1
> (§E.7 `fill_numeric_slots` grammar gains `{{table:...}}`; commentary fact pack gains
> `proposed_factors`) and Req v3.0.1 (§7.10 FR-3B-33 table slot; FR-3B-37 commentary over proposed
> factors) carry dated header notes. No change to the SQL gates, the MCP-only data path, the
> numeric-traceability default, the audit schema, or the locked eval sets. Live AI Analyst re-test
> (tables render cleanly; commentary on proposed assumptions works) is owner-triggered.

---

## Post-UAT hardening (round 6) — AI Analyst transcript evaluation & fixes (2026-06-27)

Owner ran the 20 suggested test questions across all four configured models and uploaded four AI
Analyst transcripts (Opus 4.8, Sonnet 4.6 ×2, DeepSeek V4 Flash). I verified **every** numeric claim
against the live Gold DB (read-only, run `5df3befb-57ea-4543-acf5-a1353b07a74f`) and traced the
refused/failed answers with three Explore agents.

**Evaluation finding — where the analyst answered, the figures are 100% correct.** All 12 distinct
numeric claims matched the DB exactly (WL mortality 0.5718 = 232/405.7611; Term-by-age total 84
deaths; UL gender×smoker split; CI-by-illness ranking; total TEV 173,388,672.57 + per-product split;
VUL-best/DA-worst product ranking; "Term has no GLM mortality factors" → correctly 0 rows). No
fabricated numbers; all PII/assumption-change requests correctly refused. The defects were **wrongful
refusals, one incorrect *appended* statistic, and raw/incomplete outputs** — not accuracy.

Fixes (all within the governed spine; SQL gates and MCP-only path unchanged):

1. **🔴 Wrong appended credibility (Doc-3).** "UL lapse A/E 0.9521 … credibility Z **0.0015**" — correct
   is **0.3881** (=√(163/1082)). `assemble_response` (`src/ai/chatbot/pipeline.py`) read a stored
   per-cell `credibility_z_lapse` from `row[0]` of an aggregate result (an arbitrary detail cell).
   Fixed: it now **recomputes** Z from the aggregate actual-count column
   (`actual_deaths_count`/`actual_lapses`/`actual_ci_claims`/`actual_surrenders`) via
   `src.calculation.ae_engine.compute_credibility_z` (reaffirming FR-1A-24), honouring the run's
   method (the fact pack / digest now carries `credibility_method`, so Bühlmann runs recompute with K),
   and appends the recomputed Z to `injected` so it stays traceable; falls back to the stored
   single-cell value only when no count column is present. +4 tests (`test_chatbot_aggregation.py`,
   unit + end-to-end + Bühlmann-from-digest).
   **Live re-test follow-up (deepseek-v4-pro, 2026-06-28):** the *appended* line was now correct
   (0.3881), but the model still wrote a wrong credibility (`0.0015`) in its **own prose** — it had
   the SQL compute `AVG(credibility_z_lapse)` (≈0.0007–0.0015) and slot-filled it, the exact FR-1A-24
   "never average per-cell Z" antipattern (traceable, so not blocked). `assemble_response` only governs
   the appended line, so the body slipped through. Fix: `sql_generation.md` → **v1.4** forbids
   selecting/averaging a per-cell `credibility_z*` for an overall/aggregate answer (credibility is left
   to the system-appended aggregate Z; quote a stored `credibility_z*` only for a single specific cell)
   + 1 few-shot teaching the "overall A/E and its credibility" pattern (SUMs only). +1 content test.
   **Deterministic backstop (re-test, 2026-06-28):** because #2 slipped past two prompt/code fixes, a
   prompt-only control isn't enough — `pipeline.aggregates_per_cell_stat(sql)` (sqlglot AST) now detects
   any AVG/SUM/MIN/MAX/MEDIAN over a `credibility_z*`/`se_ae*` column and **blocks** it on the
   single-query path (friendly hint, reason `credibility_aggregate`) / **skips** it on the synthesis path,
   so a non-compliant model can never surface an averaged per-cell credibility regardless of the prompt.
   COUNT and legitimate single-cell `credibility_z` reads are unaffected. +3 tests (unit + main-path block
   + synthesis skip).
2. **🔴 Two wrongful refusals (Doc-1, Doc-2).** "Where is experience most credible across products?"
   and "Which decrement contributes the largest PVFP profit-source margin?" were refused as
   OUT_OF_SCOPE though both are answerable (columns allowlisted). Cause: `routing.md` lacked
   credibility-ranking / PVFP-margin superlative examples, and `_parse_intent` silently defaulted an
   *unparseable* routing reply to OUT_OF_SCOPE. Fixed: `routing.md` → **v1.3** (superlatives /
   rankings / credibility / TEV margins / multi-part status are EXPLORATORY); `_route` gains a
   **bounded one-shot re-route retry** (`_intent_parsed`/`_merge_responses`) distinguishing a
   token-capped reply from a genuine refusal; `chatbot.max_tokens.routing` 1024 → 2048; +4 few-shots
   (cross-product credibility ranking; PVFP margins), disjoint from the locked golden set. +5 tests
   (`test_chatbot_reroute.py`) + a PVFP-margin end-to-end flow test.
3. **🟡 Two-part recon+DQ (Doc-1).** The recon half was faithful (the recon table genuinely holds
   only DA — a study-pipeline gap, not a chatbot bug), but the DQ half went unanswered though it had a
   real finding (UL & ULSG: 91.875% DQ score, 65 quarantined each). Fixed: `synthesis_plan.md` → v1.1
   adds `gold_inforce_reconciliation` + `gold_dq_run_summary` to the EXPLORATORY planner card so a
   multi-part status question fetches both (Deep analysis on the AI Analyst page).
4. **🟡 Degenerate GLM factors shown raw (Doc-3).** The WL GLM mortality `{{table:}}` rendered factors
   ≈0 with `ci_high ≈ 1e44`, no caveat. Fixed: `sql_generation.md` → v1.3 instructs the model to
   surface `credibility_z` and caveat near-zero-credibility/exploding-CI cells (optional
   `credibility_z >= 0.05` filter for the *usable* assumptions).

**Non-chatbot observations (faithful answers, underlying-data quirks):** in-force reconciliation is
only populated for DA (study-pipeline gap); CI per-illness exposure is stored as total/10 (the chatbot
reported the stored value faithfully). Both are out of chatbot scope; flagged for owner awareness.

**Suite 1163 passed, 6 skipped** (+14 over the round-5 1149/6 baseline, incl. the live re-test
follow-up and its deterministic backstop; no regressions; few-shot↔golden disjointness still green).

**Live re-test outcome (deepseek-v4-pro, 2026-06-28).** Re-asked the five failed questions. **#12**
(most credible / thinnest) — now fully correct and thorough (TERM lapse Z=0.8853 most credible;
DA_FIXED mortality / IUL CI thinnest; every Z verified against the DB). **#14** (Term GLM mortality
factors) — correctly answered "none proposed for Term" (minor: an "(or any other product)" aside is an
overstatement, since WL does have them — but the scoped query genuinely returned none). **#15** (largest
PVFP profit-source margin) — correct: mortality margin, DA line, $17,291,264.50 (all figures verified).
**#16** (recon + DQ) — correct and complete: recon passes (DA-only), UL & ULSG 91.875% / 65 quarantined.
**#2** (UL lapse credibility) — the *appended* line was correct (0.3881) but the model's prose still
showed 0.0015 → fixed by `sql_generation.md` v1.4 above (re-test owner-triggered). Specs amended in-place (Tech v2.0.1 + Req v3.0.1 round-6 header notes). Live
AI Analyst re-test (the two refusals now answer; UL-lapse credibility ≈0.39; recon+DQ both halves) is
owner-triggered.

---

## Session 18 — pre-build context & approved plan (owner-reviewed 2026-06-19)

> **Executed 2026-06-20.** This section is retained as the historical pre-build
> record; the as-built outcome (including the Python 3.12 decision) is in the
> "Session 18 — … COMPLETE" section above.

This plan was researched and **owner-approved on 2026-06-19** in a planning
session. It is recorded here because plan files are session-local — a fresh
Claude Code session inherits the build only through this doc. Authoritative task
definition remains `docs/phase3_claude_code_prompts.md` → "Session 18";
contracts in Tech Spec §E.1/§E.5/§E.6/§D.1–D.3/§F.2 and Requirements FR-3B-01..16.

**Preconditions:** Session 17 gate green ✅ (**814 passed, 6 skipped**), Phase 3a
CLOSED. **NEW precondition (blocker):** the working interpreter must be Python
≥3.10 — see the ⛔ Environment note above. The `mcp`/FastMCP SDK will not install
on 3.9.6.

### Phase 0 — Python upgrade + lockfile regeneration (DO FIRST; verify before building)
1. Owner installs the latest Python (≥3.11); point the project venv at it.
2. Add `anthropic`, `openai`, `mcp` to `requirements.in` (the comment there
   already anticipates this).
3. Regenerate the lockfile for the new interpreter:
   `uv pip compile requirements.in -o requirements.lock --generate-hashes`
   (no `--python-version 3.9` pin). Install from the lock.
4. **Re-run the full suite and confirm 814 passed / 6 skipped holds on the new
   interpreter** (treat any statsmodels/xgboost/shap pin drift as Phase-0
   cleanup, not Session 18).
5. Update the Environment-notes Python bullet once confirmed.

Only after Phase 0 is green does the build below begin. Decision recorded for the
MCP transport: build the **full FastMCP stdio server** on the upgraded
interpreter (no 3.9 import-guard workaround).

### Build (strictly additive under `src/ai/`; reads Gold + config only; dynamic SQL via `src/utils/sql_boundary.py`)
1. **`src/utils/types.py`** — append `LLMResponse` (§E.1): `text, input_tokens,
   output_tokens, provider, model, latency_ms, stop_reason: Optional[str]=None`.
   `SQLValidationResult`/`SQLGateOutcome` already exist (Session 14) — don't
   duplicate.
2. **`src/ai/llm/`** (§E.5, FR-3B-01..06):
   - `base.py` — `LLMProvider` Protocol, `complete(messages, model, max_tokens,
     temperature=0.0, system=None) -> LLMResponse`; non-streaming.
   - `anthropic_provider.py`, `deepseek_provider.py` — **lazy-import the SDK
     inside `complete()`** (anthropic SDK; openai SDK pointed at the DeepSeek
     `base_url`) so importing the package never needs a network SDK and a missing
     SDK degrades like a missing key. No provider SDK imported outside this
     package (FR-3B-01).
   - `mock_provider.py` — `MockProvider`: deterministic, fixture-keyed, zero
     network; canned responses keyed by sha256 of canonicalised
     `(model, system, messages[role,content])` JSON; `provider="mock"`, synthetic
     token counts. Fixtures under `tests/fixtures/llm/` (new) + a README
     documenting the hash scheme.
   - `client.py` — `load_llm_config(path)`, `available_models(config)` (greys
     missing-key models with reason "API key not configured", FR-3B-04; env+config
     only, no SDK import), `resolve_provider(config, model_key)` (pure dispatch
     helper for the no-key dispatch test), `complete(config, model_key, ...)`
     (resolve → dispatch; key from env var only, never logged; timeout/retry from
     config; terminal failure → `LLMProviderError` with a user-safe message).
3. **`src/ai/mcp_server/server.py`** (§E.6, FR-3B-09..16) — five tools, each
   returning structured dicts and **never raising** (FR-3B-15):
   - `query_ae_results(sql)` / `query_tev_results(sql)` — route through
     `execute_safe_select` with a **per-tool allowlist scoped to just that table**
     (AE tool rejects TEV-table SQL and vice versa). PASS →
     `{columns, rows, row_count}`; reject → `{error: gate_id, message}`.
   - `list_available_dimensions()` — metadata only; names from the allowlist,
     distinct values via the boundary with `LIMIT <= row_cap` (gate-4 compliant).
   - `get_study_run_summary(run_id)` / `get_tev_run_summary(tev_run_id)` —
     manifest metadata from `gold_study_runs` / `gold_tev_run_log` via a
     **read-only, parameterized (`?`)** query (these tables aren't on the chatbot
     allowlist; fixed-shape, PII-free metadata, no interpolation). Documented
     metadata read path.
   - Implement core logic as explicit-keyword functions (`db_path, allowlist,
     row_cap`) → directly testable. `build_server(db_path, allowlist, row_cap)`
     registers thin `(sql)->dict` FastMCP closures; `run()` →
     `mcp.run(transport="stdio")`, no network bind (FR-3B-12); module
     `TOOL_SCHEMA_VERSION` constant (FR-3B-16); no write-capable connection
     (FR-3B-11).
4. **`src/utils/db_init.py`** — append `gold_ai_eval_results` (§D.2) and
   `gold_ai_audit_log` (§D.3) to `_GOLD_AI_DDL` (exact DDL from §D.2/§D.3).
   `gold_ai_model_registry` already present (Session 15) — confirm, don't
   duplicate.
5. **Config + fixtures** — `config/llm_config.yaml` (§F.2): four models
   (`claude-opus-4-8`, `claude-sonnet-4-6`, `deepseek-v4-pro`,
   `deepseek-v4-flash`), display names, DeepSeek `sdk: openai` + `base_url`,
   per-provider `api_key_env`, `default_model: claude-sonnet-4-6`,
   `request_timeout_seconds: 60`, `max_retries: 2`; **prices left
   `<set at build>`** (STOP checkpoint). `tests/fixtures/llm/` + README.

### Scope fence (do NOT build)
No intent routing / SQL generation / chatbot pipeline (Session 20); no Skills
(Session 19); no prompt templates; no eval harness. The two new AI Gold tables
are **created but not written to** this session.

### Standing guards that must stay green
Import-graph (core never imports `src/ai/`); no-SQL-interpolation scan over
`src/ai/` (lazy SDK imports + parameterized `?` only — no f-strings/format into
SQL); write-contract; Jinja autoescape.

### Tests (TDD; MockProvider only; suite passes with keys unset — FR-3B-06)
- `tests/test_llm_client.py` — `resolve_provider` dispatch per model_key;
  `available_models` greys missing-key models; `load_llm_config` shape.
- `tests/test_mock_provider.py` — deterministic `LLMResponse`; same input →
  identical output; `provider == "mock"`.
- `tests/test_mcp_server.py` — each `query_*` **called directly** rejects
  adversarial SQL (non-SELECT, off-allowlist, Silver table, over-cap); AE tool
  rejects TEV-table SQL; metadata tools return no row data; PASS →
  `{columns, rows, row_count}`; `run()` uses stdio / binds no network interface.
- `tests/test_ai_gold_tables.py` — fresh `init_database` creates the two new
  tables with §D.2/§D.3 columns; idempotent re-init.
- `tests/test_mcp_server_realdata.py` — skip-if-absent `prod_db` (copies prod DB,
  `init_database` on the copy to add the AI tables), exercises all five tools
  end-to-end against run `ed193b59-c5d6-48cd-b5e6-43d33464dff8`.

### Verification
`unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && pytest tests/ -v --tb=short`
green; no regression below 814/6 (expect 814 + new Session-18 tests). If any TEV
path is touched in the spot-check, mirror `<tmp>/data/copy.duckdb` + `<tmp>/config/`
(helpers resolve config at `db.parent.parent`).

### STOP — OWNER INPUT before "done"  → RESOLVED 2026-06-20
Original plan: re-ask the owner for confirmed DeepSeek per-model pricing (§12.1).
As built: the owner directed sourcing the rates from public pricing instead of
hand-entering them, so all four `price_per_mtok_*` pairs in `llm_config.yaml` are
filled (Anthropic Opus 4.8 $5/$25 + Sonnet 4.6 $3/$15 from the Anthropic pricing
reference; DeepSeek V4 Pro $0.435/$0.87 + Flash $0.14/$0.28 from published V4 GA
rates) and owner-overridable — the cost display (FR-3B-43) and eval cost gate now
compute real figures.

### Reusable building blocks (verified present 2026-06-19)
- `src/utils/sql_boundary.py` — `load_allowlist`, `validate_select`,
  `execute_safe_select` (read-only, gates 1–5); returns `SQLValidationResult` on
  reject (never raises). `SELECT *` expands to the allowlisted subset.
- `src/utils/db_init.py` — `_GOLD_AI_DDL` list (append the two new tables here);
  idempotent `_ensure_column` helper for §D.4-style migrations.
- `config/ai_config.yaml` — populated `chatbot.allowlist` (gold_ae_results +
  gold_tev_results, no PII) + `chatbot.sql_row_cap: 500`.
- `tests/conftest.py` — `prod_db` (session copy of the production DB) and
  `synthetic_db` fixtures; `--keep-artifacts`, artifact size-guard.
- `src/ai/glm/registry.py` — reference for the static-parameterized INSERT
  pattern (model `?` placeholders, writable connection only for sanctioned AI
  writes).

See `docs/phase3_claude_code_prompts.md` → "Session 18" for the full prompt block.
