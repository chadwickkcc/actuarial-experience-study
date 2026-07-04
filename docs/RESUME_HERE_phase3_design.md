# RESUME / STATUS — Phase 3 Design Package

> **Update 2026-06-28 — Phase 3 BUILT, UAT-ACCEPTED & CLOSED.** This doc captures the
> *design-package* completion (2026-06-13). Sessions 14–22 were subsequently built and
> the **Phase 3 UAT was signed off / accepted by the owner on 2026-06-28** (live eval
> baseline + manual UI prompts run offline; offline gate 1163 passed, 6 skipped). See
> `docs/phase3_build_progress.md` and the completed sign-off in
> `docs/phase3_uat_script.md` / `docs/phase3_uat_runbook.md`.

**Date:** 2026-06-13
**Project:** AI-assisted actuarial experience study platform — Phase 3 (AI Layer)
**Working mode:** Doc co-authoring (explore → spec → reader-test → executable plan)

---

## ✅ STATUS: Phase 3 design package COMPLETE

All four planned steps are done. The package is internally consistent, reader-validated, and ready to hand to Claude Code.

| Step | Output | Status |
|---|---|---|
| 1 — AI function scoping | Locked scope (items 1–7 + Tier-0 hardening; six candidates excluded) | ✅ done |
| 2 — Requirements Spec | `experience_study_requirements_spec_v3_0_1.md` | ✅ done, reader-tested, locked |
| 3 — Technical Spec | `experience_study_technical_spec_v2_0_1.md` | ✅ done, reader-tested, locked |
| 4 — Claude Code prompts | `phase3_claude_code_prompts.md` (Sessions 14–22) | ✅ done, coherence-verified |

## ⏭️ RESUMPTION POINT

**Begin building.** Run **Session 14** in Claude Code first (smallest, lowest-risk; also proves the prompt format end-to-end). Open both specs alongside Claude Code; paste one session block at a time; do not start a session until the prior session's regression gate is green.

Optional pre-build gate: reader-test the prompts document (fresh Claude + both specs: "could you execute Session 15?"). Marginal value is lower than the spec reader-tests were, since the prompts are thin pointers into two already-validated specs. Owner's call.

---

## Deliverables (all in /mnt/user-data/outputs; upload latest to Project files)

| File | Role | Notes |
|---|---|---|
| `experience_study_requirements_spec_v3_0_1.md` | WHAT to build | 106 Phase-3 FRs (FR-3A-01–46, FR-3B-01–57). In Project files. |
| `experience_study_technical_spec_v2_0_1.md` | HOW to build | 24 FR-anchored interface contracts + schemas + config. In Project files. |
| `phase3_claude_code_prompts.md` | Executable plan | Sessions 14–22; all 103 Phase-3 FRs covered; 2 owner-gate checkpoints. Upload to Project files. |
| `requirements_spec_v3_0_draft_sections.md` | Audit trail | Working draft, superseded. |
| `technical_spec_v2_0_draft_sections.md` | Audit trail | Working draft, superseded. |

Document lineage: Requirements v2.1 → v3.0 (Phase 3 added) → v3.0.1 (reader-test patches). Technical v1.2 → v2.0 (Phase 3 added) → v2.0.1 (reader-test patches).

---

## Locked scope (Phase 3)

**In:** Tier-0 security hardening (S-1 SQL boundary, S-2 Jinja autoescape, T-3 lockfile) + GLM assumption proposals + XGBoost overlay + SHAP + Assumption Comparison UI + LLM provider abstraction + MCP server + two Claude Skills + guarded chatbot + eval harness.

**Out** (documented in Req §7.1): anomaly detection, tiered narratives beyond the memo Skill, survival models, macro-covariate models, agentic orchestration (revisit post-Phase 4), doc/regulatory copilot.

**Sub-phases:** 3a = Sessions 14–17 (no LLM at runtime); 3b = Sessions 18–22 (LLM layer). Gate between: 3a checklist + full regression green.

## Session map (14–22)

- 14  Security hardening + `src/ai/` skeleton + architecture tests (import-graph, write-contract)
- 15  GLM assumption engine (factors, bootstrap CIs, synthetic-truth validation)
- 16  XGBoost overlay + SHAP (challenge model, divergence flag, SHAP-JSON)
- 17  Assumption Comparison UI + TEV what-if — closes 3a
- 18  LLM provider abstraction (4 models + mock) + MCP server — STOP: DeepSeek GA pricing
- 19  Two Claude Skills (memo, SHAP explanation)
- 20  Chatbot core + 5 SQL gates + numeric-traceability post-check
- 21  RAG commentary + audit log + AI Analyst page
- 22  Eval harness + golden/adversarial sets + Phase 3 UAT — STOP: golden-set lock — closes Phase 3

## Key locked design decisions

- GLM: statsmodels, adjustment factors (not curve refits), Poisson+offset / binomial logit, bootstrap (1000, seed 42, determinism-first), explicit tolerance table (mortality +/-10% / annuity +/-15% / lapse +/-15% / WL lapse +/-25% / CI +/-20%), no credibility blending, no one-click adopt.
- GBM: XGBoost (owner's explicit choice), core API, challenge model only, fixed Option-1 hyperparameters (max_depth 3, stability-over-fit sparse-cell regularization), no tuning, truth-recovery reported-not-gated, divergence flag 10%.
- LLM: claude-opus-4-8, claude-sonnet-4-6, deepseek-v4-pro, deepseek-v4-flash; user dropdown; OpenAI-compatible DeepSeek adapter; non-streaming; mock provider for offline pytest; model strings in `llm_config.yaml` only.
- Chatbot: multi-turn (30-turn cap, 16k token window), MCP-only data access, 5 SQL gates, numeric slot-filling + mandatory traceability post-check (block-not-repair), 1M-token budget.
- Audit log: hashes-plus-dynamic-parts (deterministic reconstruction, reconciled with FR-3B-41).
- Tests: `tests/_artifacts/` only, session-scoped fixtures, 5 GB size guard, offline suite (no API keys), eval harness barred from pytest.

## Open questions (Req §12) — now pinned to sessions

1. DeepSeek V4 GA pricing → resolve at Session 18 (STOP checkpoint).
2. Golden-set authorship → Claude Code drafts at Session 22; owner reviews and locks (STOP checkpoint).
3. Reference hardware for NFR-P-05/06 perf targets → confirm (assumed same machine as Phase 1–2 UAT).

None blocks starting Session 14.

## Notes for the resuming assistant

- Phase 2 UAT: done and passed (owner confirmed 2026-06).
- Both reader tests passed: comprehension clean; the spec reader-tests surfaced real fixes (FR-3B-41 contradiction, FR-citation drift, invented column names) all since patched.
- Verification discipline used throughout: automated FR-reference reconciliation (semantic, not just existence), schema-name checks against v1.2, full coherence passes. Re-run these if any document is edited.
- Owner's working style: terse-but-complete prompts; decisive single recommendations over either/ors; feedback as change-instructions; full-replacement document versioning; falsifiable checklist items; hand-calculated verification cells; scrupulous fact-checking and correction of overclaims.
- Phase 4 (governance) remains undesigned — the natural next design effort after Phase 3 is built.
