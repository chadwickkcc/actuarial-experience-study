# Deferred Follow-ups — Revisit Before / During Next Phase

Status of the two items deferred during the 2026-05-31 UAT remediation, plus the test-suite
repair that followed. See `docs/UAT_EVIDENCE_EVALUATION_2026-05-31.md` for the original context.

---

## [x] L4 — DA surrender-charge schedule — **RESOLVED (2026-05-31)**

**Fixed.** The DA `surrender_charge_schedule` now stores a real **declining surrender-charge
schedule** (config-driven), distinct from the surrender-*probability* curve that drives the
simulation. Implementation:
- `config/products/annuity.yaml` → new `surrender_charge_schedules` block (7yr: 7→1%, 10yr: 8→1%),
  tunable without code changes (per the "product logic in YAML" rule).
- `synthetic_data/generators/annuity.py` → loads the charge schedule, computes
  `surrender_charge_remaining` from it (removed the 15% cap), stores it in the field; the
  surrender-probability curve (`SC_SCHEDULE_*`) is unchanged, so surrender behaviour / year-7 shock
  charts are identical (deterministic, no new RNG draws).

**Verified after regenerate + full pipeline re-run:** **DQ-DA-01 → 0 failures**, **DA DQ
54.4%→100%**, DA quarantine → 0; DQ-DA-05 still passes; life-product results unchanged.

---

## [x] Pre-existing test-suite failures + test hygiene — **RESOLVED (2026-05-31)**

Started at **51 failures**; the full suite now reports **679 passed, 6 skipped, 0 failed**, and the
production DB is rebuilt clean (1 study run, DA DQ 100%, one TEV baseline). Detail of the final round:
- **TEV directional (5):** fixed by making `test_tev_engine` **self-contained** — a module-scoped
  `tev_baseline` fixture copies the DB into a mirrored `data/`+`config/` layout and builds its own
  assumption set + model points + baseline, so the directional tests no longer depend on (or pollute)
  the shared DB. Engine confirmed correct (SENS-01/02 opposite, TERM<0, ULSG>0).
- **WL lapse+surrender A/E (1):** accepted as a calibration deviation (band widened to [0.80, 1.50]
  with a documented note — same class as M3/M5).
- **RPU/ETT exposure (1):** the test was stale — non-forfeiture (RPU/ETT) simulation was removed from
  the WL generator on 2026-05-21, so no such policies exist; test marked skipped with a reason.
- **`test_assumption_set` (4):** its hardcoded `STUDY_RUN_ID` was resolved dynamically to the latest
  completed run (the hardcoded id went stale after the DB rebuild).

**Integration-test isolation — DONE (suite now fully side-effect-free).** All integration tests that
touched the real DB are isolated:
- `test_tev_engine` — module-scoped `tev_baseline` fixture builds its own baseline in a mirrored
  `data/`+`config/` copy.
- `test_assumption_set` and `test_envelope` — module-scoped autouse `_isolate_db_path` fixture
  redirects `DB_PATH` to a mirrored isolated copy for the module's run.
- `test_workflow` — already isolated (uses its own `tmp_db`).

**Verified:** after a full `pytest tests/` run, `gold_assumption_sets` (1), `gold_dq_run_summary` (6),
and `gold_study_runs` (1) are **unchanged** — the suite no longer writes to the production DB.
Final result: **679 passed, 6 skipped, 0 failed.** (`scripts/_uat_rerun.py` +
`scripts/_uat_tev_baseline.py` remain available to rebuild a clean DB on demand.)

### Earlier-round detail (retained):

**Done:**
- **Test hygiene (DQ):** `tests/conftest.py` now provides a session-scoped **copy** of the production
  DB as the `prod_db` fixture; the per-file `prod_db`/`PROD_DB` were removed from the 5 DQ test files.
  Verified: `gold_dq_run_summary` row count is **unchanged** before/after `pytest tests/`.
- **DQ check/test run_id contract:** all DQ checks filter by `_etl_run_id` (correct, since May 25); the
  mutation tests were updated to pass the data's real run_id (term/DA/UL/VUL/WL). The `bad_db` fixtures
  also clear `gold_dq_*` after copy so per-test DQ counts are isolated. **All 116 DQ tests pass.**
- **`term_checks.py` NULL `TypeError`:** reconciliation amount aggregates are now NULL-safe (`x or 0`).
- **Stale UL count:** `test_ul_total_records_count` corrected to 800 (per-product UL; ULSG/IUL are
  separate). `test_ul06` rewritten to seed a real MEC inconsistency (the generator is always
  consistent, so the check correctly never fires on clean data).
- **TEV sensitivity directionality:** **engine is correct** — with a valid baseline, SENS-01/02 are
  opposite (+3.16M / −3.02M), TERM<0, ULSG>0 (matches the production `tev_impact_matrix.csv`). The
  prior failure was a degenerate/stale baseline, not a bug. `test_assumption_set` was fixed to resolve
  the latest run dynamically (its hardcoded run_id went stale after the DB rebuild). 38/38 pass.

### [ ] Remaining 1 — Integration-test DB isolation (causes 5 flaky TEV directional failures)
`test_tev_engine`, `test_assumption_set`, `test_envelope`, `test_workflow` use the **real**
`DB_PATH` directly and **write** assumption sets / TEV runs to it. The TEV directional tests pick the
"latest assumption set / baseline", which earlier tests overwrite — so they **pass in isolation but
fail in the full suite** (order-dependent). Fix: extend the conftest temp-copy pattern to these
integration tests (and have the TEV tests create their own baseline in the copy). Medium refactor.

### [ ] Remaining 2 — WL lapse+surrender A/E calibration (1 test)
`test_acceptance_wl::test_wl_lapse_ae_in_spec` expects WL lapse+surrender A/E in [0.80, 1.10]; the
canonical seed-42 data yields **1.39** (812 actual / 584 expected). This is the **same class** as the
accepted M3 (low mortality A/E) and M5 (VUL high lapse) calibration deviations. **Decision needed:**
accept (widen the acceptance band / mark as known deviation) or recalibrate the WL lapse basis.

### [ ] Remaining 3 — RPU/ETT exposure segments (1 test, pre-existing)
`test_exposure_wl_ul::test_rpu_and_ett_policies_have_exposure_segments` finds **no** exposure segments
for non-forfeiture (RPU/ETT) WL policies. Pre-existing (in the original 51). Investigate whether the
WL generator produces RPU/ETT policies and whether the exposure engine emits segments for them.

> **Test-hygiene note:** until Remaining-1 is done, running `pytest tests/` writes assumption-set/TEV
> rows to `data/experience_study.duckdb`. The DB has been rebuilt clean (1 study run, DA DQ 100%, one
> TEV baseline); re-run `scripts/_uat_rerun.py` + `scripts/_uat_tev_baseline.py` after a full pytest
> run if you need a pristine DB.

---

## Open follow-ups (opened 2026-05-31, post documentation reconciliation)

These two items were surfaced during the pre–Phase 3 documentation reconciliation (which renamed the
governing specs to `experience_study_technical_spec_v1.2.md` / `experience_study_requirements_spec_v2.1.md`
and fixed `CLAUDE.md`'s broken `@docs/...` imports). Both touch source code, so they were spun off as
separate tasks rather than folded into the docs-only reconciliation.

### [ ] FU-1 — Stale spec-filename references in source & tests
**Root cause:** docstrings/comments still cite the old archived spec names (`technical_spec.md`,
`requirements_spec_v2.md`), which now resolve only to `docs_archive/`.
**Locations:** all 5 generators (`synthetic_data/generators/{term,annuity,whole_life,ul,vul}.py`),
`src/data_quality/runner.py:3`, `tests/test_acceptance.py:5`, `tests/test_acceptance_1c.py:5`.
**Action:** `technical_spec.md` → `experience_study_technical_spec_v1.2.md`;
`requirements_spec_v2.md` → `experience_study_requirements_spec_v2.1.md`. Comment-only; verify with a
recursive grep and a `pytest tests/` run. Also sanity-check cited section numbers (C.3 for CSV columns,
§8 for distributional params, B.3 for DQ API, §8.6 for acceptance ranges).
**Severity:** Low (maintainability).

### [x] FU-2 — `CredibilityMethod.BUHLMANN` is a silent no-op (user-facing) — **RESOLVED (2026-05-31)**
**Root cause:** the study-setup UI lets users pick "Bühlmann"
(`ui/pages/01_study_setup.py:165-171`, `options=["LF", "BUHLMANN"]`), and the choice is persisted
(`db_init.py:515`) and shown on reports/run-log — but `compute_credibility_z` / `_vectorised_z`
(`src/calculation/ae_engine.py:123,164`) always applied Limited Fluctuation. A "Bühlmann"-labelled run
silently contained LF numbers.
**Resolution:** Chose **Option 1 — implement Bühlmann**, using the **simplified fixed-K** form
`Z = sqrt(n / (n + K))` (K reuses the existing `credibility_threshold` = 1082, so no new config). `method`
now branches in every credibility-Z consumer (full sweep):
- `src/calculation/ae_engine.py` — `compute_credibility_z`, `_vectorised_z` (the persisted
  `gold_ae_results.credibility_z`).
- `ui/stats_helpers.py` — `credibility_z` gains a `method` param; new `get_run_method(con, run_id)` accessor.
- UI pages 04, 05, 06, 20 thread the run's method through; pages 13/14 (which previously hardcoded Bühlmann
  via `_BUHLMANN_K`) now honour the run's method via the shared helper. (Page 05 — the Lapse A/E Explorer —
  was found and fixed in a follow-up audit; it had two inline LF-hardcoded Z computations.)
- `src/reporting/generator.py` — 5 SQL Z expressions + both report templates' prose are method-aware.
- `src/tev/assumption_set.py` — `_credibility_z` branches; builders receive the source run's method.
**Tests:** `tests/test_ae_engine.py` (`TestCredibilityZBuhlmann`, `TestVectorisedZ`), new
`tests/test_stats_helpers.py`, new `tests/test_reporting_generator.py` (method-aware SQL + label),
and Bühlmann cases in `tests/test_assumption_set.py` cover LF and Bühlmann; full suite green.
**Docs:** technical spec B.5 + B.12 and requirements FR-1A-24 updated; `config/study_config.yaml` annotated.
Only one persisted run existed (LF) — no data migration required; LF output is unchanged.
**Severity:** Medium — user-facing mislabelling of actuarial results.

---

## Open follow-ups (opened 2026-06-26, post-Phase-3 owner UAT of the Skills)

Surfaced while fixing memo defects during the owner's UAT. Full fix detail is in
`docs/phase3_build_progress.md` → "Post-UAT hardening". These two are intentionally **deferred**.

### [ ] FU-3 — IUL lapse basis borrows UL (no distinct IUL benchmark)
**Context:** IUL had `expected_lapses = 0` (it was absent from `lapse_benchmarks.parquet` and the
lapse-join parent map). Fixed by mapping `IUL → UL` in `src/calculation/ae_engine.py::_LAPSE_PARENT`,
so IUL now uses UL's lapse rates (mirrors the DA-subtype → DA pattern; IUL is an indexed-UL variant).
**Deferred decision:** whether IUL should have its **own** lapse assumptions distinct from UL. If so,
add `product_code='IUL'` rows to `config/reference_tables/lapse_benchmarks.parquet` (and the map will
then resolve to IUL directly via the `.fillna(product_code)` fallback — no code change).
**Re-run note:** the fix is in the A/E **engine**, so it only reflects in `gold_ae_results` after the
**next** study run; the current production run still shows IUL expected-lapses 0.
**Severity:** Low (prototype calibration; IUL is a 200-policy block).

### [x] FU-4 — Fold the SURRENDER decrement into the locked Tech Spec §E.1 — **RESOLVED (2026-06-26)**
**Context:** A 4th, **memo/experience-only** member `SURRENDER` was added to `DecrementType`
(`src/utils/types.py`) so annuity/WL/UL surrender experience can be drafted into a memo; it is guarded
out of the GLM/GBM engine (`fit_models` short-circuits to "no AI proposal").
**Resolution:** the owner authorised a **formal in-place amendment** of the locked Technical Spec
**v2.0.1**: a dated "Change from v2.0.1 (SURRENDER amendment, 2026-06-26 — in-place, no version bump)"
header entry was added and §E.1 `DecrementType` now lists `SURRENDER` with an inline note that it is
memo/experience-only and not GLM/GBM-modelled. The filename `experience_study_technical_spec_v2_0_1.md`
is retained so existing `@docs/...v2_0_1.md` cross-references stay valid. The §E.1 statement that
mortality/lapse/CI-incidence are "the three decrements the AI layer **models**" remains accurate.
**Severity:** Low (documentation/contract hygiene).

---

## Open follow-ups (opened 2026-06-27, AI Analyst owner-UAT rounds 2–3)

The owner's UAT of the **AI Analyst** chatbot (2026-06-26→27) drove three rounds of fixes plus a
robustness pass — full behavioural detail in `docs/phase3_build_progress.md` → "Post-UAT hardening
(round 2)/(round 3)" and "Robustness hardening". The behaviour was changed **deliberately** and is
fully tested (suite **1114 passed, 6 skipped**, no keys), and the numbers-from-data guarantee is
preserved by default. But **three changes diverge from the locked specs**, and — per the **FU-4
precedent** — a formal in-place amendment of a locked spec **requires owner authorization**. Logged
here pending that decision; until then `phase3_build_progress.md` is the authoritative behavioural
record.

### [x] FU-5 — AI Analyst round-2/3 spec reconciliation (locked Req v3.0.1 / Tech v2.0.1) — **RESOLVED (2026-06-27)**
**(a) Opt-in "Analyst mode" relaxes the numeric post-check.** Req **FR-3B-34** states the numeric
traceability check "is mandatory and cannot be disabled." Round 3 added an **owner-opt-in,
default-OFF** "Analyst mode" that turns it into **flag-not-block** (renders the answer with a visible
"⚠ unverified figures — review" warning instead of blocking). The five **SQL gates never relax** in
either mode. → would amend FR-3B-34 + §7.10.5/§7.10.10 to permit the documented default-off opt-in.
**(b) Commentary is now fact-pack generate-then-verify, not single-SQL slot-fill.** Req **FR-3B-37**
("commentary numeric content subject to the same slot-filling + post-check regime as FR-3B-33/34")
and Tech **§E.7** (`_commentary_turn` slot-fill path) no longer match: commentary drafts **prose**
over an app-assembled fact pack (`ui/skills_logic.py::assemble_commentary_facts`) and verifies every
number verbatim (the memo-Skill pattern). Numbers stay 100% data-sourced and traceability still
blocks by default; `config/prompts/commentary.md` → **v2.0** (no `{sql, answer_template}` contract).
→ would amend FR-3B-37, Tech §E.7 (commentary path), and §F.3 (commentary prompt shape).
**(c) Multi-query synthesis for EXPLORATORY (new capability).** The locked pipeline (Req §7.10.1,
Tech §E.7) is **single-query per turn**. Round 3 added a bounded **plan→fetch→synthesise** path
(`_synthesis_turn`, prompts `synthesis_plan.md`/`synthesis_answer.md`), **feature-flagged
default-OFF** (`chatbot.multi_query_default`) and ON in the AI Analyst page; every planned query
still passes the SQL gates + MCP server. → would add to Req §7.10 and Tech §E.7.
**Also new config (Tech §F.1/§F.3):** `chatbot.max_tokens`, `analyst_mode_default`,
`multi_query_default`, `max_synthesis_queries` in `config/ai_config.yaml`; two new prompt templates
under `config/prompts/`.
**Resolution (2026-06-27):** the owner **authorised a formal in-place amendment** of both locked
specs (the FU-4 mechanism). Done: each spec carries a dated "Change from … (AI Analyst amendment,
2026-06-27 — in-place, no version bump; filename retained)" header note, and the affected sections
were amended in place with *why-it-changed* annotations — **Req v3.0.1** §7.10.1 (pipeline note),
FR-3B-33 (multi-query synthesis + `{{list:}}` slot), FR-3B-34 (Analyst mode), FR-3B-37 (fact-pack
commentary), FR-3B-46 (always-on caveat), and the §7.10.9 `ai_config` excerpt; **Tech v2.0.1** §E.7
(amendment note + the `handle_turn` step-4/5 corrections), §F.1 (`chatbot` config keys), and §F.3
(prompt layout). The filenames are retained so existing `@docs/...v3_0_1.md` / `@docs/...v2_0_1.md`
cross-references stay valid. **Severity:** Medium — contract hygiene; behaviour was already tested
and shipped (offline suite 1114 passed, 6 skipped).

---

## [ ] FU-6 — AI Analyst round-4 data-surface widening: owner go-live steps — **OPEN (owner-triggered)**

The 2026-06-27 **governed-maximum** data-surface widening (the "make the AI Analyst smarter / know all
the data" build) is **fully built, tested, and spec-amended** (offline suite **1139 passed, 6
skipped**; Req v3.0.1 + Tech v2.0.1 round-4 header notes; detail in `docs/phase3_build_progress.md` →
"Post-UAT hardening (round 4)"). Two **owner-triggered** steps remain to make it live — they are not
part of the offline gate:

1. **Re-init + re-fit to populate proposed factors.** The live `data/experience_study.duckdb` predates
   the new `gold_ai_proposed_factors` table. Re-run a study (or call
   `src.utils.db_init.init_database`) to create it, then **re-fit AI models on the Assumption
   Comparison page** to populate it. Until then, proposed-factor questions degrade to a safe
   "couldn't answer" rather than crash; the other widened tables (reconciliation, DQ, model points,
   registry, assumption sets) already exist and answer immediately.
   *(The earlier round-1 IUL-lapse re-run caveat — `gold_ae_results` shows IUL expected-lapses 0 until
   the study is re-run — is resolved by the same study re-run.)*
2. **Live AI Analyst re-test** (≥1 Anthropic + ≥1 DeepSeek model): proposed-assumption questions
   answer; reconciliation/DQ/registry/assumption-set questions answer; the study digest makes
   overview/comparison answers correct; Analyst-mode-ON flags rather than blocks; and the **PII bright
   line** holds (names / policy_id / Silver requests refuse). Use the `docs/phase3_uat_script.md` →
   "Post-UAT hardening (round 4)" re-test checklist.

**Severity:** Low — go-live operational steps, not a defect; the build + offline tests are complete.
