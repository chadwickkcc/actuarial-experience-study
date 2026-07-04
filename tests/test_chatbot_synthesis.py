"""Round-3 Phase D: multi-query plan→fetch→synthesise for EXPLORATORY turns.

Opt-in (``multi_query=True``): the planner returns several gated SELECTs, each runs
through the boundary + MCP server, and the synthesiser drafts a prose answer over
the combined evidence (generate-then-verify). Default OFF leaves the single-query
path untouched. Gate-rejected queries are skipped, never executed; an invented
number blocks (or flags in Analyst mode).
"""
from __future__ import annotations

import json

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.utils.types import IntentLabel
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
    return SessionState(session_id="syn", model_key=_MODEL)


class _TwoTableMCP:
    """Returns distinct AE results for two different SELECTs by call order."""

    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def query_ae_results(self, sql):
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r

    def query_tev_results(self, sql):
        return {"error": "no_data", "message": "none"}


def _plan(*queries):
    return json.dumps({"queries": list(queries)})


def test_synthesis_fetches_multiple_queries_and_cites_both():
    mcp = _TwoTableMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
        {"columns": ["duration_band", "ae_lapse"], "rows": [["1", 1.05]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(
            {"label": "overall mortality A/E",
             "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
                    "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"},
            {"label": "lapse A/E by duration",
             "sql": "SELECT duration_band, SUM(actual_lapses)/NULLIF(SUM(expected_lapses),0) "
                    "AS ae_lapse FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL "
                    "GROUP BY duration_band LIMIT 500"},
        ),
        synthesis_answer_text=(
            "Whole Life mortality A/E is 0.5718, while lapse A/E in duration band 1 is 1.05."
        ),
    )
    result = handle_turn(
        "Compare WL mortality and lapse experience.", _state(), llm_cfg(),
        mcp, allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
        multi_query=True,
    )
    assert result.intent is IntentLabel.EXPLORATORY
    assert result.blocked is False
    assert "0.5718" in result.response_text and "1.05" in result.response_text
    assert result.traceability is not None and result.traceability.passed
    # Both queries were executed (combined into result.sql) and rows counted.
    assert result.sql.count("SELECT") == 2
    assert result.result_row_count == 2


def test_synthesis_default_off_uses_single_query_path():
    # Without multi_query, EXPLORATORY still uses the single-query slot-fill path.
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        sqlgen_reply(
            "SELECT product_code FROM gold_ae_results LIMIT 500",
            "Products: {{list:product_code}}.",
        ),
    )
    mcp = StubMCP(ae={"columns": ["product_code"], "rows": [["WL"], ["TERM"]], "row_count": 2})
    result = handle_turn(
        "Which products?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,  # multi_query defaults off
    )
    assert result.blocked is False
    assert "WL" in result.response_text and "TERM" in result.response_text
    # The synthesis planner was never called.
    assert not any("Evidence planner" in (c["system"] or "") for c in provider.calls)


def test_synthesis_skips_gate_rejected_query_but_uses_the_rest():
    mcp = _TwoTableMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(
            {"label": "bad", "sql": "DROP TABLE gold_ae_results"},  # gate-rejected
            {"label": "good",
             "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
                    "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"},
        ),
        synthesis_answer_text="Whole Life mortality A/E is 0.5718.",
    )
    result = handle_turn(
        "WL mortality?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is False
    assert "0.5718" in result.response_text
    # Only the surviving SELECT is in the executed set; the DROP never ran.
    assert "DROP" not in (result.sql or "")


def test_synthesis_invented_number_blocks_by_default():
    mcp = _TwoTableMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(
            {"label": "x",
             "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
                    "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"},
        ),
        synthesis_answer_text="WL A/E is 0.5718, with an unsupported 777 figure.",
    )
    result = handle_turn(
        "WL mortality?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"


def test_synthesis_empty_plan_blocks():
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text="not a plan at all",
    )
    result = handle_turn(
        "compare everything", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["x"], "rows": [[1]], "row_count": 1}), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is True
    assert result.block_reason == "synthesis_plan_failed"


def test_synthesis_caps_query_count():
    from src.ai.chatbot.pipeline import _parse_query_plan

    many = _plan(*[{"label": str(i), "sql": f"SELECT {i}"} for i in range(10)])
    assert len(_parse_query_plan(many, max_queries=4)) == 4


def test_synthesis_skips_credibility_aggregate_query():
    """A planned query that averages per-cell credibility is skipped (not executed);
    other evidence still answers (FR-1A-24 backstop on the synthesis path)."""
    mcp = _TwoTableMCP([
        {"columns": ["ae_lapse"], "rows": [[0.9521]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(
            {"label": "bad credibility avg",
             "sql": "SELECT AVG(credibility_z_lapse) AS z FROM gold_ae_results "
                    "WHERE product_code='UL' AND illness_code IS NULL"},
            {"label": "good",
             "sql": "SELECT SUM(actual_lapses) AS a, SUM(expected_lapses) AS e, "
                    "SUM(actual_lapses)/NULLIF(SUM(expected_lapses),0) AS ae_lapse "
                    "FROM gold_ae_results WHERE product_code='UL' AND illness_code IS NULL"},
        ),
        synthesis_answer_text="UL lapse A/E is 0.9521.",
    )
    result = handle_turn(
        "Compare UL lapse A/E and its credibility.", _state(), llm_cfg(), mcp,
        allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is False
    assert "0.9521" in result.response_text
