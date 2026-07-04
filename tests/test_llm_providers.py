"""Provider modules: response mapping, retries, missing-SDK, request shape.

The Anthropic/DeepSeek providers lazy-import their SDK inside ``complete()``, so
these tests inject a fake SDK module into ``sys.modules`` — no network, no real
keys (FR-3B-06). They cover the only Session-18 code the mock-provider path
cannot reach: the SDK request shape and the success/error translation.
"""
from __future__ import annotations

import sys
import types

import pytest

from src.ai.llm.anthropic_provider import AnthropicProvider
from src.ai.llm.deepseek_provider import DeepSeekProvider
from src.ai.llm.base import LLMProviderError
from src.utils.types import LLMResponse


# --------------------------------------------------------------------------- #
# Fake Anthropic SDK                                                          #
# --------------------------------------------------------------------------- #

class _ABlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _AUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _AResp:
    def __init__(self):
        self.content = [_ABlock("hello "), _ABlock("world")]
        self.usage = _AUsage(11, 7)
        self.stop_reason = "end_turn"


def _install_fake_anthropic(monkeypatch, create_fn):
    captured: dict = {}
    mod = types.ModuleType("anthropic")

    class Anthropic:
        def __init__(self, **kw):
            captured["ctor"] = kw
            self.messages = self

        def create(self, **kw):  # client.messages.create
            captured.setdefault("calls", []).append(kw)
            return create_fn(kw)

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return captured


def test_anthropic_maps_response_and_omits_temperature(monkeypatch):
    captured = _install_fake_anthropic(monkeypatch, lambda kw: _AResp())
    p = AnthropicProvider("sk-x", timeout_seconds=5, max_retries=2)
    r = p.complete(
        [{"role": "user", "content": "hi"}],
        model="claude-opus-4-8", max_tokens=64, temperature=0.7, system="sys",
    )
    assert isinstance(r, LLMResponse)
    assert r.text == "hello world"
    assert (r.input_tokens, r.output_tokens) == (11, 7)
    assert r.provider == "anthropic" and r.model == "claude-opus-4-8"
    assert r.stop_reason == "end_turn"
    call = captured["calls"][0]
    # Modern Anthropic models reject sampling params — temperature must NOT be sent.
    assert "temperature" not in call
    assert call["model"] == "claude-opus-4-8" and call["max_tokens"] == 64
    assert call["system"] == "sys"
    assert captured["ctor"]["api_key"] == "sk-x"


def test_anthropic_omits_system_when_none(monkeypatch):
    captured = _install_fake_anthropic(monkeypatch, lambda kw: _AResp())
    AnthropicProvider("k").complete(
        [{"role": "user", "content": "hi"}], model="claude-sonnet-4-6", max_tokens=8)
    assert "system" not in captured["calls"][0]


def test_anthropic_retries_then_raises(monkeypatch):
    def boom(kw):
        raise RuntimeError("rate limit")
    captured = _install_fake_anthropic(monkeypatch, boom)
    p = AnthropicProvider("k", max_retries=2)
    with pytest.raises(LLMProviderError):
        p.complete([{"role": "user", "content": "hi"}], model="claude-opus-4-8", max_tokens=8)
    assert len(captured["calls"]) == 3  # 1 initial + 2 retries


def test_anthropic_missing_sdk_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ImportError
    with pytest.raises(LLMProviderError):
        AnthropicProvider("k").complete(
            [{"role": "user", "content": "hi"}], model="claude-opus-4-8", max_tokens=8)


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK (DeepSeek path)                                             #
# --------------------------------------------------------------------------- #

class _Choice:
    def __init__(self):
        self.message = types.SimpleNamespace(content="ds answer")
        self.finish_reason = "stop"


class _DSResp:
    def __init__(self):
        self.choices = [_Choice()]
        self.usage = types.SimpleNamespace(prompt_tokens=9, completion_tokens=4)


def _install_fake_openai(monkeypatch, create_fn):
    captured: dict = {}
    mod = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, **kw):
            captured["ctor"] = kw
            self.chat = types.SimpleNamespace(completions=self)

        def create(self, **kw):  # client.chat.completions.create
            captured.setdefault("calls", []).append(kw)
            return create_fn(kw)

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return captured


def test_deepseek_maps_response_prepends_system_forwards_temperature(monkeypatch):
    captured = _install_fake_openai(monkeypatch, lambda kw: _DSResp())
    p = DeepSeekProvider("dk", "https://api.deepseek.com", timeout_seconds=5, max_retries=1)
    r = p.complete(
        [{"role": "user", "content": "hi"}],
        model="deepseek-v4-pro", max_tokens=32, temperature=0.3, system="sys",
    )
    assert r.provider == "deepseek" and r.model == "deepseek-v4-pro"
    assert r.text == "ds answer"
    assert (r.input_tokens, r.output_tokens) == (9, 4) and r.stop_reason == "stop"
    call = captured["calls"][0]
    # OpenAI-compatible endpoint accepts temperature — it IS forwarded here.
    assert call["temperature"] == 0.3
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert call["messages"][1] == {"role": "user", "content": "hi"}
    assert captured["ctor"]["base_url"] == "https://api.deepseek.com"
    assert captured["ctor"]["api_key"] == "dk"


def test_deepseek_no_system_does_not_prepend(monkeypatch):
    captured = _install_fake_openai(monkeypatch, lambda kw: _DSResp())
    DeepSeekProvider("dk", "https://api.deepseek.com").complete(
        [{"role": "user", "content": "hi"}], model="deepseek-v4-flash", max_tokens=8)
    assert captured["calls"][0]["messages"][0] == {"role": "user", "content": "hi"}


def test_deepseek_retries_then_raises(monkeypatch):
    def boom(kw):
        raise RuntimeError("boom")
    captured = _install_fake_openai(monkeypatch, boom)
    with pytest.raises(LLMProviderError):
        DeepSeekProvider("dk", "https://api.deepseek.com", max_retries=2).complete(
            [{"role": "user", "content": "hi"}], model="deepseek-v4-flash", max_tokens=8)
    assert len(captured["calls"]) == 3


def test_deepseek_missing_sdk_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # from openai import OpenAI -> ImportError
    with pytest.raises(LLMProviderError):
        DeepSeekProvider("k", "https://api.deepseek.com").complete(
            [{"role": "user", "content": "hi"}], model="deepseek-v4-pro", max_tokens=8)


class _EmptyChoice:
    def __init__(self, finish_reason):
        self.message = types.SimpleNamespace(content="")
        self.finish_reason = finish_reason


class _EmptyResp:
    def __init__(self, finish_reason):
        self.choices = [_EmptyChoice(finish_reason)]
        self.usage = types.SimpleNamespace(prompt_tokens=5, completion_tokens=0)


def test_deepseek_empty_content_raises_not_silent(monkeypatch):
    # An empty completion must surface as an error, never an empty LLMResponse —
    # otherwise a Skill emits a tag+footer-only draft (UAT Part 3 defect).
    _install_fake_openai(monkeypatch, lambda kw: _EmptyResp("stop"))
    with pytest.raises(LLMProviderError):
        DeepSeekProvider("dk", "https://api.deepseek.com", max_retries=0).complete(
            [{"role": "user", "content": "hi"}], model="deepseek-v4-pro", max_tokens=8)


def test_deepseek_truncated_empty_content_hints_at_token_limit(monkeypatch):
    _install_fake_openai(monkeypatch, lambda kw: _EmptyResp("length"))
    with pytest.raises(LLMProviderError) as ei:
        DeepSeekProvider("dk", "https://api.deepseek.com", max_retries=0).complete(
            [{"role": "user", "content": "hi"}], model="deepseek-v4-pro", max_tokens=8)
    assert "max_tokens" in str(ei.value)
