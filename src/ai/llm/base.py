"""Provider-agnostic LLM interface (Session 18; Tech Spec v2.0.1 §E.5).

Every LLM-dependent feature (intent routing, SQL generation, commentary, Skills)
calls a single internal client interface; provider and model are runtime
configuration, never code (FR-3B-01/03). This module defines the Protocol all
providers implement plus the shared error type. No provider SDK is imported here
or anywhere outside the concrete provider modules (FR-3B-01).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.utils.types import LLMResponse


class LLMProviderError(Exception):
    """Raised when an LLM call terminally fails (timeout, rate limit, auth,
    missing SDK or key) after the configured retries.

    Carries a user-safe ``message`` so callers (the chatbot/UI) can surface it
    without crashing the session or leaking provider internals (FR-3B-05).
    """


@runtime_checkable
class LLMProvider(Protocol):
    """The single completion contract every provider implements (FR-3B-01).

    Non-streaming only — streaming is out of scope for Phase 3. Implementations
    translate provider-native token-usage fields into ``input_tokens`` /
    ``output_tokens`` on the returned :class:`LLMResponse`.
    """

    def complete(
        self,
        messages: list[dict],          # [{"role": "...", "content": "..."}]
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: Optional[str] = None,
    ) -> LLMResponse:
        ...
