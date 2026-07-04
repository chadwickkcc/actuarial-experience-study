# Phase 4 (Governance) — UAT Script

**Purpose:** the owner-executed acceptance script that closes Phase 4. It walks the
§8.8 completion checklist (Requirements v4.0) end-to-end through the Streamlit UI,
with numeric/behavioural targets and a sign-off table. Follows the established
Phase-3 UAT-script format (`docs/phase3_uat_script.md`).

**Companion docs:** Requirements v4.0 §8 (FR-4-01..27, §8.8 checklist), Technical
Spec v3.0 §G/H/I, `docs/phase4_build_progress.md` (build log, Sessions 23–27).

**Status:** Build complete (Sessions 23–27) + post-S27 governance UI (2026-07-04): the
study-run approval, versioning/lineage and re-open capabilities now have dedicated Streamlit
pages (28 Study Run Sign-Off, 29 Versioning & Lineage), the sidebar is regrouped into 6
numbered workflow sections, and `propose` is enforced on TEV Stages 1–3. Offline regression
gate **1368 passed, 6 skipped, 0 failed** (no API keys). The manual UI walkthrough + sign-off
below is **owner-triggered** (like the Phase-3 UAT); record the result in the sign-off table.

---

## 0. Pre-flight (must pass before manual testing begins)

| # | Check | Command / action | Target |
|---|-------|------------------|--------|
| P1 | Full regression gate green | `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -q` | 1368 passed, 6 skipped, 0 failed |
| P2 | Governance-only suite green | `.venv/bin/python -m pytest tests/governance/ -q` | all pass |
| P3 | Tenancy-readiness clean | `.venv/bin/python -c "from src.governance.readiness import check_tenancy_readiness as c; print(c())"` | `[]` |
| P4 | Live DB carries Phase-4 schema | `init_database(str(DB_PATH))` was run (idempotent, additive) | `gold_governance_signoffs`, `gold_ae_governance_events`, `gold_assumption_sets.parent_set_id` present |
| P5 | App boots | `streamlit run ui/app.py` | login gate precedes all pages; no startup error |
| P6 | Seed real credentials | supply real passwords via git-ignored `config/governance_config.local.yaml`; re-seed | the four §I.2 users sign in |

> **Note (live DB):** the production `data/experience_study.duckdb` predated Phase 4
> and was migrated additively via `init_database` during the Session-27 build (data
> preserved: 1 study run, 1 assumption set). Re-run `init_database` on any DB that
> predates Phase 4 before UAT.

---

## 1. Identity & Access (FR-4-01/02/03/04/05/06)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 1.1 | Login gate blocks pre-auth | Open the app signed out | Every page blocked; login form shown; passwords never echoed |
| 1.2 | Each role signs in | Log in as `a.analyst`, `j.junior`, `s.senior`, `c.chief` in turn | Sidebar shows the signed-in user + "Sign out" |
| 1.3 | RBAC server-side | As `a.analyst`, attempt a sign-off action | Blocked (analyst lacks `sign_off`); denial logged |
| 1.4 | Session identity = actor | Perform any governed action | Recorded actor is the session user, not free text |
| 1.5 | No self-service account mgmt | Look for account/role editing UI | None exists (seed-from-config only) |

---

## 2. Versioning & Lineage (FR-4-07..11) — Versioning & Lineage page (29)

> These now have a dedicated UI: the **Versioning & Lineage** page (29) — Re-open (2.1),
> Publish/set-effective + supersede (2.2/2.3/2.4), Compare (2.5). The engine harness
> `scripts/uat_section2.py` (non-destructive) asserts all six on real data.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 2.1 | Create version lineage | On page 29, Re-open an approved set → new DRAFT child | Parent→child link recorded; child version = parent+1 |
| 2.2 | Supersession | Approve the child | Prior APPROVED set → SUPERSEDED; ≤1 APPROVED-current per lineage |
| 2.3 | Effective dating | Approve with an `effective_from`/`effective_to` range | Live-set resolver returns the set whose range contains today |
| 2.4 | Non-overlapping ranges | Attempt to approve a set producing an overlap | Rejected with a clear message (no partial write) |
| 2.5 | Cross-version comparison | Compare two versions | Changed cells + ΔTEV + per-cell rationale shown |
| 2.6 | Reproducibility | Inspect an APPROVED set | Traces to source study run + AI model + data-snapshot hash |

---

## 3. Configurable Approval Workflow (FR-4-05/12..18) — Stage 4 page + Study Run Sign-Off page (28)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 3.1 | Full chain, material ΔTEV | Propose (analyst) → sign junior → senior → chief, ΔTEV above threshold | Chief required; sequential order enforced; each level captures actor/role/level/decision/comment/attestation |
| 3.2 | Below-threshold completion | Sign a set with ΔTEV below `delta_tev_threshold` | Chain completes at `final_level_below_threshold` (senior) |
| 3.3 | A/E study-run approval | On the **Study Run Sign-Off** page (28): submit a run (analyst), then sign junior→senior→chief | Un-approved run flagged "not yet fit"; after all-APPROVE → "fit for assumption-setting" |
| 3.4 | Self-approval blocked | Sign an artifact you authored | `SegregationViolation` — blocked at every level |
| 3.5 | Wrong-level blocked | Sign a level your role does not occupy | Blocked server-side; not offered in UI |
| 3.6 | RETURN resets | RETURN with a mandatory comment | Artifact returns to editable (PROPOSED) state |
| 3.7 | Governed re-open | On page 29 (Versioning & Lineage), Re-open an APPROVED set with justification | New DRAFT child created; original immutable |
| 3.8 | Legacy single-reviewer parity | Configure a single-`chief_actuary` chain | Reproduces the legacy Phase-2 single-reviewer sign-off + writes `gold_assumption_approvals` |

---

## 4. Audit Trail & Tamper-Evidence (FR-4-19..22) — Governance & Audit page (26)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 4.1 | Unified stream | Open "Audit & Integrity"; filter by actor/role/artifact/date/action | One stream across the three separate logs; filters narrow correctly |
| 4.2 | Per-artifact timeline | Enter an artifact type + id | Chronological history for that artifact |
| 4.3 | Integrity — clean | Click "Verify integrity" on untouched logs | All chains report intact ✓ (rows checked > 0 where hashed) |
| 4.4 | Integrity — tampered | Manually edit a governance row's business column, re-verify | TAMPER DETECTED ✗ at the correct `first_divergence_seq` |
| 4.5 | Append-only | Confirm no update/delete path in the UI | Logs are append-only |

---

## 5. Governance Reporting (FR-4-23/24/25) — Governance Dashboard page (27)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 5.1 | States surface | Open "Governance Dashboard" | Every assumption set + study run by state (DRAFT/PROPOSED/STAGE3_APPROVED/APPROVED/SUPERSEDED) |
| 5.2 | Live set per lineage | Read the live-set panel | The APPROVED set live today per lineage |
| 5.3 | Pending queue | Read the pending-approvals panel | Every artifact awaiting sign-off + its next required role |
| 5.4 | Recent activity | Read the recent-activity panel | Latest governance events, newest first |
| 5.5 | Compliance pack — set | Export a compliance pack for an APPROVED assumption set | HTML with lineage + sign-offs/attestations + audit excerpt + rationale + reproducibility + report links |
| 5.6 | Compliance pack — run | Export for an approved (fit) study run | HTML with sign-offs/attestations + audit excerpt + report links |
| 5.7 | Export guard | Attempt export on a non-APPROVED / not-fit artifact | Refused (`ValueError`) with a clear message |
| 5.8 | PDF deferred | Request `fmt='pdf'` | Honest `NotImplementedError` (HTML only this phase) |
| 5.9 | Export permission | As a role lacking `export` | Export UI hidden / refused |
| 5.10 | Retention | Read the retention footer | Hard deletes disabled; archive-after-days shown (no hard deletes, FR-4-25) |

---

## 6. Tenancy Readiness (FR-4-26/27; NFR-G-06)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 6.1 | Conformance pass | `check_tenancy_readiness()` | `[]` — no hard-coded single-org blockers |
| 6.2 | Nothing tenant built | Confirm | No `tenant_id`, no RLS, no SSO anywhere in `src/governance` |
| 6.3 | Additive-retrofit shape | Confirm | A future `tenant_id` column is purely additive; constants live in `governance_config.yaml` |

---

## 7. Backwards Compatibility (NFR-G-08)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 7.1 | Phases 1–3 unchanged | Full regression gate | Green (1368 passed, 6 skipped) — calc/exposure/A/E/TEV/AI behaviour unchanged |
| 7.2 | One-way boundary | `test_regression.py::test_core_engine_does_not_import_governance` | Core engine does not import `src.governance` |

---

## 8. Phase 4 Sign-Off

| Item | Result | Evidence | Date | Signed |
|------|--------|----------|------|--------|
| §8.8 completion checklist executed end-to-end | ☐ | screenshots / log extracts | | |
| Full propose→junior→senior→chief chain with attestations | ☐ | | | |
| Chief-required (material ΔTEV) case forced | ☐ | | | |
| A/E study run submitted + approved "fit" | ☐ | | | |
| Self-approval + wrong-level both blocked | ☐ | | | |
| Log tampered → `verify_chain` caught it (correct seq) | ☐ | | | |
| Compliance pack exported (set + study run) | ☐ | | | |
| Tenancy-readiness returns no violations | ☐ | | | |
| Full Phase 1–3 regression green | ☑ | 1368 passed, 6 skipped (offline gate) | 2026-07-04 | Claude Code |
| **Owner acceptance — Phase 4 CLOSED** | ☐ | | | |

*Offline portion recorded by Claude Code, 2026-07-01. The manual UI walkthrough and
owner acceptance sign-off close Phase 4.*
