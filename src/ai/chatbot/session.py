"""Chatbot session state (Session 20; Tech Spec v2.0.1 §E.7).

A thin holder for one conversation: the running turn history, the currently
selected model (switchable mid-session, FR-3B-45), and the running token / cost
totals that drive the budget controls (FR-3B-44) and the cost display (FR-3B-43).
No LLM, no SQL, no DB — pure state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.ai.llm.client import resolve_provider
from src.utils.types import LLMResponse


@dataclass
class SessionState:
    """In-memory state for one chatbot conversation."""
    session_id:    str
    model_key:     str                              # current selection (FR-3B-45)
    turns:         list[dict] = field(default_factory=list)  # [{role, content, meta}]
    tokens_used:   int = 0                          # running, for the budget (FR-3B-44)
    cost_estimate: float = 0.0                      # running USD estimate (FR-3B-43)

    def add_turn(self, role: str, content: str, meta: Optional[dict] = None) -> None:
        """Append a turn to the history (``meta`` carries per-turn annotations)."""
        self.turns.append({"role": role, "content": content, "meta": meta or {}})


def model_prices(cfg: dict, model_key: str) -> tuple[float, float]:
    """Return ``(price_per_mtok_input, price_per_mtok_output)`` for ``model_key``.

    Reads the per-model pricing from ``llm_config.yaml`` (the only place prices
    live, FR-3B-03). Missing/placeholder prices resolve to ``0.0`` so the cost
    estimate degrades gracefully rather than raising.
    """
    _, _, model_cfg = resolve_provider(cfg, model_key)

    def _price(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return (
        _price(model_cfg.get("price_per_mtok_input")),
        _price(model_cfg.get("price_per_mtok_output")),
    )


def call_cost(response: LLMResponse, price_in: float, price_out: float) -> float:
    """USD cost of one completion from its token counts and per-Mtok prices."""
    return (
        response.input_tokens * price_in + response.output_tokens * price_out
    ) / 1_000_000.0


def record_call(state: SessionState, response: LLMResponse, cfg: dict) -> None:
    """Fold one completion's tokens and cost into the running session totals."""
    price_in, price_out = model_prices(cfg, response.model)
    state.tokens_used += response.input_tokens + response.output_tokens
    state.cost_estimate += call_cost(response, price_in, price_out)
