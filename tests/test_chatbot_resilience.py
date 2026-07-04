"""Round-3 Phase A: a provider error during generation is a safe block, not a
silent crash (the DeepSeek-commentary "silence" bug).

Only the routing and faithfulness calls used to catch ``LLMProviderError``; the
SQL-gen and commentary-gen calls did not, so a reasoning model that truncated to
empty content raised through ``handle_turn`` and the page swallowed it with no
saved turn. These tests pin that every generation path now returns a saved,
blocked ``ChatTurnResult`` with ``block_reason == "llm_error"``.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.ai.llm.base import LLMProviderError
from src.utils.types import LLMResponse
from tests.chatbot_helpers import (
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
)

_MODEL = "claude-sonnet-4-6"


class _RaiseOnGenerationProvider:
    """Routes normally, but raises LLMProviderError on the SQL-gen / commentary
    call (simulating a reasoning model truncated to empty content)."""

    name = "raises"

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        system = system or ""
        if "Intent router" in system:
            return LLMResponse(
                text=routing_reply(self.intent), input_tokens=10, output_tokens=5,
                provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn",
            )
        raise LLMProviderError("DeepSeek returned an empty response (truncated).")

    def __init__(self, intent: str):
        self.intent = intent


def _state():
    return SessionState(session_id="res", model_key=_MODEL)


def test_provider_error_on_sqlgen_is_a_safe_block_not_an_exception():
    state = _state()
    result = handle_turn(
        "What is the WL mortality A/E?", state, llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(),
        provider=_RaiseOnGenerationProvider("FACTUAL_LOOKUP"),
    )
    assert result.blocked is True
    assert result.block_reason == "llm_error"
    # The assistant turn was saved (so the UI shows a message, not silence).
    assert state.turns[-1]["role"] == "assistant"


def test_provider_error_on_commentary_is_a_safe_block_not_an_exception():
    state = _state()
    result = handle_turn(
        "Draft a commentary on WL mortality.", state, llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(),
        provider=_RaiseOnGenerationProvider("COMMENTARY_GENERATION"),
    )
    assert result.blocked is True
    assert result.block_reason == "llm_error"
    assert state.turns[-1]["role"] == "assistant"
