# Adversarial Review — Fix Log (2026-08-31)

Companion to `adversarial_review_2026-08-31.md` (findings) and
`adversarial_review_handoff.md` (plan). Records what was fixed, how it was
verified, and one **new finding discovered during the work**.

**Owner-approved batch:** B-1…B-5, the four demo-facing items, M-10, and five
cheap wins — **15 items, all delivered**.

**Gate:** 1263 → **1315 passed, 2 skipped, 0 failed** (+52 tests; skips fell from
6 to 2 because m-1 restored four). Every fix is TDD: the test was written first
and observed to fail.

---

## Delivered

| # | Fix | Verification |
|---|---|---|
| **B-1** | `lineage.approve_and_supersede` now takes a `User`, calls `rbac.require(SIGN_OFF)`, refuses a set that has not been submitted for sign-off, and records the publish in the governance trail. | The recorded bypass now raises `PermissionDenied`; `user` is a required argument, so a caller cannot omit authorisation. 5 tests. |
| **M-10** | `run_fraud_scan` takes a `User` and requires `propose`; the UI button is no longer the only gate. | Junior actuary refused engine-side; analyst still runs. 3 tests. |
| **B-2** | `save_assumption_set` validates **before** writing the YAML; `_PRESERVED_COLS` gains `approved_by`/`approved_ts`/`superseded_by`; an APPROVED re-save with changed content is refused. | A rejected save leaves the artifact byte-identical; approval attribution survives a permitted re-save. 4 tests. |
| **B-3** | `_run_reconciliation` attributes rows to each policy's **own** `product_code` (the rule the segment writer already used). | Recon deaths 1,815 → **1,207**; UL-family labels now carry their own policies. Annuities gain their real `DA_FIXED/DA_FIA/DA_VA` codes, which also closes the recon↔A/E join gap (OBS-7). 3 tests. |
| **B-4** | `aggregator` aligns row and column totals **by index**, not position. | Failing dimension pairs on live data **19/132 → 0/132**. A column headed `NaN` (previously hidden behind the crash) is gone. 4 tests. |
| **B-5** | Exposure Summary selects, orders by and displays `product_code`, with a product filter; a failing year names its product. | 2 tests. |
| **M-5** | CI rows carry `calendar_year` (grain + actual-claim matching). | CI YoY/trend/drivers work end-to-end; 7,470 CI rows all carry a year. 3 tests. |
| **M-8** | `classify_trends` gains a configurable volume floor (`trend.min_actual_claims: 20`). | DA products with zero lapses now report `insufficient_data`; the real stories (mortality +0.0848, lapse +0.21) are unchanged. 3 tests. |
| **M-9** | `log_workflow_iteration` routed through `append_event`, so the log is genuinely hash-chained. | `verify_chain` now checks real rows and **detects tamper and deletion**; previously it returned a green "intact ✓" after checking zero rows. 4 tests. |
| **m-14** | CI Explorer no longer reports the demo's own headline story as an out-of-spec **warning**; developer copy ("update the A/E engine") replaced. | 1 guard test. |
| **M-13** | Traceability sign class accepts U+2212 / en / em dash and normalises them. | `−0.6561` no longer traces to `+0.6561`; range labels still parse as two positives. 6 tests. |
| **M-14** | `aggregates_per_cell_stat` is alias-aware (fixpoint over projection aliases). | The CTE-alias exploit returning 0.00106 against a true 0.6485 is blocked; direct form and legitimate queries unaffected. 7 tests. |
| **M-7** | `anti_selection_flag` is no longer dropped by the insert (the DDL always had the column). | **809** cells now flagged — exactly the predicted count. 1 test. |
| **m-1** | Reference-table fixtures corrected (`mortality_2012iar` / `mortality_2015vbt`). | 4 tests that had silently skipped now **run and pass**, restoring the only FR-1C-12 assertion. |
| **m-3** | Confidence lower bounds floored at zero on **both** bases. | Negative bounds 1,199 / 5,018 / 390 / 1,199 → **0 / 0 / 0 / 0**. 2 tests. |

### Architecture note
`log_workflow_iteration` moved from `src/assumptions/` to `src/governance/audit.py`.
An architecture guard correctly refused the shorter route: the core engine must
not import `src/governance` (one-way boundary). The iteration log is a
hash-chained governance artifact, so it belongs on the governance side.

---

## NEW FINDING — M-20: the mortality GLM is order-sensitive (needs an owner decision)

**Not in the approved batch. Discovered while rebuilding the demo database.**

The mortality GLM converges or diverges depending on the **physical row order** of
its input — the same data, differently laid out, gives a different fit.
Demonstrated on the *unchanged* pre-fix data by shuffling rows only:

```
seed=1  TERM: CRASH (ValueError: lam value too large)   WL: did not converge
seed=2  TERM: converged, 54 factors                     WL: converged, 60 factors
seed=3  TERM: converged, 54 factors                     WL: converged, 60 factors
```

This violates FR-3A-24 ("re-fitting with identical inputs and seed must reproduce
identical coefficients") — arbitrary physical ordering is not an input an actuary
can control. It also means the **previously shipped proposals were ordering luck**:
the old build's WL fit had a sound dispersion of 0.837, but its published CI bounds
still ranged to **2.05e+48**, so even the "good" fit emitted degenerate cells.

**What was done (defensive only — the statistical cause is untouched):**
1. `load_cells` now has an explicit `ORDER BY` on the **grain** columns, so a re-fit
   on identical inputs is reproducible (FR-3A-24).
2. A diverged fit is now **withheld, loudly**: a dispersion above 1e6 (a sound fit
   runs ~0.5–1.5) returns the standard "no AI proposal available" with the reason,
   instead of publishing a factor of ~1e-41 with a ~1e+48 interval.
3. A factor cell the bootstrap cannot bound is withheld rather than published with
   a NaN interval — FR-3A-19 requires every published factor to carry a CI.
4. The bootstrap no longer crashes the whole run on an undrawable resample.

**Consequence for the demo:** on the current build **WL mortality converges (60
factors) and Term mortality is withheld** with a stated reason. The walkthrough's
AI beat uses WL, so it works. AI figures are now **14 registry rows · 224 factors**
(was 16 · 332); the walkthrough and UAT tables are updated.

**Deliberately NOT done:** the ordering was not tuned to make more fits converge.
Picking an order that produces pleasing results would be choosing luck over
correctness, and would leave the fragility in place. The real fix is statistical —
most likely excluding negligible-exposure cells that drive separation, or
regularising the Poisson fit — and that is a modelling decision for the owner, not
a silent change.

---

## Demo database

Rebuilt end-to-end: reset → study re-run → AI fit → seeded workflow → fraud scan →
both A/E reports. Run `70f2ee85-b7c5-499d-926c-28b8e5cd33f9`.

Every walkthrough figure re-verified exact: 25,000 policies · 1,206 deaths /
0.6852 · WL 611/931.26 = 0.6561 · 5,142 lapses / 0.8753 · 589 CI claims / 1.2325
across 10 codes · 249,881 exposure segments · mortality story 0.6031 → 0.6123 →
0.7541 → 0.8801 · lapse story 1.1940 / 1.8806 · fraud 1,808 scored / 32 flagged /
max 1.20 · APPROVED assumption set with 3 hash-chained sign-offs.

One intentional, explainable difference: **recon deaths are 1,207 against A/E's
1,206.** One UL policy was issued and died on the same day — a real death in the
in-force movement, but it generates no exposure segment (`exposure_years > 0`), so
it cannot appear in A/E. Correct on both sides, and now visible per product.

**Verification:** full suite 1315 passed / 2 skipped; all five governance
harnesses PASS (6/6, 7/7, 5/5, 4/4, 8/8); Streamlit boot HTTP 200 with zero
tracebacks.

---

## Design decisions — batch 5 (2026-08-31)

The owner assessed the four remaining *design* calls and chose a course for each.
Gate **1381 → 1393 passed, 3 skipped** (+12 tests, all TDD).

| # | Decision | What was built | Verification |
|---|---|---|---|
| **M-11** | Measure cumulative drift, not just the step | `materiality_vs_prior_approved` now reports the larger of (a) the change vs the nearest approved ancestor and (b) the drift since the last set signed at the chain's **final** level (or the oldest approved ancestor if none). | Three 0.04 steps report 0.12, not 0.04; a split change no longer completes below chief. A chief sign-off resets the baseline, so drift does not accumulate forever. 3 tests. |
| **m-8** | Require submission before sign-off | `record_signoff` refuses a study run with no `STUDY_RUN_SUBMITTED` event. | An unsubmitted run cannot be signed; once submitted, its submitter cannot sign it — proposer ≠ approver is live for study runs for the first time. 2 tests, plus 9 existing tests updated to submit first. |
| **m-6** | Bind the YAML to its DB row | `yaml_sha256` recorded at save over the volatile-stripped payload; verified at load — fatal for a locked set, logged for an editable one. `verify_assumption_set_integrity` is surfaced in `reproducibility_stamp`, so the compliance pack carries it. | An off-disk edit of an APPROVED set now raises instead of loading silently; a legitimate re-save refreshes the hash (no false alarm); status churn does not trip it. 5 tests. Existing rows are baselined once at migration. |
| **m-12** | Product-relative claim/premium threshold | Rule 02 compares each claim with its own product's 95th percentile, floored at the absolute ratio, falling back to the floor for products with too few claims. | Rule 02 fires on **5%** of claims, down from 32%; flags 22 → **18**; the ring is still **14/14 at max 1.20** and now occupies the **top 14 places contiguously**, ahead of every other claim. 2 tests. |

**m-7 documented, not built** (owner decision): segregation keys on the account,
not the person. Recorded as **FU-8** in `DEFERRED_FOLLOWUPS.md` and in the
`check_segregation` docstring, with the `person_id` design sketched for whenever
an identity model exists. Exploitability is low — there is no self-service
account creation, so an administrator would have to issue one human two logins —
and a partial person model would imply a guarantee it could not enforce.

**Verification:** full suite 1393 passed / 3 skipped / 0 failed; all five
governance harnesses PASS (6/6, 7/7, 5/5, 4/4, 8/8); Streamlit boot HTTP 200 with
zero tracebacks. Demo DB migrated (hash column backfilled) and its fraud scan
re-run; walkthrough and UAT figures updated to 18 flagged.

---

## Still open

- **M-12** — traceability is set-membership, not claim-verification (owner chose
  fact-reference slots; **in progress**).
- Observations **OBS-6, OBS-7, OBS-8, OBS-10** and the stale documentation items
  **OBS-1 / OBS-2** in `DEFERRED_FOLLOWUPS.md`.
- Owner-only: eval-set re-lock, the optional live eval baseline, and the browser
  walk of the demo script (needs the owner's login keystroke).
