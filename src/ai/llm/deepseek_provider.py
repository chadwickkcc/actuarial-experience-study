"""DeepSeek provider (Session 18; Tech Spec v2.0.1 §E.5, FR-3B-02).

DeepSeek exposes an OpenAI-compatible endpoint, so this provider uses the
``openai`` SDK pointed at the DeepSeek ``base_url`` (FR-3B-02). The SDK is
imported **lazily inside** :meth:`complete` (see :mod:`anthropic_provider` for
the rationale): importing this module never needs the SDK or a network, and a
missing SDK degrades like a missing key (FR-3B-05/06). No SDK import at module
scope (FR-3B-01).
"""
from __future__ import annotations

import time
from typing import Optional

from src.ai.llm.base import LLMProviderError
from src.utils.types import LLMResponse


class DeepSeekProvider:
    """Calls DeepSeek's OpenAI-compatible chat-completions API."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
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
            from openai import OpenAI  # lazy: see module docstring
        except ImportError as err:  # pragma: no cover - SDK present in lockfile
            raise LLMProviderError(
                "The DeepSeek provider is unavailable (the 'openai' package is "
                "not installed)."
            ) from err

        # OpenAI-style chat APIs carry the system prompt as the first message,
        # not a top-level field (unlike Anthropic).
        chat_messages: list[dict] = []
        if system is not None:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend(messages)

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,  # retry loop is handled here so it is provider-uniform
        )

        last_err: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            start = time.monotonic()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=chat_messages,
                )
            except Exception as err:  # noqa: BLE001 - SDK raises many error types
                last_err = err
                if attempt < self._max_retries:
                    continue
                break
            latency_ms = (time.monotonic() - start) * 1000.0
            choice = resp.choices[0]
            usage = resp.usage
            content = getattr(choice.message, "content", None) or ""
            finish = getattr(choice, "finish_reason", None)
            # Don't silently return an empty completion (DeepSeek V4 is a
            # reasoning model; if max_tokens is exhausted on reasoning the
            # answer `content` comes back empty with finish_reason='length').
            # Surface it as a clear error rather than an empty draft (FR-3B-05).
            if not content.strip():
                if finish == "length":
                    raise LLMProviderError(
                        "DeepSeek returned an empty response — it was truncated at "
                        f"the token cap (finish_reason='length', max_tokens={max_tokens}). "
                        "Increase max_tokens for this call, or try a different model."
                    )
                raise LLMProviderError(
                    "DeepSeek returned an empty response "
                    f"(finish_reason={finish!r}). Try again or use a different model."
                )
            return LLMResponse(
                text=content,
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                provider=self.name,
                model=model,
                latency_ms=latency_ms,
                stop_reason=finish,
            )

        raise LLMProviderError(
            f"DeepSeek request failed after {self._max_retries + 1} attempt(s)."
        ) from last_err
