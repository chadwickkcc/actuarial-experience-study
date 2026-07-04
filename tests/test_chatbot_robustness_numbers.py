"""Robustness — area A: how the chatbot computes/queries numbers.

Edge cases around slot-fill (NULL cells, NULL-mixed aggregates, list slots), the
multi-query synthesis evidence path, and the numeric-traceability formatting rules.
Deterministic, offline (ScriptedProvider/StubMCP, no API keys).
"""
from __future__ import annotations

from src.ai.chatbot.pipeline import (
    SlotFillError,
    _resolve_slots,
    handle_turn,
)
from src.ai.chatbot.session import SessionState
from src.ai.chatbot.traceability import verify_traceability
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)
import pytest

_MODEL = "claude-sonnet-4-6"


def _state():
    return SessionState(session_id="robn", model_key=_MODEL)


# --------------------------------------------------------------------------- #
# Slot grammar — NULL / mixed / list edge cases (pure _resolve_slots)         #
# --------------------------------------------------------------------------- #

def test_col_slot_null_cell_renders_na_and_is_not_injected():
    res = {"columns": ["ae_count"], "rows": [[None]], "row_count": 1}
    text, injected = _resolve_slots("Value: {{col:ae_count}}.", res)
    assert text == "Value: N/A."
    assert injected == []  # None is not a traceable number


def test_col_slot_implicit_row_zero_on_multirow():
    res = {"columns": ["ae_count"], "rows": [[0.5], [0.9]], "row_count": 2}
    text, injected = _resolve_slots("{{col:ae_count}}", res)
    assert text == "0.5"
    assert injected == [0.5]


def test_agg_mean_over_nulls_mixed_uses_nonnull_subset():
    res = {"columns": ["x"], "rows": [[2.0], [None], [4.0]], "row_count": 3}
    text, injected = _resolve_slots("mean={{agg:mean:x}}", res)
    assert text == "mean=3"          # (2+4)/2, NULL skipped
    assert injected == [3.0]


def test_agg_sum_over_all_null_column_blocks():
    res = {"columns": ["x"], "rows": [[None], [None]], "row_count": 2}
    with pytest.raises(SlotFillError):
        _resolve_slots("{{agg:sum:x}}", res)


def test_agg_count_over_all_null_returns_row_count():
    res = {"columns": ["x"], "rows": [[None], [None]], "row_count": 2}
    text, injected = _resolve_slots("n={{agg:count:x}}", res)
    assert text == "n=2"
    assert injected == [2.0]


def test_list_slot_single_value_has_no_trailing_comma():
    res = {"columns": ["product_code"], "rows": [["WL"]], "row_count": 1}
    text, _ = _resolve_slots("{{list:product_code}}", res)
    assert text == "WL"


def test_list_slot_numeric_and_null_mix():
    res = {"columns": ["x"], "rows": [[1.0], [None], [2.0]], "row_count": 3}
    text, injected = _resolve_slots("vals: {{list:x}}", res)
    assert text == "vals: 1, N/A, 2"
    assert 1.0 in injected and 2.0 in injected


# --------------------------------------------------------------------------- #
# Table slot — multi-row markdown rendering (Fix 1)                            #
# --------------------------------------------------------------------------- #

def test_table_slot_renders_markdown_and_injects_numbers():
    res = {
        "columns": ["attained_age_band", "actual", "expected", "ae"],
        "rows": [["45-49", 7, 6.9013, 1.0143], ["90-94", 13, 29.5133, 0.4405]],
        "row_count": 2,
    }
    text, injected = _resolve_slots("{{table:attained_age_band,actual,expected,ae}}", res)
    lines = text.splitlines()
    assert lines[0] == "| attained_age_band | actual | expected | ae |"
    assert lines[1] == "| --- | --- | --- | --- |"
    assert lines[2] == "| 45-49 | 7 | 6.9013 | 1.0143 |"
    assert lines[3] == "| 90-94 | 13 | 29.5133 | 0.4405 |"
    # Every numeric cell is traceable to the result set.
    for n in (7.0, 6.9013, 1.0143, 13.0, 29.5133, 0.4405):
        assert n in injected
    # Band labels (string cells) are not injected as numbers.
    assert "45-49" not in injected


def test_table_slot_numbers_are_traceable():
    res = {
        "columns": ["band", "ae"],
        "rows": [["45-49", 1.0143], ["90-94", 0.4405]],
        "row_count": 2,
    }
    text, _ = _resolve_slots("WL by band:\n{{table:band,ae}}", res)
    trace = verify_traceability(text, result_set=res, user_msg="")
    assert trace.passed
    assert trace.untraceable_nums == []


def test_table_slot_tolerates_whitespace_in_column_list():
    res = {
        "columns": ["band", "ae"],
        "rows": [["45-49", 1.0143]],
        "row_count": 1,
    }
    # A model may naturally write spaces after commas — this must still resolve,
    # not leave a leftover placeholder that blocks the answer.
    text, injected = _resolve_slots("{{table: band , ae }}", res)
    assert "| band | ae |" in text
    assert "| 45-49 | 1.0143 |" in text
    assert 1.0143 in injected


def test_table_slot_unknown_column_blocks():
    res = {"columns": ["band"], "rows": [["45-49"]], "row_count": 1}
    with pytest.raises(SlotFillError):
        _resolve_slots("{{table:band,nope}}", res)


def test_table_slot_empty_result_is_safe_string_not_crash():
    res = {"columns": ["band", "ae"], "rows": [], "row_count": 0}
    text, injected = _resolve_slots("{{table:band,ae}}", res)
    assert "no matching rows" in text
    assert injected == []


# --------------------------------------------------------------------------- #
# End-to-end: a NULL figure renders safely (no block)                         #
# --------------------------------------------------------------------------- #

def test_null_figure_answer_completes_without_block():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply(
            "SELECT ae_count FROM gold_ae_results WHERE product_code='WL' LIMIT 500",
            "The A/E is {{col:ae_count}}.",
        ),
    )
    res = {"columns": ["ae_count"], "rows": [[None]], "row_count": 1}
    result = handle_turn(
        "WL A/E?", _state(), llm_cfg(), StubMCP(ae=res), allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider,
    )
    assert result.blocked is False
    assert "N/A" in result.response_text
    assert result.traceability is not None and result.traceability.passed


# --------------------------------------------------------------------------- #
# Multi-query synthesis — evidence edge cases                                  #
# --------------------------------------------------------------------------- #

class _SeqMCP:
    """Returns AE results in sequence by call order (for multi-query plans)."""

    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def query_ae_results(self, sql):
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r

    def query_tev_results(self, sql):
        return {"error": "no_data", "message": "none"}


def _plan(*queries):
    import json
    return json.dumps({"queries": list(queries)})


_Q_OK = {"label": "overall", "sql": "SELECT SUM(actual_deaths_count)/NULLIF(SUM(expected_deaths_count),0) "
         "AS ae_amount FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"}
_Q_OK2 = {"label": "lapse", "sql": "SELECT SUM(actual_lapses)/NULLIF(SUM(expected_lapses),0) "
          "AS ae_lapse FROM gold_ae_results WHERE product_code='WL' AND illness_code IS NULL"}


def test_synthesis_handles_a_zero_row_query():
    # First query returns one row; second returns zero rows. The answer cites only
    # the populated figure and traces; result_row_count excludes the empty query.
    mcp = _SeqMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
        {"columns": ["ae_lapse"], "rows": [], "row_count": 0},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q_OK, _Q_OK2),
        synthesis_answer_text="Whole Life mortality A/E is 0.5718; lapse data was unavailable.",
    )
    result = handle_turn(
        "WL mortality and lapse?", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is False
    assert "0.5718" in result.response_text
    assert result.result_row_count == 1  # only the populated query's row


def test_synthesis_blocks_an_invented_cross_query_total():
    # The synthesiser must not compute its own number: 1.5218 is the sum of the two
    # evidence figures but appears in neither result set -> blocked.
    mcp = _SeqMCP([
        {"columns": ["ae_amount"], "rows": [[0.5718]], "row_count": 1},
        {"columns": ["ae_lapse"], "rows": [[0.95]], "row_count": 1},
    ])
    provider = ScriptedProvider(
        routing_reply("EXPLORATORY"),
        synthesis_plan_text=_plan(_Q_OK, _Q_OK2),
        synthesis_answer_text=(
            "Mortality A/E 0.5718 and lapse A/E 0.95 combine to 1.5218 across decrements."
        ),
    )
    result = handle_turn(
        "Combine WL mortality and lapse.", _state(), llm_cfg(), mcp, allowlist(),
        chatbot_cfg=chatbot_cfg(), provider=provider, multi_query=True,
    )
    assert result.blocked is True
    assert result.block_reason == "numeric_traceability"
    assert any("1.5218" in n for n in result.traceability.untraceable_nums)


# --------------------------------------------------------------------------- #
# Traceability formatting rules                                                 #
# --------------------------------------------------------------------------- #

def test_billions_with_commas_trace():
    res = {"columns": ["tev"], "rows": [[1200000000]], "row_count": 1}
    out = verify_traceability("Total TEV is 1,200,000,000.", result_set={"cells": res})
    assert out.passed


def test_number_inside_a_string_cell_traces():
    res = {"columns": ["duration_band", "ae"], "rows": [["6-10", 0.8]], "row_count": 1}
    out = verify_traceability(
        "In duration band 6-10 the A/E is 0.8.", result_set={"cells": res}
    )
    assert out.passed


def test_scientific_notation_does_not_trace_known_limitation():
    # The checker has no scientific-notation rule: "1.5e-6" is read as 1.5 and 6,
    # neither of which traces to 0.0000015 -> blocked. Documented behaviour.
    res = {"columns": ["rate"], "rows": [[0.0000015]], "row_count": 1}
    out = verify_traceability("The rate is 1.5e-6.", result_set={"cells": res})
    assert out.passed is False
