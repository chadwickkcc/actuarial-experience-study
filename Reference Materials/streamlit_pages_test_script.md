# Streamlit Pages Test Script — Phase 1B & 1C UI Extensions

**Pages under test:**
1. UL Account Value Monitor (FR-1B-13)
2. ULSG Shadow Account Monitor (FR-1B-14)
3. Annuity Surrender Explorer (FR-1C-13)
4. GLB Utilisation Monitor (FR-1C-14)
5. VUL Fund Value Monitor (FR-1C-15)
6. Product Comparison (FR-1C-16)
7. CI Incidence Summary (FR-1C-17)

**Calibration source:** Requirements Spec v2.1, §8.3 (generation parameters), §8.5 (macro scenario), §8.6 (A/E ranges).

**How to use:** Work through pre-flight first. Then run each page's tests in order. Mark each step with `[x]` for pass, `[F]` for fail. If any pre-flight item fails, stop and fix before running page tests — most page failures cascade from a bad study run.

---

## Pre-flight (one-time setup)

| Item | Action | Expected | Pass? |
|---|---|---|---|
| P1 | Open Home / Study Setup page | Loads in < 3s (NFR-P-05) | [ ] |
| P2 | Configure study: window 2016-01-01 to 2023-12-31, all 5 products, default exposure method | Config saves cleanly | [ ] |
| P3 | Click Run Study | Completes in < 60s (NFR-P-01); no error banner | [ ] |
| P4 | Open Study Run Log; capture latest run_id | Latest row shows your timestamp + all 5 products in scope column | [ ] |
| P5 | Open Data Quality Dashboard | DQ score visible; no critical (halting) checks failed on clean data | [ ] |
| P6 | Open Exposure Summary; confirm in-force reconciliation | Reconciliation within ±0.01% (NFR-C-01) | [ ] |

**Study run_id used for this test cycle:** ______________________

**Date tested:** ______________________

If P1–P6 all pass, proceed.

---

## 1. UL Account Value Monitor

**Spec:** FR-1B-13 — Time series of average account value by attained-age band, overlaid with credited interest rate series.

**Data scope:** UL policies only (Trad UL ~800 + ULSG ~800 + IUL ~200 = ~1,800).

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] At least one chart visible on initial render

### Tests

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 1.1 | Count attained-age bands shown | At least 4 bands (e.g. 30–39, 40–49, 50–59, 60–69, 70+) | [ ] | |
| 1.2 | Compare mean AV across bands at the same calendar year | Older bands have higher mean AV (monotonic) | [ ] | |
| 1.3 | Check overlaid credited-rate line is present and visually distinct | Second Y-axis or distinct colour; line between 2.5% and 5.5% | [ ] | |
| 1.4 | Read credited rate at 2023 (study year 8) | Approx 3.1% (per §8.5) | [ ] | Actual: _____ |
| 1.5 | Read credited rate at 2020 (study year 5) | Approx 3.0% (per §8.5; dip year) | [ ] | Actual: _____ |
| 1.6 | Look at general AV trend over 2016–2023 | Generally upward; possible flattening in 2022–2023 | [ ] | |
| 1.7 | Spot-check one policy's AV roll-forward (pick any UL policy_id; use DQ-UL-01 identity: `AV(end) ≈ AV(begin) + premiums − loads − COI + interest`) | Within 1 currency unit | [ ] | Policy_id used: _____ |
| 1.8 | If product sub-filter exists: compare Trad UL vs ULSG average AV at age 60–69 | ULSG higher (older issue ages, larger face) | [ ] | |
| 1.9 | Filter out IUL; confirm page still renders | No errors; chart updates | [ ] | |

**Page 1 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 2. ULSG Shadow Account Monitor

**Spec:** FR-1B-14 — Distribution chart of shadow account funding ratios; policies below 1.0 highlighted. Plus FR-1B-09 shadow account coverage A/E.

**Data scope:** ULSG policies only (~800, where `is_ulsg_flag = TRUE`).

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] Distribution chart visible (histogram, density, or violin)

### Tests

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 2.1 | Count total policies plotted | ~800 (not 1,800 — Trad UL and IUL must be excluded) | [ ] | Actual count: _____ |
| 2.2 | Distribution centre | Roughly centred between 0.9 and 1.1 (generated at 80–120% of AV) | [ ] | |
| 2.3 | Min funding ratio | All values ≥ 0 (DQ-UL-02). Any negatives = bug | [ ] | Min observed: _____ |
| 2.4 | Visual highlight on policies with funding ratio < 1.0 | Distinct colour, shading, or count callout | [ ] | |
| 2.5 | Count of policies with funding ratio < 1.0 | Non-zero but minority (seeded population is mostly ≥ 1.0) | [ ] | Count: _____ |
| 2.6 | Cross-check 2.5 against DQ quarantine log (DQ-UL-03) | Matches or close to the count of flagged ULSG records | [ ] | |
| 2.7 | Shadow coverage A/E displayed somewhere on page | Headline metric or summary tile | [ ] | Value: _____ |
| 2.8 | Coverage A/E numeric reasonableness | Roughly 0.9–1.1 on clean seeded data | [ ] | |
| 2.9 | Filter to non-ULSG (if filter exists): page should be empty or show error | No funding ratios for Trad UL/IUL (they're NULL) | [ ] | |

**Page 2 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 3. Annuity Surrender Explorer

**Spec:** FR-1C-13 — A/E by contract year, product type, market type; shock-lapse panel; dynamic-lapse diagnostic. Plus FR-1C-07 (full vs partial), FR-1C-11 (GLB suppression).

**Data scope:** 1,400 annuity contracts (900 fixed: DA_FIXED/DA_FIA; 500 variable: DA_VA).

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] At least three distinct panels visible: A/E view, shock-lapse panel, dynamic-lapse diagnostic

### Tests

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 3.1 | Look at A/E by contract year for years 1–5 | All in range 0.90–1.10 (§8.6) | [ ] | |
| 3.2 | Find year 7 (surrender-charge-expiry year) | Visible spike; actual surrender rate ~60% (vs 1.5–3% in earlier years) | [ ] | |
| 3.3 | Year 7 A/E ratio | Range 0.85–1.15 (§8.6) | [ ] | Actual: _____ |
| 3.4 | Shock-lapse panel separates `approaching_expiry = TRUE` contracts | Distinct bucket or tile | [ ] | |
| 3.5 | Product type cut: DA_FIXED vs DA_FIA vs DA_VA | All three present; different A/E values | [ ] | |
| 3.6 | Market type cut: NQ vs TRAD_IRA | TRAD_IRA shows lower surrender in pre-59½ ages (tax penalty effect) | [ ] | |
| 3.7 | Full surrender vs partial withdrawal toggle/view | Both decrement types selectable, with separate A/E | [ ] | |
| 3.8 | Dynamic-lapse multiplier diagnostic, years 1–5 | Multiplier flat near 1.0 (rate differential negative; cap at min(3.0, max(0.3, ...))) | [ ] | |
| 3.9 | Dynamic-lapse multiplier diagnostic, year 7 (2022, rate diff +1.0%) | Slightly above 1.0 (with k_annuity=0.8, ~1.008) | [ ] | Actual: _____ |
| 3.10 | Filter to `glwb_elected_flag = TRUE` | Surrender A/E visibly lower than non-GLB (FR-1C-11 suppression) | [ ] | |

**Page 3 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 4. GLB Utilisation Monitor

**Spec:** FR-1C-14 — Moneyness ratio distribution; GLWB utilisation rate by attained age and duration.

**Data scope:** Annuity contracts with `glwb_elected_flag = TRUE`. Per §8.3: 60% of DA_VA (~300) + 40% of DA_FIA share + 0% of DA_FIXED = ~350–500 contracts.

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] Moneyness distribution chart present
- [ ] Utilisation rate chart(s) present

### Tests

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 4.1 | Count of contracts plotted | 350–500 (not 1,400 — must filter to GLB-elected only) | [ ] | Actual count: _____ |
| 4.2 | Moneyness distribution centre, study year 8 | Centred near 1.0 with a left tail (some in-the-money) | [ ] | |
| 4.3 | Compare moneyness in 2021 vs 2022 | 2022 left tail visibly fatter (equity drawdown −18%) | [ ] | |
| 4.4 | `glwb_utilization_status` values present | Only WAITING / ACTIVE / DEPLETED; no other values | [ ] | |
| 4.5 | Utilisation rate by attained age | Near zero below age 60; rising sharply from age 65+ | [ ] | |
| 4.6 | Utilisation rate by duration | Starts near 0% at duration 1; rises with duration | [ ] | |
| 4.7 | Any contract with `glwb_elected_flag = FALSE` accidentally included | Should be zero; verify by total count match | [ ] | |
| 4.8 | If page shows expected vs actual surrender with GLB suppression toggle | With suppression: expected surrenders lower for in-the-money | [ ] | |
| 4.9 | DA_FIXED contracts (no GLB by spec) | Not present in any view | [ ] | |

**Page 4 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 5. VUL Fund Value Monitor

**Spec:** FR-1C-15 — Fund-value distribution by equity allocation band; `fund_value_to_spec_amount_ratio` time series.

**Data scope:** 800 VUL policies. Equity allocation mix per §8.3: 20% conservative (<50%) / 30% balanced (50–75%) / 50% high (≥75%). Fund returns GBM μ=7%/σ=15% yrs 1–5, μ=5%/σ=20% yrs 6–8.

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] Distribution by allocation band visible
- [ ] Time series visible

### Tests

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 5.1 | Three allocation bands present | Conservative / balanced / high (or equivalent labels) | [ ] | |
| 5.2 | Approximate count per band | ~160 conservative / ~240 balanced / ~400 high-equity | [ ] | Actual: ___/___/___ |
| 5.3 | Spread of fund-value distribution by band | High-equity widest, conservative tightest | [ ] | |
| 5.4 | `fund_value_to_spec_amount_ratio` time series, 2017 (study year 2) | Rising (equity return +22%) | [ ] | |
| 5.5 | Same series, 2022 (study year 7) | Clear drop (equity return −18%) | [ ] | |
| 5.6 | Same series, 2023 (study year 8) | Recovery (equity return +26%) | [ ] | |
| 5.7 | Volatility of ratio over time: high-equity band vs conservative band | High-equity visibly more volatile | [ ] | |
| 5.8 | Sub-account allocations reconciliation (DQ-VUL-02): separate_account_total_value = sum of sub_account values | Spot-check one policy; should match within rounding | [ ] | Policy_id used: _____ |
| 5.9 | Any policy with `fund_value_to_spec_amount_ratio < 0.5` flagged or highlighted | If page implements FR-1C-03 visual, OK; if not, note as enhancement, not bug | [ ] | |

**Page 5 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 6. Product Comparison

**Spec:** FR-1C-16 — Aggregate A/E by product across all 5 products on a single chart. Plus FR-1A-29 (CI bands, credibility flagging).

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] Single chart with all 5 products visible
- [ ] Decrement-type selector (mortality / lapse / surrender / CI) present

### Tests — A/E ranges (§8.6)

| # | Product / Decrement | Expected A/E range | Actual | Pass? |
|---|---|---|---|---|
| 6.1 | Term — mortality (count) | 0.85–1.00 | _____ | [ ] |
| 6.2 | Term — mortality (amount) | 0.80–0.95 | _____ | [ ] |
| 6.3 | Term — base lapse | 0.95–1.05 | _____ | [ ] |
| 6.4 | Term — PLT shock lapse | 0.90–1.10 | _____ | [ ] |
| 6.5 | WL — lapse / surrender | 0.90–1.05 | _____ | [ ] |
| 6.6 | UL (Trad) — lapse | 0.85–1.10 | _____ | [ ] |
| 6.7 | ULSG — lapse | 0.80–1.05 | _____ | [ ] |
| 6.8 | DA — surrender (base years) | 0.90–1.10 | _____ | [ ] |
| 6.9 | DA — surrender-charge-expiry year | 0.85–1.15 | _____ | [ ] |
| 6.10 | Annuity owner — mortality | 0.88–1.05 | _____ | [ ] |
| 6.11 | CI incidence (aggregate) | 0.90–1.10 | _____ | [ ] |

### Tests — page mechanics

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 6.12 | Decrement-type toggle works | Each toggle re-renders chart without error | [ ] | |
| 6.13 | All 5 products visible on each view (where applicable) | DA shouldn't appear in CI view; otherwise all 5 | [ ] | |
| 6.14 | Confidence intervals shown (FR-1A-29) | Error bars or shaded bands on every value | [ ] | |
| 6.15 | Low-credibility cells visually flagged (Z < 0.5) | Colour change, asterisk, hatching, or tooltip | [ ] | |
| 6.16 | Directional sanity (NFR-C-07): ULSG lapse A/E ≤ Trad UL lapse A/E | ULSG should be at the lower end of its range | [ ] | |
| 6.17 | Click on a cell drills through (FR-1A-30) | Underlying seriatim records shown with PII masked | [ ] | If drill-through not implemented for this page, mark N/A |

**Page 6 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## 7. CI Incidence Summary

**Spec:** FR-1C-17 — Aggregate CI A/E across all products with CI riders; breakdown by illness code; heat map by attained age × illness type.

**Data scope:** Policies with `ci_rider_flag = TRUE`. Per §8.3 penetration: 25% Term + 20% WL (ex-small-face) + 15% Trad UL + 15% IUL + 15% VUL + **0% ULSG** + **0% annuities**. Expected ~2,000 policies.

### Pre-conditions

- [ ] Page loads in < 3s
- [ ] Aggregate A/E visible
- [ ] Illness-code breakdown chart visible
- [ ] Heat map visible

### Tests — exclusions (critical)

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 7.1 | Annuity contracts in CI population | Zero (filter to IDs DA-/DAF-/DAV-) | [ ] | Count: _____ |
| 7.2 | ULSG policies in CI population | Zero | [ ] | Count: _____ |
| 7.3 | Total CI-rider policies plotted | ~2,000 (sum across Term/WL/UL/IUL/VUL) | [ ] | Actual: _____ |

### Tests — aggregate and illness mix

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 7.4 | Aggregate CI A/E | 0.90–1.10 (§8.6) | [ ] | Actual: _____ |
| 7.5 | Illness CI-001 (Cancer) share of claims | ~40% ± 3pp | [ ] | Actual: _____ |
| 7.6 | Illness CI-002 (MI) share of claims | ~20% ± 3pp | [ ] | Actual: _____ |
| 7.7 | Illness CI-003 (Stroke) share of claims | ~12% ± 3pp | [ ] | Actual: _____ |
| 7.8 | Total illness shares sum to 100% | Yes (sanity) | [ ] | |
| 7.9 | All 10 illness codes (CI-001 through CI-010) appear somewhere | Even rare ones present, possibly with very low counts | [ ] | |

### Tests — heat map

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 7.10 | Heat map dimensions | ~10 illness codes × ~6–7 attained age bands | [ ] | |
| 7.11 | Cancer (CI-001) intensity by age | Concentrated in middle ages (45–64); rising into older | [ ] | |
| 7.12 | MI/Stroke (CI-002/003) intensity by age | Rising sharply with age | [ ] | |
| 7.13 | Rare codes (CI-009 Blindness, CI-010 Deafness) | Sparse cells; some may be empty (correct — low incidence) | [ ] | |
| 7.14 | Low-credibility cells flagged | Distinct visual indicator (Z < 0.5) | [ ] | |

### Tests — termination consistency

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| 7.15 | Pick one CI claim event; check `termination_cause_code` | Equals `CI_ACCELERATED_BENEFIT` | [ ] | Policy_id: _____ |
| 7.16 | Same policy: face amount reduced by `ci_rider_sum_assured` | Confirmed | [ ] | |

**Page 7 verdict:** PASS / FAIL / PARTIAL — ____________________

---

## Cross-page consistency checks (do last)

| # | Test | Expected | Pass? | Notes |
|---|---|---|---|---|
| X1 | UL Account Value Monitor (Trad UL count) + ULSG Shadow Monitor (ULSG count) | Sum approximately 1,600 (excludes IUL); within ±20 of seeded counts | [ ] | |
| X2 | Annuity Surrender Explorer total count + GLB Utilisation count | All-annuity = 1,400; GLB-elected subset 350–500 | [ ] | |
| X3 | Product Comparison ULSG lapse A/E vs ULSG Shadow Coverage A/E | Both should be in the 0.8–1.05 range; if one is wildly different, one of the two pages has a data scope bug | [ ] | |
| X4 | CI Summary aggregate A/E vs Product Comparison CI A/E | Should be identical | [ ] | |
| X5 | Compare a single policy across UL AV Monitor and Product Comparison | Same policy_id should appear consistently in both | [ ] | |

---

## Sign-off

| Section | Status | Date |
|---|---|---|
| Pre-flight | | |
| Page 1 — UL Account Value | | |
| Page 2 — ULSG Shadow | | |
| Page 3 — Annuity Surrender | | |
| Page 4 — GLB Utilisation | | |
| Page 5 — VUL Fund Value | | |
| Page 6 — Product Comparison | | |
| Page 7 — CI Incidence Summary | | |
| Cross-page checks | | |

**Overall verdict:** PASS / FAIL / PARTIAL

**Top issues found (for back-log):**
1. ____________________
2. ____________________
3. ____________________

**Notes / observations:**

---

## Appendix — Quick reference

### Macro scenario (§8.5)

| Study Yr | Calendar | Market Rate | Credited | Diff | Equity | Regime |
|---|---|---|---|---|---|---|
| 1 | 2016 | 1.8% | 3.2% | −1.4% | +12% | Low-rate |
| 2 | 2017 | 2.4% | 3.2% | −0.8% | +22% | Low-rate |
| 3 | 2018 | 2.9% | 3.2% | −0.3% | −5% | Low-rate |
| 4 | 2019 | 1.9% | 3.1% | −1.2% | +31% | Low-rate |
| 5 | 2020 | 0.9% | 3.0% | −2.1% | +18% | Shock |
| 6 | 2021 | 1.5% | 2.9% | −1.4% | +29% | Rising |
| 7 | 2022 | 3.9% | 2.9% | +1.0% | −18% | Stress |
| 8 | 2023 | 4.0% | 3.1% | +0.9% | +26% | Stress |

### Product counts (§8.1)

| Product | Count | Sub-mix |
|---|---|---|
| Term Life | 3,200 | — |
| Whole Life | 2,800 | — |
| Universal Life | 1,800 | 800 Trad UL / 800 ULSG / 200 IUL |
| Variable Universal Life | 800 | — |
| Deferred Annuity | 1,400 | 900 fixed / 500 variable |
| **Total** | **10,000** | |

### CI illness weights (§8.4)

| Code | Illness | Weight |
|---|---|---|
| CI-001 | Cancer | 40% |
| CI-002 | MI | 20% |
| CI-003 | Stroke | 12% |
| CI-004 | CABG | 7% |
| CI-005 | Kidney failure | 5% |
| CI-006 | Organ transplant | 4% |
| CI-007 | MS | 3% |
| CI-008 | Paralysis | 3% |
| CI-009 | Blindness | 3% |
| CI-010 | Deafness | 3% |

### A/E calibration ranges (§8.6) — already reproduced in Page 6 above.
