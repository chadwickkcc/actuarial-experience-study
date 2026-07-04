"""LLM client: config loading, model availability, and provider dispatch
(Session 18; Tech Spec v2.0.1 §E.5, FR-3B-01..06).

All LLM-dependent features call :func:`complete`; provider and model are runtime
configuration from ``config/llm_config.yaml`` (§F.2), never code (FR-3B-03 /
NFR-CF-11). API keys are read from environment variables only — never from YAML,
code, logs, or the audit trail (FR-3B-04). No provider SDK is imported here
(FR-3B-01); the concrete provider modules import their SDK lazily.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from src.ai.llm.base import LLMProvider, LLMProviderError
from src.utils.types import LLMResponse

_KEY_MISSING_REASON = "API key not configured"


def load_llm_config(path: Path) -> dict:
    """Parse ``llm_config.yaml`` into a dict (§F.2).

    Returns the raw mapping (providers, model strings, display names, base URLs,
    ``api_key_env`` names, pricing, ``default_model``, timeout, retries). Raises
    ``LLMProviderError`` if the file is missing or has no ``providers`` block.
    """
    path = Path(path)
    if not path.exists():
        raise LLMProviderError(f"LLM config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg.get("providers"), dict) or not cfg["providers"]:
        raise LLMProviderError("llm_config.yaml is missing a non-empty providers block")
    return cfg


def available_models(config: dict) -> list[dict]:
    """List every configured model with its UI-display and enablement state.

    A model is ``enabled`` only when its provider's ``api_key_env`` resolves to a
    non-empty environment variable; otherwise it greys out with
    ``disabled_reason = "API key not configured"`` and the app still functions
    (FR-3B-04). Reads the environment only — never imports an SDK or makes a call.
    """
    out: list[dict] = []
    for provider_name, provider_cfg in config.get("providers", {}).items():
        key_env = provider_cfg.get("api_key_env", "")
        has_key = bool(os.environ.get(key_env, "").strip())
        for model in provider_cfg.get("models", []) or []:
            out.append({
                "provider": provider_name,
                "model_id": model["id"],
                "display_name": model.get("display_name", model["id"]),
                "enabled": has_key,
                "disabled_reason": None if has_key else _KEY_MISSING_REASON,
                "price_per_mtok_input": model.get("price_per_mtok_input"),
                "price_per_mtok_output": model.get("price_per_mtok_output"),
            })
    return out


def resolve_provider(config: dict, model_key: str) -> tuple[str, dict, dict]:
    """Resolve ``model_key`` to ``(provider_name, provider_cfg, model_cfg)``.

    Pure dispatch — no environment, no SDK, no network — so the routing logic is
    directly testable with no API keys present. Raises ``LLMProviderError`` if no
    configured provider owns ``model_key``.
    """
    for provider_name, provider_cfg in config.get("providers", {}).items():
        for model in provider_cfg.get("models", []) or []:
            if model.get("id") == model_key:
                return provider_name, provider_cfg, model
    raise LLMProviderError(f"Unknown model '{model_key}' (not in llm_config.yaml)")


def build_provider(
    provider_name: str,
    provider_cfg: dict,
    *,
    timeout_seconds: float,
    max_retries: int,
) -> LLMProvider:
    """Construct the concrete provider for ``provider_name``, reading its API key
    from the configured environment variable only (FR-3B-04).

    Raises ``LLMProviderError`` if the key is absent (callers surface this without
    crashing) or the provider name is unknown.
    """
    key_env = provider_cfg.get("api_key_env", "")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise LLMProviderError(
            f"{_KEY_MISSING_REASON} for provider '{provider_name}' "
            f"(set {key_env or '<api_key_env>'})."
        )

    if provider_name == "anthropic":
        from src.ai.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_key, timeout_seconds=timeout_seconds, max_retries=max_retries
        )
    if provider_name == "deepseek":
        from src.ai.llm.deepseek_provider import DeepSeekProvider
        base_url = provider_cfg.get("base_url")
        if not base_url:
            raise LLMProviderError("DeepSeek provider config is missing base_url")
        return DeepSeekProvider(
            api_key, base_url, timeout_seconds=timeout_seconds, max_retries=max_retries
        )
    raise LLMProviderError(f"Unsupported provider '{provider_name}'")


def complete(
    config: dict,
    model_key: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float = 0.0,
    system: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
) -> LLMResponse:
    """Resolve ``model_key`` to its provider and run one non-streaming completion.

    ``provider`` may be injected (e.g. the ``MockProvider`` in tests, or a shared
    client) — when given it is used directly and config is consulted only for the
    model dispatch. Otherwise the concrete provider is built from config + env
    (key from the env var only, never logged). On terminal provider failure an
    ``LLMProviderError`` with a user-safe message propagates so the caller can
    surface it without crashing the session (FR-3B-05).
    """
    resolve_provider(config, model_key)  # validates the model is configured
    if provider is None:
        provider_name, provider_cfg, _ = resolve_provider(config, model_key)
        provider = build_provider(
            provider_name,
            provider_cfg,
            timeout_seconds=float(config.get("request_timeout_seconds", 60)),
            max_retries=int(config.get("max_retries", 2)),
        )
    return provider.complete(
        messages=messages,
        model=model_key,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
    )
