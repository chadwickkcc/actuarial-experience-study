# Adversarial Review — Resume Point (paused 2026-08-31)

**Status: the review and every approved fix are COMPLETE.** Nothing is half-done, no
branch is open, the working tree is clean. What remains is owner-only work plus two
items deliberately accepted rather than built.

Read this file first on return. It is the single entry point; everything else is
linked from here.

---

## 1. Where things stand in one line

All 53 findings from `adversarial_review_2026-08-31.md` are triaged and closed:
fixed, or recorded as an accepted limitation with the reasoning. The offline gate is
**1412 passed, 3 skipped, 0 failed**, five governance harnesses pass, and the app
boots clean.

---

## 2. Repository state

Branch `main`, working tree clean. Seven commits, oldest first:

| Commit | What |
|---|---|
| `058dfdb` | Review findings report + fix handoff (docs only) |
| `5020353` | Batch 1 — 15-item approved batch (B-1…B-5, M-10, demo-facing, cheap wins) |
| `1ba55c8` | Batch 2 — actuarial correctness (M-1, M-2, M-3, M-4, M-6, M-20) |
| `059c057` | Batch 3 — fraud-narrative cluster (M-17…M-19, m-11) + 12 minor items |
| `b004b1e` | Batch 4 — demo seeding (OBS-3, OBS-4) |
| `a17b5c0` | Batch 5 — the four design decisions (M-11, m-6, m-8, m-12; m-7 documented) |
| `9f70bcd` | M-12 — the Skills cite figures by key instead of writing them |
| `605e149` | Observations — OBS-1, -2, -6, -7, -8, -10 |

Each commit message carries the full reasoning for its batch; they are the primary
record, and the fix log below summarises them.

---

## 3. Documents

| Document | What it holds |
|---|---|
| `docs/adversarial_review_2026-08-31.md` | The 53 findings — severity, repro, evidence, proposed fix — plus the verified-correct and repelled-attacks tables |
| `docs/adversarial_review_fixes.md` | **The fix log.** What was fixed, how it was verified, batch by batch, ending with what is still open |
| `docs/adversarial_review_handoff.md` | The original owner-decision record and resume protocol from the first pause |
| `docs/actuarial_fixes_progress.md` | Batch-2 working detail |
| `docs/fraud_and_minor_fixes_progress.md` | Batch-3 working detail, including the owner decisions taken during it |
| `docs/DEFERRED_FOLLOWUPS.md` | The standing deferral register — FU-7 and FU-8 are the live entries |

---

## 4. How to verify the state on return

```bash
unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY && .venv/bin/python -m pytest tests/ -v --tb=short
```

Expect **1412 passed, 3 skipped, 0 failed**. The three skips are the long-standing
ones: no DA expected surrenders in the base years, and two RPU/ETT cases the
generator deliberately does not produce.

Governance harnesses (all should pass 6/6, 7/7, 5/5, 4/4, 8/8):

```bash
for s in uat_section2 uat_section3_3_runner uat_section3_7_runner uat_section4_4_runner uat_section5_6_runner; do .venv/bin/python scripts/$s.py 2>&1 | tail -1; done
```

---

## 5. The demo database as it stands

Run `3f883e90-580a-42c8-8533-f32efebf3f05`, COMPLETE in ~12 s.

| Surface | State |
|---|---|
| Fraud scan | 1,808 claims scored · **18 flagged** · max composite 1.20 · ring is the **top 14 contiguously** |
| Assumption sets | 1 APPROVED (3 hash-chained sign-offs) + 1 at STAGE3_APPROVED awaiting level 1, so Step 3 is demonstrable |
| AI activity log | 4 seeded offline turns — 2 answered, 1 refused, 1 blocked by the numeric check |
| AI models | 16 registry rows · 324 proposed factors |

`docs/demo_walkthrough.md` and `docs/demo_refresh_uat.md` carry the current figures
and were updated whenever a fix moved one. If the DB is ever rebuilt, the documented
sequence is reset → `_uat_rerun.py` → `_uat_ai_fit.py` → `_uat_seed_workflow.py` →
`_uat_seed_ai_activity.py`, then re-run the fraud scan.

---

## 6. What is still outstanding

### Owner-only — nothing is blocked on me

1. **Eval-set re-lock.** `tests/eval/golden_set.yaml` (30 entries) and
   `adversarial_set.yaml` (12) still carry `RE-LOCK PENDING` headers from demo-refresh
   P3, which trimmed the TEV questions (G027–G032) and retargeted A007. Review and
   re-lock, or tell me to and I will update the headers.
2. **Live eval baseline (optional).** Needs API keys and spends money; the harness and
   its offline mechanics are tested and ready (`python -m src.ai.eval`).
3. **Browser walk of the demo script.** The one piece of the original review plan not
   executed: the login gate needs your password keystroke, which I will not type. Say
   when and I will drive every step after it and screenshot each of the nine beats.

### Accepted, not defects

- **FU-7** — fraud investigator-override workflow; optional fraud/commentary golden-set
  additions; the pre-existing `anti_selection_flag` quirk (now fixed under M-7).
- **FU-8** — segregation of duties keys on the account, not the person. A human with two
  logins could sign their own work. Low exploitability (no self-service account
  creation), and a real fix needs an identity model this prototype lacks; the
  `person_id` design is sketched in the entry.

### One thing worth doing before the demo

The M-12 citation contract is stricter than what it replaced: a model that ignores the
instruction and types a figure now gets blocked where it previously slid through. That
is the designed failure mode, but it is worth **one live run per configured model** to
see which comply before you present. Needs API keys, so it is yours to trigger.

---

## 7. If you want to pick up more work

There is no queued work. The natural next moves, in the order I would take them:

1. The browser walk (§6.3) — the last unexecuted piece of the review plan, and the
   only one that tests what a client actually sees.
2. The live per-model check of the citation contract (§6, last item).
3. Anything in FU-7 you decide is worth building for the client conversation.
