"""MockProvider determinism + fixture tests (Session 18; FR-3B-06)."""
from __future__ import annotations

import json

from src.ai.llm.mock_provider import MockProvider, canonical_key


def _msgs():
    return [{"role": "user", "content": "count-based A/E for Term?"}]


def test_canonical_key_is_deterministic():
    a = canonical_key("claude-sonnet-4-6", "sys", _msgs())
    b = canonical_key("claude-sonnet-4-6", "sys", _msgs())
    assert a == b and len(a) == 64


def test_canonical_key_varies_with_inputs():
    base = canonical_key("claude-sonnet-4-6", None, _msgs())
    assert base != canonical_key("deepseek-v4-pro", None, _msgs())          # model
    assert base != canonical_key("claude-sonnet-4-6", "sys", _msgs())       # system
    assert base != canonical_key(
        "claude-sonnet-4-6", None, [{"role": "user", "content": "other"}])  # message


def test_canonical_key_ignores_extra_message_keys():
    a = canonical_key("m", None, [{"role": "user", "content": "hi"}])
    b = canonical_key("m", None, [{"role": "user", "content": "hi", "name": "x"}])
    assert a == b


def test_complete_is_deterministic_and_tagged():
    mp = MockProvider()
    r1 = mp.complete(_msgs(), model="claude-sonnet-4-6", max_tokens=64)
    r2 = mp.complete(_msgs(), model="claude-sonnet-4-6", max_tokens=64)
    assert r1.provider == "mock"
    assert r1.text == r2.text
    assert (r1.input_tokens, r1.output_tokens) == (r2.input_tokens, r2.output_tokens)
    assert r1.output_tokens > 0          # synthetic but non-zero for non-empty text
    assert r1.latency_ms == 0.0


def test_registered_response_is_served():
    mp = MockProvider()
    key = canonical_key("claude-sonnet-4-6", None, _msgs())
    mp.register(key, {"text": "CANNED", "input_tokens": 3, "output_tokens": 1,
                      "stop_reason": "end_turn"})
    resp = mp.complete(_msgs(), model="claude-sonnet-4-6", max_tokens=64)
    assert resp.text == "CANNED"
    assert resp.input_tokens == 3 and resp.output_tokens == 1
    assert resp.stop_reason == "end_turn"


def test_fixture_file_is_served(tmp_path):
    key = canonical_key("claude-sonnet-4-6", "router", _msgs())
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"text": "FROM_FIXTURE", "input_tokens": 5, "output_tokens": 2}),
        encoding="utf-8",
    )
    mp = MockProvider(fixtures_dir=tmp_path)
    resp = mp.complete(_msgs(), model="claude-sonnet-4-6", max_tokens=64, system="router")
    assert resp.text == "FROM_FIXTURE"
    assert resp.input_tokens == 5 and resp.output_tokens == 2


def test_unknown_request_uses_deterministic_fallback(tmp_path):
    mp = MockProvider(fixtures_dir=tmp_path)  # empty dir → fallback path
    resp = mp.complete(_msgs(), model="claude-sonnet-4-6", max_tokens=64)
    assert resp.text.startswith("[MOCK:")
    assert resp.provider == "mock"
