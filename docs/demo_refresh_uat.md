# Demo Refresh — UAT Script & Sign-Off

**Scope:** owner acceptance of the 2026-08-30 demo refresh (P0–P8 + the
verification sweep; see `demo_refresh_progress.md`). Run against the shipped
seed-42 demo DB (run `3f883e90…`) with `streamlit run ui/app.py`. Sections 1–8 mirror the
scripted demo (`docs/demo_walkthrough.md`) — run it beat-by-beat and tick.
Expected figures are the walkthrough's quick-reference table.

## Pre-flight

- [ ] `uv pip sync requirements.lock` clean; `.venv` Python 3.12.
- [ ] Offline gate green:
      `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`
      (expected after the verification sweep: **1263 passed, 6 skipped, 0 failed**).
- [ ] App boots; login gate shows; all four seeded roles can sign in.
- [ ] Nav shows the 5 groups / 20 pages; no page errors on first open.

## 1 · Run & data quality

| # | Step | Expect | ✓ |
|---|------|--------|---|
| 1.1 | Run Study with defaults (all 7 products pre-selected) | COMPLETE in ~10 s; 25,000 policies | |
| 1.2 | Data Quality | UL 41 (98.0%) · ULSG 128 (93.6%) · IUL 7 (98.6%) quarantined; others 100%; total records 25,000 | |
| 1.3 | Override one quarantined record with a justification | governed event recorded (Audit stream shows DQ_OVERRIDE) | |

## 2 · Experience results & stories

| # | Step | Expect | ✓ |
|---|------|--------|---|
| 2.1 | Mortality A/E headline | 1,206 deaths; portfolio A/E 0.6852 | |
| 2.2 | Management Commentary, Mortality | trend badge 🔴 Worsening; A/E 0.603→0.612→0.754→0.880 (2020–23) | |
| 2.3 | Driver waterfall 2023 × age band | bars sum exactly to the +0.0722 total | |
| 2.4 | Discontinuance YoY | 2022 ≈ 1.194, 2023 ≈ 1.881; portfolio A/E 1.1461 | |
| 2.5 | CI Explorer | 589 claims, 10 illness codes, aggregate A/E 1.2237 | |

## 3 · Fraud

| # | Step | Expect | ✓ |
|---|------|--------|---|
| 3.1 | Run fraud scan | 1,808 scored; 18 flagged; max score 1.20 | |
| 3.2 | Concentrations | OFF-013 / HOSP-066 top the flagged tables (region is no longer a rule) | |
| 3.3 | Drill a shared-claimant claim | policy-year-1 CI-001, cluster of 4, evidence per rule | |
| 3.4 | (key set) Draft fraud narrative | AI-DRAFT banner; figures match the scan; no policy/claimant ids in the text | |

## 4 · AI assistance

| # | Step | Expect | ✓ |
|---|------|--------|---|
| 4.1 | Fit AI models (WL / Mortality) | GLM factors + 95% CIs, GBM challenger, SHAP; no adopt affordance | |
| 4.2 | AI Analyst: "overall mortality A/E for Whole Life?" | 0.6561 (611 vs 931.26) | |
| 4.3 | AI Analyst: adversarial ("delete the fraud table") | refused; turn audited | |
| 4.5 | Study Run Log → AI Activity Log | 4 seeded turns: 2 answered, 1 refused, 1 blocked (numeric_traceability); provider column shown | |
| 4.4 | (key set) Draft management commentary | four sections incl. Proposed Management Actions; AI-DRAFT banner; export works | |

## 5 · Assumption workflow & governance

| # | Step | Expect | ✓ |
|---|------|--------|---|
| 5.1 | Step 1 as a.analyst → create set | PROPOSED set, cells pre-populated | |
| 5.2 | Step 2: edit outside the credibility CI → save | save **blocked** with the violated bound named | |
| 5.3 | Step 2: valid edit + comment → save → submit | status STAGE3_APPROVED | |
| 5.4 | Step 3 as a.analyst | cannot sign (proposer ≠ approver) | |
| 5.5 | Sign as j.junior → s.senior → c.chief | chain completes per materiality; set APPROVED + locked. A second set ships at STAGE3_APPROVED so Step 3 is demonstrable without doing 5.1–5.3 first | |
| 5.6 | Step 2 on the approved set | editing locked; re-open only via Lineage | |
| 5.7 | Lineage: publish with effective range; compare versions | live set resolves; materiality + changed cells shown | |
| 5.8 | Study Run Sign-Off: submit + one approval | chain table advances; run not yet "fit" | |
| 5.9 | Audit & Integrity → Verify integrity | all chains "intact ✓" | |
| 5.10 | Governance Dashboard → Export compliance pack | HTML downloads; lineage + sign-offs + rationale + stamp present | |

## 6 · Owner checkpoints (blocking for close)

- [ ] **Eval re-lock:** review `tests/eval/golden_set.yaml` (30) and
      `tests/eval/adversarial_set.yaml` (12; A007 retargeted) and re-lock —
      update both headers from "RE-LOCK PENDING" to a dated lock note.
      Optionally add fraud/commentary goldens (FU-7).
- [ ] **(Optional, billed)** live eval baseline on ≥1 model with keys set:
      `.venv/bin/python -m src.ai.eval --models claude-sonnet-4-6`.
- [ ] Live demo dry-run performed end-to-end using `docs/demo_walkthrough.md`.

## Defect log

| # | Section | Description | Fix commit | Regression test |
|---|---------|-------------|------------|-----------------|
| D1 | 4 (AI) | Allowlist + a few-shot still carried the 5 economic columns dropped from `gold_assumption_sets` in P3 — generated SQL passed the boundary, then failed in DuckDB | verification sweep (2026-08-30) | `test_no_client_surface_references_tev_columns_or_terms` |
| D2 | 1 (DQ) | IUL missing from the study product lists (no DQ summary row; page total 24,500) + family DQ failures double-counted under UL/ULSG labels | verification sweep (2026-08-30) | `TestFamilySubRunScoping` (×3) |
| D3 | 2 (docs) | Walkthrough §3.2/§3.3 pointed the story figures at pages that don't render them; Run-Study default was TERM-only vs "keep defaults → 25,000" | verification sweep (2026-08-30) | figures re-verified on run `d5f56adb…` |
| D4 | misc | Stale TEV/Stage-4/Stage-2 wording in routing.md, pages 15/29, README/USER_GUIDE/CLAUDE.md; nav-label↔title mismatches (01/15/26); reset script missing fraud tables; dead code + orphan files | verification sweep (2026-08-30) | residue guard + existing suites |

## Sign-off

| Role | Name | Decision (ACCEPT / RETURN) | Date | Comment |
|------|------|----------------------------|------|---------|
| Owner |      |                            |      |         |
