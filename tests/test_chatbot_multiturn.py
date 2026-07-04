"""Multi-turn + cost controls (FR-3B-39/40/44/45).

trim_history never drops the system prompt; the max-turns prompt fires at the cap;
the per-session budget warns at 80% and hard-stops at 100% with no silent
degradation; the model is switchable mid-session.
"""
from __future__ import annotations

from src.ai.chatbot.context import trim_history
from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
)

_MODEL = "claude-sonnet-4-6"


def _turns(n_chars: int, count: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * n_chars}
        for i in range(count)
    ]


def test_trim_history_keeps_recent_within_window():
    turns = _turns(40, 3)  # ~10 tokens each
    kept = trim_history(turns, "s" * 20, token_window=20)  # system ~5 → budget 15
    assert kept == [turns[-1]]  # only the newest fits


def test_trim_history_reserves_system_prompt_budget():
    turns = _turns(40, 2)
    # System alone exceeds the window → no turns retained, but it is never dropped
    # (the system prompt is supplied to the call separately, not via this list).
    assert trim_history(turns, "s" * 200, token_window=10) == []


def test_trim_history_keeps_all_when_window_large():
    turns = _turns(40, 3)
    assert trim_history(turns, "s" * 20, token_window=100000) == turns


def test_max_turns_prompt_fires_without_llm_call():
    cfg = dict(chatbot_cfg())
    cfg["max_turns_per_session"] = 2
    state = SessionState(session_id="m", model_key=_MODEL)
    state.add_turn("user", "q1")
    state.add_turn("user", "q2")
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"))
    result = handle_turn(
        "q3", state, llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=provider,
    )
    assert result.blocked is True and result.block_reason == "max_turns_reached"
    assert provider.calls == []  # no LLM call once the cap is hit


def test_budget_hard_stop_blocks_without_degradation():
    cfg = dict(chatbot_cfg())
    state = SessionState(session_id="b", model_key=_MODEL)
    state.tokens_used = int(cfg["session_token_budget"])  # at 100%
    provider = ScriptedProvider(routing_reply("FACTUAL_LOOKUP"))
    result = handle_turn(
        "q", state, llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=provider,
    )
    assert result.blocked is True and result.block_reason == "budget_exhausted"
    assert provider.calls == []


def test_budget_warning_at_80_percent():
    cfg = dict(chatbot_cfg())
    state = SessionState(session_id="w", model_key=_MODEL)
    state.tokens_used = int(0.85 * cfg["session_token_budget"])
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "general"))
    result = handle_turn(
        "unrelated question", state, llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=provider,
    )
    assert result.response_text.startswith("Heads up")


def test_model_switch_mid_session_is_honored():
    state = SessionState(session_id="x", model_key=_MODEL)
    state.model_key = "deepseek-v4-pro"  # switch
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "general"))
    result = handle_turn(
        "unrelated", state, llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.llm_response is not None
    assert result.llm_response.model == "deepseek-v4-pro"
