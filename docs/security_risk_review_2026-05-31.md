# Security & Risk Review — Experience Study / TEV Tool

**Date:** 2026-05-31
**Reviewer:** Claude Code (codebase review, read-only)
**Scope reviewed:** Stages 1A–1C (MVP pipeline) and Stage 2 (TEV modelling) — the full
codebase as it stands immediately **before** Stage 3 (AI layer) begins.
**Coverage:** Security vulnerabilities **and** correctness / data-integrity / technical-debt risks.

---

## 1. Bottom line

**Yes — the codebase is safe to proceed to Stage 3 today.**

Under the agreed threat model (a local, single-analyst tool on one machine, localhost only,
operating on synthetic/internal data), there are **no exploitable vulnerabilities** and **no
blocking technical debt**. The code is clean of the dangerous primitives that usually drive
real incidents (no `eval`/`exec`/`pickle`, no `shell=True`, no network calls, no hardcoded
secrets, `yaml.safe_load` everywhere, read-only UI database connections).

**One condition attaches to that "yes":** several patterns that are harmless against trusted
local input become genuine vulnerabilities the moment **Stage 3 introduces untrusted input** —
a chatbot, natural-language queries, or LLM-generated SQL. The single most important
recommendation in this report is therefore:

> **Harden the SQL-construction boundary and report templating *as part of* Stage 3 — before
> any untrusted/LLM-driven input is wired to the database — not after.**

Everything else is low-severity hygiene that can be scheduled at leisure.

---

## 2. Threat model & assumptions

Severity ratings below are anchored to this model. **These assumptions are explicit — if any is
wrong, re-read the "Networked / real-data" severity column instead.**

| Assumption | Stated as |
|---|---|
| Deployment | Single analyst, one machine, **localhost only**, no network exposure |
| Data | Synthetic / internal only (seed = 42); **no real policyholder PII today** |
| Operator | **Trusted** — already owns the machine, the DB file, and the CLI |
| Stage 3 | Not yet built; **will** add an AI/chatbot/NL-query surface (untrusted input) |

Two columns are used throughout:
- **Now** — severity under the assumptions above.
- **Stage 3 / Networked** — severity once untrusted input (AI layer) or multi-user network
  exposure exists. This is a *forward-looking* rating to guide what to fix first.

---

## 3. Strengths (genuine, worth keeping)

These are real and should be preserved as the AI layer is added:

- **No dangerous execution primitives** anywhere: no `eval`, `exec`, `compile`, `pickle`,
  `marshal`, `os.system`, or `subprocess(shell=True)`.
- **No outbound network calls**; nothing binds to `0.0.0.0`.
- **No hardcoded secrets**; no `.env` file present; no credentials in code or YAML.
- **`yaml.safe_load` used consistently** for all config/assumption loading — no arbitrary-object
  deserialization.
- **UI database connections are opened `read_only=True`** — the Streamlit surface cannot mutate
  the warehouse, which sharply limits blast radius.
- **PII-aware design already present**: `src/aggregation/aggregator.py` SHA-256-hashes
  `policy_id` and bins face amounts in drill-through exports — good instinct to carry into Stage 3.
- **Product logic is config-driven** (YAML), keeping the engine product-agnostic per spec.

---

## 4. Security findings

> Note on the upstream "5 CRITICAL SQL injections": an automated pass flagged these as critical.
> On reading the actual source, **all interpolated table/column names come from hardcoded dict
> maps, literal `if/elif` assignments, or module constants**, and the one interpolated *value*
> (`latest_etl`) is a `uuid.uuid4()` read back from the DB. **No untrusted input path reaches any
> of them today.** They are correctly reclassified below as **Low now / High at Stage 3** — a
> latent pattern, not a live hole.

### S-1 — Non-parameterized SQL construction pattern
**Now: Low · Stage 3 / Networked: High · Effort: M**

f-strings interpolate table names, column lists, and a UUID directly into DuckDB SQL:

- `src/tev/model_points.py:378-401` — `FROM {table}`, `SELECT {cols}`, and a `WHERE` clause
  containing `'{latest_etl}'`.
- `src/data_quality/runner.py:280,286` — `FROM {silver_table}` (table interpolated; the WHERE
  *values* are correctly parameterized with `?`).
- `src/ingestion/run_etl.py:34` — `SELECT COUNT(*) FROM {table}`.
- `src/calculation/ae_engine.py:233` — `INSERT INTO ... ({col_list}) SELECT {col_list} ...`.
- `src/reporting/generator.py` — multiple query helpers built with f-strings.

**Impact now:** None exploitable — every input is developer/config-controlled (`_SILVER_TABLE`,
`_SILVER_COLS`, `bronze_map`/`silver_map`, `_DDL_COLS`, literal assignments) and `latest_etl` is a
server-generated UUID.
**Impact at Stage 3:** If *any* of these helpers (especially the reporting query functions or a
new "ask-the-data" helper) is reused to serve user- or LLM-supplied product codes, filters, or
SQL, this becomes a direct SQL-injection / data-exfiltration path.
**Recommendation:** Before wiring Stage 3 to the DB, establish one rule and one helper:
(1) table/column identifiers may only come from a **server-side allowlist** (reuse the existing
dict maps — e.g. raise on `product_code not in _SILVER_TABLE` instead of `KeyError`); (2) every
**value** goes through `?` placeholders. Never let the AI layer build SQL by string formatting;
give it a fixed, parameterized query surface.

### S-2 — Jinja2 `autoescape=False` in report generator
**Now: Low · Stage 3 / Networked: High · Effort: S**

`src/reporting/generator.py:17` constructs the Jinja `Environment` with `autoescape=False`.
**Impact now:** Low — template context is numeric/synthetic, rendered to local HTML files.
**Impact at Stage 3:** If reports ever render free-text assumption rationales, user notes, or
LLM-generated narrative, disabled autoescaping is an XSS/SSTI vector in the produced HTML.
**Recommendation:** Set `autoescape=True` now (cheap, no downside for numeric templates) and use
explicit `| safe` only where intentionally rendering trusted HTML.

### S-3 — No authentication on the Streamlit app
**Now: Negligible · Stage 3 / Networked: High · Effort: M (deployment, not code)**

`ui/app.py` has no auth/session isolation.
**Impact now:** None — localhost, single user.
**Impact when networked:** Anyone who can reach the host can read all study data and trigger
runs. This is a **deployment gate, not a code defect**: do not expose the app on a shared network
without an auth layer (reverse-proxy SSO, Streamlit auth, or equivalent) in front of it.

### S-4 — Unvalidated file paths (operator-supplied & DB-derived)
**Now: Low · Stage 3 / Networked: Medium · Effort: S**

- `src/ingestion/run_etl.py:64-66` — `--db/--source/--mapping` taken as raw `Path()` from CLI.
- `ui/pages/22_tev_stage3.py` — opens `yaml`/report paths read back from the DB without confining
  them to an expected base directory.
**Impact now:** Low — the operator already controls the filesystem; CLI traversal is
self-inflicted.
**Impact at Stage 3:** If file paths ever become influenced by stored/user/LLM content, this is a
local-file-read/write traversal path.
**Recommendation (defense-in-depth):** add a small `resolve()`-and-confine-to-base-dir helper and
route ETL and Stage-3 file access through it.

---

## 5. Technical-debt & correctness register

These are not security issues but speak directly to your "no hidden technical debt" goal.

### T-1 — Silent omission of a product with zero model points
**Severity: Medium (data integrity) · Effort: S**

`src/tev/model_points.py:465-477` returns a `ModelPointResult` with `model_point_count=0` and an
empty DataFrame when a product has no in-force rows — **no log, warning, or raise** at any layer.
A fully empty `gold_model_points` raises (`tev_core.py:625-626`), but a *single* product silently
vanishing from a multi-product TEV run does not. (Confirmed; matches prior investigation.)
**Impact:** A misconfigured product code, failed upstream ETL, or filter error can drop a product
from TEV results with no signal — exactly the kind of error that survives to a reported number.
**Recommendation:** Emit a `logger.warning` (and surface a UI badge) when a requested product
yields zero model points; optionally make "expected product produced 0 MPs" a loud,
acknowledge-able condition.

### T-2 — Stray `study.db` artifact at repo root
**Severity: Low (housekeeping) · Effort: trivial**

`study.db` is a **0-byte SQLite file** (created 2026-05-18) with **no references anywhere in the
Python code** — the real warehouse is `data/experience_study.duckdb` (~136 MB).
**Impact:** Confusion / accidental use; harmless but untidy.
**Recommendation:** Delete it and add a `.gitignore` entry for stray `*.db` files.

### T-3 — Unpinned dependencies, no lockfile
**Severity: Medium (reproducibility) · Effort: S**

`requirements.txt` uses `>=` floors for every package (`duckdb>=0.10.0`, `pandas>=2.0.0`, …) and
there is **no lockfile** (`*.lock`/`poetry.lock`/`Pipfile.lock`).
**Impact:** For an actuarial tool whose outputs must be *reproducible numbers*, an unpinned
`pandas`/`numpy`/`scipy`/`duckdb` upgrade can silently change results between environments or over
time. This also undermines the "seed = 42 → deterministic" guarantee.
**Recommendation:** Pin exact versions (or commit a lockfile) and record the verified version set
used to produce the current UAT/TEV baseline. Especially important before adding Stage 3's ML
stack (scikit-learn / SHAP / LLM SDKs), which expands the dependency surface considerably.

### T-4 — pandas chained-assignment / `fillna` FutureWarnings
**Severity: Low (forward-compat) · Effort: S**

Numerous `df[col] = df[col].fillna(...)` patterns across `src/tev/model_points.py`,
`src/tev/tev_core.py`, `src/calculation/ae_engine.py`, `src/aggregation/aggregator.py`. These
emit `FutureWarning`s under recent pandas and tighten further with Copy-on-Write in pandas 3.x.
**Impact:** None today; risk of breakage on a future pandas upgrade (ties back to T-3).
**Recommendation:** Modernize the assignment idioms when convenient; pin pandas until then.

### T-5 — Test-suite concessions (documented, accepted)
**Severity: Low · Effort: n/a (track only)**

Current state is healthy — **679 passed, 6 skipped, 0 failed**, suite no longer pollutes the
production DB (per `docs/DEFERRED_FOLLOWUPS.md`). The concessions, for the record:
- **6 skips:** 5 are defensive `skipif(DB/model-points not available)` guards
  (`test_assumption_set.py`, `test_tev_engine.py`, `test_envelope.py`, `test_model_points.py`) —
  legitimate. 1 is a deliberate, well-documented skip (`test_exposure_wl_ul.py:271`) because the
  generator stopped producing RPU/ETT non-forfeiture statuses on 2026-05-21.
- **WL lapse+surrender A/E band widened** from `[0.80, 1.10]` to `[0.80, 1.50]` — an accepted
  synthetic-data calibration deviation, documented.
**Recommendation:** No action needed to proceed. Keep `DEFERRED_FOLLOWUPS.md` as the standing
revisit record; consider re-tightening the WL band if/when the generator is recalibrated.

---

## 6. Prioritized action list

### Do as part of (or just before) Stage 3 — these gate untrusted input
1. **S-1** — Stand up the allowlist + parameterized-query boundary; forbid string-formatted SQL
   in the AI layer. *(High value, M effort.)*
2. **S-2** — Flip Jinja `autoescape=True`. *(High value, S effort.)*
3. **S-3** — Treat networked deployment as gated on adding authentication. *(Decision, not code.)*

### Do at leisure — hygiene, no urgency
4. **T-3** — Pin dependencies / add a lockfile (do **before** adding the Stage-3 ML stack).
5. **T-1** — Add the zero-model-point warning. *(Cheap data-integrity win.)*
6. **S-4** — Add the path-confinement helper (defense-in-depth).
7. **T-2** — Remove the stray `study.db`.
8. **T-4** — Modernize `fillna`/assignment idioms.

### Track only — no action required
9. **T-5** — Keep watching the documented test concessions.

---

## 7. Verdict restated

The Stage-2 codebase is **secure and sound for its current local, single-analyst, synthetic-data
purpose, and is safe to build Stage 3 on top of.** No item in this report blocks that. The only
*conditional* risk is the SQL/templating boundary (S-1, S-2): address it as the first piece of
Stage 3 work, so that the AI layer is wired to a parameterized, allowlisted, auto-escaping
surface from day one rather than retrofitted afterward.

*Assumptions are listed in §2; all severities are conditional on them. No source code was modified
in producing this review — every recommendation is for you to accept, schedule, or decline.*
