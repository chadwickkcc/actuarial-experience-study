# Fraud Cluster + Minor Items — COMPLETE

**Delivered:** 2026-08-31, on top of `1ba55c8`.
**Gate:** 1343 → **1375 passed, 3 skipped, 0 failed** (+32 tests, all TDD).
**Demo DB:** rebuilt, run `5d09d306-54eb-4fdb-ac91-44de42348633`.

## Owner decisions

| Item | Decision |
|---|---|
| m-11 | Regenerate with **organic claimant reuse** so the repeat-claimant rule has a real population to discriminate against. |
| M-18 | **Config roster file** of legitimate facilities; anything off-roster flags. |
| m-2 | **Enforce `export` on every download surface.** |
| M-16 | **Database wins after seeding** — config seeds users that don't exist, never overwrites `active`/`role`. |

## Fraud cluster — all done

| # | Fix | Evidence |
|---|---|---|
| M-17 | Rule 04 now needs **corroboration**, not just volume: an office must route ≥50% of its early claims through one hospital or illness, and only those claims flag. Threshold 3× → 4× median. | Rule-04 hits 27 → 15. The innocent office (56 claims, 37 hospitals, 56 claimants) no longer flags. |
| M-18 | Rule 06 replaced by a real **roster check** against `config/reference_tables/facility_roster.csv` (60 facilities). Bare region clause dropped — the configured region was the rarest by construction, not by risk. | Rule-06 hits 25 → 14; HOSP-066 caught as off-roster; 3 geography-only flags gone. |
| M-19 | Annuities get a **real basis** (`account_value` — a deferred annuity is a deposit, and its death benefit IS the account value) plus explicit `premium_basis` / `n_not_applicable`. The silent `NaN > 25 → False` trap is gone. | All 8 DA claims now evaluated, ratio exactly 1.000, correctly below the 25 threshold. |
| m-11 | Rule 05 requires a **cluster of ≥3, ≥30 days apart, deaths excluded** — one illness settling across two riders, or one person's two honest policies, no longer look like fraud. Data regenerated so ~20% of claimants hold multiple policies. | **178 claimants now hold >1 claim** (was 1 — the plant). Rule 05 still fires on only the 4 ring claims: **zero false positives**. |

**Net:** flags **32 → 22**, ring still 14/14 at max 1.20, and the ring is now the
**top 15 contiguously** with a clean break to 0.55 — the demo reads stronger, not weaker.

**Regeneration was surgical:** the reuse map is drawn from a *dedicated* RNG, so
diffing every regenerated CSV shows **`claimant_id` as the only changed column** —
no other demo figure moved.

## Minor items — all done

| # | Fix |
|---|---|
| M-15 | A **windowed** aggregate is no longer treated as single-row: it returned 159,568 rows against a 500 cap. |
| M-16 | Config seeds users that don't exist and syncs only the display name. A deactivation or demotion now **sticks**, and credentials no longer churn on every boot. |
| m-2 | New `export_button` helper gates all **9** download surfaces on the `export` right (was 2). |
| m-4 | A CI claim segment now carries its own CI exposure, consistent with FR-1A-10's treatment of deaths. |
| m-5 | Driver contributions rounded with a **largest-remainder** pass, so the *returned* payload sums to the returned delta (the maths was always exact; the payload drifted in 12 of 35 combinations). |
| m-9 | NaN materiality now **fails safe** to the full chain (it read as "immaterial"); a lost audit event is logged loudly instead of silently swallowed. |
| m-10 | Readiness scanner widened to `src/assumptions`, `src/fraud`, `src/analysis` — it had only ever scanned `src/governance`. Still 0 violations. |
| m-13 | Traceability accepts a percentage rendering of a ratio (`65.61%` for 0.6561) and the rounding boundary is symmetric. Tolerance scales with the probe, so `999.99%` cannot match `10.0`. |
| m-15 | The boundary returns gate-passing-but-invalid SQL as a rejection instead of raising a raw `duckdb.ParserException`. |
| m-16 | `movement_legs(years=[])` means *no* years; an unknown decrement raises a clear `ValueError`, matching the dimension guard. |
| m-17 | `verify_password` returns False for `None`/bytes instead of raising; `ExposureResult.recon_diff_count` is product-scoped. |
| m-18 | The masked drill-through column is now named `policy_hash`, not `policy_id`. |

## Verified outcomes

| Check | Result |
|---|---|
| m-4 CI claims in a NULL-A/E cell | **0** (was 419 of 589); CI A/E 1.2325 → **1.2237**, the unbiased value |
| m-4 CI rows with no expected basis | **0 of 7,470** |
| m-11 claimants holding >1 claim | **178** (was 1) — with **0** rule-05 false positives |
| M-19 DA claims evaluated | 8, ratio exactly 1.000 |
| m-10 readiness violations | 0 across the widened scan |
| Fraud | 1,808 scored · **22** flagged · max 1.20 · ring top-15 contiguous |

Unchanged as intended: mortality 1,206 / 0.6852 · discontinuance 6,733 / 1.1461 ·
249,881 exposure segments · 16 registry rows / 324 factors · both planted stories.

**Verification:** 1375 passed / 3 skipped; five governance harnesses PASS
(6/6, 7/7, 5/5, 4/4, 8/8); Streamlit boot HTTP 200, 0 tracebacks.

## Deliberately NOT changed

- **m-12** — rule 02 fires on 32% of claims (81% of Term). "Claim > 25× premiums"
  largely restates "this is a term policy", but at weight 0.15 it never flags
  alone, and re-tuning the threshold without a loss-experience basis would be
  guesswork dressed as rigour. Left as an evidence-padding rule; a
  product-relative threshold is the real answer and needs an owner decision.
- **m-6 / m-7 / m-8** — see the outstanding list: each needs a design decision
  (a content hash over the assumption YAML; a person identity distinct from the
  user account; an author on a never-submitted study run) rather than a fix.
