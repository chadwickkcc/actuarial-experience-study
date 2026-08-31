# Actuarial-Correctness Fixes — COMPLETE

**Delivered:** 2026-08-31, on top of commit `5020353` (the 15-item adversarial batch).
**Scope (owner-approved):** M-1, M-2, M-3, M-4, M-6, M-20 — **all six done**.
**Gate:** 1315 → **1343 passed, 3 skipped, 0 failed** (+28 tests, all TDD).
**Demo DB:** rebuilt, run `c0c86c2f-b68b-4065-a1dd-6ec5340e7a44`.

## Owner decisions (2026-08-31)

| Item | Decision |
|---|---|
| M-2 | **Universal discontinuance** — `actual_lapses` counts LAPSE + SURRENDER for every product, matching what the single benchmark rate measures. |
| M-1 | **NULL where no basis exists** — surrender A/E withheld; actual surrender counts retained. No invented rates. |
| M-4 | **Add a segment-level tie-out** — keep the movement identity, add a check that the exposure file carries the decrements the movement claims. |
| M-20 | **Filter negligible-exposure cells** before fitting (config-driven), keeping the withhold-on-divergence guard as a backstop. |

## Status

| # | Item | Status |
|---|------|--------|
| M-1 | `expected_surrenders` duplicated `expected_lapses` | ✅ `_surrender_expected` → NULL; counts kept |
| M-2 | Portfolio lapse A/E contaminated by annuities | ✅ `_discontinuance_flag`, universal |
| M-3 | WL acceptance test double-counted surrenders | ✅ double-count removed, band restored |
| M-4 | Recon never read the exposure file | ✅ `_segments_tie_out` wired into recon |
| M-6 | `silver_policy_events` triple-write | ✅ per-policy product code + replace-not-append |
| M-20 | Mortality GLM order-sensitivity | ✅ negligible-basis cells excluded; **fit now order-invariant** |

## Verified outcomes

| Check | Result |
|---|---|
| M-1 `expected_surrenders` / `ae_surrender` non-NULL rows | **0 / 0** (was every row) |
| M-1 actual surrender counts retained | 2,252 |
| M-2 portfolio discontinuance A/E | 6,733 / **1.1461** (was 5,142 / 0.8753 with a zero annuity numerator) |
| M-2 annuities now contribute actuals | 1,591 |
| M-3 WL discontinuance A/E | 1469 / 1440.29 = **1.0199** — inside the ORIGINAL spec band [0.90, 1.05] |
| M-4 recon all products pass, tie-out active | True |
| M-6 events total vs distinct | **33,642 / 33,642** — all 11,468 phantom rows gone |
| M-20 fit under 6 different row orders | **byte-identical** (TERM 51 factors disp 0.892111; WL 55 factors disp 0.924038) |
| M-20 mortality products fitted | TERM **and** WL (16 registry rows, 324 factors) |

Unchanged, as intended: mortality 1,206 / 0.6852 · WL mortality 611/931.26 =
0.6561 · CI 589 / 1.2325 / 10 codes · 249,881 exposure segments · both planted
stories (0.6031→0.6123→0.7541→0.8801; 1.1940 / 1.8806) · fraud 1,808 / 32.

**Verification:** full suite 1343 passed / 3 skipped; five governance harnesses
PASS (6/6, 7/7, 5/5, 4/4, 8/8); Streamlit boot HTTP 200, 0 tracebacks.

## Notes for the record

- The **discontinuance** framing is the crux of M-1 and M-2: one benchmark rate per
  (product, policy_year) that means lapse for Term/UL/ULSG/IUL/VUL, lapse+surrender
  for WL, and the FRDA surrender curve for annuities.
- **FR-1B-05** (a separate WL surrender A/E) remains **unmet by design** — it cannot
  be computed without a separate surrender benchmark, and splitting the combined WL
  rate would be fabricating experience. Recorded for the spec owner.
- Two tests were repointed rather than deleted, preserving their intent: the DA
  shock-lapse test now asserts on the discontinuance columns, and the commentary
  fact-pack test now asserts SURRENDER carries counts but no ratio.
- The walkthrough's lapse line is now labelled **discontinuance** and reads 1.1461.
