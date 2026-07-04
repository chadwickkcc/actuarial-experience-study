"""Round-3 Phase B: opt-in Analyst mode (flag-not-block), default OFF.

Numeric traceability is the default hard guarantee (FR-3B-34): an untraceable
number blocks. Analyst mode (explicit opt-in) instead renders the answer with a
visible "unverified figures" warning and logs it — but the SQL safety gates never
relax in either mode.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
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
_AE = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
_SQL = "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500"


def _state():
    return SessionState(session_id="am", model_key=_MODEL)


def test_untraceable_number_blocks_when_analyst_mode_off():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(_SQL, "A/E is {{col:ae_count}} and mortality fell 999.99%."),
    )
    result = handle_turn(
        "Term A/E?", _state(), llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,  # analyst_mode defaults off
    )
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"


def test_untraceable_number_flagged_not_blocked_when_analyst_mode_on():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(_SQL, "A/E is {{col:ae_count}} and mortality fell 999.99%."),
    )
    result = handle_turn(
        "Term A/E?", _state(), llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, analyst_mode=True,
    )
    assert result.blocked is False
    assert "999.99" in result.response_text
    # A visible warning is attached and the traceability failure is recorded.
    assert "unverified" in result.response_text.lower()
    assert result.traceability is not None and result.traceability.passed is False


def test_sql_gate_still_rejects_in_analyst_mode():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply("DROP TABLE gold_ae_results", "x {{col:ae_count}}"),
    )
    result = handle_turn(
        "drop it", _state(), llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, analyst_mode=True,
    )
    assert result.blocked is True
    assert result.sql_outcome is SQLGateOutcome.REJECT_NOT_SELECT
