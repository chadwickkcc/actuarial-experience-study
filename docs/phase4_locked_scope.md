# Phase 4 — Locked Scope (Governance)

**Date:** 2026-06-28
**Project:** AI-assisted actuarial experience study platform — Phase 4 (Governance)
**Working mode:** Doc co-authoring (explore → spec → reader-test → executable plan)
**Status:** LOCKED / signed off 2026-06-28. All §7 questions resolved; materiality corrected at QA. Anchors Requirements v4.0, Technical v3.0, and the Sessions 23–27 prompts.

---

## 1. Purpose

This is the locked scope that anchors Phase 4, exactly as the Phase 3 locked scope
anchored the AI layer. Everything downstream (Requirements Spec v4.0, Technical Spec
v3.0, Sessions 23+ prompts) must trace to this document. Nothing is built that is not
listed under §4 (In Scope); everything under §5 (Out of Scope) is excluded by decision,
with rationale, for the audit trail.

---

## 2. Governing principles

1. **The AI proposes and explains; the actuary decides.** Unchanged from Phases 1–3.
   No artifact reaches an approved state except through an explicit human action with a
   recorded, attributable justification.
2. **Prototype simplicity first (NEW — overarching).** This is a prototype. Governance,
   and the tool as a whole, must be easy to use, intuitive, and make it clear what is
   happening at every step. No capability is added beyond what the governance story
   needs. Where two designs satisfy the requirement, the simpler one wins. This principle
   has veto power over scope creep and is the tie-breaker throughout.

---

## 3. Scope summary

Phase 4 turns three governance fragments that already exist in pieces — the Phase 2
four-stage TEV workflow (`gold_workflow_iterations`), the Phase 2 immutable approval
record (`gold_assumption_approvals`), and the Phase 3 AI audit log (`gold_ai_audit_log`)
— into one coherent, attributable, single-org governance layer spanning A/E, TEV, and AI.
It adds the identity foundation those fragments currently lack, generalises the approval
workflow, unifies how audit is *viewed* (not how it is stored), and produces defensible
governance reporting. Multi-tenancy is **not** built; a near-zero-cost "tenancy-readiness"
lens keeps a future retrofit additive.

---

## 4. In scope

Capabilities are grouped by pillar. `(exists)` = extends something already built;
`(prereq)` = a foundation other items depend on. Each becomes one or more FR-4-xx
requirements in the spec.

### A. Identity & access — foundations
- **A1 (prereq)** Lightweight user & role model: named users, each holding one of four
  roles — **analyst** (doer/proposer), **junior actuary** (checker), **senior actuary**
  (reviewer), **chief actuary** (final approver). Replaces today's free-text actor IDs
  (`proposer_id`, `reviewer_id`, `actuary_id`) with real, referencable identities. No
  dedicated read-only/auditor role — all four roles can view the governance and audit
  surfaces; roles differ only in who may propose and who may sign off.
- **A2 (prereq)** Light authentication gate for single-org use: a **minimal username +
  password gate** backed by the users table, so the acting user is captured from the
  session rather than typed in. **No SSO, no email/password-reset flows.** A password
  (not a bare user-picker) is required so that attribution and segregation of duties are
  meaningful.
- **A3 (prereq)** Role-based permissions + segregation-of-duties enforcement:
  generalises Phase 2's "reviewer must differ from proposer" rule to every level of the
  approval chain (no user may approve their own proposal at any level).

### B. Versioning & lineage (§8.4.1)
- **B1 (exists)** Unified assumption-set version lineage: parent→child chains, explicit
  supersession, and effective-dating via a simple **`effective_from` / `effective_to`
  date range** per approved set (which set is "live" for a period).
- **B2 (exists)** Cross-version comparison/diff: what changed between two versions, the
  ΔTEV, and the rationale — extends the existing ΔTEV-vs-prior readout.
- **B3** Lineage pointers extended so every approved assumption set traces to the exact
  study run + AI model version + data snapshot that produced it (reproducibility).

### C. Configurable approval workflow (§8.4.2)
- **C1 (exists)** Configurable multi-level sign-off chain defined in YAML (roles/levels),
  generalising the hard-coded four-stage TEV flow. Default chain: **junior actuary →
  senior actuary → chief actuary**, with the **analyst** as proposer (not a sign-off
  level).
- **C2 (Decision 2 — extend)** Formal approval extended beyond TEV to the A/E side. The
  approval object is a **study run**, signed off as **"fit for assumption-setting,"**
  distinct from the TEV assumption-set approval. It runs through the same C1 chain.
- **C3** E-signature / attestation capture at each level: name, role, timestamp,
  decision, comment, and an attestation statement.
- **C4 (simplified)** A materiality-threshold → required-approval-level rule: ΔTEV above
  a **configurable governance materiality threshold** (a new threshold in
  `governance_config.yaml`, applied to ΔTEV vs the prior approved set) forces
  chief-actuary sign-off. Plus a **"my pending approvals"** queue. Time-based escalation /
  overdue chasing is **cut** (it needs notifications, which are out — see §5).
- **C5** Governed re-open / supersede: un-locking an APPROVED set spawns a **new version**
  with mandatory justification, never mutates the original.

### D. Immutable audit trail (§8.4.3) — lighter build (Decision 3)
- **D1 (exists)** A/E governance events recorded using the **existing per-module logging
  pattern** (not a physical canonical-log migration). The three logs stay separate on disk.
- **D2 (exists)** Tamper-evidence extended to the A/E and TEV governance events
  (hash-chaining, as the AI log already does), plus an integrity-verification routine.
- **D3 (usability)** A **unified audit *read* layer**: one inspection page that queries
  across all three logs — filter by actor / artifact / date / action — with a per-artifact
  history timeline. Feels unified to the user without the invasive schema migration.

### E. Governance reporting & compliance (ties to ASOP 41)
- **E1** Governance dashboard: state of every assumption set (draft / proposed / approved
  / superseded), pending approvals, recent activity, who approved what — the "clear what's
  going on" surface.
- **E2** Exportable governance / compliance pack (HTML/PDF, reusing the existing Jinja2
  report machinery with `autoescape=True`): for a given approved set — full lineage +
  signatures + audit excerpt + rationale + supporting reports, as one defensible artifact.
- **E3** Retention & immutability policy: no hard deletes; archival rules stated.

### F. Tenancy-readiness lens (Phase 5 fold-in — near-zero cost)
- **F1** Shape the new identity, versioning, and audit tables so a future `tenant_id`
  column is an **additive** retrofit, and keep org-specific values in config not code
  (reinforces the existing "configuration over customisation" principle). No `tenant_id`,
  no row-level security, no SSO is built. A short conformance check confirms no new code
  hard-codes single-org assumptions in a way that would block a later retrofit.

---

## 5. Out of scope (excluded by decision, with rationale)

- **Multi-tenancy as a build phase** (tenant_id on all tables, row-level security, SSO,
  data residency, entitlements). Rationale: primary goal is solid governance for a
  *single-org* tool; multi-tenancy is a separate "make it a SaaS product" effort. Retained
  as a documented **Phase 5 outline** only; readiness preserved cheaply via F1.
- **Notifications** (in-app or email handoff/approval alerts). Rationale: overkill for a
  single-org prototype; owner cut.
- **Time-based escalation / overdue chasing.** Rationale: depends on notifications (cut)
  and adds operational complexity against the simplicity principle.
- **Full canonical audit-log unification** (one physical event table replacing the three).
  Rationale: Decision 3 — lighter option; D3 delivers the unified *view* instead.
- **SSO / IdP integration, password-reset / account-management flows.** Rationale:
  single-org prototype; light gate only.
- **The pre-existing AI backlog** (anomaly detection, tiered narratives, survival models,
  macro-covariate models, agentic orchestration, doc/regulatory copilot). Rationale:
  out since Phase 3 scoping; Phase 4 does not pull any of it forward.

---

## 6. Current-state grounding (what Phase 4 builds on)

- **No identity layer today.** Actors are free-text `VARCHAR(50)` fields; the Streamlit
  app has no authentication (security review S-3, deferred as a deployment gate); RBAC
  exists only in the aspirational multi-tenant blueprint. A1–A3 are therefore genuinely
  new foundations, and everything in C depends on them.
- **Three governance fragments exist and are extended, not replaced:**
  `gold_workflow_iterations` (TEV 4-stage flow), `gold_assumption_approvals` (immutable
  on APPROVE), `gold_ai_audit_log` (per-turn AI audit, already hash-based).
- **Reusable machinery:** Jinja2 HTML reporting (now `autoescape=True`), the immutable-
  on-approve pattern, and the proposer≠reviewer rule (to be generalised by A3).

---

## 7. Resolved decisions (2026-06-28)

Recorded for the audit trail; all folded into §4.

1. **Login mechanism (A2).** Minimal username + password gate backed by the users table.
   No SSO, no reset flows. (A bare user-picker was rejected — it would let anyone
   impersonate the chief actuary and hollow out segregation of duties.)
2. **A/E approval object (C2).** Approval attaches to a **study run**, signed off as "fit
   for assumption-setting."
3. **Role list & chain.** Role set trimmed to **four**: analyst (doer), junior actuary
   (checker), senior actuary (reviewer), chief actuary. Peer-reviewer and auditor/
   read-only roles dropped. Sign-off chain: **junior actuary → senior actuary → chief
   actuary**; the analyst is the proposer.
4. **Materiality threshold (C4).** A new configurable governance threshold in
   `governance_config.yaml`, applied to ΔTEV vs the prior approved set. *(Reader-test
   correction, 2026-06-28: the original "reuse the existing TEV ΔTEV materiality floor"
   decision rested on a floor that does not exist — the only pre-existing "materiality
   floor" is the §6.8 envelope-width floor, a different concept — so a new threshold is
   defined, preserving the ΔTEV-based, single-source, configurable intent. Owner confirmed.)*
5. **Effective-dating (B1).** Simple `effective_from` / `effective_to` date range per
   approved set.

---

## 8. Documents, FR series, sessions, versioning

Mirrors the proven Phase 3 pipeline and the project's full-replacement versioning.

| Step | Output | Plan |
|---|---|---|
| 2 — Requirements Spec | `experience_study_requirements_spec_v4_0.md` | Full replacement → **v4.0**. Replace §8 outline with full **FR-4-xx** requirements + Phase 4 completion checklist; renumber later sections; correct cross-refs. |
| 3 — Technical Spec | `experience_study_technical_spec_v3_0.md` | Full replacement → **v3.0**. New `users`/`roles` tables; version-lineage fields; A/E approval + audit events; governance-report contracts; **tenancy-readiness appendix**; FR-anchored interface contracts. |
| 4 — Claude Code prompts | `phase4_claude_code_prompts.md` | **Sessions 23+** (continue numbering). |

**Preliminary session shape (finalised in Step 4):**
- **S23** Identity & access foundation (A1–A3): users/roles, light gate, RBAC + segregation enforcement, replace free-text actor capture. *Lands first — lowest-risk, proves the format, unblocks C.*
- **S24** Versioning & lineage (B1–B3).
- **S25** Configurable approval workflow (C1–C5), incl. A/E extension.
- **S26** Audit (D1–D3): A/E events on the existing pattern, hash/integrity extension, unified read + inspection UI.
- **S27** Governance dashboard + compliance pack + retention + tenancy-readiness conformance check (E1–E3, F1) + Phase 4 UAT.

**Verification discipline (unchanged):** reader-tests with a fresh Claude; automated
FR-reference reconciliation (semantic); schema-name checks against the live DB;
full coherence passes. Re-run on any document edit.

**Document lineage:** Requirements v3.0.1 → v4.0 (Phase 4 added). Technical v2.0.1 → v3.0
(Phase 4 added).

---

## 9. Next step

On sign-off of this scope, proceed to **Step 2 — Requirements Spec v4.0**, drafting the
FR-4-xx requirements pillar by pillar (A first, as it is the foundation), each with
falsifiable completion-checklist items.
