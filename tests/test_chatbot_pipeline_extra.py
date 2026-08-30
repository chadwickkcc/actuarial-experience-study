"""Extra Session-20 coverage: widened-table path, routing edge cases, plan parsing, cost
accounting, model-switch on the data path, empty results, negatives, and the
no-direct-DB-connection guard. MockProvider/stub only — keys unset.
"""
from __future__ import annotations

from pathlib import Path

from src.ai.chatbot.context import assemble_rag_context
from src.ai.chatbot.pipeline import (
    _parse_intent,
    _parse_plan,
    execute_via_mcp,
    generate_query_plan,
    generate_sql,
    handle_turn,
    validate_sql,
)
from src.ai.chatbot.session import SessionState, record_call
from src.utils.types import IntentLabel, LLMResponse, SQLGateOutcome
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


def _state(model=_MODEL):
    return SessionState(session_id="x", model_key=model)


# --------------------------------------------------------------------------- #
# Widened-table data path end-to-end (generic query_results tool)             #
# --------------------------------------------------------------------------- #

def test_widened_table_query_routes_to_generic_tool_and_answers():
    extra = {"columns": ["factor"], "rows": [[0.5718]], "row_count": 1}
    sql = "SELECT factor FROM gold_ai_proposed_factors WHERE product_code='WL' LIMIT 500"
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "The proposed WL factor is {{col:factor}}."))
    result = handle_turn(
        "What is the proposed WL mortality factor?", _state(), llm_cfg(), StubMCP(extra=extra), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert result.sql_outcome is SQLGateOutcome.PASS
    assert "0.5718" in result.response_text


# --------------------------------------------------------------------------- #
# execute_via_mcp routing                                                      #
# --------------------------------------------------------------------------- #

def test_execute_routes_ae_and_widened_and_rejects_unroutable():
    ae = {"columns": ["ae_count"], "rows": [[0.9]], "row_count": 1}
    extra = {"columns": ["factor"], "rows": [[1.0]], "row_count": 1}
    mcp = StubMCP(ae=ae, extra=extra)
    assert execute_via_mcp("SELECT ae_count FROM gold_ae_results LIMIT 500", mcp) == ae
    assert execute_via_mcp("SELECT factor FROM gold_ai_proposed_factors LIMIT 500", mcp) == extra
    # No table → unroutable.
    assert execute_via_mcp("SELECT 1", mcp)["error"] == "unroutable"
    # Both tables in one query → unroutable (single-table tools only).
    both = "SELECT a.ae_count FROM gold_ae_results a JOIN gold_ai_proposed_factors t ON a.product_code=t.product_code LIMIT 500"
    assert execute_via_mcp(both, mcp)["error"] == "unroutable"


# --------------------------------------------------------------------------- #
# Plan parsing robustness                                                      #
# --------------------------------------------------------------------------- #

def test_cross_table_union_passes_boundary_but_is_caught_at_routing():
    # A UNION over both allowlisted Gold tables passes gates 1-4 (both tables are
    # allowlisted) but references two tables — the single-table-scoped routing must
    # block it (and the per-table server tools would reject it too). It must never
    # execute.
    union = (
        "SELECT ae_count FROM gold_ae_results LIMIT 500 "
        "UNION SELECT factor FROM gold_ai_proposed_factors LIMIT 500"
    )
    assert validate_sql(union, allowlist(), 500).outcome is SQLGateOutcome.PASS
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(union, "x {{col:ae_count}}"))
    result = handle_turn(
        "smuggle two tables", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[1.0]], "row_count": 1}), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is True and result.block_reason == "unroutable"


def test_parse_plan_handles_fenced_and_embedded_json():
    fenced = '```json\n{"sql": "SELECT ae_count FROM gold_ae_results LIMIT 500", "answer_template": "x {{col:ae_count}}"}\n```'
    assert _parse_plan(fenced)["sql"].startswith("SELECT")
    embedded = 'Sure: {"sql": "SELECT ae_count FROM gold_ae_results LIMIT 500", "answer_template": "y"} done'
    assert _parse_plan(embedded)["answer_template"] == "y"


def test_parse_plan_rejects_malformed_or_incomplete():
    assert _parse_plan("not json at all") is None
    assert _parse_plan('{"sql": "SELECT 1"}') is None          # no answer_template
    assert _parse_plan('{"answer_template": "z"}') is None      # no sql
    assert _parse_plan("") is None


def test_generate_sql_returns_empty_when_plan_unparseable():
    provider = ScriptedProvider("", "this is not JSON")
    assert generate_sql("q", [], llm_cfg(), _MODEL, provider=provider) == ""


def test_generate_query_plan_returns_dict():
    sql = "SELECT ae_count FROM gold_ae_results LIMIT 500"
    provider = ScriptedProvider("", sqlgen_reply(sql, "x {{col:ae_count}}"))
    plan = generate_query_plan("q", [], llm_cfg(), _MODEL, provider=provider)
    assert plan["sql"] == sql and "answer_template" in plan


# --------------------------------------------------------------------------- #
# Routing robustness                                                           #
# --------------------------------------------------------------------------- #

def test_unparseable_routing_defaults_to_out_of_scope():
    assert _parse_intent("complete gibberish, no label")[0] is IntentLabel.OUT_OF_SCOPE
    provider = ScriptedProvider("garbage with no intent line")
    result = handle_turn(
        "q", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.intent is IntentLabel.OUT_OF_SCOPE
    assert result.block_reason == "refusal"


def test_refusal_emits_intent_audit_event():
    events: list[dict] = []
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "general"))
    handle_turn(
        "capital of France?", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, audit=events.append,
    )
    assert any(e["event"] == "intent" and e["intent"] == "OUT_OF_SCOPE" for e in events)


# --------------------------------------------------------------------------- #
# Validation delegation                                                        #
# --------------------------------------------------------------------------- #

def test_validate_sql_passes_good_query_and_normalizes():
    res = validate_sql("SELECT ae_count FROM gold_ae_results LIMIT 500", allowlist(), row_cap=500)
    assert res.outcome is SQLGateOutcome.PASS
    assert "ae_count" in res.sql


# --------------------------------------------------------------------------- #
# Cost accounting (FR-3B-43)                                                    #
# --------------------------------------------------------------------------- #

def test_record_call_computes_cost_from_config_prices():
    state = _state("claude-opus-4-8")  # $5 in / $25 out per Mtok
    resp = LLMResponse(text="x", input_tokens=1_000_000, output_tokens=1_000_000,
                       provider="mock", model="claude-opus-4-8", latency_ms=0.0)
    record_call(state, resp, llm_cfg())
    assert state.tokens_used == 2_000_000
    assert abs(state.cost_estimate - 30.0) < 1e-9   # 5 + 25


def test_session_totals_accumulate_across_a_data_turn():
    ae = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
    sql = "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500"
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "A/E {{col:ae_count}}."))
    state = _state()
    handle_turn("q", state, llm_cfg(), StubMCP(ae=ae), allowlist(),
                chatbot_cfg=chatbot_cfg(), provider=provider)
    # Two LLM calls (route + generate) were recorded.
    assert state.tokens_used > 0
    assert state.cost_estimate >= 0.0


# --------------------------------------------------------------------------- #
# Model switch is honored on the DATA path (both calls)                        #
# --------------------------------------------------------------------------- #

def test_model_switch_used_for_both_routing_and_generation():
    ae = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
    sql = "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500"
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "A/E {{col:ae_count}}."))
    state = _state()
    state.model_key = "deepseek-v4-pro"  # switch before the turn
    handle_turn("q", state, llm_cfg(), StubMCP(ae=ae), allowlist(),
                chatbot_cfg=chatbot_cfg(), provider=provider)
    assert len(provider.calls) == 2
    assert all(c["model"] == "deepseek-v4-pro" for c in provider.calls)


# --------------------------------------------------------------------------- #
# Empty results / negative numbers                                             #
# --------------------------------------------------------------------------- #

def test_empty_result_blocks_via_slot_fill():
    empty = {"columns": ["ae_count"], "rows": [], "row_count": 0}
    sql = "SELECT ae_count FROM gold_ae_results WHERE product_code='ZZZ' LIMIT 500"
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "A/E {{col:ae_count}}."))
    result = handle_turn("q", _state(), llm_cfg(), StubMCP(ae=empty), allowlist(),
                         chatbot_cfg=chatbot_cfg(), provider=provider)
    assert result.blocked is True and result.block_reason == "slot_fill_failed"


def test_negative_number_fills_and_traces():
    extra = {"columns": ["recon_diff_count"], "rows": [[-4480000.0]], "row_count": 1}
    sql = "SELECT recon_diff_count FROM gold_inforce_reconciliation WHERE product_code='WL' LIMIT 500"
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"), sqlgen_reply(sql, "The reconciliation difference is {{col:recon_diff_count}}."))
    result = handle_turn("q", _state(), llm_cfg(), StubMCP(extra=extra), allowlist(),
                         chatbot_cfg=chatbot_cfg(), provider=provider)
    assert result.blocked is False
    assert "-4480000" in result.response_text
    assert result.traceability is not None and result.traceability.passed


# --------------------------------------------------------------------------- #
# Stubs / guards                                                               #
# --------------------------------------------------------------------------- #

def test_assemble_rag_context_is_an_empty_stub():
    assert assemble_rag_context(["run-1"], {}) == ""


def test_chatbot_core_opens_no_direct_db_connection():
    # FR-3B-25: the chatbot reaches the DB ONLY via the MCP client. The pipeline /
    # session / context modules must not import duckdb or open a connection.
    base = Path("src/ai/chatbot")
    for name in ("pipeline.py", "session.py", "context.py"):
        src = (base / name).read_text(encoding="utf-8")
        assert "import duckdb" not in src
        assert "duckdb.connect" not in src
