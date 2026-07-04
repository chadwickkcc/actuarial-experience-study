# Phase 4 — Claude Code Session Prompts (Sessions 23–27)

**Companion documents:** Requirements Specification v4.0 (§8, FR-4-01–27) · Technical Specification v3.0 (§G/H/I)
**Date:** 2026-06-28
**Status:** LOCKED / signed off 2026-06-28 — for build. Run one session at a time, in order.
**Scope anchor:** `phase4_locked_scope.md`.

This document is the executable plan for Phase 4 (Governance). Each session below is a self-contained prompt for Claude Code. Prompts **reference** spec FRs and tech-spec contracts; they do not restate them — open both specs alongside Claude Code and paste one session block at a time. Do not start a session until the previous session's regression gate is green.

---

## Standard Session Protocol (applies to every session)

Every session prompt below assumes the following. State it once to Claude Code, or prepend it to each block.

1. **Read first.** Open Requirements v4.0 §8 (the FR-4 series and the §8.8 checklist) and Technical v3.0 §G (schemas), §H (contracts), §I (config/tests). Implement to the contracts exactly — do not invent table names, columns, or return shapes.
2. **Additive only.** Phase 4 must not alter any Phase 1–3 calculation, schema, or behaviour. New tables and additive `ALTER TABLE`s only (§G). The full Phase 1–3 regression suite must stay green.
3. **Governing principles.** Governance records *who* acted, *with what authority*, *on what basis*, immutably. **Prototype simplicity first** — build the simplest thing that satisfies the FR; do not add capability beyond the spec. Governance is ordinary application code: **no new Claude Skills or MCP servers**, and governance writes use the standard parameterized write path (never the AI read-only `sql_boundary`).
4. **Config not code.** Every governance constant (roles, chain, thresholds, attestation text, retention, users) comes from `config/governance_config.yaml` (§I.1). Nothing hard-coded (FR-4-27).
5. **Tests are part of the deliverable.** Implement the matching §I.3 test file(s) for the session, each assertion falsifiable. 
6. **Regression gate (exit).** Before declaring the session done: run the full suite (Phase 1–3 + all Phase 4 tests to date) via `.venv/bin/python -m pytest` with no API keys set; it must be green. Record the pass count.

**Environment:** macOS; activate the project venv (`source .venv/bin/activate`) before any command. The app runs via `streamlit run ui/app.py`.

**Owner checkpoints:** two STOP points requiring owner input — **Session 23** (real user identities + bootstrap passwords) and **Session 25** (materiality threshold value, attestation text, chain confirmation). **Session 27** ends with owner UAT sign-off.

---

## Session 23 — Identity & Access Foundation

**Goal:** give the tool a real identity layer and wire it into every governed action. Lands first: lowest-risk, and the approval workflow (S25) depends on it.

**Realises:** FR-4-01 to FR-4-06; NFR-G-01/G-02/G-03.

**Build (to the §H contracts):**
- DDL: `gold_users` (§G.1) into `src/utils/db_init.py` (users first; see §G intro ordering).
- `src/governance/auth.py` (§H.2): salted `hash_password`/`verify_password`, `authenticate`, `current_user`, and the `login_gate()` mounted ahead of every page in `ui/app.py`. No SSO, no reset flow; nothing reachable pre-auth.
- `src/governance/users.py` (§H.3): `seed_users_from_config` (idempotent; hashes bootstrap passwords, discards plaintext), getters.
- `src/governance/rbac.py` (§H.4): permission matrix from config; `require()` enforced **server-side**, every denial logged; `may_sign_off_at`.
- Wire session identity as the actor for governed writes (FR-4-03); new tables use `actor_user_id`, legacy free-text actor fields receive the session `username` going forward (see §H.6 actor-capture note).
- Shared types (§H.1: `Role`, `User`, `Action`).

**Acceptance (`tests/governance/test_auth.py`, `test_rbac.py`):** no plaintext password stored/logged; pre-auth access blocked; `require()` raises + logs on a disallowed action invoked directly (bypassing the UI); `may_sign_off_at` matches role to level.

**⛔ OWNER CHECKPOINT 1 — user identities.** Before seeding, the owner supplies the real user list (display names + the four roles) and bootstrap passwords to replace the `<set at first run>` placeholders in `governance_config.yaml` (§I.1/§I.2). Do not commit real passwords; seed from a local, git-ignored config.

**Exit:** Standard regression gate green.

---

## Session 24 — Versioning & Lineage

**Goal:** assumption-set version lineage, supersession, effective-dating, comparison, and reproducibility lineage.

**Realises:** FR-4-07 to FR-4-11; NFR-G-05.

**Build:**
- DDL: additive columns on `gold_assumption_sets` (§G.4: `parent_set_id`, `effective_from`, `effective_to`); keep `effective_date` set at create as today.
- `src/governance/lineage.py` (§H.5): `create_version`, `lineage_root`, `approve_and_supersede` (enforce ≤1 APPROVED-current per lineage and non-overlapping effective ranges — raise on overlap), `resolve_live_set`, `compare_versions` (reuse FR-2-46/47 ΔTEV machinery), `reproducibility_stamp`.

**Acceptance (`test_lineage.py`):** parent→child links recorded; status transitions DRAFT→PROPOSED→APPROVED→SUPERSEDED; a constructed overlapping effective range is rejected; `resolve_live_set` returns the set whose range contains the date; comparison shows changed cells + ΔTEV + rationale.

**Exit:** Standard regression gate green.

---

## Session 25 — Configurable Approval Workflow

**Goal:** generalise the single Stage-4 reviewer into the configurable multi-level chain; extend approval to A/E study runs; attestation, materiality rule, pending queue, governed re-open.

**Realises:** FR-4-12 to FR-4-18; NFR-G-03/G-08.

**Build:**
- DDL: `gold_governance_signoffs` (§G.2, hash-chained) into `db_init.py`.
- `src/governance/workflow.py` (§H.6): `load_chain`, `required_final_level` (reads `materiality.delta_tev_threshold` and `final_level_below_threshold`; study run → full chain), `next_required_level`, `check_segregation` (proposer ≠ approver absolute; distinct users per level unless `allow_multi_level_signoff`), `record_signoff` (validate role-for-level + segregation + order; hash-chained write via §H.7 `append_event`; on completing APPROVE lock the artifact and write the legacy `gold_assumption_approvals` summary; on RETURN reset to editable), `reopen`, `pending_approvals`.
- Also implement `src/governance/audit.py::append_event` (the write + hash-chain half, §H.7/§G.2) — `record_signoff` depends on it. The rest of `audit.py` (verify_chain, unified read) lands in S26.
- Refactor the Phase-2 single-reviewer Stage-4 sign-off (FR-2-42/43; the four-stage workflow is RS §6.9/FR-2-34) to consume the configured chain; the four-stage shell is retained. A single-`chief_actuary` chain must reproduce the legacy single-reviewer behaviour.
- A/E study-run submission + sign-off through the same chain (FR-4-14); un-approved runs flagged "not yet fit for assumption-setting."

**Acceptance (`test_workflow.py`, `test_segregation.py`):** a user cannot approve their own proposal at any level; with `allow_multi_level_signoff: false` one user cannot sign two levels; sequential order enforced; RETURN resets to editable; |ΔTEV| above the threshold forces chief_actuary, below it completes at `final_level_below_threshold`; a study run runs the full chain; `reopen` creates a DRAFT child and never mutates the original; a single-`chief_actuary` chain reproduces the legacy sign-off.

**⛔ OWNER CHECKPOINT 2 — governance config values.** The owner confirms/sets the chain (default junior → senior → chief), the **materiality threshold** (`delta_tev_threshold` — the default 0.01 is a placeholder), `final_level_below_threshold`, and the **attestation text** in `governance_config.yaml`.

**Exit:** Standard regression gate green.

---

## Session 26 — Audit Trail & Tamper-Evidence

**Goal:** A/E governance events on the existing pattern; append-only hash-chaining; integrity verifier; unified audit read layer.

**Realises:** FR-4-19 to FR-4-22; NFR-G-04.

**Build:**
- DDL: `gold_ae_governance_events` (§G.3, hash-chained); additive hash-chain columns on `gold_workflow_iterations`/`gold_assumption_approvals` (§G.5).
- `src/governance/audit.py` (§H.7) — complete the module (`append_event` already built in S25): `verify_chain` (recompute; report first divergence), `unified_audit_query` + `artifact_timeline` (read across all three logs into one common event shape; the AI audit log is read via its D.3 scheme).
- A "Governance & Audit" Streamlit page exposing the unified filterable stream + per-artifact timeline.

**Acceptance (`test_audit_integrity.py`):** `verify_chain` passes on an untouched log and fails on a constructed tampered entry with the correct `first_divergence_seq`; `unified_audit_query`/`artifact_timeline` span all three logs.

**Exit:** Standard regression gate green.

---

## Session 27 — Governance Reporting, Tenancy-Readiness & Phase 4 UAT

**Goal:** the governance dashboard, the exportable compliance pack, retention policy, the tenancy-readiness conformance check, and Phase 4 UAT — closes Phase 4.

**Realises:** FR-4-23 to FR-4-27; NFR-G-06/G-07; the §8.8 completion checklist.

**Build:**
- `src/governance/reporting.py` (§H.8): `dashboard_data` (states, live set per lineage, pending approvals, recent activity; meet NFR-G-07 timing), `export_compliance_pack` (HTML/PDF via the existing Jinja2 machinery, `autoescape=True`; for an APPROVED assumption set **or** approved study run — lineage + attestations + audit excerpt + rationale + report links), `retention_policy` (no hard deletes).
- Streamlit governance dashboard page (the "clear what's going on" surface).
- `src/governance/readiness.py` (§H.9): `check_tenancy_readiness` (no hard-coded single-org blockers; no `tenant_id`/RLS/SSO present; tables additively tenant-retrofittable) wired as a pytest assertion.

**Acceptance (`test_reporting.py`, `test_readiness.py`, `test_regression.py`):** compliance pack assembles correctly for an approved assumption set and an approved study run; `check_tenancy_readiness` returns no violations; full Phase 1–3 suite green.

**Phase 4 UAT:** execute the §8.8 completion checklist end-to-end through the UI (login as each role; run a full propose → junior → senior → chief chain with attestations; force a chief-required case via a material ΔTEV; submit + approve an A/E study run; attempt a self-approval and a wrong-level sign-off and confirm both are blocked; tamper a log row and confirm `verify_chain` catches it; export a compliance pack; confirm no `tenant_id`/auth bypass). Retain evidence.

**⛔ Phase 4 close:** owner UAT sign-off recorded. Phase 4 complete.

---

## Coverage map (sessions → FRs)

| Session | FRs | Tech-spec contracts |
|---|---|---|
| 23 | FR-4-01–06 | G.1; H.1/H.2/H.3/H.4 |
| 24 | FR-4-07–11 | G.4; H.5 |
| 25 | FR-4-12–18 | G.2; H.6 (+ H.7 append) |
| 26 | FR-4-19–22 | G.3, G.5; H.7 |
| 27 | FR-4-23–27 | H.8, H.9; I.1/I.2/I.3 |

All 27 Phase-4 FRs are covered; each session carries the Standard Session Protocol and exits on a green regression gate. Owner checkpoints: S23 (identities/passwords), S25 (chain/threshold/attestation), S27 (UAT sign-off).
