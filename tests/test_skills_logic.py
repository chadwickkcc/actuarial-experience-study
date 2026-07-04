"""Unit tests for the app-side Skill assembler (Session 19; ui/skills_logic.py).

DB-free helpers only — the DB-backed assemble_memo_input is covered by
tests/test_skills_realdata.py against the production Gold copy.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from src.utils.types import DecrementType
from ui import skills_logic as skills
from ui import ai_comparison_logic as logic
from ui.config import CONFIG_DIR
from ui.stats_helpers import credibility_z


_SHAP_JSON = {
    "decrement": "MORTALITY",
    "product_code": "WL",
    "cells": [
        {
            "grain_key": {"product": "WL", "duration_band": "6-10"},
            "base_value": 0.0,
            "prediction": 0.07,
            "contributions": [{"feature": "duration_band", "shap_value": 0.12, "feature_value": "6-10"}],
        },
        {
            "grain_key": {"product": "WL", "duration_band": "1-5"},
            "base_value": 0.0,
            "prediction": 0.03,
            "contributions": [],
        },
    ],
}


def test_assemble_shap_cell_input_matches_grain():
    cell = skills.assemble_shap_cell_input(_SHAP_JSON, {"product": "WL", "duration_band": "6-10"})
    assert cell is not None
    assert cell["decrement"] == "MORTALITY"
    assert cell["product_code"] == "WL"
    assert cell["prediction"] == 0.07
    assert cell["contributions"][0]["feature"] == "duration_band"


def test_assemble_shap_cell_input_returns_none_for_unknown_grain():
    assert skills.assemble_shap_cell_input(_SHAP_JSON, {"product": "WL", "duration_band": "99+"}) is None


def test_assemble_shap_cell_input_grain_is_order_insensitive():
    # Same dims, reversed order — still matches.
    cell = skills.assemble_shap_cell_input(_SHAP_JSON, {"duration_band": "6-10", "product": "WL"})
    assert cell is not None and cell["prediction"] == 0.07


def test_feature_map_for_decrement_pulls_decrement_block():
    fmap = skills.feature_map_for_decrement(logic.load_feature_to_assumption(), DecrementType.MORTALITY)
    assert "duration_band" in fmap
    assert fmap["duration_band"]["actuarial_term"] == "policy duration"


def test_available_skill_models_lists_all_configured_models():
    models = skills.available_skill_models(CONFIG_DIR)
    ids = {m["model_id"] for m in models}
    assert {"claude-opus-4-8", "claude-sonnet-4-6", "deepseek-v4-pro", "deepseek-v4-flash"} <= ids
    # With no API keys in the env, every model greys out with a reason (FR-3B-04).
    for m in models:
        assert m["enabled"] is False
        assert m["disabled_reason"]


def test_assemble_shap_cell_input_rounds_numbers_to_4dp():
    shap = {
        "decrement": "MORTALITY",
        "product_code": "WL",
        "cells": [{
            "grain_key": {"product": "WL", "duration_band": "6-10"},
            "base_value": -0.8411115407943726,
            "prediction": -0.5755044996893655,
            "contributions": [
                {"feature": "duration_band", "shap_value": 0.25713096658388773, "feature_value": "6-10"},
            ],
        }],
    }
    cell = skills.assemble_shap_cell_input(shap, {"product": "WL", "duration_band": "6-10"})
    assert cell["base_value"] == -0.8411
    assert cell["prediction"] == -0.5755
    assert cell["contributions"][0]["shap_value"] == 0.2571
    # Non-numeric fields preserved verbatim.
    assert cell["contributions"][0]["feature_value"] == "6-10"
    assert cell["contributions"][0]["feature"] == "duration_band"


def _segment_test_con():
    """In-memory Gold with multiple detail sub-cells per age band.

    Mirrors the production shape: gold_ae_results holds only fully-dimensioned
    detail rows (no single-dimension marginals), so a by-band view MUST
    re-aggregate. The young band has many zero-death sub-cells; the mid band has
    real deaths spread across sub-cells.
    """
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE gold_study_runs (run_id VARCHAR, credibility_method VARCHAR)")
    con.execute("INSERT INTO gold_study_runs VALUES ('R1', 'LF')")
    con.execute(
        "CREATE TABLE gold_ae_results ("
        "study_run_id VARCHAR, product_code VARCHAR, attained_age_band VARCHAR, "
        "illness_code VARCHAR, actual_deaths_count INTEGER, expected_deaths_count DOUBLE)"
    )
    rows = [
        # Young band: three detail sub-cells, all genuinely zero deaths.
        ("R1", "WL", "25-29", None, 0, 0.01),
        ("R1", "WL", "25-29", None, 0, 0.02),
        ("R1", "WL", "25-29", None, 0, 0.01),
        # Mid band: deaths spread over three sub-cells -> SUM=7 actual / 6.9 expected.
        ("R1", "WL", "45-49", None, 2, 2.3),
        ("R1", "WL", "45-49", None, 3, 2.3),
        ("R1", "WL", "45-49", None, 2, 2.3),
        # Per-illness CI row at 45-49 — must be EXCLUDED by illness_code IS NULL.
        ("R1", "WL", "45-49", "CI-001", 999, 999.0),
        # attained_age_band NULL aggregate row — EXCLUDED by "dim IS NOT NULL".
        ("R1", "WL", None, None, 50, 50.0),
        # Different product — EXCLUDED by the product filter.
        ("R1", "TERM", "45-49", None, 100, 1.0),
    ]
    con.executemany("INSERT INTO gold_ae_results VALUES (?,?,?,?,?,?)", rows)
    return con


def test_ae_by_segment_aggregates_one_row_per_band_not_truncated_detail():
    # Regression guard: the old helper returned raw detail rows ordered by band
    # and truncated to the first N — surfacing only the youngest (zero-death)
    # band. It must now aggregate to one row per band across the detail grain.
    con = _segment_test_con()
    try:
        out = skills._ae_by_segment(con, "R1", "WL", DecrementType.MORTALITY)
    finally:
        con.close()

    by_band = {s["segment"]: s for s in out}
    assert set(by_band) == {"25-29", "45-49"}          # one row per band
    # Young band genuinely zero (correct, not a data defect).
    assert by_band["25-29"]["ae_ratio"] == 0.0
    assert by_band["25-29"]["credibility_z"] == 0.0
    # Mid band: SUM(actual)/SUM(expected) = 7 / 6.9, Z from the AGGREGATE count.
    assert by_band["45-49"]["ae_ratio"] == round(7 / 6.9, 4)
    assert by_band["45-49"]["credibility_z"] == round(float(credibility_z(7.0, method="LF")), 4)
    assert by_band["45-49"]["credibility_z"] > 0
    # The per-illness CI row (actual 999) is excluded — A/E stays ~1.01, not huge.
    assert by_band["45-49"]["ae_ratio"] < 2.0


def _surrender_test_con():
    """In-memory Gold with surrender sub-cells per duration band."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE gold_study_runs (run_id VARCHAR, credibility_method VARCHAR)")
    con.execute("INSERT INTO gold_study_runs VALUES ('R1', 'LF')")
    con.execute(
        "CREATE TABLE gold_ae_results ("
        "study_run_id VARCHAR, product_code VARCHAR, duration_band VARCHAR, "
        "illness_code VARCHAR, actual_surrenders INTEGER, expected_surrenders DOUBLE)"
    )
    rows = [
        ("R1", "DA_FIXED", "2-5", None, 13, 18.83),
        ("R1", "DA_FIXED", "2-5", None, 0, 0.0),     # extra sub-cell, same band
        ("R1", "DA_FIXED", "6-10", None, 200, 170.0),
        ("R1", "DA_FIXED", "6-10", None, 35, 32.03),
        ("R1", "WL", "6-10", None, 99, 10.0),         # other product — excluded
    ]
    con.executemany("INSERT INTO gold_ae_results VALUES (?,?,?,?,?,?)", rows)
    return con


def test_ae_by_segment_supports_surrender_decrement():
    # SURRENDER is the memo-only decrement: aggregate actual/expected surrenders
    # by duration band, with Z from the aggregate count.
    con = _surrender_test_con()
    try:
        out = skills._ae_by_segment(con, "R1", "DA_FIXED", DecrementType.SURRENDER)
    finally:
        con.close()
    by = {s["segment"]: s for s in out}
    assert set(by) == {"2-5", "6-10"}
    assert by["6-10"]["ae_ratio"] == round(235 / 202.03, 4)   # (200+35)/(170+32.03)
    assert by["2-5"]["ae_ratio"] == round(13 / 18.83, 4)
    assert by["6-10"]["credibility_z"] == round(float(credibility_z(235.0, method="LF")), 4)
    assert by["6-10"]["credibility_z"] > 0


def test_fit_models_surrender_is_memo_only_no_proposal():
    # SURRENDER must short-circuit before any GLM config / DB access (it has no
    # GLM/GBM config) and surface the standard no-proposal state — so a bogus path
    # is never touched.
    res = logic.fit_models(
        Path("/nonexistent/none.duckdb"), "no-run",
        DecrementType.SURRENDER, "DA_FIXED", register=False,
    )
    assert res["glm"] is None and res["gbm"] is None
    assert res["shap_json_path"] == ""
    assert "surrender" in res["reasons"]
