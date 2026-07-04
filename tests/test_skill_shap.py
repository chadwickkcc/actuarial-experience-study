"""Tests for the explain_shap_results Skill (Session 19; FR-3B-21..23).

MockProvider / stub only. The explanation must speak in mapped actuarial language
(never raw feature names, FR-3B-22), make no causal claims or recommendations,
and block-not-repair on an untraceable number.
"""
from __future__ import annotations

from src.ai.llm.client import load_llm_config
from src.ai.llm.mock_provider import MockProvider
from src.ai.skills.shap_explain import explain_shap_results
from src.utils.types import LLMResponse
from ui.config import CONFIG_DIR

_SHAP_CELL = {
    "decrement": "MORTALITY",
    "product_code": "WL",
    "grain_key": {"product": "WL", "duration_band": "6-10"},
    "base_value": 0.0,
    "prediction": 0.07,
    "contributions": [
        {"feature": "duration_band", "shap_value": 0.12, "feature_value": "6-10"},
        {"feature": "attained_age_band", "shap_value": -0.05, "feature_value": "50-54"},
    ],
}

_FEATURE_MAP = {
    "duration_band": {"actuarial_term": "policy duration", "assumption_dimension": "select-period mortality"},
    "attained_age_band": {"actuarial_term": "attained age", "assumption_dimension": "attained-age mortality curve"},
}

_CLEAN_BODY = (
    "For the WL segment, the model started from a base value of 0.0 and reached a "
    "prediction of 0.07. Policy duration pushed the factor up by 0.12, while "
    "attained age pulled it down by -0.05. These are the dominant contributions."
)

_CORRUPT_BODY = _CLEAN_BODY + " An additional 0.99 effect appeared."


class _StubProvider:
    name = "stub"

    def __init__(self, text):
        self._text = text

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        return LLMResponse(
            text=self._text, input_tokens=8, output_tokens=16,
            provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn",
        )


def _cfg():
    return load_llm_config(CONFIG_DIR / "llm_config.yaml")


def test_shap_uses_actuarial_terms_not_raw_feature_names():
    out = explain_shap_results(
        _SHAP_CELL, _FEATURE_MAP, _cfg(), "claude-sonnet-4-6",
        provider=_StubProvider(_CLEAN_BODY),
    )
    assert out["blocked"] is False
    md = out["markdown"]
    low = md.lower()
    assert "AI-DRAFT" in md
    assert "policy duration" in low and "attained age" in low
    # No raw covariate / one-hot feature names leak into the explanation.
    assert "duration_band" not in md
    assert "attained_age_band" not in md
    assert out["hashes"].get("skills/shap_explain.md")


def test_shap_blocks_on_untraceable_number():
    out = explain_shap_results(
        _SHAP_CELL, _FEATURE_MAP, _cfg(), "claude-sonnet-4-6",
        provider=_StubProvider(_CORRUPT_BODY),
    )
    assert out["blocked"] is True
    assert any("0.99" in n for n in out["untraceable_nums"])
    assert not out.get("markdown")


def test_shap_is_provider_agnostic_via_mock_provider():
    out = explain_shap_results(
        _SHAP_CELL, _FEATURE_MAP, _cfg(), "deepseek-v4-pro", provider=MockProvider()
    )
    assert out["model"] == "deepseek-v4-pro"
    assert "blocked" in out and "hashes" in out


class _CapturingProvider:
    name = "capture"

    def __init__(self, text):
        self._text = text
        self.messages = None

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        self.messages = messages
        return LLMResponse(self._text, 1, 1, self.name, model, 0.0, "end_turn")


def test_shap_raw_feature_names_never_reach_the_llm():
    # The translation-before-prompt guard (FR-3B-22): the LLM input carries the
    # actuarial term, never the raw covariate / one-hot name.
    cap = _CapturingProvider(_CLEAN_BODY)
    explain_shap_results(_SHAP_CELL, _FEATURE_MAP, _cfg(), "claude-sonnet-4-6", provider=cap)
    sent = cap.messages[0]["content"]
    assert "policy duration" in sent
    assert "attained age" in sent
    assert "duration_band" not in sent
    assert "attained_age_band" not in sent


def test_shap_block_still_returns_hashes_and_model():
    out = explain_shap_results(
        _SHAP_CELL, _FEATURE_MAP, _cfg(), "claude-sonnet-4-6",
        provider=_StubProvider(_CORRUPT_BODY),
    )
    assert out["blocked"] is True
    assert out["hashes"].get("skills/shap_explain.md")
    assert out["model"] == "claude-sonnet-4-6"


def test_shap_unmapped_feature_falls_back_to_readable_name_no_raw_underscore():
    cell = dict(_SHAP_CELL)
    cell["contributions"] = [
        {"feature": "risk_class", "shap_value": 0.03, "feature_value": "PNT"},
    ]
    cap = _CapturingProvider("Underwriting risk class added 0.03 from a base of 0.0 to 0.07.")
    explain_shap_results(cell, {}, _cfg(), "claude-sonnet-4-6", provider=cap)
    sent = cap.messages[0]["content"]
    # No mapping supplied → readable fallback ("risk class"), never the raw token.
    assert "risk class" in sent
    assert "risk_class" not in sent


def test_shap_blocks_on_empty_response():
    # An empty completion must BLOCK, not emit a tag+footer-only explanation.
    out = explain_shap_results(
        _SHAP_CELL, _FEATURE_MAP, _cfg(), "deepseek-v4-pro", provider=_StubProvider("")
    )
    assert out["blocked"] is True
    assert not out.get("markdown")
    assert "empty response" in (out.get("reason") or "").lower()
    assert out["hashes"].get("skills/shap_explain.md")
