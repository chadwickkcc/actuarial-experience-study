"""UAT remediation (Session 21 fixes): correct A/E aggregation, list slots,
single-row context guard, config-driven token caps, run scoping, routing history.

These guard the fixes for the owner-UAT findings: the chatbot quoting 0 for every
A/E figure (a query-grain artefact), "products covered = DA_FIA" (no list slot),
misleading row[0] context on multi-row results, and DeepSeek routing dying on a
too-small hardcoded token cap.
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import (
    _resolve_slots,
    assemble_response,
    handle_turn,
)
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

_MODEL = "claude-sonnet-4-6"


def _state():
    return SessionState(session_id="agg", model_key=_MODEL)


def _routing_calls(provider):
    return [c for c in provider.calls if "Intent router" in (c["system"] or "")]


def _sqlgen_calls(provider):
    return [c for c in provider.calls if "SQL generation" in (c["system"] or "")]


# --- {{list:}} slot --------------------------------------------------------- #

def test_list_slot_lists_all_distinct_values():
    products = {
        "columns": ["product_code"],
        "rows": [["TERM"], ["WL"], ["UL"], ["ULSG"], ["VUL"],
                 ["DA_FIXED"], ["DA_FIA"], ["DA_VA"], ["IUL"]],
        "row_count": 9,
    }
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        sqlgen_reply(
            "SELECT DISTINCT product_code FROM gold_ae_results LIMIT 500",
            "Products covered: {{list:product_code}}.",
        ),
    )
    result = handle_turn(
        "What products are covered?", _state(), llm_cfg(),
        StubMCP(ae=products), allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    for code in ["TERM", "WL", "UL", "ULSG", "VUL", "DA_FIXED", "DA_FIA", "DA_VA", "IUL"]:
        assert code in result.response_text


def test_list_slot_dedupes_preserving_order():
    res = {"columns": ["product_code"], "rows": [["WL"], ["WL"], ["TERM"]], "row_count": 3}
    filled, _ = _resolve_slots("{{list:product_code}}", res)
    assert filled == "WL, TERM"


def test_list_slot_numeric_values_stay_traceable():
    res = {"columns": ["ae_count"], "rows": [[0.5], [0.6]], "row_count": 2}
    filled, injected = _resolve_slots("Values: {{list:ae_count}}.", res)
    assert "0.5" in filled and "0.6" in filled
    assert 0.5 in injected and 0.6 in injected


def test_list_slot_unknown_column_blocks():
    res = {"columns": ["product_code"], "rows": [["WL"]], "row_count": 1}
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        sqlgen_reply(
            "SELECT product_code FROM gold_ae_results LIMIT 500",
            "Covered: {{list:nope}}.",
        ),
    )
    result = handle_turn(
        "products?", _state(), llm_cfg(), StubMCP(ae=res), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is True
    assert result.block_reason == "slot_fill_failed"
    # Fix 3: a non-security block gives an actionable hint, not the generic refusal.
    assert "table" in result.response_text.lower()
    assert "couldn't answer that safely" not in result.response_text.lower()


# --- table slot end-to-end (Fix 1: multi-row table requests) ---------------- #

def test_table_request_renders_markdown_table_end_to_end():
    res = {
        "columns": ["attained_age_band", "actual", "expected", "ae_count"],
        "rows": [
            ["45-49", 7, 6.9013, 1.0143],
            ["80-84", 58, 107.7838, 0.5381],
            ["90-94", 13, 29.5133, 0.4405],
        ],
        "row_count": 3,
    }
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT attained_age_band, SUM(actual_deaths_count) AS actual, "
            "SUM(expected_deaths_count) AS expected, "
            "SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) AS ae_count "
            "FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL "
            "GROUP BY attained_age_band ORDER BY attained_age_band LIMIT 500",
            "WL mortality A/E by attained age band:\n"
            "{{table:attained_age_band,actual,expected,ae_count}}",
        ),
    )
    result = handle_turn(
        "table of WL mortality A/E by attained age band", _state(), llm_cfg(),
        StubMCP(ae=res), allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    # A real markdown table (header + divider + one row per band), not a comma list.
    assert "| attained_age_band | actual | expected | ae_count |" in result.response_text
    assert "| --- | --- | --- | --- |" in result.response_text
    assert "| 45-49 | 7 | 6.9013 | 1.0143 |" in result.response_text
    assert "| 90-94 | 13 | 29.5133 | 0.4405 |" in result.response_text


# --- aggregate ratio answer (the WL = 0 fix) -------------------------------- #

def test_aggregate_ratio_answer_is_not_zero():
    agg = {
        "columns": ["actual", "expected", "ae_amount"],
        "rows": [[232, 405.76, 0.5718]],
        "row_count": 1,
    }
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT SUM(actual_deaths_count) AS actual, "
            "SUM(expected_deaths_count) AS expected, "
            "SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) AS ae_amount "
            "FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL",
            "WL mortality A/E is {{col:ae_amount}} "
            "({{col:actual}} actual vs {{col:expected}} expected).",
        ),
    )
    result = handle_turn(
        "Provide A/E ratios for WL", _state(), llm_cfg(),
        StubMCP(ae=agg), allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert "0.5718" in result.response_text
    assert "232" in result.response_text


# --- run-scope guard event (Fix 4a) ----------------------------------------- #

def _run_scope_events(events):
    return [e for e in events if e.get("event") == "run_scope"]


def test_run_scope_event_marks_scoped_query_applied_true():
    res = {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
            "AS ae_amount FROM gold_ae_results "
            "WHERE study_run_id='RUN1' AND product_code='WL' AND illness_code IS NULL",
            "A/E is {{col:ae_amount}}.",
        ),
    )
    events: list = []
    handle_turn(
        "WL A/E", _state(), llm_cfg(), StubMCP(ae=res), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
        audit=events.append, rag_run_ids=["RUN1"],
    )
    scope = _run_scope_events(events)
    assert len(scope) == 1 and scope[0]["applied"] is True


def test_run_scope_event_flags_unscoped_query_applied_false():
    res = {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1}
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
            "AS ae_amount FROM gold_ae_results "
            "WHERE product_code='WL' AND illness_code IS NULL",
            "A/E is {{col:ae_amount}}.",
        ),
    )
    events: list = []
    handle_turn(
        "WL A/E", _state(), llm_cfg(), StubMCP(ae=res), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
        audit=events.append, rag_run_ids=["RUN1"],
    )
    scope = _run_scope_events(events)
    assert len(scope) == 1 and scope[0]["applied"] is False


# --- single-row context guard ----------------------------------------------- #

def test_assemble_response_skips_context_for_multirow():
    multi = {
        "columns": ["attained_age_band", "ae_count", "credibility_z", "exposure_count"],
        "rows": [["25-29", 0.0, 0.0, 2], ["60-64", 0.8, 0.9, 5000]],
        "row_count": 2,
    }
    out = assemble_response("By band shown.", multi)
    assert "Statistical context" not in out


def test_assemble_response_adds_context_for_single_row():
    single = {
        "columns": ["ae_count", "credibility_z", "exposure_count", "expected_deaths_count"],
        "rows": [[0.57, 0.46, 12345, 405.76]],
        "row_count": 1,
    }
    out = assemble_response("A/E is 0.57.", single)
    assert "Statistical context" in out
    # No actual-count column present -> fall back to the stored single-cell Z.
    assert "credibility Z 0.46" in out


def test_assemble_response_recomputes_aggregate_credibility_not_stored_cell():
    """Doc-3 bug: an aggregate row carries a stray per-cell ``credibility_z_lapse``
    (0.0015) alongside the summed ``actual_lapses`` (163). The appended Z must be
    recomputed from the aggregate count — sqrt(163/1082) = 0.3881 — not the stored
    per-cell value, and must be traceable (added to ``injected``)."""
    single = {
        "columns": ["ae_lapse", "actual_lapses", "expected_lapses", "credibility_z_lapse"],
        "rows": [[0.9521, 163, 171.2007, 0.0015]],
        "row_count": 1,
    }
    injected: list = []
    out = assemble_response("UL lapse A/E is 0.9521.", single, injected=injected)
    assert "credibility Z 0.3881" in out
    assert "0.0015" not in out
    assert any(abs(x - 0.3881) < 1e-3 for x in injected)


def test_assemble_response_recomputes_with_buhlmann_method():
    single = {
        "columns": ["ae_count", "actual_deaths_count"],
        "rows": [[0.57, 232]],
        "row_count": 1,
    }
    out = assemble_response("A/E 0.57.", single, credibility_method="BUHLMANN")
    # Buhlmann Z = sqrt(232/(232+1082)) = 0.4202, distinct from the LF 0.4631.
    assert "credibility Z 0.4202" in out


# --- config-driven token caps (DeepSeek llm_error fix) ---------------------- #

def test_max_tokens_read_from_config():
    cfg = dict(chatbot_cfg())
    cfg["max_tokens"] = {"routing": 1234, "sql_generation": 1500}
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500",
            "A/E {{col:ae_count}}.",
        ),
    )
    handle_turn(
        "Term A/E?", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.9]], "row_count": 1}),
        allowlist(), chatbot_cfg=cfg, provider=provider,
    )
    assert _routing_calls(provider)[0]["max_tokens"] == 1234
    assert _sqlgen_calls(provider)[0]["max_tokens"] == 1500


def test_default_token_caps_have_reasoning_headroom():
    # The shipped defaults must be large enough that a reasoning model's
    # routing/SQL-gen call is not truncated (the DeepSeek llm_error class).
    from src.ai.chatbot import pipeline
    assert pipeline._ROUTING_MAX_TOKENS >= 512
    assert pipeline._SQLGEN_MAX_TOKENS >= 1024


# --- run scoping ------------------------------------------------------------ #

def test_run_scope_injected_into_sqlgen_prompt():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500",
            "A/E {{col:ae_count}}.",
        ),
    )
    handle_turn(
        "Term A/E?", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.9]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
        rag_run_ids=["RUN-123"],
    )
    assert "RUN-123" in _sqlgen_calls(provider)[0]["system"]


# --- routing history -------------------------------------------------------- #

def test_routing_includes_recent_history():
    state = _state()
    state.add_turn("user", "Provide A/E for WL")
    state.add_turn("assistant", "WL A/E is 0.57.")
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='WL' LIMIT 500",
            "A/E {{col:ae_count}}.",
        ),
    )
    handle_turn(
        "Why is it that low?", state, llm_cfg(),
        StubMCP(ae={"columns": ["ae_count"], "rows": [[0.57]], "row_count": 1}),
        allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    msgs = _routing_calls(provider)[0]["messages"]
    joined = " ".join(str(m.get("content", "")) for m in msgs)
    assert "Provide A/E for WL" in joined


# --- round-6 integration: aggregate-credibility & PVFP margins end-to-end ---- #

def test_ul_lapse_credibility_recomputed_end_to_end_not_blocked():
    """Doc-3 end-to-end: an aggregate UL-lapse turn whose result carries a stray
    per-cell credibility_z_lapse (0.0015) renders the recomputed aggregate Z
    (0.3881), is NOT blocked by the traceability post-check, and never shows the
    stray 0.0015."""
    res = {
        "columns": ["ae_lapse", "actual_lapses", "expected_lapses", "credibility_z_lapse"],
        "rows": [[0.9521, 163, 171.2007, 0.0015]],
        "row_count": 1,
    }
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_lapse, actual_lapses, expected_lapses, credibility_z_lapse "
            "FROM gold_ae_results WHERE product_code='UL' AND illness_code IS NULL LIMIT 500",
            "UL lapse A/E is {{col:ae_lapse}}.",
        ),
    )
    result = handle_turn(
        "What's the UL lapse A/E and its credibility?", _state(), llm_cfg(),
        StubMCP(ae=res), allowlist(), chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert "credibility Z 0.3881" in result.response_text
    assert "0.0015" not in result.response_text


def test_pvfp_profit_source_margin_query_flows_end_to_end():
    """PVFP profit-source-margin columns (newly surfaced) route to the TEV tool,
    slot-fill, and pass traceability — the Doc-2 question is answerable."""
    tev = {
        "columns": ["mortality", "lapse", "ci", "investment_spread", "expense"],
        "rows": [[1000000.0, 250000.0, 50000.0, 800000.0, -120000.0]],
        "row_count": 1,
    }
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        sqlgen_reply(
            "SELECT SUM(pvfp_mortality_margin) AS mortality, SUM(pvfp_lapse_margin) AS lapse, "
            "SUM(pvfp_ci_margin) AS ci, SUM(pvfp_investment_spread) AS investment_spread, "
            "SUM(pvfp_expense_margin) AS expense FROM gold_tev_results WHERE sensitivity_id IS NULL",
            "Mortality margin {{col:mortality}}; investment spread {{col:investment_spread}}.",
        ),
    )
    result = handle_turn(
        "Which decrement contributes the largest profit-source margin to PVFP?",
        _state(), llm_cfg(), StubMCP(tev=tev), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert result.sql_outcome is not None


def test_assemble_response_honours_buhlmann_from_digest():
    """The run's credibility method flows from the fact pack into the recompute."""
    res = {"columns": ["ae_count", "actual_deaths_count"], "rows": [[0.57, 232]], "row_count": 1}
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count, actual_deaths_count FROM gold_ae_results "
            "WHERE product_code='WL' AND illness_code IS NULL LIMIT 500",
            "WL mortality A/E is {{col:ae_count}}.",
        ),
    )
    result = handle_turn(
        "WL mortality A/E?", _state(), llm_cfg(), StubMCP(ae=res), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
        study_digest={"credibility_method": "BUHLMANN"},
    )
    assert result.blocked is False
    assert "credibility Z 0.4202" in result.response_text   # Buhlmann, not LF 0.4631


# --- FR-1A-24 deterministic backstop: never aggregate per-cell credibility ---- #

def test_aggregates_per_cell_stat_detects_antipattern():
    from src.ai.chatbot.pipeline import aggregates_per_cell_stat as f
    assert f("SELECT AVG(credibility_z_lapse) z FROM gold_ae_results WHERE product_code='UL'") is True
    assert f("SELECT SUM(actual_lapses) a, AVG(credibility_z_lapse) z FROM gold_ae_results") is True
    assert f("SELECT MIN(se_ae_count) FROM gold_ae_results LIMIT 500") is True
    # legitimate shapes are NOT flagged
    assert f("SELECT SUM(actual_lapses) a, SUM(expected_lapses) e FROM gold_ae_results") is False
    assert f("SELECT credibility_z, ae_count FROM gold_ae_results WHERE duration_band='6-10' LIMIT 500") is False
    assert f("SELECT AVG(ae_count) FROM gold_ae_results WHERE product_code='TERM'") is False


def test_handle_turn_blocks_aggregated_credibility_with_hint():
    """The residual Doc-3 vector: a model that has the SQL AVG a per-cell credibility
    is blocked deterministically (FR-1A-24) — the wrong 0.0015 never reaches prose."""
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT SUM(actual_lapses) AS actual_lapses, SUM(expected_lapses) AS expected_lapses, "
            "SUM(actual_lapses)/NULLIF(SUM(expected_lapses),0) AS ae_lapse, "
            "AVG(credibility_z_lapse) AS credibility_z_lapse FROM gold_ae_results "
            "WHERE product_code='UL' AND illness_code IS NULL",
            "UL lapse A/E is {{col:ae_lapse}} (credibility {{col:credibility_z_lapse}}).",
        ),
    )
    result = handle_turn(
        "Overall UL lapse A/E and its credibility?", _state(), llm_cfg(),
        StubMCP(ae={"columns": ["x"], "rows": [[1]], "row_count": 1}), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is True
    assert result.block_reason == "credibility_aggregate"
    assert "averaged" in result.response_text.lower()
    assert "0.0015" not in result.response_text
