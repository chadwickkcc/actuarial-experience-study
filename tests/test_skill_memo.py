"""Tests for the interpret_ae_and_draft_memo Skill (Session 19; FR-3B-17..20).

MockProvider / stub only — no live API, suite passes with keys unset. The Skill
runs on any configured model via the provider abstraction and **blocks, never
repairs** when a number fails the deterministic traceability post-check.

Since M-12 the model cites figures as ``{{fact:<key>}}`` and the application
substitutes them; a typed digit or an unknown key blocks the draft. The canned
bodies below therefore cite, and the assertions check the *substituted* value —
so they prove the citation actually resolves to the study's own number.
"""
from __future__ import annotations

from src.ai.llm.client import load_llm_config
from src.ai.llm.mock_provider import MockProvider
from src.ai.skills.memo import interpret_ae_and_draft_memo
from src.utils.types import LLMResponse
from ui.config import CONFIG_DIR

_COMPONENT_HEADERS = [
    "Purpose and Scope",
    "Data and Study Basis",
    "Key A/E Findings by Segment",
    "Credibility Assessment",
    "Proposed Assumption Change with Rationale",
    "Limitations and Caveats",
    "Recommendation and Required Sign-off",
]

_MEMO_INPUT = {
    "product": "WL",
    "study_period": "2016-2023",
    "ae_by_segment": [{"segment": "duration 6-10", "ae_count": 0.92, "credibility_z": 0.87}],
    "prior_assumption": 1.00,
    "tev_baseline": 173400000,
    "delta_tev_vs_prior": 4480000,
    "top_drivers": ["lapse", "mortality", "expense"],
    "exclusions": [],
    "run_id": "ed193b59-c5d6-48cd-b5e6-43d33464dff8",
}

_CLEAN_BODY = """## Purpose and Scope
This memo covers the {{fact:product}} experience study for {{fact:study_period}}.

## Data and Study Basis
The study window was {{fact:study_period}} on an annual exposure basis.

## Key A/E Findings by Segment
For {{fact:ae_by_segment[0].segment}} the count A/E was
{{fact:ae_by_segment[0].ae_count}}.

## Credibility Assessment
That cell carried credibility Z of {{fact:ae_by_segment[0].credibility_z}}.

## Proposed Assumption Change with Rationale
We propose moving from the prior {{fact:prior_assumption}} multiplier toward the
observed level.

## Limitations and Caveats
Sparse cells were excluded from validation.

## Recommendation and Required Sign-off
Actuary review and governance sign-off are required before any change.
"""

_CORRUPT_BODY = _CLEAN_BODY + "\n## Note\nMortality improved 999.99% this year."


class _StubProvider:
    """Protocol-conforming provider returning a fixed body and echoing the model."""

    name = "stub"

    def __init__(self, text: str):
        self._text = text

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        return LLMResponse(
            text=self._text, input_tokens=10, output_tokens=20,
            provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn",
        )


def _cfg():
    return load_llm_config(CONFIG_DIR / "llm_config.yaml")


def test_memo_has_tag_all_components_and_footer():
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(_CLEAN_BODY)
    )
    assert out["blocked"] is False
    md = out["markdown"]
    assert md.startswith("AI-DRAFT — requires actuary review and sign-off")
    for header in _COMPONENT_HEADERS:
        assert header in md, f"missing component: {header}"
    # Footer appended by the Skill (model, date, run_id) after the body.
    assert "claude-sonnet-4-6" in md
    assert _MEMO_INPUT["run_id"] in md
    assert out["model"] == "claude-sonnet-4-6"
    assert out["hashes"].get("skills/memo.md")


def test_memo_blocks_on_untraceable_number_not_repaired():
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(_CORRUPT_BODY)
    )
    assert out["blocked"] is True
    assert any("999.99" in n for n in out["untraceable_nums"])
    assert not out.get("markdown")          # blocked, not repaired


def test_memo_is_provider_agnostic_via_mock_provider():
    # Goes through the real shipped MockProvider (deterministic fallback, zero
    # network) under a different model — proves dispatch is model/provider-agnostic.
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "deepseek-v4-flash", provider=MockProvider()
    )
    assert out["model"] == "deepseek-v4-flash"
    assert "blocked" in out and "hashes" in out


class _CapturingProvider:
    """Records the system + messages it was called with, returns a fixed body."""

    name = "capture"

    def __init__(self, text):
        self._text = text
        self.system = None
        self.messages = None
        self.model = None

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        self.system, self.messages, self.model = system, messages, model
        return LLMResponse(self._text, 1, 1, self.name, model, 0.0, "end_turn")


def test_memo_sends_template_as_system_and_input_as_user_message():
    cap = _CapturingProvider(_CLEAN_BODY)
    interpret_ae_and_draft_memo(_MEMO_INPUT, _cfg(), "claude-opus-4-8", provider=cap)
    # System prompt is the versioned memo template body.
    assert "Seven required components" in cap.system
    # The app-assembled pack is the user message, as a flat citable catalogue.
    assert cap.messages[0]["role"] == "user"
    catalogue = cap.messages[0]["content"]
    assert "product = WL" in catalogue
    assert "ae_by_segment[0].ae_count = 0.92" in catalogue
    # run_id is withheld so its digit-runs cannot widen the allowed set.
    assert _MEMO_INPUT["run_id"] not in catalogue
    assert cap.model == "claude-opus-4-8"


def test_memo_is_model_agnostic_same_body_only_footer_differs():
    body = _CLEAN_BODY
    a = interpret_ae_and_draft_memo(_MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(body))
    b = interpret_ae_and_draft_memo(_MEMO_INPUT, _cfg(), "deepseek-v4-pro", provider=_StubProvider(body))
    assert a["blocked"] is False and b["blocked"] is False
    # The eight-component body is identical; only the footer's model line differs.
    assert "claude-sonnet-4-6" in a["markdown"] and "deepseek-v4-pro" in b["markdown"]
    assert "0.92" in a["markdown"]   # the citation resolved to the study's figure
    body_a = a["markdown"].split("\n\n---\n")[0]
    body_b = b["markdown"].split("\n\n---\n")[0]
    assert body_a == body_b


def test_memo_block_still_returns_hashes_and_model():
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(_CORRUPT_BODY)
    )
    assert out["blocked"] is True
    assert out["hashes"].get("skills/memo.md")
    assert out["model"] == "claude-sonnet-4-6"


def test_memo_run_id_digits_are_not_traceable():
    # 193 is a digit-run inside the run_id UUID but is not a real metric — it must
    # NOT be accepted (run_id is excluded from the allowed-number set).
    body = _CLEAN_BODY + "\n## Note\nA spurious 193 appeared."
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(body)
    )
    assert out["blocked"] is True
    assert any("193" in n for n in out["untraceable_nums"])


def test_memo_end_to_end_via_real_mock_provider_fixture_path():
    # Exercise the shipped MockProvider's keyed-fixture lookup (not the fallback):
    # register the canned body under the exact request key the Skill builds.
    from src.ai.llm.mock_provider import canonical_key
    from src.ai.prompts import load_prompt_template
    from src.ai.skills.facts import flatten_facts, render_fact_catalogue

    tpl = load_prompt_template("skills/memo.md")
    model = "claude-sonnet-4-6"
    catalogue = render_fact_catalogue(flatten_facts(_MEMO_INPUT, exclude=("run_id",)))
    messages = [{"role": "user", "content": catalogue}]
    key = canonical_key(model, tpl.text, messages)
    provider = MockProvider(responses={key: {"text": _CLEAN_BODY, "input_tokens": 10, "output_tokens": 20}})

    out = interpret_ae_and_draft_memo(_MEMO_INPUT, _cfg(), model, provider=provider)
    assert out["blocked"] is False
    for header in _COMPONENT_HEADERS:
        assert header in out["markdown"]


def test_memo_blocks_on_empty_response_not_tag_footer_only():
    # An empty completion (e.g. a reasoning model that exhausts its token budget
    # on reasoning) must BLOCK, not silently emit a tag+footer-only memo with a
    # blank body. Whitespace-only counts as empty.
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "deepseek-v4-pro", provider=_StubProvider("   \n  ")
    )
    assert out["blocked"] is True
    assert not out.get("markdown")                       # no tag+footer file
    assert "empty response" in (out.get("reason") or "").lower()
    assert not out.get("untraceable_nums")
    assert out["hashes"].get("skills/memo.md")
    assert out["model"] == "deepseek-v4-pro"


def test_memo_not_blocked_when_band_rendered_with_en_dash():
    # A cited segment label carries its band ("duration 6-10"), and the model
    # commonly re-renders that band in prose with an en-dash ("6–10") or "6 to
    # 10". Both endpoints must still trace, so the memo is NOT false-blocked
    # (regression guard for the band/date regex bug).
    body = _CLEAN_BODY + "\n## Note\nThe 6–10 band dominates the experience.\n"
    out = interpret_ae_and_draft_memo(
        _MEMO_INPUT, _cfg(), "claude-sonnet-4-6", provider=_StubProvider(body)
    )
    assert out["blocked"] is False, out.get("untraceable_nums")
    assert "6–10" in out["markdown"]
