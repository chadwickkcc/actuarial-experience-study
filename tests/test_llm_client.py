"""LLM client dispatch + availability tests (Session 18; FR-3B-01..06).

All tests run with no API keys and via the MockProvider — no network, no SDK
calls (FR-3B-06 / NFR-T-06).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ai.llm import (
    LLMProviderError,
    MockProvider,
    available_models,
    build_provider,
    complete,
    load_llm_config,
    resolve_provider,
)
from src.utils.types import LLMResponse

_CONFIG = Path("config/llm_config.yaml")
_KEYS = ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"]


@pytest.fixture()
def cfg() -> dict:
    return load_llm_config(_CONFIG)


def _clear_keys(monkeypatch) -> None:
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_llm_config_shape(cfg):
    assert cfg["default_model"] == "claude-sonnet-4-6"
    assert cfg["request_timeout_seconds"] == 60
    assert cfg["max_retries"] == 2
    assert set(cfg["providers"]) == {"anthropic", "deepseek"}
    assert cfg["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com"


def test_load_llm_config_missing_file(tmp_path):
    with pytest.raises(LLMProviderError):
        load_llm_config(tmp_path / "nope.yaml")


def test_load_llm_config_missing_providers_block(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("default_model: x\n", encoding="utf-8")
    with pytest.raises(LLMProviderError):
        load_llm_config(cfg)


def test_available_models_surfaces_prices(cfg, monkeypatch):
    _clear_keys(monkeypatch)
    by_id = {m["model_id"]: m for m in available_models(cfg)}
    # pricing was filled from public rates (Session 18); surfaced for the cost display
    assert by_id["claude-opus-4-8"]["price_per_mtok_input"] == 5.0
    assert by_id["claude-opus-4-8"]["price_per_mtok_output"] == 25.0
    assert by_id["deepseek-v4-flash"]["price_per_mtok_input"] == 0.14


def test_build_provider_returns_concrete_provider(cfg, monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
    _, anthropic_cfg, _ = resolve_provider(cfg, "claude-opus-4-8")
    _, deepseek_cfg, _ = resolve_provider(cfg, "deepseek-v4-pro")
    a = build_provider("anthropic", anthropic_cfg, timeout_seconds=60, max_retries=2)
    d = build_provider("deepseek", deepseek_cfg, timeout_seconds=60, max_retries=2)
    assert type(a).__name__ == "AnthropicProvider"
    assert type(d).__name__ == "DeepSeekProvider"


def test_build_provider_missing_key_raises(cfg, monkeypatch):
    _clear_keys(monkeypatch)
    _, anthropic_cfg, _ = resolve_provider(cfg, "claude-opus-4-8")
    with pytest.raises(LLMProviderError):
        build_provider("anthropic", anthropic_cfg, timeout_seconds=60, max_retries=2)


@pytest.mark.parametrize(
    "model_key,expected_provider",
    [
        ("claude-opus-4-8", "anthropic"),
        ("claude-sonnet-4-6", "anthropic"),
        ("deepseek-v4-pro", "deepseek"),
        ("deepseek-v4-flash", "deepseek"),
    ],
)
def test_resolve_provider_dispatch(cfg, model_key, expected_provider):
    provider_name, provider_cfg, model_cfg = resolve_provider(cfg, model_key)
    assert provider_name == expected_provider
    assert model_cfg["id"] == model_key
    assert provider_cfg["api_key_env"]  # carries the env-var name, not a key


def test_resolve_provider_unknown_model(cfg):
    with pytest.raises(LLMProviderError):
        resolve_provider(cfg, "gpt-9-ultra")


def test_available_models_grey_out_when_no_keys(cfg, monkeypatch):
    _clear_keys(monkeypatch)
    models = available_models(cfg)
    assert len(models) == 4
    assert all(m["enabled"] is False for m in models)
    assert all(m["disabled_reason"] == "API key not configured" for m in models)
    # model strings come only from config, surfaced for the dropdown
    assert {m["model_id"] for m in models} == {
        "claude-opus-4-8", "claude-sonnet-4-6", "deepseek-v4-pro", "deepseek-v4-flash",
    }


def test_available_models_enabled_when_key_present(cfg, monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    by_id = {m["model_id"]: m for m in available_models(cfg)}
    assert by_id["claude-opus-4-8"]["enabled"] is True
    assert by_id["claude-opus-4-8"]["disabled_reason"] is None
    # DeepSeek still greyed — its key is absent; the app still functions.
    assert by_id["deepseek-v4-pro"]["enabled"] is False


def test_complete_dispatches_via_injected_provider(cfg, monkeypatch):
    _clear_keys(monkeypatch)  # prove no key needed when a provider is injected
    mock = MockProvider()
    resp = complete(
        cfg,
        "claude-sonnet-4-6",
        [{"role": "user", "content": "What is the A/E?"}],
        max_tokens=64,
        provider=mock,
    )
    assert isinstance(resp, LLMResponse)
    assert resp.provider == "mock"
    assert resp.model == "claude-sonnet-4-6"


def test_complete_unknown_model_raises(cfg):
    with pytest.raises(LLMProviderError):
        complete(cfg, "gpt-9-ultra", [{"role": "user", "content": "hi"}], max_tokens=8,
                 provider=MockProvider())


def test_complete_without_key_raises_user_safe_error(cfg, monkeypatch):
    """No injected provider and no key → LLMProviderError, never a crash (FR-3B-05)."""
    _clear_keys(monkeypatch)
    with pytest.raises(LLMProviderError) as err:
        complete(cfg, "claude-opus-4-8", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert "API key not configured" in str(err.value)
