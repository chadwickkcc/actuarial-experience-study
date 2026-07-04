# TEV Optimiser → Credibility Envelope: Specification Changes Summary

**Scope:** Replacing the single goal-seek optimiser (which maximises aggregate TEV within credibility bounds and produces a "suggested optimal assumption set") with a two-run credibility envelope analyser (which computes TEV_min and TEV_max within the same credibility bounds and reports the percentile of the proposed assumption set within that envelope, with no UI affordance to copy envelope endpoints into an assumption set).

**Rationale:** see project chat history; in brief — the original optimiser had three problems: (1) framing TEV maximisation as the objective creates assumption-setting bias risk; (2) the upper bound alone is a misleading governance artefact without the lower bound; (3) any UI path to "adopt" the suggestion inverts the proper data → A/E → θ → TEV workflow direction. The envelope reframe fixes all three.

---

## Files updated

- `experience_study_requirements_spec_v2.md` → v2.1 (17 touch-points across §1, §6.2, §6.8, §6.9, §6.10, §6.11, §9.1, §9.2, §9.4, §9.6, §8.2)
- `experience_study_technical_spec.md` → v1.1 (4 touch-points across §A schema, §B types, §B.11 module)

The optimiser source file is renamed: `src/tev/optimiser.py` → `src/tev/envelope.py`.

---

## Requirements spec changes (v2.0 → v2.1)

| # | Location | Change |
|---|---|---|
| 1 | §1.2 tech stack table | "Goal-seek optimiser" row → "Credibility envelope analyser" (description updated) |
| 2 | §1.3 file structure | `optimiser.py` → `envelope.py` |
| 3 | §1.4 design principle 4 | "optimiser suggests; actuary confirms" → "envelope informs; actuary decides; no direct-population path" |
| 4 | §2 phase map (Phase 2 row) | "goal-seek optimiser" → "credibility envelope analyser" |
| 5 | FR-2-02 | "constraint box for the goal-seek optimiser" → "...for the credibility envelope analyser" |
| 6 | **§6.8 (FR-2-27 to FR-2-33)** | **Full rewrite.** Section retitled "Credibility Envelope Analysis". Old single max-TEV optimisation replaced by two L-BFGS-B runs (TEV_min, TEV_max), plus a percentile-of-proposed metric, plus an explicit no-adoption clause. Materiality floor introduced for the percentile when the envelope width is below 0.1% of proposed TEV. |
| 7 | FR-2-38 (Stage 3 button) | "Suggest Optimal" → "Compute Credibility Envelope"; side panel is read-only |
| 8 | FR-2-41 | "Adopt optimiser suggestion" workflow removed; envelope is informational only with explicit prohibition of any UI path to populate assumption sets |
| 9 | FR-2-46 (approval record fields) | "optimiser usage flag (run + adopted)" → "envelope flag, TEV_min, TEV_max, percentile" |
| 10 | FR-2-47 (working actuary report) | "optimiser run details" → "credibility envelope analysis (TEV_min, TEV_max, envelope width, percentile, θ_min, θ_max, directional readings)" |
| 11 | FR-2-48 (impact report) | Disambiguated: "single-axis sensitivity tornado" + "credibility envelope analysis" both listed (the old "sensitivity envelope" wording collided with the new term) |
| 12 | §6.11 checklist | Two optimiser-specific checks replaced with four envelope-specific checks (convergence on both runs, TEV_min ≤ TEV_proposed ≤ TEV_max, percentile bounds, read-only labelling) |
| 13 | NFR-P-04 | Budget doubled from 60s to 120s to reflect two runs |
| 14 | NFR-C-08 | Updated to require both endpoint θ vectors within bounds AND containment property |
| 15 | NFR-CF-09 | Max evaluations now applied independently per run |
| 16 | NFR-A-06 | Audit log fields updated to envelope outputs |
| 17 | §8.2 Phase 3 AI prompt input | "optimiser usage" → "envelope analysis output" |

---

## Technical spec changes (v1.0 → v1.1)

| # | Location | Change |
|---|---|---|
| T1 | §A `gold_workflow_iterations` | Action label `OPTIMISER_RUN` → `ENVELOPE_RUN`; column `optimiser_run_flag` → `envelope_run_flag`; column `optimiser_suggestion_adopted` dropped |
| T2 | §A `gold_assumption_approvals` | Columns `optimiser_used_flag` → `envelope_run_flag`; `optimiser_adopted_flag` dropped; new columns added: `envelope_tev_min DOUBLE`, `envelope_tev_max DOUBLE`, `proposed_envelope_percentile DOUBLE` (NULL allowed for the percentile when below materiality floor) |
| T3 | §B types | `OptimiserResult` dataclass replaced by `EnvelopeResult` with fields: `proposed_tev`, `tev_min`, `tev_max`, `envelope_width_abs`, `envelope_width_pct`, `proposed_envelope_percentile`, `percentile_undefined_reason`, `theta_proposed`, `theta_min`, `theta_max`, `credibility_bounds`, `n_evaluations_min`, `n_evaluations_max`, `convergence_message_min`, `convergence_message_max`, `envelope_yaml_path` |
| T4 | **§B.11 module** | **Full rewrite.** `src/tev/optimiser.py` renamed to `src/tev/envelope.py`. `run_optimiser()` replaced by `run_envelope_analysis()` (returns `EnvelopeResult`). Algorithm now: pre-load model points → run L-BFGS-B for TEV_max → run L-BFGS-B for TEV_min → sanity-check containment → compute percentile (or NULL if below materiality floor) → write read-only audit YAML → return result. `identify_top5_decrements` and `run_tev_fast` retained unchanged. |

---

## Database migration

Since the build is in UAT and presumably contains no production-locked assumption approval records, the migration is a **hard schema replacement, not an ALTER**:

```sql
DROP TABLE IF EXISTS gold_workflow_iterations;
DROP TABLE IF EXISTS gold_assumption_approvals;
-- then re-run the schema initialisation from src/utils/db_init.py
-- with the updated DDL from §A of the technical spec v1.1
```

If you have UAT assumption approvals you wish to preserve, an ALTER-based migration is possible but adds complexity and isn't worth it at this stage.

---

## What is NOT changing

- The TEV-impact matrix (FR-2-24 to FR-2-26) — unchanged, still feeds the top-5 selection
- The sensitivity grid (FR-2-19 to FR-2-23) — unchanged
- `identify_top5_decrements()` and `run_tev_fast()` — unchanged code, reused by both new runs
- The 4-stage workflow structure — unchanged in terms of stages; Stage 3 just gets a different side panel
- The Phase 1 experience study pipeline — entirely unchanged
- The model point compression — unchanged
- The TEV projection engine — unchanged

The change is genuinely localised to the optimisation/envelope module, its UI side panel, and the two related audit tables.
