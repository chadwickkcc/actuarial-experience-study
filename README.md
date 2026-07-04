# Experience Study Tool

A Python-based actuarial experience study and TEV modelling tool for life
insurance (Term, Whole Life, UL/ULSG, VUL, Deferred Annuities). See
[`CLAUDE.md`](CLAUDE.md) and the specs under [`docs/`](docs/) for full detail.

> **New here? Start with the [User Guide](USER_GUIDE.md)** — how to install, run,
> log in (the four governance roles), and clean/reset the database.

**License.** Source-available, **view-only** — all rights reserved. The code is
shared for viewing, evaluation, and reference only; no reuse, modification, or
redistribution without written permission. See [`LICENSE`](LICENSE). All data is
synthetic; default login credentials are illustrative prototype values only.

## Installation (reproducible, from the lockfile)

Dependencies are pinned in [`requirements.lock`](requirements.lock), compiled
from [`requirements.in`](requirements.in). Builds install from the lockfile
**only** (FR-3A-04):

```bash
# with uv (recommended)
uv pip sync requirements.lock

# or with pip
pip install --no-deps --require-hashes -r requirements.lock
```

Regenerate the lockfile after changing `requirements.in` (e.g. when later
sessions add the ML/LLM stack — xgboost, shap, statsmodels, anthropic, openai,
mcp):

```bash
uv pip compile requirements.in -o requirements.lock --generate-hashes
```

## Running the tests

The regression gate runs with **no LLM API keys in the environment**
(MockProvider posture; NFR-T-06):

```bash
pytest tests/ -v --tb=short
```

Test artifacts are confined to `tests/_artifacts/` (gitignored, NFR-T-01) and
are removed automatically on a successful run. To retain them, pass
`--keep-artifacts`. To clean up manually (NFR-T-07):

```bash
rm -rf tests/_artifacts
```

## AI layer (Phase 3)

The AI layer lives under [`src/ai/`](src/ai/) and is strictly additive: the core
engine never imports from it, and it reads only the Gold layer and writes only
to `data/ai_models/` and the AI Gold tables. All AI SQL passes through the
hardened boundary in [`src/utils/sql_boundary.py`](src/utils/sql_boundary.py).
See [`src/ai/__init__.py`](src/ai/__init__.py) for the enforced contracts.

### Enabling the AI features (API keys)

The runtime AI features — the two Skills (A/E memo, SHAP explanation), the AI
Analyst chatbot, and the Stage-4 memo — call a language-model provider. API keys
are read from **environment variables only** (FR-3B-04): a key is never stored in
YAML, on disk, in logs, or in the audit trail. That is why there is intentionally
**no API-key field in the UI** — set the key in your shell before launching the
app.

Each provider's key un-greys that provider's models in the model dropdowns:

| Environment variable | Un-greys |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude Opus 4.8, Claude Sonnet 4.6 |
| `DEEPSEEK_API_KEY` | DeepSeek V4 Pro, DeepSeek V4 Flash |

Set the key(s) you have, then launch Streamlit:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # un-greys the two Claude models
export DEEPSEEK_API_KEY=sk-...          # un-greys the two DeepSeek models
streamlit run ui/app.py
```

You only need one provider's key to use that provider's models; the other simply
stays greyed. With a key set, the matching models change from greyed
("— API key not configured") to selectable and the Skill / AI Analyst / Stage-4
memo buttons work.

Notes:

- A **greyed model in a dropdown means its key is unset.** Set the env var and
  relaunch rather than selecting the greyed model and clicking (doing so just
  surfaces `API key not configured for provider '...'`).
- The two Skills default to different providers, so with no keys set **Draft A/E
  memo** reports `ANTHROPIC_API_KEY` and **Explain SHAP results** reports
  `DEEPSEEK_API_KEY` — both are the same "key not configured" condition.
- Keys live only in the process/shell environment for that run; the app persists
  nothing. The regression suite is unaffected — it runs with **no keys** by
  design (NFR-T-06).
