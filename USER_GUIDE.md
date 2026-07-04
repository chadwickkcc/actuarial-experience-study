# User Guide — Experience Study & TEV Tool

A step-by-step guide to installing, running, and maintaining the tool. For the
project overview and the reproducible-install summary, see
[`README.md`](README.md).

---

## 1. What this tool is

A Python-based actuarial **experience study** and **TEV (Traditional Embedded
Value)** modelling tool for life insurance, covering five product families —
Term Life, Whole Life, Universal Life (UL/ULSG), Variable Universal Life (VUL),
and Deferred Annuities. It runs a Bronze → Silver → Gold data pipeline, computes
A/E (actual-to-expected) experience, proposes assumptions (with an optional AI
layer), models TEV, and enforces a multi-role governance & sign-off workflow. The
UI is a Streamlit app; all data used is **synthetic** — there is no real
policyholder information.

---

## 2. Prerequisites

- **Python 3.12** (the project is pinned to 3.12; the ML/LLM stack has no wheels
  for 3.13/3.14).
- **[uv](https://github.com/astral-sh/uv)** (recommended) or `pip`.
- macOS/Linux shell (examples use `bash`/`zsh`).

---

## 3. Install

Dependencies are pinned in [`requirements.lock`](requirements.lock) (compiled
from [`requirements.in`](requirements.in)). Install from the lockfile only:

```bash
# create and populate a virtual environment with uv (recommended)
uv venv --python 3.12 .venv
uv pip sync requirements.lock

# — or — with pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install --no-deps --require-hashes -r requirements.lock
```

Run everything through the venv, e.g. `.venv/bin/python ...` or after
`source .venv/bin/activate`.

---

## 4. Set up logins (first run only)

Real passwords are supplied through a **git-ignored** local override file. Copy
the example and set your own passwords:

```bash
cp config/governance_config.local.yaml.example config/governance_config.local.yaml
# then edit config/governance_config.local.yaml and set a bootstrap_password
# for each of the four users
```

The committed [`config/governance_config.yaml`](config/governance_config.yaml)
ships only the placeholder `"<set at first run>"`, which produces an unusable
hash (nobody can log in) until you supply real passwords in the local file.
Passwords are hashed (salted PBKDF2-HMAC-SHA256, 200k iterations) at seed time
and the plaintext is discarded — only the hash + salt are stored in the database.

> **Prototype defaults.** During development the local file used
> `Analyst#2026`, `Junior#2026`, `Senior#2026`, `Chief#2026`. These are
> illustrative only — **set your own** before any non-prototype use.

---

## 5. Initialise the (empty) database

The tool uses a single-file DuckDB database at
`data/experience_study.duckdb`. Create the empty schema (no runs, no results):

```bash
.venv/bin/python -m src.utils.db_init
```

This is idempotent (safe to re-run) and creates the full schema only. The app
also **auto-creates** the database and seeds the four users on first launch, so
you can skip this step if you go straight to §6.

---

## 6. Run the app

```bash
streamlit run ui/app.py
```

A **login gate** appears first — sign in as one of the four users (see §8). After
signing in, the sidebar groups the workflow into numbered sections (Getting
Started → Experience Results → Product Monitors → AI Assistance → Assumption
Setting / TEV → Governance).

---

## 7. Get data in and run a study

The database starts empty. To populate it:

1. Open the **Study Setup / Run Study** page in the UI.
2. Generate the synthetic dataset (or trigger a study run) — this rebuilds the
   Bronze → Silver → Gold layers from the synthetic source data.
3. Explore the A/E results, product monitors, assumption proposals, and TEV pages.

The random seed for all synthetic data generation is fixed (42), so runs are
reproducible.

---

## 8. The four governance roles

Sign-off follows a three-level chain: **junior → senior → chief**. The
**proposer can never be an approver** (segregation of duties is enforced), and
the required final level depends on materiality (|ΔTEV| ≥ threshold requires the
chief actuary).

| Role | Default username | What it can do |
| --- | --- | --- |
| **Analyst** | `a.analyst` | Propose assumption changes; view results |
| **Junior Actuary** | `j.junior` | Sign-off **level 1**; view; export |
| **Senior Actuary** | `s.senior` | Sign-off **level 2** (final sign-off below the materiality threshold); view; export |
| **Chief Actuary** | `c.chief` | Sign-off **level 3** (final sign-off at/above materiality); view; export |

**Passwords are set by you** in `config/governance_config.local.yaml` (see §4) —
they are not stored in the repository. Give each person the username above and
the password you set for them. To add or change users, edit that local file (and
the `users:` block of `config/governance_config.yaml` if you want new usernames
seeded) and restart the app; users are re-seeded idempotently on start.

---

## 9. Cleaning / resetting the database

**Option A — clear run data, keep the schema and logins** (fast; for reuse or
testing):

```bash
.venv/bin/python scripts/reset_for_testing.py            # clears Bronze/Silver/Gold + AI rows
.venv/bin/python scripts/reset_for_testing.py --dry-run  # preview what would be cleared
```

Useful flags:
- `--keep-bronze` — preserve the raw Bronze loads.
- `--include-ai-models` — also delete on-disk AI model artifacts (`data/ai_models/`).
- `--include-governance` — also clear the two hash-chained governance logs
  (user accounts are always preserved).

`gold_users` (logins) are **never** cleared, and the app re-seeds them on start,
so a reset never locks you out.

**Option B — a truly pristine, empty database file** (DuckDB `DELETE` does not
shrink the file on disk):

```bash
rm -f data/experience_study.duckdb
rm -rf data/ai_models/*
.venv/bin/python -m src.utils.db_init
```

---

## 10. Enabling the AI features (optional)

The AI Skills and the AI Analyst chatbot call a language-model provider. API keys
are read from **environment variables only** — never stored in YAML, on disk, in
logs, or in the audit trail (which is why there is no API-key field in the UI).

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # un-greys Claude Opus 4.8 / Sonnet 4.6
export DEEPSEEK_API_KEY=sk-...        # un-greys DeepSeek V4 Pro / Flash
streamlit run ui/app.py
```

Set only the key(s) you have; the other provider's models simply stay greyed. The
app runs fully without any keys — the AI buttons stay disabled. See the README's
"Enabling the AI features" section for details.

---

## 11. Running the tests

The regression suite runs with **no LLM API keys** in the environment
(MockProvider posture):

```bash
unset ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY
.venv/bin/python -m pytest tests/ -v --tb=short
```

Test artifacts are confined to `tests/_artifacts/` (git-ignored) and are removed
automatically on a successful run; pass `--keep-artifacts` to retain them.
