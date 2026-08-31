# Adversarial Review — Experience Study Demo (2026-08-31)

**Scope:** independent fresh-eyes adversarial review of the completed demo refresh
(commit `d6b76e3`, gate 1263 passed / 6 skipped). Commissioned because every prior audit was
performed by the same session lineage and re-read the code for internal consistency. This
campaign instead **recomputed the numbers from the data** and **attempted actual bypasses**.

**Method.** Five independent review agents — actuarial recomputation ×2, AI-guardrail attack,
governance attack, 20-view UI sweep — plus a directly-driven edge/error wave. All LLM work
offline (MockProvider / stub providers, no API keys). Live DB read-only; every write probe on
a copy. **Every finding below was reproduced by me before being recorded**; agent claims I
could not reproduce are excluded, and one agent's 25 reported "crashes" were correctly
self-identified as harness artefacts and are not carried here.

**Integrity of the review.** Live DB logical content unchanged: identical row counts across
all 29 tables, and content fingerprints match the documented figures exactly (1,206 deaths ·
1,760.0014 expected · 5,142 lapses · 32 flagged · 124,066.33 exposure-years). Two independent
agents diffed the DB table-by-table: the *only* delta is `password_hash`/`password_salt` on
the four seeded users — the signature of `seed_users_from_config`, which re-salts on every app
boot (see **M-16**, which is itself the finding). `git status` clean; no source, test, or
config file modified. Final gate: **1263 passed, 6 skipped, 81.6s**.

---

## Headline

Two things are genuinely strong. **The headline demo figures are almost all exactly right** —
independent recomputation reproduced portfolio mortality (0.685227), WL (0.656103), CI
(1.232487), both planted stories, the fraud scan and ring, the quarantine split and the driver
waterfall, all to 4+ decimals. **The AI confidentiality perimeter held against every attack**,
including with the intent router assumed fully compromised — no PII column, Silver/Bronze
table, or file on disk was reachable by any route.

Against that, the review found five demo-breaking defects. The most serious is not an
actuarial one: **the governance layer can be bypassed completely by a user with no sign-off
permission, leaving no audit trace and passing the integrity verifier.** Since governance is
the differentiating claim of this tool, that is the finding to act on first.

The recurring pattern across the rest: **controls that validate shape rather than meaning**,
and **numbers that are internally consistent but actuarially wrong**. All were invisible to the
existing suite because the suite asserts the same conventions the code implements.

---

## BLOCKER

### B-1 — Complete governance bypass: an analyst can publish assumptions live with zero sign-offs

`lineage.approve_and_supersede` **takes no user, performs no RBAC check, and asserts no chain
precondition**:

```
signature: (assumption_set_id, effective_from, effective_to, *, db_path=…) -> None
contains rbac/require? -> False          # record_signoff, by contrast: takes user, calls require
```

`ui/views/29_assumption_lineage.py:181` states the consequence honestly:
`# UI is the only gate (approve_and_supersede has no engine RBAC)`.

**Reproduced end-to-end on a DB copy, acting only as `a.analyst`:**

```
attacker: a.analyst  role=analyst
  has SIGN_OFF permission? -> False
  rbac.require(SIGN_OFF)   -> DENIED (PermissionDenied)   <-- correctly blocked here

analyst drafts child 05e3923e… mortality multiplier 1.0 -> 0.5 (halved)
sign-offs on the child set: 0
approve_and_supersede() called by the analyst -> returned without error

AFTER:
  05e3923e…  APPROVED    approved_by=None     2020-01-01 .. 2099-12-31   <-- ATTACKER'S SET
  04a682c2…  SUPERSEDED  approved_by=c.chief  None .. None

LIVE SET today -> 05e3923e…   (attacker's set? True)
audit events for the attacker's set: 0
verify_chain(signoffs) -> ok=True  rows_checked=3
```

A user denied `SIGN_OFF` one line earlier **halved every mortality assumption, made it the
live basis, demoted the chief-actuary-approved set to SUPERSEDED, and left zero audit
events** — and every detective control still reports green.

Only **1 of 14** state-mutating governance entry points enforces authorisation
(`workflow.record_signoff`). `create_version` and `reopen` accept a `User` but never call
`require()`; the rest cannot check at all because they take no user. This directly contradicts
FR-4-04 / NFR-G-02, which require server-side enforcement *precisely because* UI hiding is not
a control.

Same root cause, also reproduced: a **SUPERSEDED set can be resurrected to APPROVED** — there
is no status precondition either.

**Fix:** add `user: User` to `approve_and_supersede`; `rbac.require(user, Action.SIGN_OFF)`;
assert the chain is complete (`next_required_level(...) is None`) and status ∈
{STAGE3_APPROVED, APPROVED}; emit an `append_event` row so publishing is auditable. Then sweep
the other 13 entry points.

*Partial mitigation:* the compliance pack does not fabricate approval — it prints "No sign-offs
recorded". A careful reader can catch it; the dashboard cannot.

### B-2 — A *failed*, correctly-rejected save silently overwrites a locked APPROVED artifact

`src/assumptions/assumption_set.py:722-723` writes the YAML **before** the lock guard runs:

```python
assumption_set.save_yaml(yaml_path)                        # ← disk write happens FIRST
_insert_assumption_set_metadata(db_path, assumption_set)   # ← LockedStatusTransition raised HERE
```

The guard fires and the caller sees an exception — but the approved artifact's content is
already overwritten on disk. Since `load_assumption_set` reads multipliers from YAML and status
from the DB, the set now reads as **APPROVED with attacker-supplied assumptions**:

```
save raised LockedStatusTransition: …is APPROVED (locked) and cannot be re-saved…
YAML changed on disk despite the raise? True
reloaded first mortality multiplier: 1.0 -> 0.0001      status (DB): APPROVED
```

Relatedly, the guard *permits* an "idempotent" APPROVED re-save without comparing content, and
`_PRESERVED_COLS` omits `approved_by`, `approved_ts`, `superseded_by` — so a permitted re-save
succeeds silently and **erases who approved it and when**.

**Fix:** validate before writing (move the lock check ahead of `save_yaml`, or write to a temp
file and rename only after the metadata write commits); add the three attribution columns to
`_PRESERVED_COLS`; treat an APPROVED re-save with changed content as a `LockedStatusTransition`.

### B-3 — In-force reconciliation triple-counts the UL family; portfolio deaths overstated 50%

`_run_reconciliation` (`src/exposure/engine.py:477`, called `:739`) receives the whole
**family** frame from `_load_silver_policies(con, product_code)` (`:655`) and stamps every row
with the *invoked* `product_code`. The segment writer handles the family correctly (`:719-724`);
recon does not.

```
recon total deaths = 1815      A/E total deaths = 1206      overstatement +609 (+50.5%)

product_code  deaths  max_end_if     true policy count
IUL             302        3152      500      <-- a 500-policy product reporting 3,152 in force
UL              306        3273     2000
ULSG            302        3159     2000
```

Every row's arithmetic still closes (`recon_diff_count = 0` on all 56), so `recon_passes` is
TRUE and nothing alerts — the defect is scope, not arithmetic. This is the **same root cause**
the P8 sweep fixed for `run_dq_checks`; the fix was never carried to the exposure/recon path or
the ETL event builder (M-6).

**Fix:** slice `policies_df` to `product_code == product_code` before passing it to
`_run_reconciliation`, mirroring the P8 DQ slicing.

### B-4 — Pivot and heat map throw a red error on any `premium_jump_ratio_band` dimension

Reproduced at engine level, independent of the UI:

```python
aggregate_ae(row_dims=["attained_age_band"], col_dims=["premium_jump_ratio_band"], …)
# ValueError: Length of values (18) does not match length of index (11)

dimension pairs failing: 19 / 132
```

User-visible on **Mortality A/E** and **Lapse A/E** — both expose the dimension in *both*
dropdowns, so it is two clicks away on the main results pages:
red `Could not load pivot: Length of values (18) does not match length of index (11)`.

Root cause `src/aggregation/aggregator.py:165` — `row_totals` is grouped `dropna=False` over
the full frame (18 age bands) while `pivot_table` drops all-NaN rows (11 remain, because
`premium_jump_ratio_band` is NULL for every non-Term product); `.values` then assigns
positionally with mismatched length.

**Fix (one line):** `…​.reindex(pivot.index)` instead of `.values`; same treatment for the
`mean_measures`/`sum` branches.

### B-5 — Exposure Summary renders 56 unlabelled reconciliation rows, 7 per calendar year

`ui/views/03_exposure_summary.py:52-61` selects no `product_code` and does not group. The
client sees seven identically-labelled rows per year — three of them near-duplicate views of
the same UL family — with nothing indicating which product any row is. Walkthrough beats §2
and §3 send the client to this page. Needed independently of B-3.

---

## MAJOR — actuarial correctness

### M-1 — `expected_surrenders` is a verbatim copy of `expected_lapses`; surrender A/E is invalid

`src/calculation/ae_engine.py:601` and `:612` are the same expression; `:703` sets
`surrender_exposure = lapse_exposure_count`. Identical in **158,548 of 158,548** rows.
Meanwhile `actual_lapses` already *includes* surrenders (`:608`) while `actual_surrenders` is a
strict subset (`:611`).

| Product | `ae_surrender` | Why it is meaningless |
|---|---|---|
| TERM, UL, ULSG, IUL, VUL | **0.0000** | zero surrenders over a full lapse denominator (TERM: 0 / 1991.23) |
| WL | **0.4589** | numerator surrenders only; denominator combined lapse **+** surrender |
| DA_FIXED/DA_FIA/DA_VA | 1.0842 / 1.2318 / 1.1037 | correct only by coincidence — the DA "lapse" benchmark *is* the FRDA surrender curve |

FR-1B-05 is not satisfied. `ae_surrender` is on the AI allowlist and rendered on Product
Comparison (`13_product_comparison.py:248,404`).

### M-2 — Portfolio lapse A/E is contaminated by annuities, and it inverts the demo story

Annuity decrements are `SURRENDER`; the discontinuance rule (`ae_engine.py:605-610`) folds
surrenders into `actual_lapses` **for WL only**, so annuities contribute a full denominator
against a zero numerator (1,422.04 expected, 0 actual).

| Measure | Value |
|---|---|
| As reported (headline) | 5142 / 5874.61 = **0.8753** |
| Excluding annuities | 5142 / 4452.57 = **1.1548** |
| Counting DA surrenders as discontinuances | 6733 / 5874.61 = **1.1461** |

The headline understates lapse experience by ~24% and **contradicts the narrative**: §3.3 sells
a lapse spike while the quick-reference headline says the portfolio is lapsing 12% *below*
expected.

### M-3 — The "WL lapse calibration deviation" is a test arithmetic bug; the band was widened to hide it

`tests/test_acceptance_wl.py:140` computes `SUM(actual_lapses + actual_surrenders)` — but
`actual_lapses` already includes surrenders, so every WL surrender is double-counted.

```
actual_lapses 1469 (= 808 LAPSE + 661 SURRENDER)   actual_surrenders 661
test numerator 2130 → 2130 / 1440.293 = 1.4789
TRUE A/E            → 1469 / 1440.293 = 1.0199
```

**1.0199 sits inside the original spec band [0.90, 1.05].** Yet `DEFERRED_FOLLOWUPS.md`
Remaining-2 records it as an accepted *synthetic-data calibration deviation* and the band was
widened to [0.80, 1.50]. There is no calibration deviation — **a real defect was documented as
an accepted data quirk.**

### M-4 — In-force reconciliation does not validate the exposure file it is presented as validating

`_run_reconciliation` reconciles `policies_df` against itself and **never reads
`gold_exposure_segments`** (no reference in lines 477-600). Demonstrated on a clean schema —
recon returns `all_pass=True` with **zero** exposure segments in the database. FR-1A-14 /
DQ-TL-14 and the demo talking point present it as the control proving the exposure file ties
out. It cannot detect an exposure defect of any kind — which is exactly why B-3 went unnoticed.

### M-5 — CI year-on-year analytics are dead end-to-end, and the page states a falsehood

All 1,020 CI rows have `calendar_year IS NULL`, while `compute_yoy_movement` filters
`calendar_year IS NOT NULL`:

```
MORTALITY      yoy_years=8   trend=worsening
LAPSE          yoy_years=8   trend=worsening
CI_INCIDENCE   yoy_years=0   trend=insufficient_data
```

No CI entry reaches the fact pack, so the AI commentary skill can never mention CI trends. The
page contradicts itself: selecting CI_INCIDENCE shows *"No experience for this decrement in the
selected run"* while the same page's justification expander shows TERM CI A/E = 1.3897. CI is a
headline demo story (589 claims, A/E 1.2325).

### M-6 — `silver_policy_events` triple-writes the UL family: 11,468 phantom rows

```
total 45110   distinct (policy_id, event_type, event_date) 33642   → 11,468 duplicates
IUL 5734 rows / 4500 policies · UL 5734 / 4500 · ULSG 5734 / 4500
```

A/E and fraud are insulated (A/E counts from exposure segments; the fraud runner de-duplicates
— independently confirmed: 1,808 scored = exactly the distinct-event count). But FR-1A-04's
event timeline is wrong on its face and any future consumer inherits it.

### M-7 — `anti_selection_flag` is FALSE in all 159,568 rows; FR-1B-10 is non-functional

Computed in the engine, never included in the insert. **809 cells qualify** (`ae_lapse > 1.5`
on UL/ULSG/IUL: IUL 134 · UL 447 · ULSG 228). `DEFERRED_FOLLOWUPS` FU-7#3 calls this
"harmless"; with 809 qualifying cells the flag is materially wrong, not merely absent — and it
is on the AI allowlist, so the analyst can read a false negative.

### M-8 — `classify_trends` issues confident badges on zero experience

```
DA_FIA/DA_FIXED/DA_VA  LAPSE      "stable"     A/E [0,0,0]   actual claims [0,0,0]
DA_VA                  MORTALITY  "improving"  slope -0.0746  based on 2 claims
```

"DA lapse trend: stable" for products with **literally zero lapses ever**.
`ui/skills_logic.py:396` drops only `insufficient_data`, so these reach the fact pack and are
narrated by the commentary skill as fact. An actuary will catch this immediately.
**Fix:** gate on a minimum aggregate claim count / credibility Z.

---

## MAJOR — governance & guardrail integrity

### M-9 — "Verify integrity" shows a green tick on a log with no integrity protection

`gold_workflow_iterations` is listed in `_VERIFIABLE_CHAINS` and the UI's `_VERIFIABLE_LOGS`,
but its writer never hashes:

```
log_workflow_iteration writes entry_hash? -> False
gold_workflow_iterations: 2 rows, 0 hashed
verify_chain -> ok=True  rows_checked=0     → UI renders green "intact ✓" after checking ZERO rows
```

`verify_chain` filters `WHERE entry_hash IS NOT NULL`, so it checks nothing and passes. An
agent demonstrated deleting every real row and inserting a forgery attributed to **C. Chief** —
the forged event appears in the unified audit stream and integrity still reports intact.
Walkthrough beat §8.2 clicks this button live. A green tick on an unprotected log is worse than
no check.

### M-10 — Fraud scan is protected by a disabled button only; no server-side RBAC

`ui/views/17_fraud_monitor.py:46,50` disables the button, then calls `run_fraud_scan(...)` with
no `require()`. `src/fraud/runner.py` imports no `rbac` and takes no `User`, yet writes three
Gold tables. Every comparable page re-checks server-side (`20:289`, `21:401`, `21:506`,
`28:87`); 17 is the only PROPOSE-gated page that does not. Same class as B-1, contradicts
NFR-G-02.

### M-11 — Materiality is evadable by splitting a change across versions

Materiality is measured per-version against the immediately prior approved version, never
cumulatively. Ten legitimate 0.04 steps — each below the 0.05 threshold, each with a complete
valid chain — moved a multiplier 1.0 → 1.4, **8× the threshold, with no chief-actuary review
at any point**. Nothing was bypassed; the rule itself is evadable. A governance-design
decision, not a code bug.
**Fix:** also measure against the currently-live/root approved basis and require the higher
level, or add a cumulative-drift-since-chief-approval check.

### M-12 — Numeric traceability is set-membership, not claim-verification

`verify_traceability` asks "does this number appear *somewhere* in the supporting data?", never
"is this the value of the thing the sentence claims". The commentary fact pack is 62 KB / 901
distinct values and A/E ratios cluster in [0,2] at 2 dp, so a fabricated ratio almost always
collides:

```
random 2-dp value in [0,2] : 88.0% pass      random integer 1-99 : 56.0%      random 4-dp : 2.8%
```

Confirmed end-to-end on the **hard-block** surface with the real skill and the real live fact
pack — all three figures below are wrong, none blocked:

> *"Portfolio mortality A/E deteriorated by −0.68 year on year… Term lapse A/E is 1.37 for
> 2023, and the lapse A/E stands at 0.88%."*

This exports as an `AI-DRAFT` .md the actuary is told has been numerically verified. On a small
single-query result set the same check is strong (4 dp blocks 97%+); the weakness is specific
to the four generate-then-verify skills — memo, management commentary, fraud narrative, chat
commentary — i.e. the client-facing deliverables. **Structural, not a patch.**

### M-13 — Unicode minus (U+2212) passes the check and inverts a figure's sign

`_NUMBER_RE`'s sign class is ASCII `[-+]`; a well-typeset LLM writes U+2212:

```
_NUMBER_RE on '−0.05'  →  tokens=['0.05']       # sign silently dropped
"The A/E moved by −0.6561 this year."  →  RENDERS, untraceable=[]
```

The checker validates `+0.6561`; the reader sees `−0.6561`. In an experience study that turns
deterioration into improvement. Survives both `handle_turn` and the skills.

### M-14 — The round-6 credibility backstop is evaded by a CTE alias (regression)

`aggregates_per_cell_stat` matches column *names* inside an aggregate; a projection alias
defeats it. **Independently reproduced:**

```
backstop blocks direct AVG(credibility_z_lapse) -> True
backstop blocks CTE-aliased AVG(z)              -> False
CTE form passes SQL gates                       -> PASS
CTE form returns                                -> 0.0010626985
TRUE aggregate Z (LF, n=455)                    -> 0.6485
```

A **610× understatement of statistical credibility** — and *traceable* (it came from the data),
so M-12's check will not catch it either. Exactly the defect the backstop was built for after
round 6, resurrected by a one-line rename. Existing coverage
(`tests/test_chatbot_aggregation.py:386`) tests only the direct-column form.

### M-15 — Gate 4 (row cap) bypassed by a bare window aggregate

`_is_fully_aggregated` sees an aggregate and no bare column, so it declares the statement
single-row — but a windowed aggregate returns one row *per input row*:

```
SELECT SUM(ae_count) OVER () FROM gold_ae_results     -- no LIMIT
  boundary: PASS      rows through MCP: 159568  (cap = 500)
  chained into {{table:}} → rendered answer: 2,393,533 chars
```

Availability/cost rather than confidentiality. `AVG(x) OVER (PARTITION BY …)`, `ROW_NUMBER()`
and `QUALIFY` are all correctly rejected — only this shape slips.

### M-16 — `ui/app.py` writes to the database at boot, before authentication

`ui/app.py:20-40` runs `_ensure_governance_users()` → `seed_users_from_config()` *above*
`login_gate()`, so an unauthenticated request triggers it. Every server start re-derives all
four salts and hashes (this is the sole cause of the DB delta observed during this review).
Consequences: the app cannot run against a read-only DB, credentials-at-rest churn on every
restart, and **`active=False` does not survive a restart** — every seed branch hardcodes
`active=True` and re-converges `role` to config, so a deactivated or demoted user is silently
reinstated.

---

## MAJOR — fraud module vs the demo narrative

The scan reproduces bit-exactly (all six rule hit counts, all 1,808 composite scores to 1e-9,
32 flagged, max 1.20 — see *Verified*). These are defects of **discrimination**, not arithmetic.

### M-17 — Rule 04 produces 9 of the 32 headline flags from one innocent office

Office first-policy-year claim median is 3.0 → threshold `max(3×3, 5)` = 9.0. Two offices
cross it: OFF-013 (18, the ring) and **OFF-026 (9 — exactly on the threshold)**.

```
flagged with rule04: 32     without rule04: 23     flags existing ONLY via rule 04: 9

OFF-013:  56 claims  29 hospitals  7 regions  53 claimants    <-- ring (3 repeat claimants)
OFF-026:  56 claims  37 hospitals  6 regions  56 claimants    <-- fully organic, no clustering
```

OFF-026 contributes **7 of the 32 flagged claims** and will appear directly beneath OFF-013 in
the demo's concentration table (§5.3) with nothing distinguishing it. One fewer early claim and
it vanishes entirely.

### M-18 — "Ghost hospital detection" is a hardcoded list with zero marginal detection power

Rule 06 is pure `isin` membership against `config/fraud_config.yaml`
(`hospitals: [HOSP-066]`, `regions: [SOUTHWEST]`) — **not** a roster check.

```
rule06 hits = 25:  via HOSP-066 = 14   via SOUTHWEST = 25   HOSP-066 outside SOUTHWEST = 0
```

Every HOSP-066 claim is already SOUTHWEST, so deleting the hospital clause changes nothing. A
genuinely unknown facility outside the 60-hospital roster but absent from the YAML would **not**
be caught — yet `demo_walkthrough.md:70` sells exactly that detection. The surviving clause is
geography: 3 non-ring claims are flagged *solely* for being in the rarest region.

### M-19 — Rule 02 is structurally blind to the entire annuity family

`src/fraud/rules.py:77` hardcodes `premium = 0.0` for DA (correct — `silver_annuity_contracts`
has no `annual_premium`), but the arithmetic yields NaN and `NaN > 25` is silently `False`.
**0 of 8 DA claims fire rule 02**; all 8 score 0.0 on every rule. Impact today is bounded
(0 of 32 flags change) — the defect is that a product family is silently scored against 5 rules
while reported as scored against 6.

---

## MINOR

| # | Finding |
|---|---|
| **m-1** | **4 tests skip on wrong reference-table filenames.** `tests/test_ae_engine_1c.py:270,282` want `mortality_iar2012.parquet` / `mortality_vbt2015.parquet`; the real files are `mortality_2012iar.parquet` / `mortality_2015vbt.parquet`. All four assertions **pass** once corrected — including `test_tables_are_different`, the only check that FR-1C-12 (annuity mortality on 2012 IAR, not 2015 VBT) is honoured. Two-line fix restoring real coverage. |
| **m-2** | **`export` permission enforced on only 2 of 9 download surfaces.** `analyst` has no `export` right and is correctly denied the compliance pack (27) — yet can download claim-level flagged-fraud data (`17:166`, incl. office/hospital/region), both actuary reports (07), the AI memo (22) and the factors CSV (15). Either the permission means something everywhere, or the 27/28 gates are theatre. |
| **m-3** | **Negative confidence lower bounds are stored.** `ui/stats_helpers.poisson_ci` floors at 0; the engine's `_add_stat_columns` does not. `ci_lower_count` < 0 in 1,199 rows · `ci_lower_lapse` 5,018 · `ci_lower_ci` 390 · `ci_lower_amount` 1,199 (sample: `n=1 → lo = −129.59`). On the AI allowlist and in CSV exports. SE arithmetic itself is exact. |
| **m-4** | **CI claim segments receive zero CI exposure** (`engine.py:269`). All 589 CI_CLAIM segments carry `ci_rider_in_force_flag=False`, so the claim counts in the numerator while its 181.02 exposure-years are excluded — inconsistent with the Balducci treatment of deaths (FR-1A-10). Aggregate bias small (1.2325 → 1.2237); display effect is not — 419 of 589 claims (71%) land in cells with `expected_ci_claims = 0`, so `ae_ci` is NULL there. |
| **m-5** | **The "contributions sum exactly" claim is false for the returned values.** The maths is exact unrounded (`\|diff\| = 0.000e+00`), but `attribute_drivers` returns `round(…, 6)` per segment, so the payload drifts up to n×5e-7 — exceeding the documented 1e-9 in **12 of 35** combos. The test asserts 1e-9 on two combos that happen to land clean. Docstring, progress doc and the page caption all overstate it. |
| **m-6** | **No integrity binding between the DB row and the YAML.** A direct on-disk edit of an APPROVED set's YAML is wholly undetected; `reproducibility_stamp` covers run/model/data-snapshot only. |
| **m-7** | **Segregation keys on `user_id`, not person.** A second account for the same human can sign off their own work. Exploitability low (no self-service account creation), but it is a stated *absolute* rule with a structural gap. |
| **m-8** | **A never-submitted study run has no author**, so proposer≠approver is a no-op and a run can reach "fit for assumption-setting" with no submission event. |
| **m-9** | **NaN materiality fails open** — `abs(nan) > 0.05` is False → treated as immaterial (senior suffices). `_emit_study_run_event` also swallows all exceptions, so an audit event can be silently lost while the sign-off commits. |
| **m-10** | **Readiness scanner scope is stale.** It scans only `src/governance/*.py` + `db_init.py`. The refresh moved the assumption lifecycle to `src/assumptions/` and added `src/fraud/`, `src/analysis/` — none are scanned. `setattr(o,'tenant_id',…)` also evades while `getattr` is caught. |
| **m-11** | **Rule 05 can only ever fire on the planted ring.** `claimant_id` is a deterministic function of `policy_id` for every organic policy, so a repeat claimant is structurally impossible outside the seed. Zero false positives *and* zero false negatives — but it is a plant-detector, not a fraud detector. |
| **m-12** | **Rule 02 fires on 32% of all claims** (581/1,808; 81% of Term). "Claim > 25× premiums paid" largely restates "this is a term policy". Never flags alone at weight 0.15, but pads every flagged claim's evidence. |
| **m-13** | Traceability: `%` suffix passes unchecked (data `0.6561` → `"0.6561%"` renders, a 100× misstatement); rounding-boundary asymmetry false-blocks `0.6851`/`0.6850` against data `0.68515` (fail-closed). |
| **m-14** | **Demo's own planted story renders as a spec violation.** `06_ci_explorer` shows on every render: `⚠ CI A/E 1.232 is outside specification range 0.9–1.1` — presenting the headline narrative as an out-of-spec warning. Also `"Re-run the study after updating the A/E engine"` is developer copy shown to clients. |
| **m-15** | `execute_safe_select` raises a raw `duckdb.ParserException` for gate-passing but DuckDB-invalid SQL — contained inside the MCP server's `except`, but violates the boundary's "never raises on bad user SQL" contract for other callers. |
| **m-16** | `movement_legs` labels annuities `DA` while every other commentary surface splits `DA_FIXED/DA_FIA/DA_VA`; any join silently drops the annuity book. Unknown decrement raises a bare `KeyError` where the *dimension* guard correctly raises `ValueError`. `movement_legs(years=[])` silently ignores the filter. |
| **m-17** | `verify_password` raises instead of returning `False` for `None`/`bytes` input (the `try` wraps only `hash_password`). Not reachable via Streamlit. `ExposureResult.recon_diff_count` sums across all products, not the invoked one — currently harmless (correct by accident). |
| **m-18** | Drill-through column labelled `policy_id` actually contains a 12-char SHA-256 prefix. Masking **works**; the header should read `policy_hash` — it is the column a client will scrutinise. |

---

## OBSERVATION

- **OBS-1 — `DEFERRED_FOLLOWUPS.md` is stale post-P3.** Remaining-1 and the test-hygiene note
  name `test_tev_engine`, `test_envelope` and `scripts/_uat_tev_baseline.py`, **all deleted in
  P3**. The two surviving named tests are now properly isolated (verified:
  `test_assumption_set.py:69` mirrors to a temp copy; `test_workflow.py:31` uses a fresh temp
  DB). The warning would send a maintainer on an unnecessary rebuild.
- **OBS-2 — Remaining-3 (RPU/ETT) is already answered.** The generator produces **zero**
  RPU/ETT policies — all 7,000 WL are `ACTIVE`. `tests/test_exposure_wl_ul.py:271` records that
  non-forfeiture simulation was deliberately removed on 2026-05-21. Close the item. (Its note
  that "base lapse now represents lapse+surrender only" is the context making M-1/M-3 legible.)
- **OBS-3 — Three demo-relevant tables are empty.** `gold_ai_audit_log` (0),
  `gold_ae_governance_events` (0), `gold_ai_eval_results` (0). Walkthrough §6.2 says "show the
  AI Activity Log" — on the shipped DB that table is **empty**, and without an API key nothing
  will populate it during the demo.
- **OBS-4 — Step 3 sign-off is unexercisable on the shipped DB.** The only seeded set is already
  APPROVED, so page 22 offers just the memo button — the chain, attestation and RETURN path
  cannot be demoed. Consider seeding a second set left at `STAGE3_APPROVED`.
- **OBS-5 — The APPROVED set is never live** (`effective_from`/`effective_to` both NULL), so
  `resolve_live_set` returns `None` and the dashboard shows no live set until the presenter
  publishes one in beat §7.5.
- **OBS-6 — The hash chain is unkeyed**, so an attacker with DB write access and the source can
  re-chain after an edit and `verify_chain` will pass. This is inherent to a keyless SHA-256
  chain. **I checked the docs: they consistently say "tamper-evident", never "tamper-proof" —
  no overstatement found.** Worth one explicit UI caveat if the audience may read "intact ✓" as
  cryptographic proof.
- **OBS-7 — ≤1 APPROVED-current per lineage is enforced only on the publish path**, not as an
  invariant; NULL-range members are skipped by the overlap check.
- **OBS-8 — `agent_id` is a pseudonymous identifier for a natural person** and is arguably
  personal data. Correctly absent from every LLM-reachable surface — flagged so the
  classification is deliberate rather than incidental.
- **OBS-9 — The AI Analyst page hard-codes Analyst mode ON** (`value=True`), so the numeric
  check is already flag-not-block there. M-12/M-13 therefore bite hardest on the **skills**,
  which block unconditionally and export as signed deliverables.
- **OBS-10 — Other:** 14 policies contribute no exposure (issue = termination date; correct per
  the `exposure_years > 0` constraint). Recon labels annuities `DA` while A/E splits them three
  ways, so no join key exists. Extreme single-claim cells reach A/E 134.99 (Z = 0.0304 flags
  them, but they dominate an unfiltered axis). `workflow_session_id` is fabricated when Step 3
  is opened directly.
- **OBS-11 — Coverage gaps worth closing cheaply:** `ui/app.py` has never been AppTested but
  **is** testable — ~15 lines would lock the 5-group/20-page registry. `ui.config.DB_PATH`
  redirects cleanly, so empty-DB page tests are cheap; that whole dimension is uncovered.

---

## Verified correct (independent recomputation)

Recomputed with independent SQL against `gold_*`/`silver_*`, not by reading `src/`.
Ratio-of-sums throughout; mortality/lapse scoped `illness_code IS NULL`. Run `d5f56adb…`,
`credibility_method = LF`, threshold 1082.

| Claim | Computed | ✓ |
|---|---|---|
| 25,000 policies; per-product split | exact (DA_FIXED 1541 + DA_FIA 659 = 2200; DA_VA 1300) | ✅ |
| 1,206 deaths · A/E 0.6852 | 1206 / 1760.001378 = **0.685227** | ✅ |
| WL 0.6561 = 611 / 931.26 | 611 / **931.255921** = **0.656103** | ✅ |
| 589 CI · 10 codes · 1.2325 | 589 / 477.895536 = **1.232487** | ✅ |
| Mortality story 0.603→0.612→0.754→0.880 | **0.603143 · 0.612265 · 0.754078 · 0.880093** (+0.2770) | ✅ |
| Lapse story 2022 1.194 · 2023 1.881 | **1.193988 · 1.880550** (2016–21 mean 1.003) | ✅ |
| DQ quarantine UL 41 · ULSG 128 · IUL 7 | 41 (97.95%) · 128 (93.60%) · 7 (98.60%); total 25,000 | ✅ |
| **Quarantine union — each policy once** | 176 rows, 176 distinct policies, 0 under >1 label — **P8 fix holds** | ✅ |
| 249,881 exposure segments | exact; 0 outside (0, 1.0001]; 124,066.33 policy-years | ✅ |
| Exposure accounting | 182 UL-family policies without segments = 176 quarantined + 6 zero-day; **no unexplained drops** | ✅ |
| Per-cell credibility Z | 476,664 rows recomputed, **max abs error 0.0** | ✅ |
| SE / CI arithmetic | reproduces with **max abs error 0.0**; correctly NULL where claims = 0 | ✅ |
| inf / NaN / negative expected | 0 / 0 / 0; 0 divide-by-zero A/E | ✅ |
| IUL lapse non-zero (FU-3) | 136 / 103.061568 = **1.3196** — fixed | ✅ |
| Fraud: all 6 rule hit counts | 142 / 581 / 154 / 27 / 4 / 25 — all exact | ✅ |
| Fraud: 1,808 composite scores | **max abs diff 0.000e+00**; 32 flagged; max 1.20 | ✅ |
| Fraud claims universe | rebuilt from scratch: 2,606 events → 1,808 distinct — exact | ✅ |
| Ring entities | OFF-013 · HOSP-066 · CLM-424242 ×4 (within ±10%) · SOUTHWEST — all rank #1 | ✅ |
| Commentary slopes | mortality **+0.0848** · lapse **+0.21**, both worsening | ✅ |
| 2023 driver waterfall +0.0722 | 18 segments, **\|diff\| = 0.000e+00** unrounded | ✅ |
| Fact-pack PII bright line | regex sweep of 62,634-byte JSON for policy/`CLM-`/`HOSP-`/`OFF-`/`AGT-` — **0 matches** | ✅ |
| Commentary robustness | 4 decrements × 9 products × 8 years × all dims — **0 exceptions** | ✅ |
| **UI render matrix** | **20 views × 5 auth states = 100/100 clean**; unauth leaks nothing (0 dataframes/metrics) | ✅ |
| **Fresh empty DB** | **zero crashes on all 20 pages**; 17 show actionable empty states, 3 correct zero renders | ✅ |
| Degenerate filter combinations | 47 trials, 0 crashes; zero-row selections render proper empty states | ✅ |
| Empty-DB engine behaviour | fraud scan, YoY, trends, movement_legs, fact pack degrade cleanly; `assemble_fraud_facts` fails loudly with a clear `ValueError` | ✅ |
| 16 registry rows · 332 proposed factors | 8 GLM + 8 GBM; 332 | ✅ |

### Attacks correctly repelled

**AI confidentiality perimeter — held completely, including with the router assumed fully
compromised.** PII, write and exfiltration prompts forced to `FACTUAL_LOOKUP` were *every one*
stopped at the gates.

- **Filesystem via table functions** — `read_csv_auto('/etc/passwd')`, `read_parquet`,
  `read_text`, `read_json_auto`, `glob`, `sniff_csv`, bare-path `FROM`, `generate_series` —
  **all REJECT_ALLOWLIST**. The highest-value angle is closed.
- Schema/catalog reads; off-allowlist reads and joins (`gold_dq_quarantine`,
  `gold_exposure_segments`, `gold_fraud_scores`, `gold_users`, `silver_*`) — rejected.
- Derived-table alias smuggling; CTE over an off-allowlist table; quoted / schema-qualified /
  mixed-case identifiers — rejected.
- Statement stacking and comment injection — REJECT_PARSE. Non-SELECT (CTAS, PRAGMA, ATTACH,
  `COPY … TO`, INSTALL, LOAD, CALL, SET) — rejected.
- Row-cap evasion (`LIMIT 999999`, computed/subquery LIMIT, unlimited GROUP BY, `QUALIFY`,
  partitioned windows, `ROW_NUMBER()`) — rejected **except** the M-15 shape.
- `SELECT *` expands to the allowlisted subset. MCP per-tool scoping holds; tool count **4**,
  `TOOL_SCHEMA_VERSION` **"3.0"**. Cross-table UNION caught twice downstream.
- **Run-id digit leakage** (the round-4 fix) holds in **all four** skills, including the two
  added later. Slot-grammar abuse → `SlotFillError` in every case.

**Governance — the controls that exist are solid.** `record_signoff` denied to `analyst`;
author signing own artifact blocked at all three levels; same user signing two levels blocked;
out-of-order sign-off blocked; wrong role blocked; double-approve blocked.

- **Hash chain**: modified column / broken `prev_hash` / deleted middle row / re-ordered `seq` /
  forged comment — **all detected** with correct `first_divergence_seq`.
- **Concurrency**: 2/4/8 parallel processes *and* threads appending — **no duplicate `seq`, no
  broken chain, no false tamper alarm**; losing writers fail loudly. Behaves exactly as
  documented.
- Timestamps (naive UTC, tz-aware, +08:00, microseconds, float drift) all recompute identically.
- Inverted and overlapping effective ranges rejected; added/removed cells measured correctly vs
  neutral 1.0; lineage root with no ancestor → full chain.
- `current_user()` not spoofable via env vars or module globals. Passwords: salted PBKDF2-SHA256,
  200k iterations, distinct per-user salts, no plaintext. RBAC matrix consistent across all 4
  roles × 4 actions; **missing config fails closed**.
- Readiness scanner: all previously-fixed evasions (import alias, `getattr`, dict key,
  `importlib`, aliased SSO, unparseable file) still caught.

**UI**: `require_auth()` gate solid on all 20 views (warning + `st.stop()` before any query);
page 29's complementary propose/sign-off gating is exemplary.

---

## Recommended triage

**Fix before the client demo**
- **B-1** governance bypass — the tool's differentiating claim currently does not hold.
- **B-3 / B-5** in-force reconciliation (wrong numbers *and* an unreadable table on a
  walkthrough page).
- **B-4** pivot crash — a red error two clicks away on the main results pages (one-line fix).
- **M-5** CI page states a falsehood on a headline story; **M-8** zero-experience trend badges;
  **M-9** green "intact ✓" on an unprotected log (beat §8.2 clicks it); **m-14** the planted CI
  story rendering as a spec violation.
- **OBS-3 / OBS-4** seed an AI-audit row and a `STAGE3_APPROVED` set, or adjust beats §6.2/§7.

**Fix before the numbers are relied on**
B-2 (locked-artifact overwrite) · M-1, M-2, M-3, M-6, M-7 (actuarial correctness) · M-10
(fraud RBAC) · M-14, M-15 (guardrail regressions with runnable exploits) · M-16 (boot-time
credential churn / `active=False` reverting) · m-1 (two-line fix restoring FR-1C-12 coverage).

**Design decisions for the owner, not straight bugs**
M-12 (traceability is structurally set-membership — needs claim-binding, not a patch) · M-11
(materiality splitting) · M-17, M-18, M-19, m-11 (fraud rules weaker than the narrative — either
strengthen the rules or soften the talking points) · M-4 (what should reconciliation actually
reconcile?) · m-2 (does `export` mean anything?).

**Documentation**
OBS-1, OBS-2 (close two stale deferred items) · m-5 (soften the "exact-sum" claim) · OBS-6
(one-line tamper-evident caveat) · update the walkthrough lapse headline once M-2 is decided.

---

*Review performed offline; no API keys used; no source or test file modified; live database
logical content unchanged; gate re-verified at 1263 passed / 6 skipped.*
