"""Fact-pack commentary path + faithfulness judge (round 3; FR-3B-36/37/38/46).

Round 3 replaced the single-SQL commentary route with a generate-then-verify route
over an app-assembled **fact pack** (like the memo Skill): the model writes prose,
no SQL or slot-fill; every number is then checked verbatim against the fact pack
(``run_id`` excluded) plus the tool's own grounding context. An invented number is
blocked (default) or flagged (Analyst mode). The persistent AI-draft banner is
present and survives export. The faithfulness judge is off by default and, when on,
flags (never blocks) and is logged. Scripted provider — keys unset, no network.
"""
from __future__ import annotations

import copy

from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.utils.types import IntentLabel
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)

_MODEL = "claude-sonnet-4-6"
_AE = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
_AI_BANNER = "AI-drafted — pending actuary review"

# An app-assembled fact pack (display-rounded), with a run_id whose digits must NOT
# leak into the traceable allowed-set.
_FACTS = {
    "run_id": "run1aaaa",
    "products": ["WL", "TERM"],
    "by_product": [
        {"product": "WL", "decrements": {"MORTALITY": {"overall": {
            "actual": 232, "expected": 405.7611, "ae_ratio": 0.5718,
            "exposure": 14652.9374, "credibility_z": 0.4631,
        }}}},
    ],
}


def _state():
    return SessionState(session_id="c1", model_key=_MODEL)


def _ground(tmp_path):
    p = tmp_path / "working_actuary_run1aaaa.html"
    p.write_text("<p>The block exposure was 1234 policy-years.</p>", encoding="utf-8")
    return {"reports": [str(p)]}


def _commentary_provider(prose: str, faithfulness: str = "") -> ScriptedProvider:
    return ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION", "asks for prose"),
        commentary_text=prose,
        faithfulness_text=faithfulness,
    )


def _run(provider, tmp_path, *, cfg=None, facts=_FACTS, events=None, analyst_mode=None):
    return handle_turn(
        "Summarise the Whole Life mortality result.", _state(), llm_cfg(),
        StubMCP(ae=_AE), allowlist(), chatbot_cfg=cfg or chatbot_cfg(),
        provider=provider, audit=(events.append if events is not None else None),
        rag_run_ids=["run1aaaa"], rag_artifact_paths=_ground(tmp_path),
        commentary_facts=facts, analyst_mode=analyst_mode,
    )


def test_commentary_is_grounded_and_banner_tagged(tmp_path):
    provider = _commentary_provider(
        "Whole Life mortality A/E was 0.5718 (232 actual deaths against 405.7611 "
        "expected). The block exposure was 1234 policy-years."
    )
    result = _run(provider, tmp_path)
    assert result.intent is IntentLabel.COMMENTARY_GENERATION
    assert result.blocked is False
    assert _AI_BANNER in result.response_text
    assert "0.5718" in result.response_text   # from the fact pack
    assert "232" in result.response_text      # from the fact pack
    assert "1234" in result.response_text     # quoted from the grounding report
    assert result.traceability is not None and result.traceability.passed
    # No SQL is generated on the commentary route any more.
    assert result.sql is None


def test_commentary_blocks_an_invented_number(tmp_path):
    provider = _commentary_provider(
        "Whole Life mortality A/E was 0.5718, and an unsupported figure 999 appears."
    )
    result = _run(provider, tmp_path)
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"


def test_commentary_invented_number_flagged_in_analyst_mode(tmp_path):
    provider = _commentary_provider(
        "Whole Life mortality A/E was 0.5718, plus an unsupported 999."
    )
    result = _run(provider, tmp_path, analyst_mode=True)
    assert result.blocked is False
    assert "999" in result.response_text
    assert "unverified" in result.response_text.lower()


def test_commentary_run_id_digits_excluded_from_traceable_set(tmp_path):
    # A distinctive run_id digit-run must NOT become traceable (run_id is excluded
    # so its UUID digits can't mask an invented figure).
    facts = {
        "run_id": "run-77777",
        "by_product": [{"product": "WL", "decrements": {"MORTALITY": {
            "overall": {"ae_ratio": 0.5718}
        }}}],
    }
    provider = _commentary_provider("WL A/E was 0.5718; a stray 77777 also appears.")
    result = _run(provider, tmp_path, facts=facts)
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"


def test_empty_prose_blocks_as_generation_failed(tmp_path):
    result = _run(_commentary_provider(""), tmp_path)
    assert result.blocked is True
    assert result.block_reason == "commentary_generation_failed"


def test_banner_survives_markdown_export(tmp_path):
    from ui.ai_analyst_logic import export_conversation_markdown

    state = SessionState(session_id="c1", model_key=_MODEL)
    handle_turn(
        "Summarise WL mortality.", state, llm_cfg(), StubMCP(ae=_AE), allowlist(),
        chatbot_cfg=chatbot_cfg(),
        provider=_commentary_provider("WL mortality A/E was 0.5718."),
        rag_run_ids=["run1aaaa"], rag_artifact_paths=_ground(tmp_path),
        commentary_facts=_FACTS,
    )
    assert _AI_BANNER in export_conversation_markdown(state)


def test_faithfulness_off_by_default_no_judge_call(tmp_path):
    provider = _commentary_provider("WL mortality A/E was 0.5718.", faithfulness="2")
    events: list[dict] = []
    _run(provider, tmp_path, events=events)
    assert not any("Faithfulness judge" in (c["system"] or "") for c in provider.calls)
    turn = next(e for e in events if e.get("event") == "turn")
    assert turn["faithfulness_score"] is None


def test_faithfulness_on_low_score_flags_not_blocks_and_is_logged(tmp_path):
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    cfg["faithfulness_flag_threshold"] = 3
    provider = _commentary_provider("WL mortality A/E was 0.5718.", faithfulness="2")
    events: list[dict] = []
    result = _run(provider, tmp_path, cfg=cfg, events=events)
    assert result.blocked is False                       # flag, never block
    assert "Low faithfulness" in result.response_text
    turn = next(e for e in events if e.get("event") == "turn")
    assert turn["faithfulness_score"] == 2
    assert any("Faithfulness judge" in (c["system"] or "") for c in provider.calls)


def test_faithfulness_high_score_no_warning(tmp_path):
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    provider = _commentary_provider("WL mortality A/E was 0.5718.", faithfulness="5")
    result = _run(provider, tmp_path, cfg=cfg)
    assert result.blocked is False
    assert "Low faithfulness" not in result.response_text


def test_faithfulness_unparseable_score_no_warning_no_log(tmp_path):
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["faithfulness_llm_judge"] = True
    provider = _commentary_provider(
        "WL mortality A/E was 0.5718.", faithfulness="not a score"
    )
    events: list[dict] = []
    result = _run(provider, tmp_path, cfg=cfg, events=events)
    assert result.blocked is False
    assert "Low faithfulness" not in result.response_text
    turn = next(e for e in events if e.get("event") == "turn")
    assert turn["faithfulness_score"] is None


def test_commentary_audit_records_commentary_hash_and_context_ref(tmp_path):
    events: list[dict] = []
    _run(_commentary_provider("WL mortality A/E was 0.5718."), tmp_path, events=events)
    turn = next(e for e in events if e.get("event") == "turn")
    assert "commentary.md" in turn["prompt_template_hashes"]
    assert "routing.md" in turn["prompt_template_hashes"]
    assert turn["retrieved_context_ref"]["run_ids"] == ["run1aaaa"]


def test_score_faithfulness_wrapper_parses_and_handles_unparseable():
    """The public faithfulness wrapper still parses a 1-5 score / None."""
    from src.ai.chatbot.pipeline import score_faithfulness

    good = _commentary_provider("ignored", faithfulness="4")
    assert score_faithfulness("draft", "grounding", llm_cfg(), _MODEL, provider=good) == 4
    bad = _commentary_provider("ignored", faithfulness="no number here")
    assert score_faithfulness("d", "g", llm_cfg(), _MODEL, provider=bad) is None


def test_generate_commentary_plan_wrapper_still_parses_json():
    """The legacy JSON wrapper is retained and still parses a {sql, template}."""
    from src.ai.chatbot.pipeline import generate_commentary_plan

    provider = ScriptedProvider(
        routing_reply("COMMENTARY_GENERATION"),
        commentary_text=sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500",
            "A/E is {{col:ae_count}}.",
        ),
    )
    plan = generate_commentary_plan(
        "summary", [], "grounding", llm_cfg(), _MODEL, provider=provider
    )
    assert plan is not None and plan["sql"].startswith("SELECT")
