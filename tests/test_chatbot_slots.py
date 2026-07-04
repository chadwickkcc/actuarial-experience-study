"""fill_numeric_slots — the fixed §E.7 placeholder grammar (FR-3B-33).

Numbers are filled programmatically from the result set; the LLM never emits a
value. Any unresolved/malformed placeholder blocks the turn (raises SlotFillError).
"""
from __future__ import annotations

import pytest

from src.ai.chatbot.pipeline import SlotFillError, _resolve_slots, fill_numeric_slots

RS = {
    "columns": ["ae_count", "duration_band", "expected_deaths_count"],
    "rows": [[0.92, "6-10", 1200.0], [0.88, "11-15", 800.0]],
    "row_count": 2,
}


def test_col_slot_single_row():
    assert fill_numeric_slots("A/E {{col:ae_count}}", RS) == "A/E 0.92"


def test_col_slot_explicit_row_index():
    assert fill_numeric_slots("second {{col:ae_count[1]}}", RS) == "second 0.88"


def test_col_slot_string_value_passthrough():
    assert fill_numeric_slots("band {{col:duration_band}}", RS) == "band 6-10"


def test_agg_functions():
    assert fill_numeric_slots("{{agg:sum:expected_deaths_count}}", RS) == "2000"
    assert fill_numeric_slots("{{agg:mean:expected_deaths_count}}", RS) == "1000"
    assert fill_numeric_slots("{{agg:min:ae_count}}", RS) == "0.88"
    assert fill_numeric_slots("{{agg:max:ae_count}}", RS) == "0.92"
    assert fill_numeric_slots("{{agg:count:ae_count}}", RS) == "2"


def test_unknown_column_blocks():
    with pytest.raises(SlotFillError):
        fill_numeric_slots("{{col:not_a_column}}", RS)


def test_row_index_out_of_range_blocks():
    with pytest.raises(SlotFillError):
        fill_numeric_slots("{{col:ae_count[9]}}", RS)


def test_leftover_or_malformed_placeholder_blocks():
    with pytest.raises(SlotFillError):
        fill_numeric_slots("value {{not_the_grammar}}", RS)


def test_unknown_aggregate_column_blocks():
    with pytest.raises(SlotFillError):
        fill_numeric_slots("{{agg:sum:not_a_column}}", RS)


def test_resolve_slots_collects_injected_numbers_for_traceability():
    text, injected = _resolve_slots(
        "{{col:ae_count}} and {{agg:sum:expected_deaths_count}}", RS
    )
    assert text == "0.92 and 2000"
    # The system-computed aggregate is reported so the post-check treats it as
    # traceable to the result set (it is a deterministic function of the data).
    assert 0.92 in injected and 2000.0 in injected
