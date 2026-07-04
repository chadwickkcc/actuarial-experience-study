"""Mock provider (Session 18; Tech Spec v2.0.1 §E.5, FR-3B-06).

A deterministic, fixture-driven, zero-network provider so the full pytest suite
passes with **no API keys present** (FR-3B-06 / NFR-T-06). Responses are keyed by
a sha256 of the canonicalised ``(model, system, messages[role, content])`` so the
same request always yields the same ``LLMResponse``, independent of process or
ordering. The hashing/fixture scheme is documented in
``tests/fixtures/llm/README.md``.

Lookup order for a request key:
  1. an in-memory ``responses`` mapping passed to the constructor;
  2. a fixture file ``{fixtures_dir}/{key}.json`` if ``fixtures_dir`` is set;
  3. a deterministic synthetic fallback derived from the key — still zero-network
     and reproducible, so tests that only need *a* stable response need not
     pre-compute any hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from src.utils.types import LLMResponse

_NAME = "mock"


def canonical_key(
    model: str,
    system: Optional[str],
    messages: list[dict],
) -> str:
    """Return the sha256 key for a request (the fixture identity).

    Canonicalisation (documented in the fixtures README so authors can
    reproduce it): a JSON object ``{"model", "system", "messages"}`` where
    ``messages`` keeps its order but each entry is reduced to ``{"role",
    "content"}``; serialized with ``sort_keys=True`` and compact separators,
    ``ensure_ascii=False``. ``system`` of ``None`` serializes as JSON ``null``.
    """
    payload = {
        "model": model,
        "system": system,
        "messages": [
            {"role": m.get("role"), "content": m.get("content")} for m in messages
        ],
    }
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _approx_tokens(text: str) -> int:
    """Deterministic synthetic token count (~4 chars/token), min 1 for non-empty."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class MockProvider:
    """Deterministic, fixture-keyed stand-in for a real LLM provider."""

    name = _NAME

    def __init__(
        self,
        responses: Optional[dict[str, dict]] = None,
        fixtures_dir: Optional[Path] = None,
    ) -> None:
        self._responses = dict(responses) if responses else {}
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir else None

    def register(self, key: str, payload: dict) -> None:
        """Register a canned response payload for a precomputed request key."""
        self._responses[key] = dict(payload)

    def complete(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: Optional[str] = None,
    ) -> LLMResponse:
        key = canonical_key(model, system, messages)
        payload = self._lookup(key)
        if payload is None:
            # Deterministic synthetic fallback — stable for a given request.
            text = f"[MOCK:{key[:12]}] deterministic response"
            in_text = (system or "") + "".join(m.get("content", "") for m in messages)
            payload = {
                "text": text,
                "input_tokens": _approx_tokens(in_text),
                "output_tokens": _approx_tokens(text),
                "stop_reason": "end_turn",
            }
        return LLMResponse(
            text=payload["text"],
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            provider=self.name,
            model=model,
            latency_ms=0.0,
            stop_reason=payload.get("stop_reason"),
        )

    def _lookup(self, key: str) -> Optional[dict]:
        if key in self._responses:
            return self._responses[key]
        if self._fixtures_dir is not None:
            fixture = self._fixtures_dir / f"{key}.json"
            if fixture.exists():
                with fixture.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
        return None
