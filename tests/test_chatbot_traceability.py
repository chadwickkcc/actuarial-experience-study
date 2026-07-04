"""Mandatory numeric post-check in the chatbot path (FR-3B-34).

Reuses the existing, unmodified Session-19 ``verify_traceability``. A non-traceable
number in the final answer blocks the turn (block-not-repair); a clean answer
passes.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
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
_SQL = "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500"


def _state():
    return SessionState(session_id="t", model_key=_MODEL)


def test_clean_slot_filled_answer_passes_traceability():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(_SQL, "The count-based A/E is {{col:ae_count}}."),
    )
    result = handle_turn(
        "Term mortality A/E?", _state(), llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert result.traceability is not None and result.traceability.passed
    assert "0.92" in result.response_text


def test_seeded_non_traceable_number_blocks_the_answer():
    # The answer template carries a literal number absent from the result set —
    # the post-check must block it (not repair).
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(_SQL, "A/E is {{col:ae_count}} and mortality fell 999.99%."),
    )
    result = handle_turn(
        "Term mortality A/E?", _state(), llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"
    assert result.traceability is not None
    assert any("999.99" in n for n in result.traceability.untraceable_nums)
