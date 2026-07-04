# RESUME / STATUS — Phase 4 Design Package (Governance)

> **Supersedes `RESUME_HERE_phase3_design.md` as the active pointer.** Phase 3 was BUILT,
> UAT-accepted and CLOSED on 2026-06-28 (offline gate 1163 passed, 6 skipped). Phase 4
> (Governance) has now been **designed, reader-tested, QA cross-checked, and LOCKED**
> (2026-06-28). Building Phase 4 has not started.

**Date:** 2026-06-28
**Project:** AI-assisted actuarial experience study platform — Phase 4 (Governance, single-org)
**Working mode:** Doc co-authoring (explore → spec → reader-test → executable plan)

---

## ✅ STATUS: Phase 4 design package COMPLETE & LOCKED

All four steps done; internally consistent, reader-tested, QA cross-checked, and signed off. Ready to hand to Claude Code.

| Step | Output | Status |
|---|---|---|
| 1 — Scope | `phase4_locked_scope.md` | ✅ locked (signed off 2026-06-28) |
| 2 — Requirements Spec | `experience_study_requirements_spec_v4_0.md` | ✅ locked — reader-tested, QA-passed |
| 3 — Technical Spec | `experience_study_technical_spec_v3_0.md` | ✅ locked — reader-tested, QA-passed |
| 4 — Claude Code prompts | `phase4_claude_code_prompts.md` (Sessions 23–27) | ✅ locked — reader-tested, coverage-verified |

## ⏭️ RESUMPTION POINT

**Begin building.** Run **Session 23** (Identity & Access Foundation) in Claude Code first — lowest-risk, proves the prompt format, and unblocks the approval workflow (which depends on real identities). Open Requirements v4.0 §8 and Technical v3.0 §G/H/I alongside Claude Code; paste one session block at a time; do not start a session until the prior session's regression gate is green.

**Owner inputs needed during the build (two STOP checkpoints + UAT):**
- **Session 23 STOP** — supply the real user identities (display names + the four roles) and bootstrap passwords to replace the `<set at first run>` placeholders in `governance_config.yaml` (seed from a local, git-ignored config; never commit real passwords).
- **Session 25 STOP** — confirm/set the chain, the **materiality `delta_tev_threshold`** (the 0.01 default is a placeholder), `final_level_below_threshold`, and the **attestation text**.
- **Session 27** — owner UAT sign-off closes Phase 4.

---

## Deliverables (all in /mnt/user-data/outputs; upload to Project files)

| File | Role | Notes |
|---|---|---|
| `phase4_locked_scope.md` | Scope anchor | Pillars A–F; decisions; OUT list. Locked. |
| `experience_study_requirements_spec_v4_0.md` | WHAT to build | Full replacement of v3.0.1; §8 = FR-4-01–27 + §8.8 checklist; §10.8 NFR-G-01–08. |
| `experience_study_technical_spec_v3_0.md` | HOW to build | Full replacement of v2.0.1; Sections G (schemas), H (contracts), I (config/tests). |
| `phase4_claude_code_prompts.md` | Executable plan | Sessions 23–27; all 27 FR-4 covered; 2 owner STOPs + UAT. |

Document lineage: Requirements v3.0.1 → **v4.0** (Phase 4 added). Technical v2.0.1 → **v3.0** (Phase 4 added). Both are full-replacement versions.

---

## Locked scope (Phase 4 — Governance, single-org)

**In:** identity foundation (`users` + four roles + minimal username/password gate) · server-side RBAC with absolute proposer≠approver segregation · configurable multi-level sign-off chain (default junior→senior→chief) generalising the single Stage-4 reviewer, **extended to A/E study-run approval** · assumption-set version lineage + supersession + effective-dating · unified audit **read** layer over the three existing per-module logs + hash-chained tamper-evidence + integrity verifier · governance dashboard + exportable compliance pack + retention policy · multi-tenancy **readiness** lens.

**Out** (by decision): multi-tenancy build (tenant_id/RLS/SSO — deferred to a documented **Phase 5** outline; readiness only) · notifications · time-based escalation/overdue chasing · SSO/password-reset/account-management · full physical canonical-log unification.

**Governing principles:** the actuary decides; governance records *who/with what authority/on what basis*, immutably. **Prototype simplicity first** (tie-breaker, veto over scope creep).

## Session map (23–27)

- 23  Identity & access: `gold_users`, login gate, RBAC, segregation, session-identity actor capture (FR-4-01–06) — **STOP: user identities/passwords**
- 24  Versioning & lineage: parent/child, supersession, effective-dating, comparison, reproducibility (FR-4-07–11)
- 25  Configurable approval workflow: YAML chain, sequential sign-off, A/E extension, attestation, materiality, pending queue, governed re-open (+ `audit.append_event`) (FR-4-12–18) — **STOP: chain/threshold/attestation**
- 26  Audit: A/E events, hash-chaining, integrity verifier, unified read layer + page (FR-4-19–22)
- 27  Governance dashboard, compliance pack, retention, tenancy-readiness conformance, Phase 4 UAT (FR-4-23–27) — **closes Phase 4**

## Key locked design decisions

- **Identity:** four roles (analyst=proposer, junior=checker, senior=reviewer, chief=final); minimal username/password gate, salted hashes, **no SSO/reset**; seeded from config; no in-app account management.
- **Workflow:** four-stage workflow shell (RS §6.9/FR-2-34) retained; the single Stage-4 reviewer (FR-2-42/43) is replaced by the configurable chain; a single-`chief_actuary` chain reproduces legacy behaviour. proposer≠approver absolute; distinct user per level by default (`allow_multi_level_signoff: false`).
- **A/E approval:** attaches to a **study run** ("fit for assumption-setting"); always runs the full chain (no ΔTEV shortcut).
- **Materiality:** a **new** configurable governance threshold (`delta_tev_threshold`) on ΔTEV-vs-prior; above → chief required, below → `final_level_below_threshold`. *(Not the §6.8 envelope-width floor — that was a reader-test correction; no pre-existing ΔTEV floor existed.)*
- **Versioning:** parent_set_id lineage; supersession; `effective_from`/`effective_to` range (live-set resolver); ≤1 APPROVED-current per lineage; non-overlapping ranges.
- **Audit (lighter build):** three logs stay physically separate; unified **read** layer only; append-only + hash-chain (`entry_hash = sha256(canonical||prev_hash)`, content precisely defined in §G.2); integrity verifier; governance writes use the **standard parameterized write path** (not the AI read-only `sql_boundary`).
- **Tenancy readiness:** tables shaped for an additive `tenant_id` retrofit; config-not-code conformance check; nothing tenant-related built.

## Open items

- **Phase 4:** none — all six scope questions resolved 2026-06-28; the one open item (distinct approvers per level) was confirmed.
- **Phase 5 (multi-tenancy):** documented outline only, by decision. Readiness preserved by FR-4-26/27.

## Notes for the resuming assistant

- **QA pass (2026-06-28) findings, all fixed before lock:** (a) the requirements reader-test caught a materiality overclaim ("reuse existing floor" — no such floor; corrected to a new governance threshold) and a four-stage/sign-off-chain conflation; (b) the tech-spec reader-test caught an underspecified `entry_hash` content rule, `append_event` wrongly routed through the AI read-only `sql_boundary`, and a missing `final_level_below_threshold` config key; (c) the prompts reader-test + cross-doc audit caught the scope doc still saying "reuse existing floor", a `B.7` mislabel of the Phase-2 workflow (B.7 is `assumption_set.py`), and an ambiguous `append_event` build location (now: built in S25, completed in S26). All corrected and re-verified.
- **Verification discipline (re-run on any edit):** automated FR-reference reconciliation (RS FR-4 set must equal Tech FR-4 set, currently 27/27 identical), schema-name/anchor checks against the live tech spec, out-of-scope-leakage scan, session→FR map consistency, full coherence passes.
- **Owner's working style:** terse-but-complete; decisive single recommendations over either/ors; feedback as change-instructions; full-replacement document versioning; falsifiable checklist items; scrupulous fact-checking and correction of overclaims (label synthesis vs established practice).
- **Build environment:** macOS; `source .venv/bin/activate` then `streamlit run ui/app.py`; project at `/Users/chadwickkcc/Documents/Claude/Claude Code/Actuarial Function/Experience Studies`. Run tests via `.venv/bin/python -m pytest` with no API keys set; full Phase 1–3 regression must stay green throughout Phase 4.
