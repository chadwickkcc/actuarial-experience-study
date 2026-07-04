"""Intent routing + refusals (FR-3B-27/28/42).

Intent classification is logged BEFORE any data access; out-of-scope and
write/assumption-change requests get a templated refusal with no data access.
MockProvider/scripted only — keys unset.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import classify_intent, handle_turn
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
_AE = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}


def _state():
    return SessionState(session_id="s1", model_key=_MODEL)


def test_classify_intent_parses_label_and_reason():
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP", "asks for one figure"))
    intent, reason = classify_intent(
        "What is Term mortality A/E?", llm_cfg(), _MODEL, provider=provider
    )
    assert intent is IntentLabel.FACTUAL_LOOKUP
    assert "figure" in reason


def test_intent_logged_before_any_data_access():
    events: list[dict] = []
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500",
            "A/E is {{col:ae_count}}.",
        ),
    )
    mcp = StubMCP(ae=_AE, on_call=events.append)
    handle_turn(
        "Term mortality A/E?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, audit=events.append,
    )
    kinds = [e["event"] for e in events]
    assert "intent" in kinds and "data_access" in kinds
    assert kinds.index("intent") < kinds.index("data_access")


def test_out_of_scope_refuses_without_data_access():
    events: list[dict] = []
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "general knowledge"))
    mcp = StubMCP(ae=_AE, on_call=events.append)
    result = handle_turn(
        "What is the capital of France?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, audit=events.append,
    )
    assert result.intent is IntentLabel.OUT_OF_SCOPE
    assert result.block_reason == "refusal"
    assert "can only answer" in result.response_text
    assert not any(e["event"] == "data_access" for e in events)


def test_assumption_change_request_is_refused():
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "asks to change an assumption"))
    mcp = StubMCP(ae=_AE)
    result = handle_turn(
        "Set the WL lapse assumption to 0.5.", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.intent is IntentLabel.OUT_OF_SCOPE
    assert result.sql is None
    assert "change assumptions" in result.response_text


def test_commentary_route_drafts_grounded_answer_with_banner():
    """Round 3: commentary drafts prose over an app-assembled fact pack (no SQL),
    carrying the persistent AI-draft banner; numbers trace to the fact pack."""
    events: list[dict] = []
    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION", "asks for prose"),
        commentary_text="Term mortality A/E was 0.92 over the study period.",
    )
    result = handle_turn(
        "Write a summary of the Term results.", _state(), llm_cfg(),
        StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, audit=events.append,
        commentary_facts={"products": ["TERM"], "term_mortality_ae": 0.92},
    )
    assert result.intent is IntentLabel.COMMENTARY_GENERATION
    assert result.blocked is False
    assert "AI-drafted — pending actuary review" in result.response_text
    assert "0.92" in result.response_text  # traced to the fact pack
    # Intent is still logged for the turn.
    assert any(e.get("event") == "intent" for e in events)
