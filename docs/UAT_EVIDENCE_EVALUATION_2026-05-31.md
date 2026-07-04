# UAT Evidence Evaluation — `temp_output/`

**Date:** 2026-05-31  **Reviewer:** Claude (fresh, independent review)
**Study run:** `21E8F001` (2016-01-01 → 2023-12-31, ANNUAL exposure, LF credibility)
**TEV assumption set:** `82bbf100…` | Baseline TEV **$173,096,606**

> This is an independent re-evaluation performed from the raw evidence and the live code. It does
> **not** rely on the earlier `UAT_EVIDENCE_TRIAGE_2026-05-23.md`. Every artefact in `temp_output/`
> was examined and, where a finding was material, traced to the underlying CSV data and source code.

---

## 1. Scope & method

Evidence examined (88 files):

| Type | Count | Notes |
|---|---|---|
| PNG charts (`newplot*.png`) | 52 | All viewed; 4 highest-stakes charts re-verified directly against data/code |
| CSV exports | 29 | All parsed and cross-checked numerically |
| HTML reports | 5 | Working Actuary, Chief Actuary, TEV Working Actuary, TEV Impact, Audit-trail UUIDs |
| YAML envelope artefacts | 2 | Credibility-envelope governance record |

Judged against spec ground-truth from `experience_study_requirements_spec_v2.1.md` and
`experience_study_technical_spec_v1.1.md`:
A/E ≥ 0 always; clean-data mortality A/E target 0.85–1.00 (FR-1A / Section 9);
credibility `Z = min(1, √(actual/1082))` (FR-1A-24); Poisson CI on A/E (FR-1A-25);
CI bands + Z<0.5 flagging required on all A/E charts (FR-1A-29); bands ordered ascending.

**Where automated review conflicted with the data, the chart and source were inspected to resolve it**
— this corrected at least one false positive (see §5).

---

## 2. Findings summary

| ID | Severity | Area | One-line | Disposition |
|---|---|---|---|---|
| **H1** | HIGH | Credibility | Aggregate credibility-Z ≈ 0 for all products/illnesses (averaging cell-level Z) → cred-weighted A/E collapses to ~1.0 | **Fix (WS1)** |
| **H2** | HIGH | Product Comparison | Results **table** shows impossible 95% CI (negative lower, ±hundreds) while the chart is correct | **Fix (WS1)** |
| **H3** | HIGH | Annuity surrender | "Observed surrender rate" reaches ~960% (rate > 100% impossible) | **Fix (WS2)** |
| **H4** | HIGH | DA data quality | `benefit_base` NULL for non-variable GLB → DQ-DA-02 quarantines 577 recs → DA experience decimated | **FIXED + re-run verified** (DQ-DA-02 0 failures; DA DQ 54.4%→93.2%) |
| **M1** | MED | CI explorer | Age-band heatmaps reverse-ordered; no Z<0.5 flagging; outlier cells (~1500%) unflagged | **Fix (WS1)** |
| **M2** | MED | A/E heatmap | Diverging colour scale centred at 0 implies negatives; sparse outliers dominate | **Fix (WS6)** |
| **M3** | MED | Calibration | Clean-data mortality A/E below 0.85–1.00 (WL 0.57, ULSG 0.68, UL 0.69, IUL 0.73) | **Accepted — by-design** |
| **M4** | MED-HIGH→**N/A** | TEV envelope | `success:false` + abnormal termination; `theta_max`=baseline but `tev_max` ≠ baseline TEV | **Investigated — works as designed (not a bug)** |
| **M5** | MED | VUL lapse | VUL lapse A/E ≈ 2.18 (160–285% across years) | **Accepted — by-design** |
| L1 | LOW | Export | Missing chart titles on exported PNGs | **Accepted — won't fix** |
| L2 | LOW | Export | Duplicate exports (33≈34, 49≈51) | Housekeeping |
| L3 | LOW | Layout | Axis-label clipping / legend truncation | **Fix (WS6)** |
| L4 | LOW | DA data | DQ-DA-01 surrender-charge mismatch (now 95 — the only DA quarantine left) | **Root-caused; documented — deferred (data-model/spec)** |
| L5 | LOW | CI charts | Gender CI A/E bars lack CI/error bars (FR-1A-29) | **Fix (WS1)** |
| L6 | LOW | TEV charts | Sensitivity heatmap lacks colour-scale legend; tornado lacks 0 line | **Fix (WS6)** |

---

## 3. HIGH-severity findings (detail)

### H1 — Aggregate credibility-Z collapses to ≈ 0
- **Evidence.** `15-53_export.csv`: Credibility Z = **0.000 for every product** (TERM with 84 deaths
  should be ≈0.279; WL 232 → ≈0.463). `15-44_export_2.csv`: CI Cred Z = 0.001–0.004 for 1–14 claims
  (should be ≈0.03–0.11). With Z≈0, the **Cred-Wtd A/E column collapses to the complement (~1.00)**
  for all products — credibility weighting is effectively disabled.
- **Root cause.** Aggregate Z is computed as the **average of cell-level Z**
  (`ui/pages/06_ci_explorer.py:88` → `AVG(credibility_z_ci)`) rather than recomputed from the
  **summed** claim count. Averaging cells that each have ~0 claims yields ~0.
- **Spec impact.** FR-1A-24 (credibility weighting) and FR-1A-29 (Z<0.5 flagging).
- **Files.** `ui/pages/06_ci_explorer.py`, `13_product_comparison.py`, `14_ci_incidence_summary.py`,
  `src/aggregation/aggregator.py`, `src/calculation/ae_engine.py`.

### H2 — Product-Comparison results **table** shows impossible 95% CI
- **Evidence.** `15-53_export.csv` "95% CI Lower/Upper": TERM **[-186.68, +575.59]**,
  DA_FIA [-198.04, +610.62], VUL [-147.00, +453.24]. A/E CIs can never be negative; a CI on
  A/E≈0.94 should be ≈[0.74, 1.16].
- **Key nuance.** The **chart on the same page is correct** — `newplot (41)` Mortality A/E error bars
  are sensible (Term ≈ [0.75, 1.15]) because the chart path
  (`ui/pages/13_product_comparison.py:124-130 _add_ci_cred`, `(ae−1.96·se).clip(lower=0)`) is fine.
  The **table** uses a different / mis-aggregated source. Engine cell-level CI
  (`ae_engine.py:188-191`) is also fine. So the bug is localized to the table-build path.
- **Files.** `ui/pages/13_product_comparison.py`.

### H3 — Annuity "Observed surrender rate" exceeds 100%
- **Evidence.** `newplot (26)`: an "Observed rate" by contract year peaks at **~960% (year 1)** and
  sits at 300–680% thereafter. A decrement rate cannot exceed 100%.
- **Root cause.** `ui/pages/10_annuity_surrender.py` sections 4–6 compute
  `observed_rate = SUM(actual_surrenders) / SUM(exposure_years)` (≈lines 148/181). When policies
  surrender early, central exposure-years is fractional, inflating the ratio far above 1.0. Needs an
  at-risk (beginning-of-period in-force) denominator, or a ≤100% cap with a clear caption.
- **Files.** `ui/pages/10_annuity_surrender.py`.

### H4 — DA `benefit_base` NULL → 577 quarantined → DA experience decimated
- **Evidence.** DQ-DA-02 ("benefit_base must be >= 0 for all GLB contracts", **Error**) **fails 577**
  records (`15-40_export.csv`); DA DQ score **54.4%, 639/1400 quarantined** (`15-45_export_4.csv`).
  Downstream: DA_FIA mortality A/E **0.21** (1 death vs 4.7 expected); DA_FIXED & DA_VA show **0 deaths**
  over 700–2,050 exposure-years (`15-52_export_2.csv`, `15-53_export.csv`).
- **Root cause.** Generator `synthetic_data/generators/annuity.py:235,368` sets `benefit_base = None`
  for non-variable / non-GLWB contracts, while DQ-DA-02
  (`src/data_quality/checks/annuity_checks.py:131-150`) flags `benefit_base IS NULL OR < 0` for **all**
  GLB (incl. GMDB) contracts → generator-vs-check scope mismatch. Resolve by either populating
  `benefit_base` for all GLB contracts or narrowing DQ-DA-02 scope.
- **Files.** `synthetic_data/generators/annuity.py`, `src/data_quality/checks/annuity_checks.py`.

---

## 4. MEDIUM / LOW findings (detail)

- **M1.** `newplot (10)/(11)/(45)` CI A/E by attained-age band appear **descending** (75-79→15-19);
  cells reach A/E ≈ 15 (1,500%) with **no Z<0.5 flagging**. FR-1A-29. Files: `06_ci_explorer.py`,
  `14_ci_incidence_summary.py`.
- **M2.** `newplot (3)` A/E heatmap uses a diverging RdBu scale centred at **0** (legend −5…+5). No
  cell is negative (verified vs `15-42_export.csv`), but on-target (~1.0) cells look pale and sparse
  outliers (9.09, 6.70) dominate. Centre the scale at **1.0** and clamp/annotate sparse cells. Files:
  `04_mortality_ae.py` / `05_lapse_ae.py`.
- **M3 (ACCEPTED — by-design).** Clean-data mortality A/E below the Section-9 0.85–1.00 band —
  **WL 0.57 (DQ 100%)**, ULSG 0.68, UL 0.69, IUL 0.73 (`newplot (41)` / `15-53`). Engine maths is
  correct; this reflects accepted synthetic-data variation. **No recalibration** — recorded here as a
  known deviation from the acceptance band.
- **M4 (INVESTIGATED — works as designed, NOT a bug).** The `envelope_*.yaml` signals
  (`success:false`, `ABNORMAL_TERMINATION_IN_LNSRCH`, `theta_max`=1.0 with `tev_max`≠proposed) looked
  inconsistent on first read. Code + test review resolved it:
  (1) `run_tev_fast` uses a **25-yr fast projection** while `proposed_tev` is the **full 60-yr** run,
  so `tev_max = run_tev_fast(theta_max)` is internally consistent. `src/tev/envelope.py:318-319`
  **deliberately clamps** `tev_min`/`tev_max` to bracket the full proposed, and
  `test_envelope.py::test_containment_*` asserts exactly this. Containment held
  (164.68M <= 173.10M <= 173.81M).
  (2) `success=False` on optimizer non-convergence is the **designed, tested** graceful path —
  `tests/stress_test_2_convergence_failure.py` explicitly verifies `success=False` + optimizer
  diagnostics + finite bounds + a computed percentile on non-convergence.
  **No code change made.** Remaining nuance: the recurring `ABNORMAL_TERMINATION_IN_LNSRCH` on the
  near-flat fast-TEV objective is a numerical-robustness *enhancement* opportunity (optional, future),
  not a correctness defect.
- **M5 (ACCEPTED — by-design).** VUL lapse A/E ≈ **2.18** (241 vs 110.7 expected; `15-53`),
  160–285% across calendar years (`newplot (37)`). Accepted as intended VUL dynamic-lapse behaviour.
- **L1 (ACCEPTED — won't fix).** Missing titles on exported PNGs `(19)(26)(27)(28)(51)` — titles are
  set via `st.subheader` in-app and dropping them from the PNG export is acceptable.
- **L2.** Duplicate exports: `(33)`≈`(34)`; `(49)`/`(51)` near-duplicate sensitivity heatmaps
  (housekeeping).
- **L3.** Label clipping: `newplot (4)` right-most `WL_LIFE_PA[Y]` clipped; legend `TRAD_IR`
  truncation; tight axes on `(16)`. Adjust margins.
- **L4 (ROOT-CAUSED — documented, not auto-fixed).** After the H4 fix + re-run, DA's remaining
  quarantines (95) are **all** DQ-DA-01. Root cause: `SC_SCHEDULE_7YR/10YR` in
  `synthetic_data/generators/annuity.py` are the **surrender-probability curve** (year-7 = 0.60,
  year-10 = 0.55 are *behavioral shock* rates), yet the same list is stored as
  `surrender_charge_schedule`. The generator caps stored `surrender_charge_remaining` at 15% of AV,
  while DQ-DA-01 expects `av × schedule_rate`, so shock-year contracts (rate 55–60%) deviate >50% and
  quarantine. The **check is correct**; the data model conflates the surrender-*probability* curve
  with the surrender-*charge* penalty schedule. **Recommended fix (deferred):** add a separate real
  declining charge schedule (e.g. 7%→0%) for `surrender_charge_*`, keeping the probability curve for
  the surrender simulation. Deferred because it is a data-model/spec change that risks the
  **validated** year-7 surrender-shock charts; WARN-severity, 95/1400 records. Awaiting user call.
- **L5.** Gender CI A/E bars `(12)/(46)` lack CI/error bars (FR-1A-29 consistency).
- **L6.** Sensitivity heatmap `(49)` lacks a colour-scale legend; tornado `(48)` could add a 0
  reference line.

---

## 5. Verified OK / corrected false positives (for comfort)

- **No negative A/E anywhere.** A "negative heatmap cells" claim from automated review was a
  diverging-colour-scale misread; the underlying `15-42_export.csv` data is entirely ≥ 0.
- **Mortality A/E chart CI bands are correct** (modest, floored at 0). Only the *table* export (H2)
  is wrong.
- **TEV reconciles.** Per product ANW+VIF=TEV and PVFP−PVCoC=VIF (`15-55`); TOTAL TEV $173,096,606
  matches the envelope and both TEV reports; the impact matrix correctly maps Longevity→annuities and
  CI-Incidence→CI-rider products, and shows mortality shocks = 0 for DA.
- **Shock-lapse explorer** (`newplot (6)` / `15-43_export_2`): correct Poisson CI (lower floored at
  0), credibility greying, ascending jump-ratio bands — the credibility/CI machinery works correctly
  here, which contrasts with H1/H2 and helps localize them.
- **Duration-band ordering** is fixed (categorical dtype) on GLWB charts `(30)/(31)`.
- **4-stage TEV workflow + approval audit trail** intact (`15-58`, `15-59`, audit-trail UUIDs).
- **Data quality** 100% for TERM / WL / VUL; 91.9% for UL / ULSG.

---

## 6. Remediation workstreams (planned)

| WS | Covers | Summary |
|---|---|---|
| WS1 | H1, H2, M1, L5 | Centralise aggregate CI/Z (recompute from summed claims; Poisson CI floored at 0); fix product-comparison table; add Z<0.5 flagging to CI heatmaps & gender charts |
| WS2 | H3 | Fix annuity observed-rate denominator (at-risk in-force) or cap ≤ 100%; relabel |
| WS3 | H4, L4 | Reconcile `benefit_base` generation with DQ-DA-02 scope; fix DQ-DA-01 schedule; regenerate DA data (seed 42) + re-run pipeline |
| WS4 | M3, M5 | **Documentation only** — accepted by-design deviations recorded above |
| WS5 | M4 | Investigate envelope non-convergence; make `theta_max`/`tev_max` consistent; gate percentile on `success` |
| WS6 | M2, L2, L3, L6 | Colour scale centred at 1.0 + outlier clamp; de-dupe exports; margins/legend fixes |

**End-to-end verification:** re-run the study + TEV pipeline, re-export the affected charts/tables,
and query `study.db` to confirm — (a) aggregate Z and CI are sane, (b) DA DQ score recovers and DA
deaths > 0, (c) no observed rate > 100%, (d) envelope `success:true` with consistent θ — then run
`pytest tests/`.

---

## 7. Remediation outcomes & verification (executed 2026-05-31)

**Files changed**
- `ui/stats_helpers.py` (new) — shared aggregate `credibility_z`, `poisson_ci` (floored at 0), `credibility_weighted_ae`.
- `ui/pages/20_tev_stage1.py`, `06_ci_explorer.py`, `04_mortality_ae.py` — recompute Z/CI from summed claims instead of `AVG(per-cell)`; CI heatmap colour clamp + low-cred caption; gender CI error bars (WS1, M1, L5).
- `src/reporting/generator.py` — credibility-weighted A/E derived from aggregate Z×A/E; CI illness Z from summed claims (WS1).
- `ui/pages/10_annuity_surrender.py` — observed-rate denominator uses at-risk exposure across all segments (WS2/H3).
- `ui/pages/04_mortality_ae.py`, `22_tev_stage3.py` — A/E heatmap clamped to 0–2 centred at 1.0; axis margins; sensitivity-heatmap caption (WS6).
- `synthetic_data/generators/annuity.py` — `benefit_base` emitted for any GMDB/GLWB contract (WS3/H4).
- `scripts/_uat_rerun.py` (new) — headless full-study re-run utility.

**Verified (queries against the re-run; new run replaces 21e8f001 as latest):**
- **H1/H2** — product/illness credibility-Z and 95% CI now recompute from aggregate counts: TERM Z 0.279 (was 0.000), CI lower never negative (was −186.68), cred-wtd A/E varies sensibly (WL 0.80, was ~1.0). Report headline cred-wtd A/E 0.765 (was ~1.0 via AVG). CI illness Z e.g. CI-001 = 0.114 (was 0.004).
- **H3** — annuity observed surrender rate now peaks at 46% (the year-7 shock); no rate > 100% (was 961%).
- **H4** — `benefit_base` populated for all 1,111 GLB contracts → **DQ-DA-02: 0 failures** (was 577); **DA DQ score 54.4% → 93.2%**, quarantine 639 → 95 (all now DQ-DA-01); DA surrender experience recovered. Pipeline re-run COMPLETE; life-product results unchanged vs original (TERM 84 deaths, WL 232, IUL 6, etc.); DA mortality remains near-zero (M3-class, accepted).
- **M4** — confirmed working-as-designed (no change).
- **L4** — root-caused (surrender-charge-schedule conflation); deferred.
- All changed files `py_compile` clean.

**Test-suite status (IMPORTANT — pre-existing, not introduced here).** `pytest tests/` → 627 passed,
51 failed, 5 skipped. The 51 failures are **pre-existing and unrelated to these fixes**:
(1) they sit in modules untouched by this work (`term_checks.py:518` `None+None` `TypeError`; TEV
sensitivity-directionality in the TEV engine; UL/VUL DQ);
(2) the DA `bad_db` tests (`test_dq_da` DA02/04/05) fail on a check-vs-test contract mismatch —
`check_dq_da_02` filters `WHERE _etl_run_id = run_id` but the tests pass a fresh `uuid`, so the check
sees 0 rows regardless of the seeded value (the test file's own header comment wrongly says the DA
checks don't filter by run_id);
(3) count-expectation failures (e.g. `test_ul_total_records_count` expects 1800) fail against the
**original** run too (the pipeline produces UL=800), proving the stale expectation predates this work.
**Test-hygiene flaw observed:** the integration tests' `prod_db` fixture targets the real
`data/experience_study.duckdb` and the clean-data DQ tests **write** to it (`run_dq_checks`), so
running the suite pollutes the production DB with duplicate DQ-summary/quarantine rows. After the
suite run, the DB was restored from backup and the pipeline re-run once cleanly (1 DQ-summary row per
product confirmed). These pre-existing test issues are out of scope for the temp_output UAT and are
flagged for separate triage.
