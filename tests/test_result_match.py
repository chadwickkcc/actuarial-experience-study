"""Tests for the FR-3B-51 result-match rule (Session 22; Tech Spec §E.9/§F.5).

Covers all five clauses with ``value_check: true`` and the columns+row-count-only
path with ``value_check: false``; numeric tolerance, NULL handling, column-order
insensitivity, and the error/empty -> miss contract.
"""
from __future__ import annotations

from src.ai.eval.result_match import results_match


def test_identical_result_sets_match():
    cols = ["product_code", "ae_count"]
    rows = [["TERM", 0.93], ["WL", 1.01]]
    assert results_match(rows, cols, rows, cols, value_check=True) is True


def test_column_order_is_ignored():
    ref_cols = ["product_code", "ae_count"]
    ref_rows = [["TERM", 0.93]]
    gen_cols = ["ae_count", "product_code"]
    gen_rows = [[0.93, "TERM"]]
    assert results_match(gen_rows, gen_cols, ref_rows, ref_cols, value_check=True) is True


def test_row_order_is_ignored_sorted_multiset():
    cols = ["product_code", "ae_count"]
    ref = [["TERM", 0.93], ["WL", 1.01]]
    gen = [["WL", 1.01], ["TERM", 0.93]]
    assert results_match(gen, cols, ref, cols, value_check=True) is True


def test_numeric_tolerance_within_rel_tol_matches():
    cols = ["ae_count"]
    assert results_match([[1.0000000004]], cols, [[1.0]], cols, value_check=True) is True


def test_numeric_difference_beyond_tolerance_is_a_miss():
    cols = ["ae_count"]
    assert results_match([[1.01]], cols, [[1.0]], cols, value_check=True) is False


def test_near_zero_uses_absolute_tolerance():
    cols = ["delta_tev"]
    assert results_match([[1e-10]], cols, [[0.0]], cols, value_check=True) is True
    assert results_match([[1e-3]], cols, [[0.0]], cols, value_check=True) is False


def test_nulls_match_nulls():
    cols = ["product_code", "ae_count"]
    assert results_match([["TERM", None]], cols, [["TERM", None]], cols, value_check=True) is True


def test_null_versus_value_is_a_miss():
    cols = ["ae_count"]
    assert results_match([[None]], cols, [[0.0]], cols, value_check=True) is False


def test_mismatched_column_set_is_a_miss():
    assert results_match(
        [[1.0]], ["ae_count"], [[1.0]], ["ae_amount"], value_check=True
    ) is False


def test_mismatched_row_count_is_a_miss():
    cols = ["product_code"]
    assert results_match([["TERM"]], cols, [["TERM"], ["WL"]], cols, value_check=True) is False


def test_value_check_false_ignores_values_but_checks_shape():
    cols = ["attained_age_band", "ae_count"]
    ref = [["45-54", 0.9], ["55-64", 1.1]]
    gen = [["35-44", 0.5], ["65-74", 0.7]]   # different values, same shape
    assert results_match(gen, cols, ref, cols, value_check=False) is True


def test_value_check_false_still_requires_matching_columns_and_rowcount():
    cols = ["attained_age_band", "ae_count"]
    ref = [["45-54", 0.9], ["55-64", 1.1]]
    assert results_match([["45-54", 0.9]], cols, ref, cols, value_check=False) is False
    assert results_match(
        [["45-54", 0.9], ["55-64", 1.1]], ["x", "ae_count"], ref, cols, value_check=False
    ) is False


def test_none_inputs_count_as_a_miss():
    cols = ["ae_count"]
    assert results_match(None, None, [[1.0]], cols, value_check=True) is False
    assert results_match(None, None, [[1.0]], cols, value_check=False) is False


def test_numeric_versus_string_cell_is_a_miss():
    cols = ["v"]
    assert results_match([["1"]], cols, [[1]], cols, value_check=True) is False


def test_integer_and_float_equal_value_match():
    cols = ["n"]
    assert results_match([[5]], cols, [[5.0]], cols, value_check=True) is True
