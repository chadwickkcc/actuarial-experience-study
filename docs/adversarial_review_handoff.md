# Adversarial Review — Handoff & Fix Plan

**Paused:** 2026-08-31, after the review phase, before any code change.
**Companion:** `docs/adversarial_review_2026-08-31.md` (the findings report — read that first).
**Repo state at pause:** commit `d6b76e3`, working tree clean except two **untracked** new docs
(this file and the report). Gate green: **1263 passed, 6 skipped, 81.6s**.

---

## 1. Status

The adversarial review is **complete**. Five independent review agents (actuarial
recomputation ×2, AI-guardrail attack, governance attack, 20-view UI sweep) plus a
directly-driven edge/error wave produced **53 findings**: 5 BLOCKER, 19 MAJOR, 18 MINOR,
11 OBSERVATION. Every finding was reproduced before being recorded.

**No source, test, or config file has been modified.** Live DB logical content verified
unchanged (1,206 deaths · 5,142 lapses · 32 flagged / max 1.20 · 1 APPROVED set by `c.chief` ·
3 sign-offs · 4 users).

One review item is **outstanding**: the real-browser walk of `demo_walkthrough.md` (Wave C2).
It needs the owner to type the demo password at the login gate; Claude drives every step after
that keystroke. The headless AppTest sweep (Wave C1) *was* completed — 100/100 renders clean —
so C2 adds visual/layout confirmation, not correctness coverage.

---

## 2. Owner decisions on record

| Date | Decision |
|---|---|
| 2026-08-31 | Review scope: **all angles** (actuarial numbers, AI guardrails, governance bypass, UI/demo flow, edge states) |
| 2026-08-31 | LLM testing: **offline only** — no API keys, MockProvider / stub providers, zero spend |
| 2026-08-31 | Fix policy: **report first**, owner triages, then fix approved items each with a regression test |
| 2026-08-31 | UI method: **both** headless AppTest and real browser |
| 2026-08-31 | **Approved fix batch: B-1 … B-5 plus the four demo-facing items** (enumerated in §3) |
| 2026-08-31 | **Pause before executing fixes**; resume on owner's return |

---

## 3. Approved fix batch — 9 items

Each fix: minimal diff + one regression test; gate kept green; no live-DB mutation without
explicit authorisation.

### B-1 — Governance bypass (highest priority)
`src/governance/lineage.py::approve_and_supersede` takes no user, performs no RBAC check, and
asserts no chain precondition.
**Fix:** add `user: User`; call `rbac.require(user, Action.SIGN_OFF)`; assert the chain is
complete (`next_required_level(...) is None`) and status ∈ {STAGE3_APPROVED, APPROVED}; emit an
`append_event` row so publishing is auditable. Update the one call site
(`ui/views/29_assumption_lineage.py:181`) and delete the "UI is the only gate" comment.
**Regression test:** the reproduction in Appendix A must raise `PermissionDenied`.

### B-2 — Failed save overwrites a locked APPROVED artifact
`src/assumptions/assumption_set.py:722-723` writes YAML before the lock guard runs.
**Fix:** validate before writing — move the lock check ahead of `save_yaml`, or write to a temp
file and rename only after the metadata write commits. Also add `approved_by`, `approved_ts`,
`superseded_by` to `_PRESERVED_COLS`, and treat an APPROVED re-save with changed content as
`LockedStatusTransition`.

### B-3 — Reconciliation triple-counts the UL family
`src/exposure/engine.py:739` passes the whole family frame to `_run_reconciliation`.
**Fix:** slice `policies_df` to the invoked `product_code` first, mirroring the P8 DQ slicing.
**Expected after fix:** recon total deaths 1815 → **1206**; IUL `end_if` ≈ 500-scale, not 3,152.
Note this changes `gold_inforce_reconciliation`, so the live DB needs a rebuild (§6).

### B-4 — Pivot/heat-map crash on `premium_jump_ratio_band`
`src/aggregation/aggregator.py:165` assigns positionally with `.values` against a
`dropna=False` grouping.
**Fix (one line):** `.reindex(pivot.index)` instead of `.values`; apply the same treatment to
the `mean_measures`/`sum` branches if they use `.values`.
**Expected after fix:** 19/132 failing dimension pairs → 0.

### B-5 — Exposure Summary renders 56 unlabelled recon rows
`ui/views/03_exposure_summary.py:52-61` selects no `product_code` and does not group.
**Fix:** add `product_code` to the SELECT and the displayed columns (or pivot by product).

### M-5 — CI page states a falsehood on a headline story
All 1,020 CI rows have `calendar_year IS NULL`; `compute_yoy_movement` filters it out, so the
page shows *"No experience for this decrement"* while the same page shows TERM CI A/E 1.3897.
**Fix:** populate `calendar_year` on CI rows in the A/E engine (preferred — restores CI YoY,
trends and driver waterfall), or aggregate CI over a CI-appropriate time key. Until then the
page must not claim "no experience".

### M-8 — Zero-experience trend badges
`classify_trends` reports "DA lapse: stable" for products with literally zero lapses ever, and
these reach the AI fact pack as fact.
**Fix:** gate on a minimum aggregate actual-claim count (or credibility Z) and return
`insufficient_data` below it.

### M-9 — Green "intact ✓" on a log with no integrity protection
`gold_workflow_iterations` is in `_VERIFIABLE_CHAINS` but `log_workflow_iteration` never
hashes, so `verify_chain` checks **zero rows** and returns `ok=True`. Walkthrough §8.2 clicks
this button live.
**Fix:** route `log_workflow_iteration` through `append_event`, **or** remove the table from
the verifiable set and label it "not hash-protected" in the UI. Prefer the former.

### m-14 — The planted CI story renders as a spec violation
`ui/views/06_ci_explorer.py` shows `⚠ CI A/E 1.232 is outside specification range 0.9–1.1` on
every render — the demo's own headline narrative presented as an error. Same page shows
developer copy: *"Re-run the study after updating the A/E engine."*
**Fix:** widen the band or reword to "above the expected basis — see Management Commentary";
replace the developer copy with "This product has no CI rider cover."

---

## 4. Does this batch fix every defect? — **No.**

**It closes 9 of 53 findings.** Remaining: 44 (14 MAJOR, 18 MINOR, 11 OBSERVATION, plus M-2
which is arguably demo-facing). The batch is well-chosen for *demo readiness* — it fixes
everything that visibly breaks or misleads during the walkthrough. It does **not** make the
numbers correct or the governance perimeter whole.

### 4.1 The one gap I would argue back into the batch

**M-10 — the fraud scan has no server-side RBAC.** This is the *same defect class as B-1*:
`ui/views/17_fraud_monitor.py:46,50` disables the button, then calls `run_fraud_scan(...)` with
no `require()`; `src/fraud/runner.py` imports no `rbac`, takes no `User`, and writes three Gold
tables. Every comparable page re-checks server-side (`20:289`, `21:401`, `21:506`, `28:87`).

If B-1 is fixed and M-10 is not, the headline governance claim is only half-closed — a
demonstrated bypass on one page and an identical unguarded path on another. It is a
~5-line fix. **Recommend adding it to the batch.**

### 4.2 Cheap, high-value, not in the batch

| # | Finding | Effort |
|---|---|---|
| **M-13** | Unicode minus (U+2212) passes traceability and inverts a figure's sign | regex change |
| **M-14** | CTE-alias evasion of the credibility backstop (round-6 regression; 610× understatement) | small |
| **m-1** | 4 tests skip on wrong reference-table filenames — restores the only FR-1C-12 assertion | 2 lines |
| **m-3** | Negative confidence lower bounds stored (1,199 / 5,018 / 390 / 1,199 rows) | 1 line (`np.maximum(0, …)`) |
| **M-7** | `anti_selection_flag` FALSE in all 159,568 rows; 809 cells qualify | small (insert column list) |

### 4.3 Actuarial correctness — deferred, needs a decision

- **M-1** `expected_surrenders` is a verbatim copy of `expected_lapses` in all 158,548 rows;
  surrender A/E reads **0.0000** for five life products. FR-1B-05 unmet. Rendered on Product
  Comparison and on the AI allowlist.
- **M-2** Portfolio lapse A/E (0.8753) is contaminated by annuity denominators; ex-annuity
  **1.1548**. As published it *contradicts* the lapse-spike story the demo tells.
  **This is a walkthrough figure** — arguably belongs with the demo-facing group.
- **M-3** The documented "WL calibration deviation" is a test double-count; true A/E 1.0199 is
  inside the original band. Fix the test, restore the band, close `DEFERRED_FOLLOWUPS`
  Remaining-2.
- **M-4** Reconciliation never reads `gold_exposure_segments` — it cannot detect an exposure
  defect, which is why B-3 went unnoticed. Needs a design decision on what it should reconcile.
- **M-6** `silver_policy_events` triple-writes the UL family (11,468 phantom rows). Same root
  cause as B-3; A/E and fraud are insulated today.

### 4.4 Design decisions for the owner — not straight bugs

- **M-12** Numeric traceability is set-membership, not claim-verification (88% of fabricated
  2-dp ratios pass). Structural — needs claim-binding, not a patch.
- **M-11** Materiality is evadable by splitting a change across versions (10 × 0.04 = 0.40, no
  chief review).
- **M-17 / M-18 / M-19 / m-11** Fraud rules are weaker than the walkthrough claims: "ghost
  hospital detection" is a hardcoded list with zero marginal power; 9 of 32 flags come from one
  innocent office; rule 02 blind to annuities; rule 05 can only fire on the planted ring.
  Either strengthen the rules or soften the talking points.
- **M-15** Window-aggregate row-cap bypass · **M-16** boot-time credential churn and
  `active=False` reverting · **m-2** `export` enforced on only 2 of 9 download surfaces.

### 4.5 Demo-staging items (no code)

- **OBS-3** `gold_ai_audit_log` is empty — walkthrough §6.2 "show the AI Activity Log" shows an
  empty table, and without an API key nothing populates it live.
- **OBS-4** The only assumption set is already APPROVED, so Step 3's sign-off chain,
  attestation and RETURN path cannot be demonstrated. Seed a second set at `STAGE3_APPROVED`.
- **OBS-5** That set has `effective_from`/`effective_to` NULL, so no live set resolves until
  the presenter publishes one in §7.5.

---

## 5. Resume protocol

1. Read `docs/adversarial_review_2026-08-31.md` (findings) then this file (§3 batch).
2. Confirm the baseline still holds:
   ```bash
   unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -q
   ```
   Expect **1263 passed, 6 skipped**.
3. Confirm the owner's answer on §4.1 (add M-10?) and §4.2 (add the cheap wins?).
4. Fix in this order — governance first, then data, then UI:
   **B-1 → (M-10) → B-2 → B-3 → B-4 → B-5 → M-9 → M-5 → M-8 → m-14**
   B-1/B-2/B-3 carry the most risk; B-4/B-5/m-14 are near-trivial.
5. Each fix: write the regression test first (it should fail), apply the minimal diff, confirm
   the test passes and the full gate stays green.
6. **B-3 changes `gold_inforce_reconciliation` content** — after it lands, rebuild the demo DB:
   ```
   scripts/reset_for_testing.py → scripts/_uat_rerun.py → scripts/_uat_ai_fit.py → scripts/_uat_seed_workflow.py
   ```
   then re-run the fraud scan and regenerate the two A/E reports. Re-verify every
   `demo_walkthrough.md` quick-reference figure afterwards.
7. Update `docs/demo_refresh_uat.md` defect log (D5…) and `DEFERRED_FOLLOWUPS.md`
   (OBS-1 and OBS-2 close two stale items).

---

## 6. Environment facts needed to resume

- Interpreter: `.venv/bin/python` (Python 3.12, uv-managed). Never the system Python.
- Gate: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`
- Live DB: `data/experience_study.duckdb` (144,191,488 bytes; run `d5f56adb-478a-44f9-9b26-404d6cb05ad0`).
  **Its file hash changes on every app boot** — `seed_users_from_config` re-salts the four users
  (finding M-16). Compare *row counts and content*, not the file hash.
- Demo credentials live in git-ignored `config/governance_config.local.yaml`
  (`a.analyst`, `j.junior`, `s.senior`, `c.chief`). Claude does not type passwords — the owner
  performs the login keystroke for the browser walk.
- **Both new docs are untracked.** A `git clean -fd` would delete them. Commit when ready.

---

## Appendix A — Governance bypass reproduction (becomes the B-1 regression test)

Runs on a **copy**; must raise `PermissionDenied` once B-1 is fixed.

```python
from src.governance.lineage import create_version, approve_and_supersede, resolve_live_set
from src.governance.users import get_user_by_username
from src.assumptions.assumption_set import load_assumption_set, save_assumption_set

analyst = get_user_by_username("a.analyst", db_path=COPY)   # role=analyst, NO sign_off right
child = create_version(orig_set_id, run_id, analyst, db_path=COPY)
a = load_assumption_set(child, COPY)
for m in a.mortality_multipliers:
    m.multiplier = round(m.multiplier * 0.5, 6)             # halve every mortality assumption
save_assumption_set(a, COPY)
approve_and_supersede(child, date(2020, 1, 1), date(2099, 12, 31), db_path=COPY)
```

Observed today (the defect):

```
rbac.require(SIGN_OFF) -> DENIED (PermissionDenied)     <-- correctly blocked here
approve_and_supersede() -> returned without error       <-- but not here
  05e3923e…  APPROVED    approved_by=None     2020-01-01 .. 2099-12-31   <-- attacker's set
  04a682c2…  SUPERSEDED  approved_by=c.chief                             <-- chief's set demoted
LIVE SET today -> 05e3923e…       audit events: 0       verify_chain: ok=True
```

## Appendix B — Other reproductions worth keeping as tests

```python
# B-4 — 19/132 dimension pairs raise
aggregate_ae(row_dims=["attained_age_band"], col_dims=["premium_jump_ratio_band"], …)
# ValueError: Length of values (18) does not match length of index (11)

# M-9 — a log with zero integrity protection reports intact
verify_chain("gold_workflow_iterations")   # -> ok=True, rows_checked=0

# M-14 — CTE alias evades the credibility backstop
sql = ("WITH t AS (SELECT credibility_z_lapse AS z FROM gold_ae_results "
       "WHERE product_code='UL') SELECT AVG(z) FROM t")
aggregates_per_cell_stat(sql)   # -> False (should be True); returns 0.00106 vs true Z 0.6485

# M-4 — reconciliation passes with zero exposure segments
_run_reconciliation(con, policies_df, "TERM", run_id, start, end)   # -> True, 0 segments in DB
```
