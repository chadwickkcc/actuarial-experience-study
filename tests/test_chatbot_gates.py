"""SQL validation gates in the chatbot path (FR-3B-31) + server re-enforcement.

Every generated statement passes the five gates; a rejection blocks the turn,
records the gate, and is NEVER silently rewritten. The MCP server re-enforces the
gates independently of the chatbot (FR-3B-10), proven by calling a tool directly.
"""
from __future__ import annotations

from pathlib import Path

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.ai.mcp_server.server import query_ae_results_impl
from src.utils.types import SQLGateOutcome
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)

_MODEL = "claude-sonnet-4-6"


def _state():
    return SessionState(session_id="g", model_key=_MODEL)


def _run(sql: str):
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "Answer {{col:ae_count}}.")
    )
    return handle_turn(
        "a question", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )


def test_gate_1_parse_rejects_multiple_statements():
    result = _run("SELECT 1; SELECT 2")
    assert result.blocked is True
    assert result.sql_outcome is SQLGateOutcome.REJECT_PARSE


def test_gate_2_rejects_non_select():
    result = _run("DROP TABLE gold_ae_results")
    assert result.blocked is True
    assert result.sql_outcome is SQLGateOutcome.REJECT_NOT_SELECT
    # The offending statement is recorded verbatim — never rewritten.
    assert result.sql == "DROP TABLE gold_ae_results"


def test_gate_3_rejects_off_allowlist_table():
    result = _run("SELECT a FROM silver_term_policies LIMIT 10")
    assert result.blocked is True
    assert result.sql_outcome is SQLGateOutcome.REJECT_ALLOWLIST


def test_gate_4_rejects_uncapped_scan():
    result = _run("SELECT ae_count FROM gold_ae_results")
    assert result.blocked is True
    assert result.sql_outcome is SQLGateOutcome.REJECT_ROWCAP


def test_rejected_sql_is_not_rewritten():
    bad = "SELECT ae_count FROM gold_ae_results"  # uncapped scan
    result = _run(bad)
    assert result.sql == bad
    assert result.block_reason and result.block_reason.startswith("gate_")


def test_server_reenforces_gates_independently_of_chatbot():
    # The AE tool, called directly, rejects a TEV-table query before any DB open.
    out = query_ae_results_impl(
        "SELECT tev FROM gold_tev_results LIMIT 10",
        db_path=Path("/nonexistent.duckdb"), allowlist=allowlist(), row_cap=500,
    )
    assert "error" in out
    # And it rejects a non-SELECT.
    out2 = query_ae_results_impl(
        "DROP TABLE gold_ae_results",
        db_path=Path("/nonexistent.duckdb"), allowlist=allowlist(), row_cap=500,
    )
    assert "error" in out2
