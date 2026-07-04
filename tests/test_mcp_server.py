"""MCP server tests (Session 18; FR-3B-09..16).

The adversarial cases call the server's tool functions **directly** — bypassing
any chatbot — to prove the five gates are enforced server-side regardless of
caller (FR-3B-10). Uses the synthetic Gold DB (all tables present, A/E rows
populated) so happy-path queries return real shapes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.sql_boundary import load_allowlist
from src.ai.mcp_server.server import (
    _STDIO_TRANSPORT,
    TOOL_SCHEMA_VERSION,
    build_server,
    get_study_run_summary_impl,
    list_available_dimensions_impl,
    query_ae_results_impl,
    query_tev_results_impl,
)

_AI_CONFIG = Path("config/ai_config.yaml")


@pytest.fixture(scope="module")
def allowlist() -> dict:
    return load_allowlist(_AI_CONFIG)


# --------------------------------------------------------------------------- #
# Server-side gate enforcement (called directly on the server)                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "sql,gate_prefix",
    [
        ("SELECT ae_count FROM gold_ae_results LIMIT 1; SELECT 1", "gate_1"),   # multi-statement
        ("DROP TABLE gold_ae_results", "gate_2"),                              # not SELECT
        ("INSERT INTO gold_ae_results VALUES (1)", "gate_2"),                  # DML
        ("PRAGMA database_list", "gate_2"),                                    # PRAGMA
        ("SELECT policy_id FROM silver_term_policies LIMIT 5", "gate_3"),      # Silver/PII
        ("SELECT termination_cause_code FROM gold_ae_results LIMIT 5", "gate_3"),  # off-allowlist col
        ("SELECT tev FROM gold_tev_results LIMIT 5", "gate_3"),                # other Gold table
        ("SELECT product_code FROM gold_ae_results", "gate_4"),               # unbounded scan
    ],
)
def test_query_ae_rejects_adversarial_sql(synthetic_db, allowlist, sql, gate_prefix):
    out = query_ae_results_impl(sql, db_path=synthetic_db.db_path, allowlist=allowlist)
    assert "error" in out
    assert out["error"].startswith(gate_prefix)
    assert "rows" not in out  # nothing executed


def test_ae_tool_rejects_tev_table_and_vice_versa(synthetic_db, allowlist):
    ae = query_ae_results_impl(
        "SELECT tev FROM gold_tev_results LIMIT 5",
        db_path=synthetic_db.db_path, allowlist=allowlist)
    tev = query_tev_results_impl(
        "SELECT ae_count FROM gold_ae_results LIMIT 5",
        db_path=synthetic_db.db_path, allowlist=allowlist)
    assert ae["error"].startswith("gate_3")
    assert tev["error"].startswith("gate_3")


# --------------------------------------------------------------------------- #
# Happy-path shapes                                                           #
# --------------------------------------------------------------------------- #

def test_query_ae_limit_returns_rows(synthetic_db, allowlist):
    out = query_ae_results_impl(
        "SELECT product_code, actual_deaths_count FROM gold_ae_results LIMIT 5",
        db_path=synthetic_db.db_path, allowlist=allowlist)
    assert out["columns"] == ["product_code", "actual_deaths_count"]
    assert out["row_count"] == len(out["rows"]) == 5
    assert all(len(r) == 2 for r in out["rows"])


def test_query_ae_full_aggregate_passes_without_limit(synthetic_db, allowlist):
    out = query_ae_results_impl(
        "SELECT SUM(actual_deaths_count) AS total FROM gold_ae_results",
        db_path=synthetic_db.db_path, allowlist=allowlist)
    assert out["row_count"] == 1
    assert out["columns"] == ["total"]


def test_list_dimensions_is_metadata_only(synthetic_db, allowlist):
    out = list_available_dimensions_impl(db_path=synthetic_db.db_path, allowlist=allowlist)
    assert set(out) == {"dimensions"}
    by_name = {d["name"]: d["values"] for d in out["dimensions"]}
    assert "product_code" in by_name
    assert "TERM" in by_name["product_code"]   # populated from the synthetic data
    # gender is low-cardinality and fully enumerated by the bounded scan
    assert set(by_name["gender"]) <= {"M", "F", "U"}


def test_get_study_run_summary_not_found(synthetic_db):
    out = get_study_run_summary_impl("does-not-exist", db_path=synthetic_db.db_path)
    assert out["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Tool surface + transport                                                    #
# --------------------------------------------------------------------------- #

def test_build_server_exposes_the_governed_tool_surface(synthetic_db, allowlist):
    """Five original tools + the generic ``query_results`` (FR-3B-09 amended
    2026-06-27 for the governed-maximum data-surface widening)."""
    server = build_server(synthetic_db.db_path, allowlist, 500)
    names = sorted(t.name for t in server._tool_manager.list_tools())
    assert names == [
        "get_study_run_summary",
        "get_tev_run_summary",
        "list_available_dimensions",
        "query_ae_results",
        "query_results",
        "query_tev_results",
    ]


def test_serve_uses_stdio_only(synthetic_db, allowlist):
    """serve() runs over stdio and never binds a network interface (FR-3B-12)."""
    from src.ai.mcp_server import server as server_mod

    captured = {}

    class _StubServer:
        def run(self, **kwargs):
            captured.update(kwargs)

    server_mod.serve(_StubServer())
    assert captured == {"transport": "stdio"}
    assert _STDIO_TRANSPORT == "stdio"


def test_tool_schema_version_constant():
    assert TOOL_SCHEMA_VERSION == "2.0"
