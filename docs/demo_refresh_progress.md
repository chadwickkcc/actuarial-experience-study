# Demo Refresh — Build Progress & Handoff Log

**Purpose:** single source of truth for demo-refresh status so any fresh session can resume.
Read together with `demo_refresh_scope.md` (decision authority) and
`demo_refresh_prompts.md` (per-phase session blocks).

**Resume protocol for a fresh session:** read CLAUDE.md → this status board → the next phase's
block in `demo_refresh_prompts.md` → `demo_refresh_scope.md` for the design locks it cites.
One phase per session; gate green before proceeding; commit per phase.

**Gate:** `unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short`

---

## Status board

| Phase | Title | Size | Status |
|-------|-------|------|--------|
| P0 | Driver docs + baseline verification | S | ✅ COMPLETE (2026-08-30) |
| P1 | Groundwork: re-home lifecycle, cut monitors, recon fix | M | — |
| P2 | Assumption workflow v2 + materiality + legacy retirement | L | — |
| P3 | TEV purge | L | — |
| P4 | Data expansion + regeneration | L | — |
| P5 | Fraud module | L | — |
| P6 | Management commentary | L | — |
| P7 | Slickness pass | M | — |
| P8 | Docs, demo script, UAT | M | — |

**Test baseline history:**
| Point | Suite |
|---|---|
| Pre-refresh (P0 baseline) | 1368 passed, 6 skipped |

**Owner checkpoints:** P3 eval re-lock (pending) · P8 UAT sign-off (pending).

---

## Environment notes

- Interpreter: uv-managed `.venv`, Python 3.12. Run everything via `.venv/bin/python`.
- Live demo DB: `data/experience_study.duckdb` (~94 MB pre-refresh; rebuilt fresh at every
  DDL/generator phase per the live-DB rebuild rule — reset → `scripts/_uat_rerun.py` →
  `scripts/_uat_ai_fit.py`).
- Git: repo on `main`; one commit per phase.

---

## P0 — Driver docs — COMPLETE (2026-08-30)

- Approved plan captured from the planning session (owner approved 2026-08-30); decisions D1–D4
  recorded in `demo_refresh_scope.md` §2.
- Authored `demo_refresh_scope.md`, `demo_refresh_prompts.md`, this progress doc.
- CLAUDE.md updated: demo-refresh section + supersession pointers (rules 1–2 defer to the scope
  doc for changed schemas/interfaces).
- Baseline gate verified: **1368 passed, 6 skipped** (no API keys).

**Next session: P1** (see `demo_refresh_prompts.md` → P1).
