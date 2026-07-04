"""LLM provider abstraction + mock provider (§7.7; Session 18).

Provider-agnostic client: every LLM-dependent feature calls :func:`complete`,
and provider/model are runtime configuration (``config/llm_config.yaml``), never
code (FR-3B-01/03). No provider SDK is imported at package scope — the concrete
providers import their SDK lazily inside ``complete`` so this package imports
with zero network and zero keys (FR-3B-06).
"""
from src.ai.llm.base import LLMProvider, LLMProviderError
from src.ai.llm.client import (
    available_models,
    build_provider,
    complete,
    load_llm_config,
    resolve_provider,
)
from src.ai.llm.mock_provider import MockProvider, canonical_key

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "MockProvider",
    "canonical_key",
    "available_models",
    "build_provider",
    "complete",
    "load_llm_config",
    "resolve_provider",
]
