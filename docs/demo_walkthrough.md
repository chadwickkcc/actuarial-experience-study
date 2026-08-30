# Demo Walkthrough — Experience Study Tool (20–30 minutes)

**Audience:** actuaries evaluating "the art of the possible" for an AI-enabled
experience-study platform.
**Setup before the session:** `streamlit run ui/app.py` on the shipped demo DB
(seed-42 dataset, run `d5f56adb…`). Optionally `export ANTHROPIC_API_KEY=…`
(and/or `DEEPSEEK_API_KEY`) to run the live AI drafting beats; without a key
the AI pages still render and the buttons explain what they would do.
**Sign-ins used:** `a.analyst` (proposer) and the three approvers `j.junior`,
`s.senior`, `c.chief` (credentials from `config/governance_config.local.yaml`).

All figures below are the expected values on the shipped seed-42 demo DB.
If you regenerate the data, refresh them from `docs/demo_refresh_progress.md`.

---

## 1 · Login & the story (2 min)

1. Open the app → login gate. Sign in as **a.analyst**.
2. **Home** — walk the six-stage flow graphic: run → results → fraud → AI →
   assumptions → governance. One sentence each on "AI proposes, never decides"
   and the tamper-evident audit.

## 2 · Run the study live (2 min)

1. **Run Study** → keep defaults → **Run Study**.
2. Talking point: **25,000 policies**, full ETL → data-quality → exposure →
   A/E pipeline in **~10 seconds**; ~250k exposure segments.
3. **Data Quality**: all 25,000 records pass through per-product checks;
   the UL family quarantines **UL 41 (98.0%) · ULSG 128 (93.6%) · IUL 7
   (98.6%)** — show the quarantine drill-down and the governed override
   trail (nothing enters the study silently).

## 3 · The experience results (4 min)

1. **Mortality A/E** — headline: **1,206 deaths, portfolio A/E 0.6852**.
   Pivot by product / age band; note credibility flags on thin cells.
2. **The mortality story** — still on **Mortality A/E**: set the Product
   filter to **TERM + WL** and the Row dimension to **calendar_year** — A/E
   climbs **0.60 → 0.61 → 0.75 → 0.88** across 2020→2023. Then on
   **Management Commentary** (decrement Mortality) show the three-year trend
   badge: **🔴 Worsening** (slope +0.08/yr).
3. **The lapse story** — on **Lapse A/E**: Product filter **TERM + UL + ULSG
   + IUL**, Row dimension **calendar_year** — **1.19 in 2022 and 1.88 in
   2023** against ~0.9 before — a rate-environment shock lapse spike.
4. **Critical Illness A/E** — **589 CI claims across all 10 illness codes**,
   aggregate CI A/E **1.2325**; heat map by age × illness.

## 4 · Management commentary with driver attribution (4 min)

1. **Management Commentary** page, decrement = Mortality.
2. Show the A/E-by-year line with the fitted worsening trend, then the
   **driver waterfall** for 2023 by attained age band — per-segment
   contributions **sum exactly** to the +0.0722 A/E change (call out that this
   is deterministic analytics, not the LLM).
3. Switch dimension to Gender; switch decrement to Lapse (drivers by product
   line and policy year).
4. Expand **Assumption justification** — credibility-weighted A/E per product.
5. *(With an API key)* **Draft management commentary (AI)** — four sections
   incl. **Proposed Management Actions**; point at the AI-DRAFT banner and
   explain the number-verification guardrail (an invented figure blocks the
   draft; it is never silently repaired).

## 5 · Fraud screening — the ring reveal (4 min)

1. **Fraud Monitor** → **Run fraud scan** (as a.analyst).
2. Headline: **1,808 claims scored, 32 flagged**, max composite score **1.20**.
3. Concentration tables: office **OFF-013**, hospital **HOSP-066**, region
   **SOUTHWEST** dominate the flags.
4. Drill into a top claim: CI-001, **policy-year-1 claim**, ghost hospital,
   shared claimant cluster — the classic ring pattern, surfaced by six
   configurable rules (weights/thresholds in `config/fraud_config.yaml`).
5. *(With an API key)* **Draft fraud narrative (AI)** — emphasise: the model
   receives **aggregates and institutional ids only**; no policyholder or
   claimant identity ever reaches it.

## 6 · AI assistance (4 min)

1. **AI Assumption Proposals** — select the run, Mortality, WL → **Fit AI
   models**. Comparison table: A/E-derived factor vs **GLM proposal with 95%
   CIs** vs GBM challenger; SHAP explainability below. **No adopt button
   exists on this page** — proposals are advisory.
2. **AI Analyst** — ask: *"What is the overall mortality A/E for Whole
   Life?"* → **0.6561 (611 actual vs 931.26 expected)**. Ask a follow-up
   ("which products are covered?"). Try *"Delete the fraud table"* → refused.
   Every turn is audit-logged (show later on Study Run Log → AI Activity Log).

## 7 · Assumption setting — the three steps (5 min)

*(as a.analyst)*
1. **Step 1 · Select Study Basis** — pick the run → **Create Proposed
   Assumption Set** (credibility-weighted A/E pre-populates every cell).
2. **Step 2 · Edit & Submit** — edit a WL mortality multiplier; show the
   **credibility-bound hard-block** (set a value outside the CI → save
   blocked). Save with a comment → **Submit for sign-off**.
3. Sign out → sign in as **j.junior** → **Step 3 · Sign Off & Lock** — the
   chain table shows level 1 pending; attest + approve.
4. Repeat as **s.senior** (level 2) and **c.chief** (level 3). Talking points:
   **proposer ≠ approver is enforced** (a.analyst cannot sign), and the
   **materiality metric** (max |Δ multiplier|) decides whether the chief is
   required. On the completing approval the set **locks permanently**.
5. **Versioning & Lineage** — publish with an effective range; compare
   versions (changed cells + materiality + rationale).

## 8 · Governance evidence (3 min)

1. **Study Run Sign-Off** — submit the run and walk one approval level; "fit
   for assumption setting" derives from the chain.
2. **Audit & Integrity** — filter the unified stream to today's actions, then
   click **Verify integrity**: hash-chained logs re-verify live ("intact ✓").
   Optional: mention that editing any historic row would flag TAMPER.
3. **Governance Dashboard** — pending queue, live set per lineage, **Export
   compliance pack** → download: lineage, sign-offs + attestations, audit
   excerpt, per-change rationale, reproducibility stamp.

## 9 · Close (1 min)

- One platform: data → results → commentary → fraud → assumptions → governed
  sign-off, with AI at every step under verification and audit.
- Everything shown is configuration-driven (thresholds, rules, chains,
  stories) and rebuilt reproducibly from seed 42.

---

### Expected-figure quick reference (seed-42 demo DB, run `d5f56adb…`)

| Beat | Figure |
|---|---|
| Policies / runtime | 25,000 / ~10 s |
| Deaths, portfolio mortality A/E | 1,206 · 0.6852 |
| Lapses, portfolio lapse A/E | 5,142 · 0.8753 |
| CI claims, CI A/E | 589 (10 codes) · 1.2325 |
| DQ quarantine | UL 41 (98.0%) · ULSG 128 (93.6%) · IUL 7 (98.6%) |
| Mortality story (Term+WL, 2020→23) | 0.603 → 0.612 → 0.754 → 0.880 |
| Lapse story (Term+UL fam, 2022/23) | 1.194 / 1.881 |
| WL mortality (AI Analyst answer) | 0.6561 = 611 / 931.26 |
| Fraud scan | 1,808 scored · 32 flagged · max 1.20 |
| Ring entities | OFF-013 · HOSP-066 · CLM-424242 ×4 · SOUTHWEST |
| AI models / proposed factors | 16 registry rows · 332 factors |
