# Phase 4 (Governance) — Build Progress & Handoff Log

**Purpose:** single source of truth for what has been built across Phase 4
(Governance) sessions, so any new Claude Code session can resume without losing
context. Read this with `phase4_claude_code_prompts.md` (per-session prompts),
`phase4_locked_scope.md` (scope anchor), and the authoritative specs
(`experience_study_requirements_spec_v4_0.md` §8, `experience_study_technical_spec_v3_0.md` §G/H/I).

**Regression gate (every session):** `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short` green (MockProvider posture). **Requires a populated `data/experience_study.duckdb`** — see the environment note below. **Current gate: 1368 passed, 6 skipped, 0 failed (2026-07-04)** — the S27 close was 1325; +20 from the post-S27 governance UI work, then **+23 from the governance-output audit + remediation** (see the "Governance-output audit" section at the end of this doc — two rounds of fixes to segregation identity capture, APPROVED-set immutability across both the transition and save paths, compliance-pack warnings, and audit-reader identity resolution). **Phase 4 build COMPLETE (Sessions 23–27) + post-S27 governance UI; the owner §8.8 UI walkthrough + sign-off is the remaining owner-triggered close — see `docs/phase4_uat_script.md`.**

---

## Status board

| Session | Title | Status |
|--------|-------|--------|
| 23 | Identity & Access Foundation | ✅ COMPLETE (2026-06-29) |
| 24 | Versioning & Lineage | ✅ COMPLETE (2026-06-29) |
| 25 | Configurable Approval Workflow | ✅ COMPLETE (2026-06-29) |
| 26 | Audit Trail & Tamper-Evidence | ✅ COMPLETE (2026-07-01) |
| 27 | Governance Reporting, Tenancy-Readiness & Phase 4 UAT | ✅ COMPLETE (2026-07-01) — **Phase 4 build CLOSED; owner UAT sign-off pending** |

**Owner checkpoints:** S23 (real user identities + bootstrap passwords) — **deferred to deploy** by owner decision (build uses the four §I.2 default seed users with placeholder passwords + a git-ignored local override). S25 (chain / materiality threshold / attestation text) — **RESOLVED 2026-06-29: proceed with the documented `governance_config.yaml` defaults** (chain junior→senior→chief, `delta_tev_threshold: 0.01`, `final_level_below_threshold: senior_actuary`, default attestation text); all owner-overridable in config with no code change. S27 (UAT sign-off).

---

## UAT fix (2026-07-03) — login gate was bypassable (UAT §1, test 1.1; FR-4-02 / NFR-G-01)

**Symptom (owner UAT):** opening the app signed out, every page was still clickable —
only the sidebar looked different (no icons). The login gate did not block pages.

**Root cause:** the page scripts lived in `ui/pages/`, a **Streamlit-reserved directory
name**. Streamlit auto-discovers any `pages/` folder next to the entrypoint and builds a
*second*, directly-routable multipage navigation whose scripts run **without executing
`ui/app.py`** — where `login_gate()` lives. So the auto nav bypassed the gate entirely
(the icon-less sidebar was that auto nav; the intended `st.navigation` menu carries the
`icon=` emojis). The app already used programmatic `st.navigation` + `st.Page`, so the
`pages/` directory was redundant *and* re-enabled the harmful auto convention.

**Fix (rename + defense-in-depth):**
- **Renamed `ui/pages/` → `ui/views/`** (23 page scripts; filenames unchanged) and updated
  `_PAGES_DIR` in `ui/app.py`, so only the login-gated `st.navigation` menu remains.
- Added a shared **`require_auth()`** guard to `ui/config.py` and invoked it in **every**
  view (pages 26/27 refactored from their inline `current_user()`/`st.stop()` blocks to
  the shared helper). Pure defense-in-depth: under normal flow `app.py`'s `login_gate`
  runs first on every rerun, so the guard never fires for a signed-in user.
- Updated the 5 tests that hard-coded the `ui/pages/...` path; the AppTest render tests for
  pages 15/16 now patch `src.governance.auth.current_user` (as page 27's test already did)
  so they render past the new guard.
- New `tests/test_auth_gate.py` locks the fix: no `ui/pages/` reserved dir exists, `app.py`
  points at `views`, and every view calls `require_auth()`.

No schema, no Phase 1–3 behaviour change. Regression gate re-run after the fix.

---

## UAT fixes (2026-07-04) — Part 4 (Audit Trail & Tamper-Evidence) + related

Owner UAT of Part 4 raised four items (4.1–4.4). Two were correct-by-design (written up
for sign-off); two were fixed; one incidental tz flake was corrected. No schema change; no
Phase 1–3 behaviour change. The locked eval/audit contracts are untouched.

**4.1 — analyst could "approve" at Stage 3 but not sign off at Stage 4 → CORRECT BY
DESIGN (+ two improvements applied).** The Stage-3 "Approve" button is a *workflow-
progression* step: it sets the set STATUS to `STAGE3_APPROVED` (`transition_assumption_set_status`,
no RBAC) and logs an iteration — it is **not** a governance sign-off. The real sign-off is
the Stage-4 chain (junior→senior→chief), which the analyst is correctly blocked from
(`may_sign_off_at` + missing `sign_off`). The confusion came from the word "Approve".
Applied:
- **Relabel** (`ui/views/22_tev_stage3.py`): the Stage-3 decision is now "**Submit for
  sign-off**" (button + Option-A header + comment label + page intro); the logged action is
  `SUBMITTED_S4` (was `APPROVED_S3` — a value already listed in `db_init.py`; nothing reads
  the old string, and Stage 4 / the workflow engine gate on the STATUS `STAGE3_APPROVED`,
  never on the action string). The "Refine → Stage 2" option is unchanged.
- **Enforce `propose`** (FR-4-04 segregation): Stages 1–3 previously had **no** RBAC — any
  authenticated user could Save/Run/Submit. Added a UI-layer gate (kept in `ui/` to respect
  `test_core_engine_does_not_import_governance`): new `ui/config.user_can(user, action)`
  wrapper over `rbac.is_permitted`; each stage page captures `_user = require_auth()`,
  disables its write button(s) for non-proposers with an explanatory caption, and re-checks
  `rbac.require(_user, Action.PROPOSE)` server-side at the write. Only the analyst role holds
  `propose`, so actuary roles are blocked from proposing (view access stays open). The
  free-text `workflow_author_id`/`ACTUARY_1` actor label is left as-is — tightening it to the
  authenticated identity is a larger segregation change, recorded as a deferred follow-up.

**4.2 — an uppercase run id showed "No history recorded" → BUG FIXED (+ a data fact).**
`audit._passes_filter` compared `artifact_id` with a raw case-sensitive `!=`, while the UI
only `.strip()`s the input and DuckDB stores ids lowercase — so `E290621F…` never matched
`e290621f…`. Fixed to compare case-insensitively (one line; covers both `artifact_timeline`
and the page's Section-A filter). Regression:
`test_audit_integrity.test_artifact_timeline_and_filter_are_case_insensitive`. **Data fact:**
even in correct case, the rebuilt study run has **no** governance history because it was
never *submitted* through governance (`gold_ae_governance_events` is empty). To see a
populated timeline in UAT, enter the APPROVED **assumption set** id (`2b177be3…`), which has
its 3 sign-offs; a study-run timeline appears only after a run is submitted/approved
(demonstrated non-destructively by `scripts/uat_section3_3_runner.py`).

**4.3 — "Verify integrity" showed 3 of 4 logs at "0 hashed rows" → ALL CORRECT.**
`gold_governance_signoffs` reports 3/3 (the real sign-offs). `gold_ae_governance_events` is
empty (nothing submitted). The two Phase-2 legacy logs (`gold_workflow_iterations`,
`gold_assumption_approvals`) carry the §G.5 retrofit hash columns but they are intentionally
un-backfilled (NULL), so the verifier "begins each chain at the first hashed row" and returns
`ok, rows_checked=0`. No fix needed; no code change.

**4.4 — tamper test has no destructive UI path → engine harness delivered.**
`scripts/uat_section4_4_runner.py` (non-destructive; mirrors the 3.3/3.7 runners) copies the
live DB and proves `verify_chain`: (A) clean → intact, rows_checked=3; (B) business-column
tamper → `ok=False`, divergence at the tampered row's seq (2); (C) deleted middle row →
`ok=False`, linkage break at the next seq (3); (D) live DB still intact afterward. Runs 4/4
PASS with the live DB untouched.

**Incidental — pre-existing tz-boundary flake fixed.** `test_audit_filter_dimensions` used
local `date.today()` against naive-UTC event timestamps, so it failed when the local date
was a day ahead of UTC (GMT+8, before UTC midnight). Switched the test to `datetime.utcnow().date()`
to match how the governance layer stores timestamps.

**Files:** `src/governance/audit.py`, `ui/config.py`, `ui/views/{20,21,22}_tev_stage*.py`;
new `scripts/uat_section4_4_runner.py`, `tests/governance/test_propose_gate.py`; tests added
to `tests/governance/test_audit_integrity.py`. Live AI/governance UI re-test of the relabel +
propose gate is owner-triggered.

### Part 5 (Governance Reporting) — 5.6 compliance pack for a study run

**5.6 — "export for an approved (fit) study run" errored "…is not yet fit" → EXPECTED
(that is the 5.7 guard), + one latent case fix + a harness.** A study-run pack requires the
run to be *fit for assumption-setting* (its governance sign-off chain complete,
`is_study_run_fit`), and there is **no UI to approve a study run** (Stage 4 approves
assumption sets, not runs — engine/harness-only, like §3.3). A freshly rebuilt run has no
sign-offs, so `export_compliance_pack(STUDY_RUN, …)` correctly refuses it (this is exactly
Test 5.7). To exercise 5.6, the run must first be made fit via `submit_study_run` + the full
junior→senior→chief chain.
- **Latent case fix:** the page-27 export input was `aid.strip()` (case-sensitive); a valid
  but uppercase/mixed-case id would have been reported "not found / not fit". Now
  `aid.strip().lower()` (`ui/views/27_governance_dashboard.py`) — same class as the 4.2 fix.
- **Harness:** `scripts/uat_section5_6_runner.py` (non-destructive) submits + fully approves
  the run on a temp copy, exports the study-run pack, and asserts it carries the three
  sign-offs + attestations, the audit excerpt (incl. `STUDY_RUN_SUBMITTED`), and the
  supporting-report links; it also confirms the pre-fit export is refused (5.7). Runs **8/8
  PASS**, live DB untouched (`gold_ae_governance_events` still 0).

**5.5 pack review (owner-supplied `compliance_pack_assumption_set_2b177be3…html`) — CORRECT
/ expected.** Lineage (single root v1, APPROVED), 3-level sign-offs + attestations, audit
excerpt, "no cell-level changes vs parent" rationale (correct for a root), reproducibility
stamp, and the three report links all render as specified (FR-4-24). Observations, none a
defect: (i) blank Effective From/To — the Stage-4 chain doesn't set effective ranges (the
documented effective-dating seam); (ii) the audit action reads `APPROVED_S3` because the
export predates the 4.1 relabel (future exports show `SUBMITTED_S4`); (iii) WORKFLOW rows show
the free-text actor `ACTUARY_1` while SIGNOFF/APPROVAL rows show the authenticated users — the
known free-text-vs-identity gap left as a deferred follow-up. (The `â`/`Î` glyphs in the
pasted copy are paste-encoding mojibake, not in the UTF-8 file.)

**Files (Part 5):** `ui/views/27_governance_dashboard.py`; new `scripts/uat_section5_6_runner.py`.

---

## Governance lifecycle UI (2026-07-04) — closing the engine-only seams

Owner-approved build after the UAT surfaced that several governance capabilities were
**engine + tests only, no UI** (deliberate locked-scope seams). Cross-referencing every
`src/governance` public function against `ui/` callers confirmed the gaps: study-run
approval (`submit_study_run` + `record_signoff(STUDY_RUN)`), the whole versioning/lineage
surface (`create_version`, `approve_and_supersede`, `resolve_live_set`, `compare_versions`,
`lineage_root`), and governed `reopen`. Effect: the UI could take a set to APPROVED but
never advance the lifecycle (approve a run, roll to v2, set effective dates, supersede,
compare). Owner chose **"Full lifecycle UI."** Built entirely in the UI layer; **the
governance engine is unchanged** (all functions already existed and are covered by
`tests/governance/`).

**Reconciliation of the two approval paths (design decision):** the Stage-4 sign-off chain
(`record_signoff`) remains the **approval authority** (sets status APPROVED). Effective-
dating + supersession (`approve_and_supersede` — which has no RBAC and no status
precondition) is exposed as a separate **"Publish"** action that the UI **restricts to
already-APPROVED sets** and gates on `sign_off`, so the chain is never bypassed and
`approve_and_supersede`'s contract (and the lineage tests) stay intact. Lifecycle:
create v1 → Stages 2–4 approve → Publish v1 (effective range) → Re-open → DRAFT v2 →
Stages 2–4 → Publish v2 (supersedes v1, becomes live).

**Built:**
- **Study Run Sign-Off** (`ui/views/28_study_run_signoff.py`) — run selector (COMPLETE
  runs); proposer **Submit** (`submit_study_run`, gated `propose`); the reused Stage-4
  sign-off core with `ArtifactType.STUDY_RUN` (chain progress, `next_required_level`,
  `pending_approvals`, `may_sign_off_at` + `check_segregation`, attestation + comment +
  APPROVE/RETURN → `record_signoff(…STUDY_RUN…, version=None, delta_tev=None)`); when
  `is_study_run_fit` → compliance-pack export (gated `export`). Actor = `current_user()`.
- **Versioning & Lineage** (`ui/views/29_assumption_lineage.py`) — lineage browser
  (`lineage_overview` → members + `resolve_live_set` today); **Re-open** (`reopen`, gated
  `propose`, hands the DRAFT child to Stage 2 via session state); **Publish**
  (`approve_and_supersede`, gated `sign_off`, APPROVED-only, catches
  `OverlappingEffectiveRange`/`ValueError`); **Compare** (`compare_versions` → changed
  cells + ΔTEV + rationale, `view`).
- **Helper** `ui/governance_logic.py` (pure, read-only): `list_complete_study_runs`,
  `study_run_submitted`, `list_assumption_sets`, `lineage_overview`.
- **Nav**: both pages added to the "Governance & Audit" group (`ui/app.py`).

Re-open/Publish have **no engine RBAC** (they're lineage/workflow ops), so the UI is the
only gate — buttons are disabled for the wrong role **and** the click handler re-checks
`user_can` before calling. Study-run submit/sign-off/export are additionally enforced
server-side (`require`/`record_signoff`).

**Tests:** `tests/governance/test_lifecycle_ui.py` (+5) — `governance_logic` helper shapes
+ AppTest render/role-gating smokes for both pages (analyst vs chief; render-only, no live
mutation). **Suite 1343 passed, 6 skipped.** Live DB untouched (0 AE governance events, 2
assumption sets unchanged); the 3.7/4.4/5.6 harnesses still pass. Live UI walkthrough
(submit→approve run→export; re-open→v2→Stage 2; publish→supersede→dashboard live-set;
compare) is owner-triggered.

**Files:** new `ui/views/28_study_run_signoff.py`, `ui/views/29_assumption_lineage.py`,
`ui/governance_logic.py`, `tests/governance/test_lifecycle_ui.py`; modified `ui/app.py`.
Engine unchanged.

---

## UI clarity & navigation restructure (2026-07-04) — presentation only

Owner UX review: the home page was crowded (dense two-column text + two stale
page-listing dataframes that never mentioned the AI or Governance pages) and the sidebar
was a long flat list (one 14-page group + two single-item AI groups) that didn't teach the
workflow. Presentation-only change — **no engine/page-logic/schema change**; every view keeps
`require_auth()`.

- **Sidebar regrouped** (`ui/app.py` `st.navigation`) into **6 workflow-ordered groups**
  (4/6/5/2/4/4 items, was 14/1/1/4/4): **Getting Started** (Home · Study Setup · Data
  Quality · Run Log) → **Experience Results (A/E)** (Exposure · Mortality · Lapse · CI
  Explorer · CI Summary · Product Comparison) → **Product Monitors** (UL · ULSG · VUL · DA
  surrender · GLB) → **AI Assistance** (Assumption Comparison · AI Analyst) → **Assumption
  Setting (TEV)** (Stages 1–4) → **Governance** (Audit · Dashboard · Study Run Sign-Off ·
  Versioning & Lineage). Page files/titles/icons unchanged; only grouping + order. Top→bottom
  = the end-to-end flow; the two AI pages merged into one group placed where they're used.
- **Home page rebuilt** (`ui/views/00_home.py`): a `st.graphviz_chart` end-to-end workflow
  diagram (AI shown as a dashed *advisory* feed; Governance wrapping runs + assumption sets),
  a numbered "how to run the model" list with clickable `st.page_link`s, two explainer cards
  ("Where the AI pages fit" / "Where Governance fits"), and an all-pages quick-reference in a
  collapsed expander (replaces the stale dataframes; now covers all six groups). `_link`
  degrades to a labelled hint if rendered outside the MPA nav context (isolated AppTest).
- **Test:** `tests/test_home_apptest.py` (render smoke: workflow section, steps, both cards).
  `test_lifecycle_ui.py` page-28 assertion relaxed to be tolerant of live governance state
  (the run may already be submitted) — the deterministic propose gate stays in
  `test_propose_gate`. **Suite 1344 passed, 6 skipped.** Runtime verified via an entrypoint
  AppTest render (`st.page_link` resolves; home renders clean).

> **Note (live DB, 2026-07-04):** the owner live-tested the new lifecycle pages — the live DB
> now carries a `STUDY_RUN_SUBMITTED`+`STUDY_RUN_APPROVED` for run `e290621f` (so it is now
> *fit*, and its compliance pack can be exported from the UI directly), 6 sign-offs, and two
> DRAFT v2 assumption sets from governed re-opens. Legitimate usage, not test pollution.

**Follow-ups (2026-07-04):** (1) sidebar group headers **numbered** ("1 · Getting Started" …
"6 · Governance") to make the workflow order explicit (`ui/app.py`). (2) **Compliance-pack
template fix** (`src/reporting/templates/compliance_pack.html.j2`) after evaluating two
owner-generated packs: a **study-run** pack numbered its sections **2-3-4** (reserving "1"
for the assumption-set-only Lineage section it never renders, and skipping "5"), and the
intro paragraph claimed the pack "assembles the lineage … rationale, and reproducibility"
which a study-run pack lacks. Fixed with a sequential Jinja section counter (study run now
**1-2-3**: Sign-Offs · Audit · Supporting Reports; assumption set unchanged at **1-6**) and
an artifact-aware intro. Presentation-only; applies to future exports. (The assumption-set
pack was already correct; its blank effective dates, `APPROVED_S3` audit label, and
`ACTUARY_1` free-text actor are pre-existing known items — effective dates are now fillable
via the new **Publish** action, and fresh exports show `SUBMITTED_S4`.) **Suite 1344 passed,
6 skipped.**

---

## ✅ Environment note — production DuckDB repopulated 2026-06-29 (was empty; pre-existing 2026-06-28)

**RESOLVED (2026-06-29):** the owner re-ran a study via the Streamlit "Run Study"
(1 COMPLETE run, 3,200 silver Term, 81,868 A/E rows; `gold_users` survived). To
restore the full Phase-1+2+3 prod-DB state the realdata/integration tests expect,
two further steps were run programmatically (additive; sanctioned write targets):
(1) **AI models fit** for the run — WL mortality (58 factors) + TERM/WL/VUL lapse
(5 each); sparse combos correctly returned the "No AI proposal" guardrail —
materialising 146 `gold_ai_proposed_factors` rows; (2) **TEV workflow** —
`create_assumption_set_from_ae_run` → `build_model_points` (all products) →
`run_tev` baseline (total TEV 173,388,672.57) → `run_sensitivity_grid` (11),
giving 1 assumption set + 12 TEV runs + 72 results. Full gate then **1189 passed,
6 skipped, 0 failed**. Note: a pre-existing test-isolation quirk — `test_model_points`
writes model points into the real prod DB, which un-skips `test_envelope`'s class
(its `skipif` checks model points but the fixture needs an assumption set) — meant
the envelope tests required the TEV state above to pass. **Fixed (2026-06-29):**
`tests/test_envelope.py` now gates `TestRunEnvelopeAnalysis` on a combined
`_envelope_preconditions_met()` (model points **and** assumption set **and**
baseline TEV run) instead of model points alone — so it skips cleanly (never errors
in the fixture) on a DB that has model points but no TEV-workflow state. Verified
both ways: passes with the full state, skips (no error) with model points only.

### Original note (pre-existing empty DB — 2026-06-28)

As of 2026-06-29, `data/experience_study.duckdb` (204 MB) and its only backup
`data/experience_study.duckdb.bak-2026-06-27` (142 MB) both contain **0 study
runs and 0 silver rows** — the study data was cleared **before** the Session-23
session began (DB last modified 2026-06-28 18:41). This is unrelated to Phase 4.

Consequence: ~89 data-dependent tests fail and ~88 extra tests skip, because they
copy/read the prod DB (the DQ suites via the `prod_db` fixture; `test_model_points`,
`test_assumption_set`, `test_*_realdata`, `test_assumption_comparison_apptest`
directly). The "1163 passed, 6 skipped" Phase-3 baseline was recorded against a
populated DB that no longer exists in either file.

**To restore the full regression gate:** repopulate `data/experience_study.duckdb`
by running a study through the Streamlit app ("Run Study"; synthetic CSVs in
`synthetic_data/output/` are intact, seed 42 → reproducible), then re-run the gate.
**Owner-triggered** (chosen 2026-06-29). Session 23 itself was verified independently
of the prod DB (see below).

---

## Session 23 — Identity & Access Foundation — COMPLETE ✅ (2026-06-29)

**Goal delivered:** the identity foundation the rest of Phase 4 depends on — a
`gold_users` registry + four roles, a minimal salted username/password login gate
ahead of every Streamlit page, server-side RBAC with segregation-of-duties
primitives, and the authenticated session identity as the canonical actor source.
Strictly additive; governance is ordinary application code **outside `src/ai/`**
(uses the standard parameterized write path, not the AI read-only `sql_boundary`).

**Realises:** FR-4-01..06; NFR-G-01/G-02/G-03. **Contracts:** Tech Spec §G.1,
§H.1/H.2/H.3/H.4, §I.1/I.2/I.3.

### Owner decisions (confirmed 2026-06-29)
- **Credentials:** build with the four §I.2 default seed users (`a.analyst`,
  `j.junior`, `s.senior`, `c.chief`) + `<set at first run>` placeholder passwords
  in the committed `governance_config.yaml`; real credentials supplied at deploy via
  a git-ignored `config/governance_config.local.yaml` whose `users` block overrides
  by username. (Owner Checkpoint 1 = a deploy-time action, not a build blocker.)
- **Hashing:** stdlib `hashlib.pbkdf2_hmac` (SHA-256, 200 000 iterations, per-user
  hex salt) — **no new dependency**, so no `requirements.lock` regeneration (avoids
  the documented numba/llvmlite/shap pin-drift risk).

### Files added
| Path | What |
|------|------|
| `src/governance/__init__.py` | Package docstring; records the FR-4 contracts + the "outside `src/ai/`, standard write path" posture. |
| `src/governance/auth.py` | §H.2: `hash_password`/`verify_password` (salted PBKDF2, constant-time compare, fail-closed on bad salt), `authenticate` (read-only, no info leak), `current_user` (lazy Streamlit; None outside a runtime/tests), `login_gate` (form + `st.stop()` until authed), `logout`. `UNUSABLE_HASH="!"` sentinel for un-set passwords; `PLACEHOLDER_PASSWORD` constant. |
| `src/governance/users.py` | §H.3: `seed_users_from_config` (idempotent upsert by username; committed config overridden by the git-ignored `governance_config.local.yaml`; hashes bootstrap passwords + discards plaintext; placeholder → UNUSABLE_HASH on insert and password preserved on re-seed), `get_user`/`get_user_by_username`/`list_users`. |
| `src/governance/rbac.py` | §H.4: `Action` enum, `PermissionDenied`, `load_permission_matrix`, `is_permitted`, `require` (server-side; logs every denial via `logging` — see note), `may_sign_off_at`. |
| `config/governance_config.yaml` | Full §I.1 schema (roles, permissions, approval_chain, segregation, materiality, attestation_text, retention, users). S23 consumes roles/permissions/users; the rest is inert until S25/S27. Placeholder passwords only. |
| `tests/governance/__init__.py`, `conftest.py` | `gov_env` fixture: temp DB via `init_database` + seed from an in-test config carrying real test passwords (committed config stays secret-free; prod DB never touched). |
| `tests/governance/test_auth.py` (18), `test_rbac.py` (8) | 26 tests — hashing, seed/authenticate, gold_users schema + idempotency, local-override password, pre-auth no-identity, permission matrix, `require` block+log, role↔level, end-to-end seeded permissions. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended the §H.1 block: `Role`/`ArtifactType`/`Decision` enums; frozen `User`/`ChainLevel`/`SignoffRecord` dataclasses. (Whole block added now — `ChainLevel` is needed by `rbac.may_sign_off_at` this session; S24/S25 consume the rest.) |
| `src/utils/db_init.py` | New `_GOVERNANCE_DDL` with `gold_users` (§G.1); appended to the `all_ddl` concatenation (`… + _GOLD_AI_DDL + _GOVERNANCE_DDL`). Idempotent via `CREATE TABLE IF NOT EXISTS`. No `_COLUMN_MIGRATIONS` change (lineage/effective-dating columns are S24). |
| `ui/app.py` | Mounted `login_gate()` after `st.set_page_config` and before `st.navigation`; sidebar shows the signed-in user + a "Sign out" button. No page-registration change. |
| `.gitignore` | Ignore `config/governance_config.local.yaml` and `.env.local` (real-credentials path). |

### Sequencing decision (documented)
The §H.4 spec says `require()` denials are logged "via H.7 `append_event`" — but
`append_event` and the governance log tables are **Session 25/26** deliverables.
To keep S23 self-contained, `require()` logs denials via Python `logging`
(`logging.getLogger("governance.rbac")`); the falsifiable test asserts the raise
**and** the log record. S26 may additionally route denials to the governance audit
log once `append_event` exists. This satisfies FR-4-04 ("rejected and logged") at
S23 maturity.

### Definition of done — all met (verified independently of the prod DB)
- [x] `gold_users` created with the §G.1 columns; init idempotent (schema test).
- [x] Login gate blocks pre-auth; passwords stored only as salted hashes; no SSO/reset.
- [x] Session identity is the canonical actor (`current_user`); free-text actors not used in Phase-4 surfaces.
- [x] RBAC enforced server-side: a disallowed action invoked directly is rejected + logged.
- [x] Segregation primitive (`may_sign_off_at`) matches role↔level (the absolute proposer≠approver rule lands in S25 with the workflow engine).
- [x] All governance constants in `governance_config.yaml` (FR-4-27); placeholder passwords only.
- [x] **26 new governance tests pass; 8 AI-architecture guards pass** (governance is outside `src/ai/` → no false trips).
- [x] **No regressions:** suite excluding the prod-DB-dependent files = **908 passed, 47 skipped, 0 failed** (incl. the 26 governance tests).
- [x] **Full regression gate:** GREEN — **1189 passed, 6 skipped, 0 failed** (2026-06-29), after the owner repopulated the DB via "Run Study" and the AI models + TEV baseline/sensitivity state were materialised (see the environment note). = Phase-3 baseline 1163 + 26 governance tests.

### Verification commands
- Governance only: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/governance/ -v --tb=short` → 26 passed.
- Guards: `… -m pytest tests/test_ai_architecture.py` → 8 passed.
- No-regression subset (prod-DB files ignored): 908 passed, 47 skipped, 0 failed.
- Login-gate smoke (owner): `streamlit run ui/app.py` → login form precedes all pages; a seeded credential signs in; "Sign out" returns to the gate. (Pending owner repopulation of the DB to seed real users; default seed users have placeholder/unusable passwords until the local override is supplied.)

### Notes for Session 24
- Build version lineage on `gold_assumption_sets`: additive `parent_set_id`,
  `effective_from`, `effective_to` (§G.4) via the existing `_COLUMN_MIGRATIONS`
  + `_ensure_column` mechanism in `db_init.py` (keep `effective_date` set at create).
- Implement `src/governance/lineage.py` (§H.5). Reuse the §H.1 types already added.
- The four §H.1 value objects (Role/User/ChainLevel/ArtifactType/Decision/SignoffRecord)
  are in `src/utils/types.py`; `Action` is in `src/governance/rbac.py`.

---

## Session 24 — Versioning & Lineage — COMPLETE ✅ (2026-06-29)

**Goal delivered:** assumption-set **version lineage** — parent→child chains,
supersession, effective-dating with a live-set resolver, cross-version comparison
(changed cells + ΔTEV + rationale), and a reproducibility stamp. Strictly additive;
governance is ordinary application code **outside `src/ai/`** (standard parameterized
write path, not `sql_boundary`). Lineage functions are pure data operations — RBAC
enforcement is the Session-25 workflow engine's job (the §H.5 contract has no
`require()` calls).

**Realises:** FR-4-07 … FR-4-11; NFR-G-05. **Contracts:** Tech Spec §G.4, §H.5.

### Files added
| Path | What |
|------|------|
| `src/governance/lineage.py` | §H.5: `create_version` (clone-from-parent → DRAFT, or seed-root via `create_assumption_set_from_ae_run`), `lineage_root` (walk to root, cycle-guarded), `approve_and_supersede` (non-overlapping range check → raise `OverlappingEffectiveRange`; set APPROVED + range + `approved_ts`; supersede prior APPROVED set(s), ≤1 APPROVED-current per lineage), `resolve_live_set` (APPROVED set whose range contains `as_of`), `compare_versions` (multiplier diff across all five decrement types + ΔTEV from each set's latest baseline `gold_tev_run_log.total_tev`, NaN if missing + per-cell rationale), `reproducibility_stamp` (LEFT JOIN assumption-set → study run → AI model registry). |
| `tests/governance/test_lineage.py` (26) | parent-link/version-increment/DRAFT; clone content; `lineage_root` multi-level; root seed; approve sets status+range; 2nd approval supersedes 1st + ≤1 APPROVED; overlap rejected (no partial mutation); live-set in/out of range; live-set ignores SUPERSEDED; compare changed cells + ΔTEV + rationale; no-change empty diff; ΔTEV NaN when TEV missing; reproducibility stamp traces to study run; **plain re-save preserves lineage columns**. **Post-build review (+11):** live-set boundary-date inclusivity; live-set accepts any member id + unknown-id→None; overlap enforced across SUPERSEDED ranges; inverted range→ValueError; 3-version lineage → single APPROVED + per-window resolve; `lineage_root`/`reproducibility_stamp` unknown-id→ValueError; compare detects added/removed cells; compare spans multiple decrements; clone is isolated from parent; DRAFT re-save preserves parent link. |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Added `DRAFT` to `AssumptionSetStatus` (additive; `STAGE3_APPROVED` retained for the Phase-2 shell); added the frozen `VersionDiff` dataclass (§H.5: `changed_cells`/`delta_tev`/`rationale_by_cell`). |
| `src/utils/db_init.py` | Appended the §G.4 columns to `_COLUMN_MIGRATIONS` — `parent_set_id VARCHAR(36)` (NULL = root), `effective_from DATE`, `effective_to DATE` — applied idempotently by the existing `_ensure_column` loop. |
| `src/tev/assumption_set.py` | Generalised `_insert_assumption_set_metadata`'s AI-provenance read-back/preserve loop to also carry `parent_set_id`/`effective_from`/`effective_to` across the DELETE+INSERT, so a later plain `save_assumption_set` (e.g. a Stage-2 edit) does not wipe the lineage/effective-dating columns. |

### Design decisions
- **`create_version`**: parent given → `copy.deepcopy` the parent, new id, `version+1`,
  status DRAFT, then record `parent_set_id` via a parameterized UPDATE (the metadata
  INSERT omits it). Parent `None` → seed a root from the A/E run, then mark DRAFT with
  `parent_set_id = NULL`.
- **Status reconciliation**: `DRAFT` added additively; FR-4-07's
  DRAFT→PROPOSED→APPROVED→SUPERSEDED transitions that lineage owns are DRAFT (create)
  and APPROVED/SUPERSEDED (approve_and_supersede); the PROPOSED step is the Session-25
  workflow's responsibility.
- **`VersionDiff.delta_tev` stays `float`** per §H.5 — `float('nan')` when a baseline
  TEV run is missing, rather than widening the type.
- **Overlap rule**: a requested effective range is rejected if it overlaps *any* set in
  the lineage that already carries a range (including a soon-to-be-superseded one); the
  check runs **before** any write, so a rejected approval leaves all rows unchanged.

### Definition of done — all met
- [x] §G.4 columns added idempotently; `effective_date` still set at create.
- [x] `create_version` records parent links + increments version; new version starts DRAFT (FR-4-07).
- [x] Status transitions (DRAFT→APPROVED→SUPERSEDED) behave per spec; ≤1 APPROVED-current per lineage (FR-4-08; NFR-G-05).
- [x] Constructed overlapping effective range rejected; `resolve_live_set` returns the set whose range contains the date, else `None` (FR-4-09).
- [x] `compare_versions` reports changed cells + ΔTEV + rationale (FR-4-10); `reproducibility_stamp` traces to source run + data-snapshot hash (FR-4-11).
- [x] Plain re-save preserves the lineage columns (persistence guard).
- [x] AI-architecture guards green (governance outside `src/ai/` → no false trips).
- [x] **Full regression gate: GREEN — 1215 passed, 6 skipped, 0 failed (2026-06-29)** = 1189 baseline + 26 new lineage tests; no regressions.

### Post-build review (2026-06-29)
A careful correctness/completeness review against §H.5 + FR-4-07…11 found the build
complete and correct; it added **one defensive robustness improvement** and **+11
strengthening tests** (no functional defects found):
- **`resolve_live_set` now accepts any lineage member id**, normalising it to the
  lineage root before resolving (and returns `None` for an unknown id, never
  raising). Previously a non-root id silently returned `None`. This is a
  backward-compatible superset of the §H.5 contract (a root id behaves exactly as
  documented), so no locked-spec amendment is required.
- **+11 tests** locking boundary-date inclusivity, member-id normalisation,
  overlap-across-SUPERSEDED-ranges, the inverted-range guard, a 3-version
  single-APPROVED chain, unknown-id error paths, add/remove cell diffs,
  multi-decrement diffs, clone↔parent isolation, and DRAFT re-save parent
  preservation.
- Reviewed but **intentionally left as-is** (existing, deliberate behaviour): the
  metadata re-save preserves only the lineage columns (`parent_set_id`/
  `effective_from`/`effective_to`) and the AI-provenance columns; the approval
  lifecycle columns (`approved_ts`/`approved_by`/`superseded_by`) are *not*
  preserved across a plain re-save because APPROVED/SUPERSEDED sets are immutable
  (FR-2-44/FR-4-15) and never re-saved — preserving them would be out-of-scope
  scope-creep that changes Phase-2 persistence semantics.

### Verification commands
- Lineage only: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/governance/test_lineage.py -v` → 26 passed.
- Full gate: `unset … && .venv/bin/python -m pytest tests/ -q` → 1215 passed, 6 skipped, 0 failed.

### Notes for Session 25
- The lineage engine is ready for the approval workflow: `create_version` (used by
  `reopen`), `approve_and_supersede`, `resolve_live_set`, `compare_versions`.
- `gold_governance_signoffs` (§G.2, hash-chained) + `src/governance/workflow.py` (§H.6)
  + `src/governance/audit.py::append_event` (write half, §H.7) are the S25 deliverables.
- `DRAFT` status is in place; the workflow adds the PROPOSED transition and the
  materiality/segregation/attestation logic. Owner Checkpoint 2 (chain / `delta_tev_threshold`
  / `final_level_below_threshold` / attestation text) is a STOP in S25.

---

## Session 25 — Configurable Approval Workflow — COMPLETE ✅ (2026-06-29)

**Goal delivered:** the Phase-2 single-reviewer Stage-4 sign-off generalised into a
**configurable multi-level approval chain**, extended to A/E **study runs**, with
attestation capture, a **materiality-driven required level**, a pending-approvals
queue, and **governed re-open** — all backed by the new append-only, hash-chained
`gold_governance_signoffs` log. Governance stays ordinary application code **outside
`src/ai/`** (standard parameterized write path, never `sql_boundary`); a single-
`chief_actuary` chain reproduces the legacy single-reviewer behaviour (NFR-G-08).

**Realises:** FR-4-05/12/13/14/16/17/18; NFR-G-03/G-08. **Contracts:** Tech Spec §G.2,
§H.6, §H.7 (write half).

### Owner Checkpoint 2 (RESOLVED 2026-06-29)
Owner chose **proceed with the documented `governance_config.yaml` defaults**: chain
junior→senior→chief, `materiality.delta_tev_threshold: 0.01`,
`final_level_below_threshold: senior_actuary`, and the default attestation text. The
engine reads all four from config; values stay owner-overridable with no code change
(no config edits were made this session). Owner also chose **engine + tests + full
multi-level Stage-4 UI** for the build scope.

### Files added
| Path | What |
|------|------|
| `src/governance/audit.py` | §H.7 **write half**: `append_event(table, content, *, db_path)` — assigns `seq = MAX(seq)+1` (first row 1), `prev_hash` = prior row's `entry_hash` (`""` for the first), `entry_hash = sha256(canonical ‖ prev_hash)` with the §G.2 canonical rule (all business cols **except** prev/entry, **incl.** `seq`; sorted keys; ISO-8601 dates; NULL→json null), and a static `?`-placeholder INSERT built from a trusted internal `_HASH_CHAINED_TABLES` registry (table never from caller input). Structured (`_canonical_row`/`_entry_hash` helpers + registry) so S26's `verify_chain`/unified read slot in. |
| `src/governance/workflow.py` | §H.6 chain engine: `load_chain`, `required_final_level` (study run→full chain; `\|ΔTEV\|`>threshold→chief level, else `final_level_below_threshold`), `next_required_level` (per-**round** — sign-offs since the last RETURN — honouring the round's fixed `required_final_level`), `check_segregation` (proposer≠approver absolute + distinct-signer-per-level unless `allow_multi_level_signoff`; `SegregationViolation`), `record_signoff` (RBAC `require` + `may_sign_off_at` chain-order/role gate + segregation + mandatory comment → hash-chained row via `append_event`; on completing assumption-set APPROVE locks the set and writes the legacy `gold_assumption_approvals` summary; RETURN resets to PROPOSED), `reopen` (→ DRAFT child via `lineage.create_version`, mandatory justification, original immutable), `pending_approvals`, plus `is_study_run_fit` (derived "fit for assumption-setting"). |
| `tests/governance/test_workflow.py` (21) · `test_segregation.py` (4) | 25 tests — schema+idempotency lock; `append_event` first-row/linkage/recompute/unknown-table; chain load + materiality (study-run full chain, material→chief, below→senior); full material chain locks + writes summary; below-threshold completes at senior; sequential order; RETURN→PROPOSED; mandatory comment; analyst-no-permission (logged); study-run full chain + `is_study_run_fit`; single-chief reproduces legacy; reopen DRAFT child preserves original + requires justification; pending-by-role; and segregation (author-cannot-sign-even-with-matching-role, distinct-signer block, allow-multi permits, wrong-role/out-of-order). |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/db_init.py` | New `_GOVERNANCE_SIGNOFF_DDL` (`gold_governance_signoffs` §G.2 + `idx_signoff_artifact`) appended to `all_ddl` (`… + _GOVERNANCE_DDL + _GOVERNANCE_SIGNOFF_DDL`). Idempotent `IF NOT EXISTS`. No hash-chain columns on the Phase-2 logs (that is S26 / §G.5). |
| `ui/pages/23_tev_stage4.py` | **Full multi-level Stage-4 rebuild** (report/memo sections untouched): the actor is the authenticated `auth.current_user()` (FR-4-03) — the free-text reviewer input is gone; a chain-progress table, "My pending approvals" expander, role-for-level/segregation pre-checks, the configured attestation + attest checkbox + mandatory comment + APPROVE/RETURN, and `record_signoff(...)` (with a `legacy_context` from session state for full `gold_assumption_approvals` fidelity). The ΔTEV **fraction** vs the prior approved set is derived from the Stage-3 session state to drive materiality. A single-chief chain still behaves like the legacy one-reviewer flow. |

### Design decisions / reconciliations
- **`append_event` is the single write path** for the hash-chained log; `record_signoff` never hand-writes the row — so S26's `verify_chain` matches how rows were written.
- **Per-round chain state.** A RETURN starts a new round; `next_required_level`/segregation evaluate only the current round. `required_final_level` is fixed at the round's first sign-off (from the caller-supplied ΔTEV fraction) and reused, so the materiality decision is stable across the chain.
- **Legacy bridge.** On a completing assumption-set APPROVE, `record_signoff` reuses `src.tev.workflow.transition_assumption_set_status` (lock) and `record_governance_approval` (summary), so the Phase-2 report/"All approvals" expander keep working. Missing context (workflow session, baseline TEV, iterations) is read from the DB / defaulted when no `legacy_context` is supplied, so the engine works standalone in tests.
- **Study-run author capture is deferred to S26.** A study run has no author column until §G.3 `gold_ae_governance_events` (STUDY_RUN_SUBMITTED); at S25 the proposer≠approver author check is a no-op for study runs (the distinct-signer-per-level rule still applies), and "fit for assumption-setting" is derived from sign-off rows (`is_study_run_fit`).
- **Signatures match §H.6/§H.7** with keyword-only `*, db_path`/`config_path` extras (the established S23/S24 convention) so functions stay unit-testable against the `gov_env` temp DB.

### Definition of done — all met
- [x] `gold_governance_signoffs` created (§G.2) idempotently; DDL↔writer column-alignment locked by a schema test.
- [x] `append_event` append-only + hash-chained (first row empty `prev_hash`; `prev_hash` links the chain; `entry_hash` recomputes from stored columns by the §G.2 rule); no update/delete path (FR-4-20).
- [x] Sequential multi-level sign-off enforced; RETURN resets to editable; mandatory comment + attestation captured (FR-4-13/15).
- [x] Materiality: `\|ΔTEV\|` above threshold forces chief; at/below completes at `final_level_below_threshold`; a study run always runs the full chain (FR-4-14/16).
- [x] Segregation: a user cannot sign an artifact they authored at any level; one user cannot sign two levels unless `allow_multi_level_signoff` (FR-4-05).
- [x] `reopen` creates a DRAFT child and never mutates the original (FR-4-18); `pending_approvals` filters by the user's level (FR-4-17).
- [x] A single-`chief_actuary` chain reproduces the legacy single-reviewer sign-off + writes the `gold_assumption_approvals` summary (NFR-G-08).
- [x] AI-architecture guards green (governance outside `src/ai/` — no false trips on the interpolation/import/write-contract scans).
- [x] **Full regression gate: GREEN — 1253 passed, 6 skipped, 0 failed (2026-06-29)** = 1215 baseline + 38 Session-25 tests (25 build + 13 post-build review); no regressions.

### Post-build review (2026-06-29)
An adversarial correctness/robustness review (independent agent + self-review) found **no
governance-bypass bug** but one **latent hash-chain landmine** and several robustness/coverage
gaps. All fixed in-place; **+13 tests** (25 → 38), gate stayed green.
- **🔴 Hash-chain reproducibility (the landmine, FIXED).** `append_event` hashed the in-memory
  `signoff_ts` via `isoformat()`, but DuckDB `TIMESTAMP` is timezone-**naive** — a tz-aware
  datetime is stored with its offset dropped (wall-clock shifted by the local zone), so a
  Session-26 `verify_chain` recomputing from the stored column would have reported a **false
  tamper** on a legitimately-written row. Masked today only because `record_signoff` uses naive
  `utcnow()`, but `append_event` is the public §H.7 entrypoint S26 writes more tables through.
  Fix: `audit._normalize_value` coerces a tz-aware datetime to **naive UTC** before *both* the
  hash and the INSERT, so stored==hashed and the result is environment-independent (demonstrated:
  pre-fix recompute mismatched on a GMT+8 box). Locked by `test_recompute_matches_with_float_and_tzaware_ts`
  (float `delta_tev` + tz-aware `signoff_ts`) and `test_tampered_row_recompute_mismatches`.
- **🟡 Materiality fallback (FIXED).** `required_final_level` raised `ValueError` when the
  configured `final_level_below_threshold` (or chief) role wasn't in the chain — e.g. a
  single-`chief_actuary` chain with a below-threshold ΔTEV would crash. Now falls back to the
  **final** chain level (`_level_of_role_or`). Also switched `len(chain)` → `chain[-1].level` so
  non-contiguous level numbering can't truncate the chain; added an empty-chain guard in
  `next_required_level`/`record_signoff`. Locked by `test_required_final_level_role_not_in_chain_falls_back_to_final`,
  `test_single_chief_below_threshold_completes_at_chief`, `_at_threshold_`, `_negative_delta_`.
- **🟡 `reopen` hardened (FIXED).** Added the FR-4-18 **APPROVED-only** guard (a non-APPROVED set
  is rejected), and replaced a dead best-effort write (`AssumptionSet` has no `description` field,
  so the justification was silently dropped) with a **durable, loud** `UPDATE gold_assumption_sets.description`
  on the child. Locked by `test_reopen_rejects_non_approved_set`, `test_reopen_records_justification_on_child`.
- **🟡 `pending_approvals` (FIXED).** Now lists `STAGE3_APPROVED` as well as `PROPOSED` — the
  Phase-2 shell submits a set to the chain at `STAGE3_APPROVED`, so the queue would otherwise miss
  real in-flight sets. Locked by `test_pending_approvals_includes_stage3_approved`.
- **Coverage added** for the round engine's subtle invariants: RETURN-then-resubmit re-evaluates
  materiality (`test_return_then_resubmit_reevaluates_materiality`), the required final level is
  fixed at a round's first sign-off (`test_required_final_level_fixed_at_first_signoff_of_round`),
  a complete chain rejects an extra sign-off (`test_complete_chain_rejects_extra_signoff`), and a
  study-run RETURN leaves it not fit (`test_study_run_return_makes_it_not_fit`).
- **Accepted as-is (documented, not bugs):** `delta_tev=None` on an assumption set runs the full
  chain (fail-safe / stricter); `append_event` `seq` is `MAX+1` under a single-writer prototype
  assumption (the `seq UNIQUE` constraint is the loud safety net) — both noted in the docstrings.

### Verification commands
- Workflow + segregation only: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/governance/test_workflow.py tests/governance/test_segregation.py -v` → 25 passed.
- Governance + guards: `… -m pytest tests/governance/ tests/test_ai_architecture.py -q` → 85 passed.
- Full gate: `unset … && .venv/bin/python -m pytest tests/ -q` → 1240 passed, 6 skipped, 0 failed.
- UI smoke (owner-triggered): `streamlit run ui/app.py` → log in as each seeded role; run a full propose → junior → senior → chief chain with attestations; force a chief-required case via a material ΔTEV; attempt a self-approval and a wrong-level sign-off and confirm both are blocked.

### Notes for Session 26 (Audit Trail & Tamper-Evidence)
- `src/governance/audit.py` is structured for the **read/verify half**: add `verify_chain` (recompute via `_canonical_row`/`_entry_hash`, report `first_divergence_seq`), `unified_audit_query`, `artifact_timeline`. The `_HASH_CHAINED_TABLES` registry is where `gold_ae_governance_events` and the §G.5 hash-chain columns on the Phase-2 logs get added.
- DDL to add (§G.3/§G.5): `gold_ae_governance_events` (hash-chained) + additive `seq`/`prev_hash`/`entry_hash` columns on `gold_workflow_iterations` / `gold_assumption_approvals` (via `_COLUMN_MIGRATIONS`).
- Study-run **author/submitter** capture lands here (STUDY_RUN_SUBMITTED event), which lets `check_segregation` enforce proposer≠approver for study runs and `pending_approvals` list submitted-but-unsigned runs.

---

## Session 26 — Audit Trail & Tamper-Evidence — COMPLETE ✅ (2026-07-01)

**Goal delivered:** the audit layer that closes FR-4-19…22 / NFR-G-04 — the new
hash-chained **A/E governance-events** log, the §G.5 hash-chain columns on the two
Phase-2 logs, the **read/verify half** of `src/governance/audit.py`
(`verify_chain` tamper-evidence + `unified_audit_query` / `artifact_timeline` over
the three physically-separate logs), the A/E-event **writers** with guarded
natural-hook emission (owner choice), and a "Governance & Audit" Streamlit page.
Strictly additive; no Phase 1–3 behaviour change. **Contracts:** Tech Spec §G.3,
§G.5, §H.7 (read/verify half).

### Owner decision (confirmed 2026-07-01)
- **A/E-event wiring depth: "mechanism + natural hooks."** Build the `record_ae_event`
  writer AND wire the events that occur naturally, each **guarded** so a failed emit
  can never break the primary action. NOT chosen: retrofitting the Phase-2 legacy
  writers to hash-chaining (their §G.5 columns stay schema-ready / NULL; the verifier
  begins each chain at the first hashed row).

### Design decisions
- **Two registries.** `_HASH_CHAINED_TABLES` (the `append_event` **write** allowlist)
  gains only `gold_ae_governance_events` — kept minimal so `append_event` can never
  open an unintended write path into a Phase-2 table. A new `_VERIFIABLE_CHAINS`
  **verify** registry (superset) additionally covers the two Phase-2 logs, so
  `verify_chain` can be asked about any governance log; one with no hashed rows
  verifies as `ok=True, rows_checked=0` (§G.5).
- **`verify_chain` mirrors `append_event` byte-for-byte.** It reads hashed rows
  (`WHERE entry_hash IS NOT NULL ORDER BY seq`), and per row checks (a) linkage —
  stored `prev_hash` == prior hashed row's `entry_hash` (first expects `""`) — and
  (b) integrity — `entry_hash` recomputed from the stored business columns (via the
  unchanged `_normalize_value`/`_canonical_row`/`_entry_hash`, incl. `seq`) equals the
  stored value. Read-back values are already storage-form (naive datetime / float /
  int / str / None → `_normalize_value` no-op), so the recompute is exact. Returns
  the `seq` of the first failing row.
- **Unified read layer, not merged storage (FR-4-22).** `unified_audit_query` projects
  five source tables to one common shape `{ts, actor, actor_user_id, role,
  artifact_type, artifact_id, artifact, action, detail, source}` — the Phase-4
  sign-off log (`SIGNOFF`), the A/E events (`AE_EVENT`), the Phase-3 AI audit log
  (`AI`, §D.3 projection), and the legacy Phase-2 workflow/approval logs
  (`WORKFLOW`/`APPROVAL`, free-text actor). One `gold_users` lookup resolves
  display-name/role; each source read is defensively try/except-guarded; `AuditFilter`
  is applied in Python; sorted ts-DESC. `artifact_timeline` reuses the projection
  filtered to one artifact, sorted ts-ASC.
- **Guarded natural-hook emission.** `record_signoff` emits STUDY_RUN_APPROVED (on
  chain completion) / STUDY_RUN_RETURNED (on RETURN) for **study-run** artifacts only,
  via a `try/except`-wrapped `_emit_study_run_event` (the sign-off is already durably
  written first). The DQ dashboard (`ui/pages/02_data_quality.py`) records a
  DQ_OVERRIDE event at the UI action site after a successful override (using
  `current_user()` + the quarantine row's `study_run_id`) — the Phase-1 core
  `override_quarantine_record` is left untouched. Assumption-set sign-off flow is
  unchanged.

### Files added
| Path | What |
|------|------|
| `ui/pages/26_governance_audit.py` | "Governance & Audit" page: filterable unified audit stream, per-artifact timeline, and a "Verify integrity" button (per-log `IntegrityResult`). Read-only; all four roles may view (auth-only gate). |
| `tests/governance/test_audit_integrity.py` (21) | DDL/migration column-locks + idempotency; `record_ae_event`/`submit_study_run` roundtrip; `verify_chain` ok-on-untouched, ok/0 on empty & unhashed-Phase-2, ValueError on unknown table, correct `first_divergence_seq` on a business-column tamper, a broken-linkage tamper, **and a deleted middle row**, float+tz-aware roundtrip; `unified_audit_query` spans all three logs + resolves display_name + every `AuditFilter` dimension; `artifact_timeline` chronology + artifact isolation; **the Phase-2 column-constant ↔ physical-schema lock**; and the **guarded natural-hook emits** (study-run APPROVE→`STUDY_RUN_APPROVED` / RETURN→`STUDY_RUN_RETURNED`; assumption-set path emits none). |

### Files modified
| Path | Change |
|------|--------|
| `src/utils/types.py` | Appended §H.7 `IntegrityResult` + `AuditFilter` (frozen dataclasses; `AuditFilter.role: Role`, `date_*: date` per the contract). |
| `src/utils/db_init.py` | New `_GOVERNANCE_EVENTS_DDL` (`gold_ae_governance_events` §G.3 + `idx_ae_events_run`) appended to `all_ddl`; six additive `_COLUMN_MIGRATIONS` (`seq`/`prev_hash`/`entry_hash` on `gold_workflow_iterations` + `gold_assumption_approvals`, nullable — no retroactive `UNIQUE`). |
| `src/governance/audit.py` | Completed the read/verify half: `_AE_EVENT_COLUMNS` + Phase-2 column lists, `gold_ae_governance_events` added to the write allowlist, new `_VERIFIABLE_CHAINS`, `record_ae_event` / `submit_study_run`, `verify_chain`, `unified_audit_query` (+ `_passes_filter`), `artifact_timeline`. Write half (`append_event`, `_canonical_row`, `_entry_hash`, `_normalize_value`) untouched. |
| `src/governance/workflow.py` | `record_signoff` emits guarded STUDY_RUN_APPROVED/RETURNED for study-run artifacts (`_emit_study_run_event`); assumption-set path unchanged. |
| `ui/pages/02_data_quality.py` | Guarded DQ_OVERRIDE governance event after a successful quarantine override. |
| `ui/app.py` | New "Governance & Audit" nav group → page 26. |

### Definition of done — all met
- [x] `gold_ae_governance_events` created (§G.3); the six §G.5 columns migrated idempotently onto the Phase-2 logs; column-lock + idempotency tests pass.
- [x] `verify_chain` passes on an untouched log and fails on a constructed tampered entry (business column **and** broken linkage) with the correct `first_divergence_seq` (FR-4-21); unknown table → ValueError; empty/unhashed → ok/0.
- [x] `record_ae_event`/`submit_study_run` write hash-chained A/E events; guarded natural hooks emit STUDY_RUN_APPROVED/RETURNED (sign-off) + DQ_OVERRIDE (DQ page).
- [x] `unified_audit_query` + `artifact_timeline` span all three logs into one common shape; `AuditFilter` dimensions narrow correctly (FR-4-22).
- [x] "Governance & Audit" page renders the unified stream, per-artifact timeline, and verify-integrity results; read-only; all four roles may view.
- [x] AI-architecture guards green (governance outside `src/ai/` — the `verify_chain` registry-built SQL is not in the interpolation-scan scope; the connection is read-only).
- [x] **Full regression gate: GREEN — 1274 passed, 6 skipped, 0 failed (2026-07-01)** = 1253 baseline + 21 Session-26 tests (16 build + 5 post-build review); no regressions.

### Verification commands
- Audit only: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/governance/test_audit_integrity.py -v` → 21 passed.
- Governance + guards: `… -m pytest tests/governance/ tests/test_ai_architecture.py -q` → 119 passed.
- Full gate: `unset … && .venv/bin/python -m pytest tests/ -q` → 1274 passed, 6 skipped, 0 failed.
- UI smoke (owner-triggered): `streamlit run ui/app.py` → the "Governance & Audit" page shows the unified stream, a per-artifact timeline, and per-log integrity results; tamper a governance row and confirm `verify_chain` flags it.

### Post-build review (2026-07-01)
An adversarial correctness/robustness review (independent agent + self-review) **found no
defects** — every high-risk invariant was empirically confirmed (clean chain verifies; a
tampered business column, a tampered `prev_hash`, and a **deleted middle row** are each caught
with the correct `first_divergence_seq`; empty/unhashed logs verify clean; the tz-aware→naive
and float/int/NULL round-trips reproduce the hash; `append_event` still rejects the Phase-2 and
AI tables; no circular import; `IntegrityResult`/`AuditFilter` match §H.7). **+5 strengthening
tests** (16 → 21): the guarded natural-hook emits (study-run APPROVE→`STUDY_RUN_APPROVED`,
RETURN→`STUDY_RUN_RETURNED`, and the assumption-set path emitting **no** A/E event — the
unchanged-path guard), the append-only **deleted-middle-row** tamper, and a **Phase-2
column-constant ↔ physical-schema lock** (so a future hash-chaining retrofit can't silently
hash the wrong column set). Documented design notes (not defects): (a) in `unified_audit_query`
SIGNOFF rows use the *stored* `actor_role` while AE_EVENT rows resolve role *live* from
`gold_users` (None for a non-governance actor, e.g. a free-text DQ-override id) — a display/filter
nicety, both PII-free (commented in `audit.py`); (b) `verify_chain` opens read-only so it is not
empty-safe on a nonexistent DB file (immaterial — `init_database` always precedes; the page wraps
each call); (c) single-writer `seq` assignment is acknowledged in the docstring (a `seq UNIQUE`
collision fails loudly rather than corrupting).

### Notes for Session 27 (Governance Reporting, Tenancy-Readiness & Phase 4 UAT)
- The audit read layer (`unified_audit_query`, `artifact_timeline`) and `verify_chain`
  are ready for the compliance pack (`export_compliance_pack`) and dashboard
  (`dashboard_data`) in `src/governance/reporting.py` (§H.8), and for the Phase-4 UAT's
  tamper-detection step.
- `record_ae_event` / `submit_study_run` exist if S27 wants an explicit study-run
  "submit for approval" affordance; `check_segregation` still treats the study-run
  author check as a no-op (the STUDY_RUN_SUBMITTED author is now recorded, so a future
  session can enforce proposer≠approver for study runs from it).
- `src/governance/readiness.py::check_tenancy_readiness` (§H.9) + the governance
  dashboard/compliance-pack pages remain the S27 deliverables.

---

## Session 27 — Governance Reporting, Tenancy-Readiness & Phase 4 UAT — COMPLETE ✅ (2026-07-01) — **Phase 4 build CLOSED**

**Goal delivered:** the "clear what's going on" governance surface + the defensible
export layer that close Phase 4, plus the tenancy-readiness conformance guard and the
Phase-4 UAT script. Strictly additive; governance stays ordinary application code
**outside `src/ai/`** (read-only parameterized DuckDB, reusing the existing
`autoescape=True` Jinja2 machinery, never the AI `sql_boundary`). No schema change (all
tables already exist), no Phase 1–3 behaviour change.

**Realises:** FR-4-23/24/25/26/27; NFR-G-06/G-07; the §8.8 completion checklist.
**Contracts:** Tech Spec §H.8 (reporting), §H.9 (readiness), §I.1/I.3.

### Owner decisions (2026-07-01)
- **Compliance pack = HTML now, PDF deferred** — reuse the existing Jinja2 machinery;
  `fmt='pdf'` is an honest `NotImplementedError` (no new dependency, no lockfile regen).
- **UI driven headlessly** — Claude Code authored the UAT script + tests + ran the
  offline gate to green *and* drove the dashboard page via `AppTest` + a live Streamlit
  boot smoke; the live click-through UAT + sign-off stays owner-triggered.

### Files added
| Path | What |
|------|------|
| `src/governance/reporting.py` | §H.8: `dashboard_data` (states by lineage/status + live-set-per-lineage via `lineage.resolve_live_set` + **global** pending queue via `workflow.next_required_level` + recent activity via `audit.unified_audit_query`), `export_compliance_pack` (APPROVED assumption set **or** fit study run → lineage + sign-offs/attestations + audit excerpt + per-change rationale via `lineage.compare_versions` + reproducibility stamp + report links; renders `compliance_pack.html.j2`; `fmt='pdf'`→`NotImplementedError`; non-approved→`ValueError`), `retention_policy` (FR-4-25 config block, no hard deletes). Defensive readers (`_read_assumption_set_rows`/`_study_run_artifact_ids`) tolerate a pre-Phase-4 DB. |
| `src/reporting/templates/compliance_pack.html.j2` | Six-section governance/compliance document (lineage, sign-offs+attestations, audit excerpt, rationale, reproducibility, supporting reports); numbers/identifiers only (autoescape-safe). |
| `src/governance/readiness.py` | §H.9: `check_tenancy_readiness` (returns `[]` on pass) + the reusable `_scan_governance_code` AST scanner (identifier/import-based, so docstrings mentioning tenant_id/RLS/SSO never self-flag). Checks: config-not-code presence, no `tenant_id` identifier / SSO import in `src/governance`, no `tenant_id` column in the DDL. |
| `ui/pages/27_governance_dashboard.py` | The dashboard page (FR-4-23): artifact-state metrics + tables, live-set-per-lineage, global pending queue, recent activity, and an `export`-gated compliance-pack export + download (FR-4-24), plus the retention footer (FR-4-25). Read-only; all four roles view. |
| `tests/governance/test_reporting.py` (13) · `test_readiness.py` (5) · `test_regression.py` (14) · `test_governance_dashboard_apptest.py` (3) | 35 new tests — full APPROVED-lineage + approved-study-run recipes through `record_signoff`; pack content asserts (lineage/attestation/rationale/repro/report links); non-approved + pdf + bad-fmt guards; readiness pass + negative self-tests; import-health + core-does-not-import-governance guard; unauthenticated + authenticated AppTest render + source-wiring guard. |

### Files modified
| Path | Change |
|------|--------|
| `ui/app.py` | Added `27_governance_dashboard.py` ("Governance Dashboard", 📊) to the "Governance & Audit" nav group. |

### Design decisions / fixes found during build
- **DuckDB connection-overlap (fixed).** The first cut held one read-only connection
  open across `compare_versions`, which opens its **own** connection → a swallowed
  conflict silently emptied the rationale section. Refactored every reporting helper to
  own a short-lived read-only connection and run sequentially — no two connections to
  the same file are ever open at once.
- **Live prod-DB predated Phase 4 (fixed + migrated).** `data/experience_study.duckdb`
  had **no** Phase-4 governance schema (no `gold_governance_signoffs`, no
  `parent_set_id`), so the dashboard hard-crashed. Two fixes: (a) `dashboard_data` now
  degrades gracefully on a pre-Phase-4 DB (column/table-tolerant defensive reads); (b)
  the live DB was **migrated additively** via idempotent `init_database` (data preserved:
  1 study run, 1 assumption set) — matching prior sessions' "re-init the live DB" note.
- **AppTest auth.** AppTest session-state injection didn't reliably reach
  `auth.current_user`; the authenticated render test patches `src.governance.auth.current_user`
  (which the page binds at import during `run()`), exercising the real dashboard body.

### Definition of done — all met
- [x] `dashboard_data` returns states / live-set-per-lineage / global pending / recent activity (FR-4-23); tolerant of a pre-Phase-4 DB.
- [x] `export_compliance_pack` assembles a correct HTML pack for an APPROVED assumption set (lineage + attestations + audit + rationale + reproducibility + report links) **and** an approved study run (FR-4-24); non-approved/not-fit → `ValueError`; `fmt='pdf'` → `NotImplementedError`.
- [x] `retention_policy` returns the config block; no hard deletes (FR-4-25).
- [x] `check_tenancy_readiness()` returns `[]` on the shipped tree; the scanner fires on planted `tenant_id`/SSO violations and ignores comment/string mentions (FR-4-26/27; NFR-G-06).
- [x] Governance Dashboard page live under the "Governance & Audit" nav group; `AppTest` render (unauth + authed) + live Streamlit boot smoke both clean; compliance-pack export spot-checked (six sections, 5.5 KB).
- [x] AI-architecture guards green; core engine does not import `src.governance`.
- [x] **Full regression gate GREEN — 1307 passed, 6 skipped, 0 failed** (= 1274 baseline + 33 Session-27 tests; no regressions).
- [x] Phase-4 UAT script authored (`docs/phase4_uat_script.md`) covering the §8.8 checklist + sign-off table.

### Verification commands
- Session-27 only: `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/governance/test_reporting.py tests/governance/test_readiness.py tests/governance/test_regression.py tests/governance/test_governance_dashboard_apptest.py -v` → 35 passed.
- Full gate: `unset … && .venv/bin/python -m pytest tests/ -q` → **1307 passed, 6 skipped, 0 failed**.
- UI smoke (owner-triggered): `streamlit run ui/app.py` → sign in; the "Governance Dashboard" page shows states / live set / pending / recent activity and exports a compliance pack for an APPROVED artifact.

### Post-build review (2026-07-01)
Two independent adversarial reviewers + self-audit over `reporting.py`, `readiness.py`,
the template, and the tests. **No governance-bypass or crash on the shipped tree**, but
several real quality/robustness fixes + **four scanner-evasion gaps** were closed (the
tenancy scanner is the *sole* automated enforcement of "no tenancy built", so its gaps
were the highest-value finds). **+18 tests (33 → 51); gate 1307 → 1325, no regressions.**

- **🔴 Rationale `dimension` rendered as a raw Python dict** in the compliance HTML
  (`compare_versions` returns `dimension` as a dict). Added `_fmt_dimension` → readable
  `product=TERM, gender=M, duration_band=1-5`. Locked by `test_pack_rationale_dimension_is_readable`.
- **🟡 `_rationale_rows` swallowed all diff errors as a reassuring "no changes"** — false in a
  compliance document. Now a diff failure (e.g. parent YAML unloadable) surfaces a
  "comparison unavailable" marker row instead. Locked by `test_pack_rationale_failure_marker`.
- **🟡 Global pending queue omitted submitted-but-unsigned study runs** — `_study_run_artifact_ids`
  read only `gold_governance_signoffs`; now unions `STUDY_RUN_SUBMITTED` events from
  `gold_ae_governance_events` (absence-tolerant), so a freshly-submitted run surfaces.
  Locked by `test_dashboard_pending_includes_submitted_unsigned_study_run`.
- **🟡 Duplicate TEV report links** (N runs → N identical references) collapsed to a single
  "TEV impact report (N run(s))" link. Locked by `test_supporting_reports_dedupes_tev_links`.
- **🟢 Orphan lineage-root** — `_root_from_parent_map` now stops at the deepest *known*
  ancestor (an orphan is its own root) rather than returning a phantom id, matching
  `lineage._root_of`.
- **🔴 (scanner) `check_tenancy_readiness` evasions closed:** the AST scan now also flags
  `from x import tenant_id` (ast.alias), `getattr(x,"tenant_id")`, `d["tenant_id"]`, and a
  dynamic `importlib.import_module("<sso>")`, and **reports (never crashes on) an unparseable
  governance file**. Locked by 7 new `test_readiness` cases (import-alias, attribute, getattr,
  subscript, dynamic-SSO, unparseable-file, DDL-`tenant_id`-column negative).
- **Coverage added:** empty-DB dashboard, an `approve_and_supersede` lineage (SUPERSEDED bucket
  + live-set = current version, FR-4-08/09), global pending spanning two roles, audit-excerpt +
  reproducibility-stamp content in the pack, a directly-approved root pack, a study-run pack has
  no lineage section, and retention `hard_delete: True` coercion.
- **Documented seam (not a defect):** `live_set_id` is resolved from the effective-date range,
  which `lineage.approve_and_supersede` sets — the Stage-4 chain (`record_signoff`) locks a set
  to APPROVED but does **not** set effective dates, so a purely chain-approved set shows
  `live_set_id = None` until a range is recorded. The dashboard faithfully reflects the stored
  data; wiring effective-dating into the Stage-4 approval is a workflow/lineage-integration
  decision for the owner (out of Session-27 scope). Noted in the `dashboard_data` docstring.

### Remaining to close Phase 4
- **Owner UAT** — execute `docs/phase4_uat_script.md` §1–7 through the UI and record the §8 sign-off (the offline portion is already ticked). That owner acceptance closes Phase 4.

---

## End-of-session review (2026-07-04) — audit + hardening of the session's work

After a long session (UAT Parts 4–6 fixes, the governance lifecycle UI, and the UI
restructure), a careful review was run: three independent audit agents (propose-enforcement
completeness; pages 28/29 + `governance_logic` correctness; documentation consistency), all
five UAT harnesses, and the full gate.

- **Correctness fix (found by the audit):** propose-enforcement on Stage 3 was **incomplete** —
  the two primary buttons (Run TEV, Submit for sign-off) were gated, but three secondary write
  actions were not: **Compute Credibility Envelope** (writes TEV runs + YAML + log row),
  **Generate Working Actuary Report** (writes HTML), and **Refine → return to Stage 2** (writes a
  log row). All three now carry `disabled=not _can_propose` + a server-side `require(_user,
  Action.PROPOSE)` re-check. Locked by a source-guard test
  (`test_propose_gate.test_every_stage3_write_button_is_propose_gated`). Stages 1–2 and pages 28/29
  audited clean (no defects); added defensive `except` guards on page 29's re-open/publish.
- **Harness robustness:** the live DB moved on (owner live-tested — run submitted+approved, v2
  DRAFT children), which broke three harnesses that assumed a pristine baseline. Fixed:
  `uat_section2.py` **rewritten** to the non-destructive temp-copy pattern (it was an older
  placeholder that lacked the `sys.path` insert, had unfilled `<placeholder>` ids, and targeted the
  **live DB** — now safe); `uat_section3_3_runner.py` + `uat_section5_6_runner.py` now reset the
  target run's governance state on their COPY for a deterministic precondition, and 5.6's
  "live untouched" check compares before/after instead of a stale `== 0`. **All five harnesses
  pass** (2: 6/6 · 3.3: 7/7 · 3.7: 5/5 · 4.4: 4/4 · 5.6: 8/8), live DB untouched.
- **Docs reconciled:** test count updated 1325→**1345** here and in `docs/phase4_uat_script.md`
  (P1, §7.1, sign-off row) + `CLAUDE.md`; UAT script Sections 2/3 now point at pages 28/29; the
  stale `ui/pages/` path in the tech spec corrected to `ui/views/`.

**Final gate: 1345 passed, 6 skipped, 0 failed.** No product defects outstanding; owner UI
walkthrough + §8 sign-off remain the only close. *(Point-in-time — superseded the same day by the
"Governance-output audit + remediation" section below, which took the gate to 1368.)*

---

## Governance-output audit + remediation (2026-07-04) — owner uploaded live workflow artifacts

The owner ran the full governance workflow (assumption-set chain approval + study-run sign-off +
compliance-pack export) and uploaded the outputs (TEV iteration log, unified audit-stream exports,
version/live-set exports, compliance-pack HTML) for a suspicious review. The **figures were sound**
(TEV `$173,388,673` = baseline; hash-chained sign-off log trustworthy; distinct-signer rule works),
but a code-level audit (6 Explore/adversarial agents across two rounds) found **six issues + two
same-class holes found only on adversarial re-review**. All fixed within the governed spine; no spec
change (the fixes realise existing FR-4-03/05/24). **Gate: 1345 → 1368 passed, 6 skipped** (+23 tests).

### Round 1 — the six findings
1. **🔴 Segregation of duties silently non-functional for assumption sets (FR-4-05).**
   `gold_assumption_sets.author_id` came from a free-text Stage-1 box defaulting to `"ACTUARY_1"`;
   `check_segregation` (`src/governance/workflow.py:247`) compares it to the signer's authenticated
   `username`/`user_id`, so the placeholder never matched and proposer≠approver never fired. **Fix:**
   the TEV stages now capture `current_user()` — Stage 1/2 read-only `author_id = _user.username`,
   Stage 3 falls back to `_user.username`, Stage 4's proposer = the set's stored `author_id`
   (`ui/views/20-23_tev_stage*.py`). The study-run path was already correct.
2. **🔴 An APPROVED (locked) set could be silently unlocked.** `transition_assumption_set_status`
   (`src/tev/workflow.py`) was an unconditional UPDATE. **Fix:** a from-state guard raises
   `LockedStatusTransition` on any move away from APPROVED except SUPERSEDED; the Stage-3 submit
   button is disabled once `STAGE3_APPROVED`/`APPROVED` (also fixes #6 re-submission noise).
3. **🟠 APPROVED but not effective-dated / not live** → compliance-pack now renders a "**Not yet
   effective**" warning banner (owner chose *tighten→warn*).
4. **🟠 Compliance pack didn't check its source study run's governance state** → now computes
   `is_study_run_fit(source_study_run_id)` and warns (or hard-refuses when
   `compliance.require_fit_source_run: true`, new `config/governance_config.yaml` key).
5. **🟡 Inconsistent actor identity across the three logs** → `unified_audit_query`
   (`src/governance/audit.py`) gained a username→display map (`resolve_named`) so the legacy
   APPROVAL `reviewer_id` and the WORKFLOW `actuary_id` resolve to the same display name + user_id +
   role the sign-off log shows. `artifact_timeline` inherits this (delegates to `unified_audit_query`).
6. **🟡 Re-submissions cluttered the trail** → covered by the Stage-3 button guard in #2.

### Round 2 — same-class holes found on adversarial re-review (the "something slipped through" ones)
- **🔴 `save_assumption_set` bypassed the #2 guard entirely.** `_insert_assumption_set_metadata`
  (`src/tev/assumption_set.py`) writes `status` unconditionally, and the Stage-2 editor forces
  `status=PROPOSED` before saving — so loading an APPROVED set in Stage 2 and clicking Save silently
  unlocked it. **Fix:** the same lock guard now lives inside `_insert_assumption_set_metadata`
  (refuses to re-save an existing APPROVED row with a non-terminal status → `LockedStatusTransition`),
  and Stage 2 shows a 🔒 banner + disables the editor/save for APPROVED sets (UI + server-side).
- **🔴 DQ quarantine override still self-asserted identity.** `ui/views/02_data_quality.py` wrote a
  free-text `override_actuary_id` (defaulting to `"actuary-1"`). **Fix:** bound to `current_user()`
  (read-only field) — same bug class as #1.
- **🟡 Stage-1 resume misattributed the proposer.** Resuming another user's set overwrote
  `workflow_author_id` with the current user (display/legacy-summary only — segregation was safe as
  it reads the DB author). **Fix:** resume now preserves the resumed set's stored `author_id`.

### Not fixed in code — recorded for the owner
- **Migration:** any assumption set created BEFORE this fix carries `author_id="ACTUARY_1"` (or
  similar) and therefore still **fails open** for proposer≠approver (the check can't match a
  placeholder). Such legacy sets must be remapped to the real author (or re-proposed). Characterised
  by `tests/governance/test_segregation.py::test_real_username_author_is_blocked_but_placeholder_author_fails_open`.
- **Defense-in-depth:** `create_assumption_set(_from_ae_run)` accepts `author_id` as an unvalidated
  string (no `gold_users` cross-check); the invariant relies on the UI passing `current_user()`.
  Tightening the engine was deliberately deferred (many tests + legitimate flows pass arbitrary
  author strings; would be a wider change).
- **Minor (accepted):** the "not yet effective" banner tests presence of an effective range, not
  whether *today* is within it — a fully-dated but future/stale range won't warn. The dominant real
  case (chain-approved, never published → both dates NULL) is covered.

### Tests added (+23)
`tests/governance/test_identity_capture.py` (new: author capture across stages, Stage-2/3 guards, DQ
override, resume author, audit-reader resolution), `tests/governance/test_locked_set_immutability.py`
(new: save-path lock, idempotent re-save, happy path, mid-chain RETURN, RETURN-after-complete),
`tests/governance/test_segregation.py` (+1 characterisation), `tests/governance/test_reporting.py`
(+6 banner/gating incl. study-run-no-banner), `tests/test_workflow.py` (+2 transition guard).

**Gate after remediation: 1368 passed, 6 skipped, 0 failed.**
