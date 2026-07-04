"""Routing over-refusal fixes (post-UAT round 6, Doc-1/Doc-2).

Two legitimate data questions were wrongly refused as OUT_OF_SCOPE: a cross-product
credibility-ranking question and a PVFP profit-source-margin question. The offline
guards here cover the deterministic parts of the fix:
  * the bounded re-route retry that distinguishes an unparseable routing reply from
    a genuine refusal (so a data question is not silently defaulted to OUT_OF_SCOPE);
  * the enriched routing prompt guidance and the new few-shot examples.
The actual model-side classification is an owner-triggered live re-test.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import classify_intent, load_few_shots
from src.utils.types import IntentLabel, LLMResponse
from tests.chatbot_helpers import FEW_SHOTS, llm_cfg, routing_reply
from ui.config import CONFIG_DIR

_MODEL = "claude-sonnet-4-6"


class _RerouteProvider:
    """Returns the next scripted routing reply on each routing call (zero network)."""

    name = "reroute"

    def __init__(self, routing_sequence):
        self._routing = list(routing_sequence)
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        self.calls.append({"system": system or ""})
        text = ""
        if "Intent router" in (system or ""):
            text = self._routing[min(self._i, len(self._routing) - 1)]
            self._i += 1
        return LLMResponse(
            text=text, input_tokens=5, output_tokens=5,
            provider=self.name, model=model, latency_ms=1.0, stop_reason="end_turn",
        )

    def routing_calls(self) -> int:
        return sum(1 for c in self.calls if "Intent router" in c["system"])


def test_reroute_recovers_unparseable_routing():
    # First reply has no INTENT line (e.g. a token-capped reasoning reply); the
    # retry returns a valid label -> the data question is NOT refused.
    provider = _RerouteProvider(["(thinking, no answer yet)", routing_reply("EXPLORATORY")])
    intent, _ = classify_intent(
        "Across products, where is our experience most credible and where is it thinnest?",
        llm_cfg(), _MODEL, provider=provider,
    )
    assert intent is IntentLabel.EXPLORATORY
    assert provider.routing_calls() == 2  # one re-route, then stop


def test_reroute_gives_up_safely_to_out_of_scope():
    # Two unparseable replies -> the safe OUT_OF_SCOPE default still applies, and we
    # do not loop indefinitely (exactly one retry).
    provider = _RerouteProvider(["nope", "still nothing usable"])
    intent, _ = classify_intent("hello there", llm_cfg(), _MODEL, provider=provider)
    assert intent is IntentLabel.OUT_OF_SCOPE
    assert provider.routing_calls() == 2


def test_parseable_routing_makes_no_retry():
    provider = _RerouteProvider([routing_reply("FACTUAL_LOOKUP")])
    intent, _ = classify_intent("What is the WL mortality A/E?", llm_cfg(), _MODEL, provider=provider)
    assert intent is IntentLabel.FACTUAL_LOOKUP
    assert provider.routing_calls() == 1  # no retry when the first reply parses


def test_routing_prompt_covers_superlatives_and_margins():
    text = (CONFIG_DIR / "prompts" / "routing.md").read_text(encoding="utf-8").lower()
    assert "most credible" in text
    assert "profit-source margin" in text
    assert "reconciliation pass" in text  # multi-part status question example


def test_few_shots_cover_credibility_ranking_and_pvfp_margins():
    blob = " ".join(
        (p["question"] + " " + p["sql"]).lower() for p in load_few_shots(FEW_SHOTS)
    )
    assert "pvfp_mortality_margin" in blob          # PVFP profit-source margin example
    assert "most credible" in blob                  # cross-product credibility example


def test_sql_prompt_forbids_per_cell_credibility_for_aggregates():
    # The residual #2 fix: an overall/aggregate answer must not select/average a
    # per-cell credibility_z*; the system appends the correct aggregate Z.
    text = (CONFIG_DIR / "prompts" / "sql_generation.md").read_text(encoding="utf-8").lower()
    assert "per-cell" in text
    assert "avg(credibility_z_lapse)" in text       # the antipattern is named
    assert "appends the correct aggregate" in text
