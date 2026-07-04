"""Anthropic provider (Session 18; Tech Spec v2.0.1 §E.5, FR-3B-01/02/05).

The ``anthropic`` SDK is imported **lazily inside** :meth:`complete` so that
importing this module never requires the SDK or a network connection: a missing
SDK degrades exactly like a missing API key (a clear ``LLMProviderError``),
keeping the offline test suite green (FR-3B-06). No SDK import appears at module
scope (FR-3B-01).
"""
from __future__ import annotations

import time
from typing import Optional

from src.ai.llm.base import LLMProviderError
from src.utils.types import LLMResponse


class AnthropicProvider:
    """Calls Anthropic's Messages API and returns a unified ``LLMResponse``."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    def complete(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: Optional[str] = None,
    ) -> LLMResponse:
        try:
            import anthropic  # lazy: see module docstring
        except ImportError as err:  # pragma: no cover - SDK present in lockfile
            raise LLMProviderError(
                "The Anthropic provider is unavailable (the 'anthropic' package "
                "is not installed)."
            ) from err

        client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        # `temperature` is intentionally NOT forwarded to the Anthropic Messages
        # API. The configured Anthropic models are the modern family (Opus 4.8,
        # Sonnet 4.6); on Opus 4.7+/Fable the sampling parameters
        # (temperature/top_p/top_k) are removed and a request carrying them is
        # rejected with a 400. The abstraction keeps `temperature` in its
        # signature for interface uniformity (and the DeepSeek/OpenAI-compatible
        # path does forward it), but Anthropic determinism is the default and
        # does not use it. `_ = temperature` documents the deliberate non-use.
        _ = temperature
        if system is not None:
            kwargs["system"] = system

        last_err: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            start = time.monotonic()
            try:
                resp = client.messages.create(**kwargs)
            except Exception as err:  # noqa: BLE001 - SDK raises many error types
                last_err = err
                if attempt < self._max_retries:
                    continue
                break
            latency_ms = (time.monotonic() - start) * 1000.0
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", None) == "text"
            )
            return LLMResponse(
                text=text,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                provider=self.name,
                model=model,
                latency_ms=latency_ms,
                stop_reason=getattr(resp, "stop_reason", None),
            )

        raise LLMProviderError(
            f"Anthropic request failed after {self._max_retries + 1} attempt(s)."
        ) from last_err
