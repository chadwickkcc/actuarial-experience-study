"""Tests for the deterministic numeric post-check (Session 19; Tech Spec §E.7).

``verify_traceability`` is pulled forward from the Session-20 chatbot module
because the two Skills (memo, SHAP) need it now to enforce the block-not-repair
guardrail (FR-3B-19/22). Session 20 consumes the same module unchanged.

The check is deterministic and LLM-free: every numeric token in the rendered
text must trace to a value present in the supporting data (the ``result_set``)
or echoed from the user's own message.
"""
from __future__ import annotations

from src.ai.chatbot.traceability import verify_traceability
from src.utils.types import TraceabilityResult


def test_clean_numbers_trace_to_result_set():
    result_set = {"ae_count": 0.92, "exposure": 12345.0, "claims": 41}
    answer = "The A/E ratio was 0.92 over 12,345 exposure-years with 41 claims."
    res = verify_traceability(answer, result_set)
    assert isinstance(res, TraceabilityResult)
    assert res.passed
    assert res.untraceable_nums == []


def test_invented_number_is_flagged():
    result_set = {"ae_count": 0.92}
    answer = "The A/E ratio was 0.92 but mortality improved 999.99 percent."
    res = verify_traceability(answer, result_set)
    assert not res.passed
    assert any("999.99" in n for n in res.untraceable_nums)


def test_numbers_echoed_from_user_message_are_allowed():
    result_set = {"ae_count": 0.92}
    answer = "For the 8-year study, the A/E ratio was 0.92."
    # 8 is not in the result_set but is echoed from the user's own question.
    res = verify_traceability(answer, result_set, user_msg="Show the 8-year study A/E.")
    assert res.passed


def test_rounding_and_formatting_tolerance():
    # Result carries full precision; the answer rounds/formats it.
    result_set = {"tev": 173_400_000.0, "ratio": 0.923456}
    answer = "TEV was 173,400,000 and the ratio rounded to 0.92."
    res = verify_traceability(answer, result_set)
    assert res.passed


def test_percent_and_currency_tokens_trace():
    rs = {"ratio": 92.0, "tev": 173400000.0}
    assert verify_traceability("Up 92% to $173,400,000.", rs).passed


def test_negative_traces_but_sign_mismatch_blocks():
    rs = {"shap": -0.05}
    assert verify_traceability("It contributed -0.05.", rs).passed
    # Same magnitude, wrong sign — strict check blocks (documented behaviour).
    res = verify_traceability("It contributed 0.05.", rs)
    assert not res.passed and any("0.05" in n for n in res.untraceable_nums)


def test_hyphenated_band_numbers_trace_from_string_value():
    rs = {"segments": [{"segment": "duration 6-10"}]}
    assert verify_traceability("For duration 6-10 the trend held.", rs).passed


def test_band_upper_endpoint_traces_regardless_of_dash_style():
    # The data carries an ASCII-hyphen band; the model commonly re-renders the
    # band with an en-dash or "to". The upper endpoint must trace in every form —
    # a hyphen between two digits is a range separator, not a minus sign.
    rs = {"segments": [{"segment": "25-29"}, {"segment": "duration 6-10"}]}
    assert verify_traceability("The 25-29 band moved.", rs).passed       # ascii hyphen
    assert verify_traceability("The 25–29 band moved.", rs).passed  # en-dash
    assert verify_traceability("The 25—29 band moved.", rs).passed  # em-dash
    assert verify_traceability("Ages 25 to 29 moved; duration 6 to 10 held.", rs).passed


def test_date_fragments_trace_both_directions():
    # A study period stored as ISO dates must trace when the model writes the
    # month/day/year in prose (the earlier Stage-4 "12, 31" false block).
    rs = {"study_period": "2016-01-01 to 2023-12-31"}
    assert verify_traceability(
        "The study ran from 2016 through 2023, ending on 12/31.", rs
    ).passed


def test_in_window_year_traces_via_study_years():
    # A year the model references that is carried in study_years is allowed.
    rs = {"study_period": "2016 to 2023", "study_years": [2016, 2017, 2018, 2019, 2020]}
    assert verify_traceability("Rates dipped in 2020.", rs).passed
    # A year outside the window (not in the JSON) still blocks.
    assert not verify_traceability("Rates dipped in 2099.", rs).passed


def test_empty_answer_has_no_tokens_and_passes():
    assert verify_traceability("", {"a": 1.0}).passed
    assert verify_traceability("No numbers here at all.", {"a": 1.0}).passed


def test_number_absent_from_both_sources_blocks():
    rs = {"a": 0.92}
    res = verify_traceability("Values 0.92 and 7.", rs, user_msg="the 0.92 cell")
    assert not res.passed and any("7" == n for n in res.untraceable_nums)


def test_recursive_extraction_over_nested_and_columns_rows():
    # Nested dict (Skills' memo_input shape).
    nested = {"segments": [{"ae": 1.05}, {"ae": 0.88}], "tev": {"baseline": 100.0}}
    assert verify_traceability("Values 1.05, 0.88 and 100.", nested).passed
    # {columns, rows} shape (chatbot, forward-compatible with Session 20).
    rs = {"columns": ["ae_count", "n"], "rows": [[0.92, 41], [1.10, 7]]}
    assert verify_traceability("0.92, 41, 1.10, 7.", rs).passed
    assert not verify_traceability("0.92 and 5.5", rs).passed
