"""AI Analyst app-side logic (Session 21; FR-3B-43).

Conversation export preserves banners; the model list greys missing-key models;
RAG artifact resolution finds the tool's own methodology docs for a run. No DB or
network required (config + Reference Materials are read-only on disk).
"""
from __future__ import annotations

from ui import ai_analyst_logic as logic
from src.ai.chatbot.session import SessionState


def test_export_conversation_preserves_banner_and_metadata():
    state = SessionState(session_id="exp12345", model_key="claude-sonnet-4-6")
    state.add_turn("user", "Summarise Term.")
    state.add_turn(
        "assistant",
        "**AI-drafted — pending actuary review.**\n\nTerm A/E was 0.92.",
        {"commentary": True},
    )
    state.tokens_used = 123
    state.cost_estimate = 0.0042
    md = logic.export_conversation_markdown(state)
    assert "AI-drafted — pending actuary review" in md
    assert "Term A/E was 0.92." in md
    assert "exp12345" in md and "claude-sonnet-4-6" in md
    assert "123" in md and "0.0042" in md


def test_available_models_greys_missing_keys():
    # Keys are unset in the test environment -> every model is disabled with a reason.
    models = logic.available_analyst_models()
    assert models, "expected configured models"
    assert all(set(("model_id", "display_name", "enabled")) <= set(m) for m in models)
    for m in models:
        if not m["enabled"]:
            assert m["disabled_reason"]


def test_default_model_key_is_configured():
    assert logic.default_model_key() in [m["model_id"] for m in logic.available_analyst_models()]


def test_resolve_rag_for_run_returns_methodology_docs():
    resolved = logic.resolve_rag_for_run("ed193b59-c5d6-48cd-b5e6-43d33464dff8")
    assert set(resolved) == {"reports", "methodology"}
    # The shipped methodology docs exist on disk and are grounded.
    assert resolved["methodology"], "expected shipped methodology docs to resolve"
    assert all(p.endswith(".md") for p in resolved["methodology"])


def test_chatbot_config_exposes_rag_and_faithfulness_keys():
    cfg = logic.chatbot_config()
    assert "rag" in cfg and "methodology_docs" in cfg["rag"]
    assert cfg.get("faithfulness_llm_judge") is False  # off by default (FR-3B-46)


def test_run_turn_threads_faithfulness_toggle(monkeypatch):
    """The sidebar toggle overrides the config flag for the turn (FR-3B-46)."""
    from src.utils.types import ChatTurnResult, IntentLabel, LLMResponse

    captured: dict = {}

    def fake_handle_turn(user_msg, state, cfg, mcp, allow, **kw):
        captured.clear()
        captured.update(kw)
        return ChatTurnResult(
            session_id=state.session_id, intent=IntentLabel.FACTUAL_LOOKUP,
            response_text="ok", sql=None, sql_outcome=None, result_row_count=None,
            traceability=None,
            llm_response=LLMResponse(
                text="", input_tokens=0, output_tokens=0,
                provider="x", model="m", latency_ms=0.0,
            ),
            blocked=False, block_reason=None,
        )

    monkeypatch.setattr(logic, "handle_turn", fake_handle_turn)
    state = SessionState(session_id="t", model_key="claude-sonnet-4-6")

    logic.run_turn("Term A/E?", state, run_id=None, faithfulness=True)
    assert captured["chatbot_cfg"]["faithfulness_llm_judge"] is True

    logic.run_turn("Term A/E?", state, run_id=None, faithfulness=None)
    assert captured["chatbot_cfg"].get("faithfulness_llm_judge") is False
