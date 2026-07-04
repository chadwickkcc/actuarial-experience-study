"""Robustness — area B: how the chatbot interacts with the LLM.

Model-switch on every answer path, reply/JSON parsing robustness, faithfulness
score parsing, provider errors mid-turn, and token/cost accumulation. Deterministic
and offline (scripted providers, no API keys).
"""
from __future__ import annotations

import copy
import json

from src.ai.chatbot.pipeline import (
    _parse_intent,
    _parse_plan,
    _parse_query_plan,
    _parse_score,
    handle_turn,
)
from src.ai.chatbot.session import SessionState
from src.ai.llm.base import LLMProviderError
from src.utils.types import IntentLabel, LLMResponse
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
_OTHER = "deepseek-v4-pro"


def _state(model=_MODEL):
    return SessionState(session_id="robl", model_key=model)


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


_Q = {"label": "overall", "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
      "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"}


def _models_used(provider, marker):
    return [c["model"] for c in provider.calls if marker in (c["system"] or "")]


# --------------------------------------------------------------------------- #
# Model switch honoured on every path                                          #
# --------------------------------------------------------------------------- #

def test_model_switch_used_on_synthesis_plan_and_answer():
    mcp = _SeqMCP([{"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q),
        synthesis_answer_text="WL mortality A/E is 0.5718.",
    )
    handle_turn(
        "WL mortality?", _state(_OTHER), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert _models_used(provider, "Evidence planner") == [_OTHER]
    assert _models_used(provider, "Evidence synthesis") == [_OTHER]


def test_model_switch_used_on_commentary_and_faithfulness(tmp_path):
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION"),
        commentary_text="WL mortality A/E was 0.5718.",
        faithfulness_text="4",
    )
    handle_turn(
        "Summarise WL mortality.", _state(_OTHER), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=provider,
        commentary_facts={"by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}}}}]},
    )
    assert _models_used(provider, "Commentary drafting") == [_OTHER]
    assert _models_used(provider, "Faithfulness judge") == [_OTHER]


# --------------------------------------------------------------------------- #
# Reply / JSON parsing robustness (pure parsers)                               #
# --------------------------------------------------------------------------- #

def test_parse_plan_handles_truncated_and_non_string_and_prose():
    assert _parse_plan('{"sql": "SELECT 1') is None                # truncated JSON
    assert _parse_plan('{"sql": 5, "answer_template": "x"}') is None  # non-string sql
    assert _parse_plan("here you go:\n```json\n{\"sql\":\"SELECT 1 AS a\","
                       "\"answer_template\":\"a={{col:a}}\"}\n```") is not None  # fenced+prose


def test_parse_query_plan_accepts_list_and_dict_and_rejects_garbage():
    listed = json.dumps([{"label": "x", "sql": "SELECT 1"}])
    assert len(_parse_query_plan(listed, 4)) == 1
    dicted = json.dumps({"queries": [{"label": "x", "sql": "SELECT 1"}]})
    assert len(_parse_query_plan(dicted, 4)) == 1
    assert _parse_query_plan("not json", 4) == []
    # items missing a usable sql are skipped
    assert _parse_query_plan(json.dumps({"queries": [{"label": "x"}, {"sql": ""}]}), 4) == []


def test_routing_label_variants_parse_or_failsafe():
    assert _parse_intent("INTENT:FACTUAL_LOOKUP\nREASON: y")[0] is IntentLabel.FACTUAL_LOOKUP
    assert _parse_intent("intent: exploratory\nreason: y")[0] is IntentLabel.EXPLORATORY
    assert _parse_intent("INTENT: BANANA\nREASON: y")[0] is IntentLabel.OUT_OF_SCOPE
    assert _parse_intent("no label at all")[0] is IntentLabel.OUT_OF_SCOPE


def test_faithfulness_score_parsing_variants():
    assert _parse_score("the score is 2") == 2
    assert _parse_score("2/5") == 2
    assert _parse_score("rated 5 out of 5") == 5
    assert _parse_score("7") is None          # out of the 1-5 range
    assert _parse_score("no number") is None


# --------------------------------------------------------------------------- #
# Provider errors mid-turn (never crash the session)                           #
# --------------------------------------------------------------------------- #

class _PlanOKAnswerRaises:
    name = "x"

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        system = system or ""
        if "Intent router" in system:
            return self._r(routing_reply("EXPLORATORY"), model)
        if "Evidence planner" in system:
            return self._r(_plan(_Q), model)
        if "Evidence synthesis" in system:
            raise LLMProviderError("truncated synthesis answer")
        return self._r("", model)

    @staticmethod
    def _r(text, model):
        return LLMResponse(text=text, input_tokens=10, output_tokens=5,
                           provider="x", model=model, latency_ms=0.0, stop_reason="end_turn")


def test_provider_error_mid_synthesis_is_a_safe_block():
    mcp = _SeqMCP([{"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}])
    state = _state()
    result = handle_turn(
        "WL mortality?", state, llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=_PlanOKAnswerRaises(), multi_query=True,
    )
    assert result.blocked is True
    assert result.block_reason == "llm_error"
    assert state.turns[-1]["role"] == "assistant"


class _CommentaryOKJudgeRaises:
    name = "x"

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        system = system or ""
        if "Intent router" in system:
            return self._r(routing_reply("COMMENTARY_GENERATION"), model)
        if "Commentary drafting" in system:
            return self._r("WL mortality A/E was 0.5718.", model)
        if "Faithfulness judge" in system:
            raise LLMProviderError("judge unavailable")
        return self._r("", model)

    @staticmethod
    def _r(text, model):
        return LLMResponse(text=text, input_tokens=10, output_tokens=5,
                           provider="x", model=model, latency_ms=0.0, stop_reason="end_turn")


def test_faithfulness_judge_error_does_not_block_the_draft():
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    result = handle_turn(
        "Summarise WL mortality.", _state(), llm_cfg(), StubMCP(), allowlist(),
        chatbot_cfg=cfg, provider=_CommentaryOKJudgeRaises(),
        commentary_facts={"by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}}}}]},
    )
    assert result.blocked is False
    assert "0.5718" in result.response_text
    assert "AI-drafted" in result.response_text


# --------------------------------------------------------------------------- #
# Token / cost accumulation across a multi-call turn                           #
# --------------------------------------------------------------------------- #

def test_token_cost_accumulate_across_synthesis_turn():
    mcp = _SeqMCP([{"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q),
        synthesis_answer_text="WL mortality A/E is 0.5718.",
    )
    state = _state()
    handle_turn(
        "WL mortality?", state, llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    # Three LLM calls (routing + plan + answer), each 12 in / 24 out via ScriptedProvider.
    assert len([c for c in provider.calls]) == 3
    assert state.tokens_used == 3 * (12 + 24)
    assert state.cost_estimate > 0
