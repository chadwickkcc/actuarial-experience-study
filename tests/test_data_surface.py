"""Governed-maximum data-surface widening tests (Steps E + F, 2026-06-27).

Covers the widened allowlist + the generic ``query_results`` MCP tool over the
additional PII-free Gold tables, the pipeline routing to it, the materialised
``gold_ai_proposed_factors`` writer, and — critically — the **PII-reachability
guard**: the bright line that separates this governed-maximum surface from raw
access. No PII-bearing column or table may ever be reachable.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.ai.mcp_server.server import (
    QUERYABLE_TABLES,
    query_results_impl,
)
from src.ai.chatbot.pipeline import execute_via_mcp
from src.ai.proposals import _COLUMNS, _INSERT_SQL, write_proposed_factors
from src.utils.db_init import init_database
from src.utils.sql_boundary import load_allowlist
from src.utils.types import FactorCell
from ui.config import CONFIG_DIR

_AI_CONFIG = CONFIG_DIR / "ai_config.yaml"


# --------------------------------------------------------------------------- #
# Allowlist <-> queryable-tables sync, and the PII bright line                #
# --------------------------------------------------------------------------- #

def test_queryable_tables_equal_allowlist_keys():
    """The pipeline/server routing set must equal the configured allowlist keys
    (no drift — a table reachable by one path but not the other is a bug)."""
    allow = load_allowlist(_AI_CONFIG)
    assert set(QUERYABLE_TABLES) == set(allow.keys())


# Unambiguous PII / filesystem substrings (narrow, to avoid e.g. cv_metric_name).
_PII_SUBSTRINGS = (
    "policy_id", "contract_id", "date_of_birth", "holder_name",
    "first_name", "last_name", "file_path", "artifact_path", "shap_json_path",
)
# Exact person-identifier / sensitive columns that must never be allowlisted.
_PII_EXACT = {
    "ssn", "author_id", "approved_by", "reviewer_id", "actuary_id",
    "proposer_id", "override_actuary_id", "yaml_file_path",
}
# Tables that carry PII (policy_id) or are raw layers — never allowlist keys.
_FORBIDDEN_TABLES = {
    "gold_dq_quarantine", "gold_exposure_segments", "gold_workflow_iterations",
    "gold_assumption_approvals", "gold_study_runs",
}


def test_no_pii_column_is_allowlisted():
    allow = load_allowlist(_AI_CONFIG)
    for table, cols in allow.items():
        for col in cols:
            low = col.lower()
            assert low not in _PII_EXACT, f"PII column {table}.{col} is allowlisted"
            assert not any(frag in low for frag in _PII_SUBSTRINGS), (
                f"PII-looking column {table}.{col} is allowlisted"
            )


def test_no_pii_or_raw_table_is_allowlisted():
    allow = load_allowlist(_AI_CONFIG)
    keys = set(allow.keys())
    assert not (keys & _FORBIDDEN_TABLES), f"forbidden table(s) allowlisted: {keys & _FORBIDDEN_TABLES}"
    # No Silver/Bronze layer table is reachable.
    assert not any(t.startswith(("silver_", "bronze_")) for t in keys)


def test_pii_query_is_rejected_by_the_boundary(tmp_path):
    """A query for a PII column on an allowlisted table is rejected (gate 3)."""
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    # gold_model_points is allowlisted but policy-level identifiers are not present
    # in its allowlist; gold_exposure_segments (policy_id) is not queryable at all.
    out = query_results_impl(
        "gold_exposure_segments",
        "SELECT policy_id FROM gold_exposure_segments LIMIT 5",
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert out.get("error") == "table_not_queryable"


# --------------------------------------------------------------------------- #
# The generic query_results tool                                              #
# --------------------------------------------------------------------------- #

def _fresh_db(tmp_path) -> Path:
    db = tmp_path / "ds.duckdb"
    init_database(str(db))
    return db


def test_query_results_reaches_a_widened_table(tmp_path):
    """An allowlisted widened table is reachable (empty result, not an error)."""
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    out = query_results_impl(
        "gold_inforce_reconciliation",
        "SELECT product_code, recon_passes FROM gold_inforce_reconciliation LIMIT 5",
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert "error" not in out, out
    assert out["columns"] == ["product_code", "recon_passes"]
    assert out["row_count"] == 0  # empty fresh table


def test_query_results_rejects_off_allowlist_column(tmp_path):
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    out = query_results_impl(
        "gold_dq_run_summary",
        "SELECT check_results FROM gold_dq_run_summary LIMIT 5",  # not allowlisted
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert out.get("error") == "gate_3_allowlist"


def test_query_results_rejects_non_queryable_table(tmp_path):
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    out = query_results_impl(
        "gold_study_runs", "SELECT 1 FROM gold_study_runs LIMIT 1",
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert out.get("error") == "table_not_queryable"


# --------------------------------------------------------------------------- #
# Pipeline routing to the generic tool                                        #
# --------------------------------------------------------------------------- #

class _RecordingMCP:
    def __init__(self):
        self.calls = []

    def query_ae_results(self, sql):
        self.calls.append(("ae", sql)); return {"columns": [], "rows": [], "row_count": 0}

    def query_tev_results(self, sql):
        self.calls.append(("tev", sql)); return {"columns": [], "rows": [], "row_count": 0}

    def query_results(self, table, sql):
        self.calls.append((table, sql)); return {"columns": [], "rows": [], "row_count": 0}


def test_execute_via_mcp_routes_widened_table_to_generic_tool():
    mcp = _RecordingMCP()
    execute_via_mcp(
        "SELECT product_code FROM gold_inforce_reconciliation LIMIT 5", mcp
    )
    assert mcp.calls == [("gold_inforce_reconciliation",
                          "SELECT product_code FROM gold_inforce_reconciliation LIMIT 5")]


def test_execute_via_mcp_still_routes_ae_and_tev_and_rejects_cross_table():
    mcp = _RecordingMCP()
    execute_via_mcp("SELECT ae_count FROM gold_ae_results LIMIT 1", mcp)
    execute_via_mcp("SELECT tev FROM gold_tev_results LIMIT 1", mcp)
    assert mcp.calls[0][0] == "ae" and mcp.calls[1][0] == "tev"
    # A cross-table query references two queryable tables -> unroutable, no call.
    out = execute_via_mcp(
        "SELECT a.ae_count FROM gold_ae_results a, gold_tev_results t LIMIT 1", mcp
    )
    assert out.get("error") == "unroutable"
    assert len(mcp.calls) == 2  # no third call made


# --------------------------------------------------------------------------- #
# Proposed-factors writer (Step F)                                            #
# --------------------------------------------------------------------------- #

def test_insert_column_list_matches_columns_in_order():
    """Lock the static INSERT column list against _COLUMNS (no off-by-one)."""
    inside = _INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    cols = [c.strip() for c in inside.split(",")]
    assert cols == _COLUMNS


def _cell(grain, factor, ci_low=None, ci_high=None, ev=100.0, z=0.5, ae=1.0):
    return FactorCell(
        grain_key=grain, factor=factor, ci_low=ci_low, ci_high=ci_high,
        expected_events=ev, credibility_z=z, ae_derived_factor=ae,
    )


def test_write_proposed_factors_roundtrips_and_replaces(tmp_path):
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    factors = [
        _cell({"product": "TERM", "sex": "M", "smoker": "NS", "attained_age_band": "45-49"}, 0.92,
              ci_low=0.85, ci_high=0.99),
        _cell({"product": "TERM", "sex": "F", "smoker": "NS", "attained_age_band": "45-49"}, 0.88),
    ]
    n = write_proposed_factors("m1", "run1", "GLM", "MORTALITY", "TERM", factors, db)
    assert n == 2

    out = query_results_impl(
        "gold_ai_proposed_factors",
        "SELECT sex, attained_age_band, factor FROM gold_ai_proposed_factors "
        "WHERE product_code = 'TERM' AND decrement = 'MORTALITY' AND model_type = 'GLM' "
        "ORDER BY sex LIMIT 500",
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert out["row_count"] == 2
    assert out["columns"] == ["sex", "attained_age_band", "factor"]
    assert {r[0] for r in out["rows"]} == {"M", "F"}

    # Re-writing the same (run, decrement, product, model_type) REPLACES, not appends.
    write_proposed_factors("m2", "run1", "GLM", "MORTALITY", "TERM", [factors[0]], db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM gold_ai_proposed_factors WHERE run_id='run1' "
            "AND decrement='MORTALITY' AND product_code='TERM' AND model_type='GLM'"
        ).fetchone()[0]
    finally:
        con.close()
    assert total == 1


def test_write_proposed_factors_empty_is_noop(tmp_path):
    db = _fresh_db(tmp_path)
    assert write_proposed_factors("m", "r", "GLM", "MORTALITY", "TERM", [], db) == 0


def test_write_proposed_factors_skips_nan_factor(tmp_path):
    db = _fresh_db(tmp_path)
    assert write_proposed_factors(
        "m", "r", "GLM", "LAPSE", "WL", [_cell({"product": "WL"}, float("nan"))], db
    ) == 0


def test_write_proposed_factors_maps_lapse_and_ci_grain(tmp_path):
    """Grain dims map to the right columns: lapse -> duration_band (sex/smoker/age
    NULL); CI -> attained_age_band + sex (duration NULL). A column-mapping bug
    (e.g. writing duration into the sex column) is caught here."""
    db = _fresh_db(tmp_path)
    write_proposed_factors(
        "mL", "runX", "GLM", "LAPSE", "WL",
        [_cell({"product": "WL", "duration_band": "6-10"}, 1.10)], db,
    )
    write_proposed_factors(
        "mC", "runX", "GLM", "CI_INCIDENCE", "TERM",
        [_cell({"attained_age_band": "45-54", "sex": "M"}, 1.05)], db,
    )
    con = duckdb.connect(str(db), read_only=True)
    try:
        lapse = con.execute(
            "SELECT sex, smoker, attained_age_band, duration_band FROM "
            "gold_ai_proposed_factors WHERE decrement='LAPSE'"
        ).fetchone()
        ci = con.execute(
            "SELECT sex, smoker, attained_age_band, duration_band FROM "
            "gold_ai_proposed_factors WHERE decrement='CI_INCIDENCE'"
        ).fetchone()
    finally:
        con.close()
    assert lapse == (None, None, None, "6-10")        # lapse grain -> duration_band
    assert ci == ("M", None, "45-54", None)           # CI grain -> age band + sex


def test_query_results_enforces_gates_on_widened_tables(tmp_path):
    """The generic tool gates the new tables exactly like the AE/TEV tools:
    a non-SELECT and an uncapped bare scan are both rejected server-side."""
    db = _fresh_db(tmp_path)
    allow = load_allowlist(_AI_CONFIG)
    not_select = query_results_impl(
        "gold_model_points", "DELETE FROM gold_model_points",
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert not_select.get("error") == "gate_2_select"
    over_cap = query_results_impl(
        "gold_model_points", "SELECT product_code FROM gold_model_points",  # no LIMIT, not aggregated
        db_path=db, allowlist=allow, row_cap=500,
    )
    assert over_cap.get("error") == "gate_4_rowcap"


def test_execute_via_mcp_routes_cte_over_widened_table():
    """A CTE over a widened table still routes to the generic tool (the CTE name
    is not a physical queryable table, so the single physical table wins)."""
    mcp = _RecordingMCP()
    sql = ("WITH x AS (SELECT product_code FROM gold_dq_run_summary) "
           "SELECT product_code FROM x LIMIT 5")
    execute_via_mcp(sql, mcp)
    assert mcp.calls and mcp.calls[0][0] == "gold_dq_run_summary"
