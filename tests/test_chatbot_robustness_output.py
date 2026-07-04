"""Robustness — area C: how the chatbot assembles the final response/output.

assemble_response context, ChatTurnResult shape per path, per-turn audit fields,
and Markdown export of banners/warnings. Deterministic, offline.
"""
from __future__ import annotations

import copy
import json

from src.ai.chatbot.pipeline import assemble_response, handle_turn
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
from ui.ai_analyst_logic import export_conversation_markdown

_MODEL = "claude-sonnet-4-6"


def _state():
    return SessionState(session_id="robo", model_key=_MODEL)


class _SeqMCP:
    def __init__(self, results):
        self._results, self._i = list(results), 0

    def query_ae_results(self, sql):
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r

    def query_tev_results(self, sql):
        return {"error": "no_data", "message": "none"}


def _plan(*qs):
    return json.dumps({"queries": list(qs)})


_Q1 = {"label": "mortality", "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
       "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"}
_Q2 = {"label": "lapse", "sql": "SELECT SUM(actual_lapses)/NULLIF(SUM(expected_lapses),0) "
       "AS ae_lapse FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"}


def _turn_event(events):
    return next(e for e in events if e.get("event") == "turn")


# --------------------------------------------------------------------------- #
# assemble_response — partial context columns                                  #
# --------------------------------------------------------------------------- #

def test_assemble_response_renders_only_present_context_bits():
    single = {"columns": ["ae_count", "credibility_z"], "rows": [[0.57, 0.46]], "row_count": 1}
    out = assemble_response("A/E is 0.57.", single)
    assert "Statistical context" in out
    assert "credibility Z 0.46" in out
    assert "exposure" not in out          # no exposure column present
    assert "expected events" not in out   # no expected column present


# --------------------------------------------------------------------------- #
# ChatTurnResult shape per path                                                #
# --------------------------------------------------------------------------- #

def test_synthesis_result_shape():
    mcp = _SeqMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
        {"columns": ["ae_lapse"], "rows": [[0.95]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q1, _Q2),
        synthesis_answer_text="Mortality A/E 0.5718; lapse A/E 0.95.",
    )
    r = handle_turn(
        "compare", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert r.blocked is False
    assert r.sql.count("SELECT") == 2
    assert r.sql_outcome is SQLGateOutcome.PASS
    assert r.result_row_count == 2


def test_commentary_result_shape_has_no_sql():
    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION"),
        commentary_text="WL mortality A/E was 0.5718.",
    )
    r = handle_turn(
        "summarise", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
        commentary_facts={"by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}}}}]},
    )
    assert r.blocked is False
    assert r.sql is None
    assert r.sql_outcome is None
    assert r.result_row_count is None


# --------------------------------------------------------------------------- #
# Per-turn audit fields                                                         #
# --------------------------------------------------------------------------- #

def test_synthesis_audit_records_synthesis_templates_only():
    mcp = _SeqMCP([{"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q1),
        synthesis_answer_text="WL mortality A/E is 0.5718.",
    )
    events: list[dict] = []
    handle_turn(
        "WL mortality?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
        audit=events.append,
    )
    hashes = _turn_event(events)["prompt_template_hashes"]
    assert "synthesis_plan.md" in hashes
    assert "synthesis_answer.md" in hashes
    assert "sql_generation.md" not in hashes
    assert "routing.md" in hashes


def test_analyst_mode_flag_is_audited_as_unblocked_failure():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='WL' LIMIT 500",
            "A/E is {{col:ae_count}} and an unsupported 999.99 appears.",
        ),
    )
    events: list[dict] = []
    result = handle_turn(
        "WL A/E?", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
        analyst_mode=True, audit=events.append,
    )
    assert result.blocked is False
    turn = _turn_event(events)
    assert turn["blocked"] is False
    assert turn["traceability_passed"] is False
    assert turn["untraceable_nums"] and any("999.99" in n for n in turn["untraceable_nums"])


# --------------------------------------------------------------------------- #
# Markdown export preserves warnings/banners                                   #
# --------------------------------------------------------------------------- #

def test_export_preserves_analyst_mode_warning():
    state = _state()
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='WL' LIMIT 500",
            "A/E is {{col:ae_count}} and an unsupported 999.99 appears.",
        ),
    )
    handle_turn(
        "WL A/E?", state, llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider, analyst_mode=True,
    )
    md = export_conversation_markdown(state)
    assert "unverified" in md.lower()


def test_export_preserves_faithfulness_warning():
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    cfg["faithfulness_flag_threshold"] = 3
    state = _state()
    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION"),
        commentary_text="WL mortality A/E was 0.5718.",
        faithfulness_text="2",
    )
    handle_turn(
        "summarise", state, llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=provider,
        commentary_facts={"by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}}}}]},
    )
    md = export_conversation_markdown(state)
    assert "Low faithfulness" in md
    assert "AI-drafted" in md


def test_commentary_banner_is_first_even_for_short_prose():
    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION"),
        commentary_text="WL A/E 0.5718.",
    )
    r = handle_turn(
        "summarise", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
        commentary_facts={"by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}}}}]},
    )
    assert r.response_text.lstrip().startswith("**AI-drafted")
