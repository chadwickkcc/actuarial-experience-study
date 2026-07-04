"""Tests for the eval-harness CLI guards (Session 22; FR-3B-53, NFR-L-04)."""
from __future__ import annotations

import pytest

from src.ai.eval.__main__ import (
    _assert_not_under_pytest,
    confirm_cost,
    estimate_cost,
    format_table,
    main,
    select_models,
)
from src.ai.eval.runner import EvalMetrics
from tests.chatbot_helpers import llm_cfg


def test_assert_not_under_pytest_raises_inside_pytest():
    with pytest.raises(RuntimeError):
        _assert_not_under_pytest()


def test_main_refuses_to_run_under_pytest():
    with pytest.raises(RuntimeError):
        main([])


def test_confirm_cost_below_threshold_proceeds_without_prompting():
    def _no_input(_prompt):  # would fail the test if called
        raise AssertionError("input should not be requested below threshold")

    assert confirm_cost(1.0, 5.0, input_fn=_no_input) is True


def test_confirm_cost_above_threshold_prompts_and_honours_answer():
    assert confirm_cost(9.0, 5.0, input_fn=lambda _p: "y") is True
    assert confirm_cost(9.0, 5.0, input_fn=lambda _p: "yes") is True
    assert confirm_cost(9.0, 5.0, input_fn=lambda _p: "n") is False
    assert confirm_cost(9.0, 5.0, input_fn=lambda _p: "") is False


def test_estimate_cost_scales_with_questions_and_prices():
    models = [{"price_per_mtok_input": 3.0, "price_per_mtok_output": 15.0}]
    assert estimate_cost(0, models) == 0.0
    assert estimate_cost(40, models) > 0.0
    # Unset prices -> zero estimate, never crashes.
    assert estimate_cost(40, [{"price_per_mtok_input": None,
                               "price_per_mtok_output": None}]) == 0.0


def test_select_models_filters_by_requested_ids():
    cfg = llm_cfg()
    picked = select_models(cfg, ["claude-sonnet-4-6"])
    assert [m["model_id"] for m in picked] == ["claude-sonnet-4-6"]
    assert len(select_models(cfg, None)) >= 2  # all configured models


def test_format_table_includes_each_model_and_metrics():
    metrics = EvalMetrics(
        model="claude-opus-4-8", execution_accuracy=0.9, gate_integrity=1.0,
        refusal_correctness=1.0, intent_routing_acc=0.95, numeric_traceability=1.0,
    )
    table = format_table([metrics])
    assert "claude-opus-4-8" in table
    assert "0.90" in table
