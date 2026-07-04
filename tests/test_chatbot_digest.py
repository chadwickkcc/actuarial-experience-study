"""Study-digest-in-every-turn tests (governed-maximum build, Step D).

The AI Analyst injects a compact, app-assembled "study at a glance" digest (the
same fact pack used for commentary) into the routing / SQL-gen / synthesis system
prompts, and adds its figures to the numeric-traceability allowed-set on the data
paths — so the model always knows the whole study's shape and a digest figure it
quotes still traces to the database. The digest defaults to ``None`` (eval/tests
unaffected); these tests exercise it explicitly.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import _render_digest, handle_turn
from src.ai.chatbot.session import SessionState
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)

_DIGEST = {
    "run_id": "abc123",
    "study_period": "2016-01-01 to 2023-12-31",
    "products": ["TERM", "WL"],
    "tev_baseline": 1234.0,
    "delta_tev_vs_prior": None,
    "by_product": [
        {
            "product": "WL",
            "decrements": {
                "MORTALITY": {
                    "overall": {
                        "actual": 232,
                        "expected": 405.7611,
                        "exposure": 14652.9374,
                        "ae_ratio": 0.5718,
                        "credibility_z": 0.4631,
                    },
                    "by_segment": [],
                }
            },
        }
    ],
}


def test_render_digest_includes_products_overall_ae_and_tev():
    text = _render_digest(_DIGEST)
    assert "Study at a glance" in text
    assert "TERM, WL" in text          # product coverage
    assert "WL mortality" in text      # per-product per-decrement overall line
    assert "0.5718" in text            # overall A/E
    assert "0.4631" in text            # aggregate credibility Z
    assert "1234" in text              # baseline TEV


def test_render_digest_empty_inputs_are_inert():
    assert _render_digest(None) == ""
    assert _render_digest({}) == ""
    # A product with no overall A/E for a decrement contributes no line.
    assert "overall A/E" not in _render_digest(
        {"products": ["TERM"], "by_product": [{"product": "TERM", "decrements": {}}]}
    )


def _state() -> SessionState:
    return SessionState(session_id="s-digest", model_key="claude-sonnet-4-6")


def test_digest_text_reaches_the_sqlgen_system_prompt():
    prov = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code = 'WL' LIMIT 500",
            "The WL mortality A/E is {{col:ae_count}}.",
        ),
    )
    mcp = StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5718]], "row_count": 1})
    handle_turn(
        "What is the WL mortality A/E?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=prov, study_digest=_DIGEST,
    )
    sqlgen_calls = [c for c in prov.calls if "SQL generation" in (c["system"] or "")]
    assert sqlgen_calls, "expected a SQL-generation call"
    assert "Study at a glance" in sqlgen_calls[0]["system"]
    assert "WL mortality" in sqlgen_calls[0]["system"]


def test_digest_only_figure_is_traceable_on_the_data_path():
    """A figure present only in the digest (not the SQL result) still traces."""
    prov = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code = 'WL' LIMIT 500",
            # 1234 is the baseline TEV — present in the digest, NOT in this result.
            "WL mortality A/E is {{col:ae_count}}; the baseline TEV is 1234.",
        ),
    )
    mcp = StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5718]], "row_count": 1})
    # Default (hard) numeric traceability — analyst_mode off — so a non-traceable
    # number would BLOCK. With the digest joined to the allowed-set, 1234 traces.
    result = handle_turn(
        "WL mortality A/E and baseline TEV?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=prov, study_digest=_DIGEST,
        analyst_mode=False,
    )
    assert not result.blocked, f"digest figure should trace; got {result.block_reason}"
    assert result.traceability is not None and result.traceability.passed


def _synth_plan(sql: str) -> str:
    import json
    return json.dumps({"queries": [{"label": "wl", "sql": sql}]})


def test_digest_reaches_synthesis_path_and_traces():
    """On the multi-query (synthesis) path the digest is injected into the planner
    + synthesiser prompts and joined to the traceability allowed-set, so a
    digest-only figure in the synthesised prose traces (even in strict mode)."""
    prov = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_synth_plan(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='WL' LIMIT 5"
        ),
        # 1234 is the baseline TEV — only in the digest, not in the evidence below.
        synthesis_answer_text="Across products the baseline embedded value is 1234.",
    )
    mcp = StubMCP(ae={"columns": ["ae_count"], "rows": [[0.57]], "row_count": 1})
    result = handle_turn(
        "Compare the products and summarise the embedded value.", _state(),
        llm_cfg(), mcp, allowlist(), chatbot_cfg=chatbot_cfg(), provider=prov,
        study_digest=_DIGEST, multi_query=True, analyst_mode=False,
    )
    assert not result.blocked, f"digest figure should trace on synthesis path; got {result.block_reason}"
    assert "1234" in result.response_text
    synth_calls = [
        c for c in prov.calls
        if "Evidence planner" in (c["system"] or "") or "Evidence synthesis" in (c["system"] or "")
    ]
    assert synth_calls, "expected synthesis planner/answer calls"
    assert all("Study at a glance" in c["system"] for c in synth_calls)


def test_digest_only_figure_blocks_without_the_digest():
    """Control: the same digest-only figure blocks when no digest is supplied."""
    prov = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code = 'WL' LIMIT 500",
            "WL mortality A/E is {{col:ae_count}}; the baseline TEV is 1234.",
        ),
    )
    mcp = StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5718]], "row_count": 1})
    result = handle_turn(
        "WL mortality A/E and baseline TEV?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=prov, study_digest=None,
        analyst_mode=False,
    )
    assert result.blocked and result.block_reason == "numeric_traceability"


def test_digest_widens_but_does_not_defeat_traceability():
    """Safety lock: a genuinely invented number (not in the digest, the result, or
    the user message) STILL blocks even with the digest present — the digest only
    makes *data-sourced* figures traceable, it does not defang the post-check."""
    prov = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code = 'WL' LIMIT 500",
            # 98765 is nowhere — not in the digest, the result, or the question.
            "WL mortality A/E is {{col:ae_count}}; an unrelated figure is 98765.",
        ),
    )
    mcp = StubMCP(ae={"columns": ["ae_count"], "rows": [[0.5718]], "row_count": 1})
    result = handle_turn(
        "WL mortality A/E?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=prov, study_digest=_DIGEST,
        analyst_mode=False,
    )
    assert result.blocked and result.block_reason == "numeric_traceability"
    assert result.traceability is not None and "98765" in result.traceability.untraceable_nums
