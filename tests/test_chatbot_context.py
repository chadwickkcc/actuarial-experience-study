"""A/E answers carry exposure + credibility context (FR-3B-35)."""
from __future__ import annotations

from src.ai.chatbot.pipeline import assemble_response, handle_turn
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
_AE = {
    "columns": ["ae_count", "exposure_count", "expected_deaths_count", "credibility_z"],
    "rows": [[0.92, 15000.0, 1200.0, 0.87]],
    "row_count": 1,
}
_SQL = (
    "SELECT ae_count, exposure_count, expected_deaths_count, credibility_z "
    "FROM gold_ae_results WHERE product_code='WL' LIMIT 500"
)


def test_assemble_response_appends_context_when_present():
    out = assemble_response("The A/E is 0.92.", _AE)
    assert "Statistical context" in out
    assert "exposure 15000" in out
    assert "expected events 1200" in out
    assert "credibility Z 0.87" in out


def test_assemble_response_noop_without_context_columns():
    bare = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
    assert assemble_response("A/E 0.92.", bare) == "A/E 0.92."


def test_ae_answer_carries_context_end_to_end():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(_SQL, "The count-based A/E is {{col:ae_count}}."),
    )
    result = handle_turn(
        "WL mortality A/E with context?", SessionState(session_id="c", model_key=_MODEL),
        llm_cfg(), StubMCP(ae=_AE), allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert "credibility Z 0.87" in result.response_text
    assert "expected events 1200" in result.response_text
    assert result.traceability is not None and result.traceability.passed
