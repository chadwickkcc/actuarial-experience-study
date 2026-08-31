"""Seed the AI Activity Log with genuine offline turns (adversarial review OBS-3).

``gold_ai_audit_log`` ships empty, so the walkthrough beat that says "show the AI
Activity Log" lands on an empty table — and without an API key nothing populates
it during the demo either.

These are REAL turns through the real guarded pipeline: real intent routing, the
five SQL gates, execution via the MCP server against the live Gold data, real
slot-filling and the real numeric-traceability post-check, each writing its own
audit row. Only the LLM's text is canned, supplied by an offline scripted
provider — which is exactly what the MockProvider exists for and what the whole
regression suite runs on.

The audit rows therefore record what actually happened, including the provider
that produced them. Nothing here fabricates an AI call that did not occur: three
turns are seeded, one of which is deliberately BLOCKED so the guardrail is
visible in the log rather than merely described.

Part of the live-DB rebuild sequence (reset → _uat_rerun → _uat_ai_fit →
_uat_seed_workflow → THIS). Idempotent: skips when rows already exist.

Usage:  .venv/bin/python scripts/_uat_seed_ai_activity.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
import yaml

from src.ai.chatbot.audit import make_db_audit_sink
from src.ai.chatbot.mcp_client import InProcessMCPClient
from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.ai.llm.client import load_llm_config
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.sql_boundary import load_allowlist
from src.utils.types import LLMResponse

CONFIG = ROOT / "config"


class _OfflineProvider:
    """Zero-network provider: returns a canned reply per prompt template.

    Identical in kind to the MockProvider the regression suite runs on. It
    supplies only the model's words; every gate, query and check downstream is
    the real thing.
    """

    name = "mock"

    def __init__(self, routing: str, sqlgen: str = "") -> None:
        self._routing = routing
        self._sqlgen = sqlgen

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        system = system or ""
        if "Intent router" in system:
            text = self._routing
        elif "SQL generation" in system:
            text = self._sqlgen
        else:
            text = ""
        return LLMResponse(
            text=text, input_tokens=180, output_tokens=60,
            provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn",
        )


def _routing(label: str, reason: str) -> str:
    return f"INTENT: {label}\nREASON: {reason}"


def _sqlgen(sql: str, template: str) -> str:
    return json.dumps({"sql": sql, "answer_template": template})


#: (question, routing label, routing reason, sql, answer template)
_TURNS = [
    (
        "What is the overall mortality A/E for Whole Life?",
        "FACTUAL_LOOKUP", "asks for a single stored A/E figure",
        "SELECT SUM(actual_deaths_count) AS actual, SUM(expected_deaths_count) AS expected "
        "FROM gold_ae_results WHERE product_code = 'WL' AND illness_code IS NULL",
        "Whole Life mortality A/E is {{col:actual}} actual deaths against "
        "{{col:expected}} expected.",
    ),
    (
        "Which products are covered by this study?",
        "EXPLORATORY", "asks for the covered product set",
        "SELECT DISTINCT product_code FROM gold_ae_results "
        "WHERE illness_code IS NULL ORDER BY product_code LIMIT 20",
        "The study covers {{list:product_code}}.",
    ),
    (
        "List the policy identifiers behind the worst A/E cells.",
        "OUT_OF_SCOPE", "requests policyholder identifiers",
        "", "",
    ),
    (
        # Deliberately blocked: the template asserts a figure the data does not
        # support, so the numeric post-check refuses to render it. Seeded so the
        # guardrail is demonstrable in the log, not just described.
        "Summarise Term mortality for me.",
        "FACTUAL_LOOKUP", "asks for a stored A/E figure",
        "SELECT SUM(actual_deaths_count) AS actual FROM gold_ae_results "
        "WHERE product_code = 'TERM' AND illness_code IS NULL",
        "Term mortality A/E improved by 42.4242 versus the prior study.",
    ),
]


def main() -> int:
    db = Path(DEFAULT_DB_PATH)
    if not db.exists():
        print(f"No demo DB at {db} — run scripts/_uat_rerun.py first.")
        return 1

    con = duckdb.connect(str(db), read_only=True)
    try:
        existing = con.execute("SELECT COUNT(*) FROM gold_ai_audit_log").fetchone()[0]
        run = con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if existing:
        print(f"gold_ai_audit_log already has {existing} row(s) — nothing to seed.")
        return 0
    if run is None:
        print("No COMPLETE study run — run scripts/_uat_rerun.py first.")
        return 1

    llm_cfg = load_llm_config(CONFIG / "llm_config.yaml")
    allowlist = load_allowlist(CONFIG / "ai_config.yaml")
    with (CONFIG / "ai_config.yaml").open("r", encoding="utf-8") as fh:
        chatbot_cfg = (yaml.safe_load(fh) or {}).get("chatbot", {})
    with (CONFIG / "chatbot_few_shots.yaml").open("r", encoding="utf-8") as fh:
        few_shots = (yaml.safe_load(fh) or {}).get("few_shots", [])

    mcp = InProcessMCPClient(db, allowlist, int(chatbot_cfg.get("sql_row_cap", 500)))
    sink = make_db_audit_sink(db)
    state = SessionState(session_id=str(uuid.uuid4()), model_key=llm_cfg["default_model"])

    print(f"Seeding AI activity against run {run[0][:8]}… (offline provider)")
    for question, label, reason, sql, template in _TURNS:
        provider = _OfflineProvider(_routing(label, reason), _sqlgen(sql, template))
        result = handle_turn(
            question, state, llm_cfg, mcp, allowlist,
            chatbot_cfg=chatbot_cfg, few_shots=few_shots,
            provider=provider, audit=sink,
            prompts_dir=CONFIG / "prompts",
        )
        if result.blocked:
            verdict, detail = "BLOCKED", f" ({result.block_reason})"
        elif str(getattr(result.intent, "value", result.intent)) == "OUT_OF_SCOPE":
            verdict, detail = "REFUSED", " (out of scope, no data access)"
        else:
            verdict, detail = "answered", ""
        print(f"  {verdict:9s}{detail:26s} {question[:52]}")

    con = duckdb.connect(str(db), read_only=True)
    try:
        n, blocked = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN blocked THEN 1 ELSE 0 END) "
            "FROM gold_ai_audit_log"
        ).fetchone()
    finally:
        con.close()
    print(f"DONE: {n} audit row(s), {blocked} blocked — the AI Activity Log has content.")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
