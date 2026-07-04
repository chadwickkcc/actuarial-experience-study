"""Shared offline helpers for the Session-20 chatbot tests (not collected).

Provides a scripted, zero-network provider that returns the routing reply or the
SQL-generation reply depending on which system prompt it was called with, a stub
MCP client that returns canned result sets (with an optional ordering hook), and
loaders for the shipped config so the tests exercise the real prompts/allowlist.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import yaml

from src.ai.llm.client import load_llm_config
from src.utils.sql_boundary import load_allowlist
from src.utils.types import LLMResponse
from ui.config import CONFIG_DIR

AI_CONFIG = CONFIG_DIR / "ai_config.yaml"
LLM_CONFIG = CONFIG_DIR / "llm_config.yaml"
FEW_SHOTS = CONFIG_DIR / "chatbot_few_shots.yaml"


def llm_cfg() -> dict:
    return load_llm_config(LLM_CONFIG)


def allowlist() -> dict:
    return load_allowlist(AI_CONFIG)


def chatbot_cfg() -> dict:
    with AI_CONFIG.open("r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("chatbot", {})


class ScriptedProvider:
    """Returns a routing / SQL-gen / commentary / faithfulness reply by system prompt.

    Routes on a distinctive marker in each prompt template's title so one provider
    drives a whole turn. Records every call so tests can assert the provider was
    (not) invoked and on which model.
    """

    name = "scripted"

    def __init__(
        self,
        routing_text: str,
        sqlgen_text: str = "",
        commentary_text: str = "",
        faithfulness_text: str = "",
        synthesis_plan_text: str = "",
        synthesis_answer_text: str = "",
    ):
        self._routing = routing_text
        self._sqlgen = sqlgen_text
        self._commentary = commentary_text
        self._faithfulness = faithfulness_text
        self._synthesis_plan = synthesis_plan_text
        self._synthesis_answer = synthesis_answer_text
        self.calls: list[dict] = []

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        self.calls.append(
            {"system": system, "messages": messages, "model": model,
             "max_tokens": max_tokens}
        )
        system = system or ""
        if "Intent router" in system:
            text = self._routing
        elif "SQL generation" in system:
            text = self._sqlgen
        elif "Commentary drafting" in system:
            text = self._commentary
        elif "Faithfulness judge" in system:
            text = self._faithfulness
        elif "Evidence planner" in system:
            text = self._synthesis_plan
        elif "Evidence synthesis" in system:
            text = self._synthesis_answer
        else:  # pragma: no cover - defensive
            text = ""
        return LLMResponse(
            text=text, input_tokens=12, output_tokens=24,
            provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn",
        )


class StubMCP:
    """Canned MCP client: returns fixed result dicts; emits an optional hook."""

    def __init__(self, ae=None, tev=None, on_call: Optional[Callable[[dict], None]] = None):
        self._ae = ae
        self._tev = tev
        self._on_call = on_call

    def _emit(self, tool: str, sql: str) -> None:
        if self._on_call is not None:
            self._on_call({"event": "data_access", "tool": tool, "sql": sql})

    def query_ae_results(self, sql: str) -> dict:
        self._emit("query_ae_results", sql)
        return self._ae if self._ae is not None else {"error": "no_data", "message": "none"}

    def query_tev_results(self, sql: str) -> dict:
        self._emit("query_tev_results", sql)
        return self._tev if self._tev is not None else {"error": "no_data", "message": "none"}


def routing_reply(label: str, reason: str = "test") -> str:
    return f"INTENT: {label}\nREASON: {reason}"


def sqlgen_reply(sql: str, answer_template: str) -> str:
    import json

    return json.dumps({"sql": sql, "answer_template": answer_template})
