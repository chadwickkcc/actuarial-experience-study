"""MCP server end-to-end through FastMCP's own machinery (Session 18).

The other MCP tests call the ``*_impl`` functions directly. These drive the
tools through FastMCP's registered-tool dispatch (``call_tool``) — proving the
closures are wired correctly and that results survive the tool layer — and
assert the returned dicts are JSON-serializable, which the MCP transport
requires (DuckDB can yield numpy/Decimal/date types that ``json.dumps`` rejects).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.utils.sql_boundary import load_allowlist
from src.ai.mcp_server.server import (
    build_server,
    get_study_run_summary_impl,
    list_available_dimensions_impl,
    query_ae_results_impl,
)

_AI_CONFIG = Path("config/ai_config.yaml")


@pytest.fixture(scope="module")
def allowlist() -> dict:
    return load_allowlist(_AI_CONFIG)


def _call(server, name, args=None):
    """Invoke a registered tool through FastMCP's async tool dispatch."""
    return asyncio.run(server._tool_manager.call_tool(name, args or {}))


def test_query_tool_invokes_through_fastmcp(synthetic_db, allowlist):
    srv = build_server(synthetic_db.db_path, allowlist, 500)
    out = _call(srv, "query_ae_results",
                {"sql": "SELECT product_code, actual_deaths_count FROM gold_ae_results LIMIT 3"})
    assert isinstance(out, dict)
    assert sorted(out) == ["columns", "row_count", "rows"]
    assert out["row_count"] == 3


def test_adversarial_rejected_through_fastmcp(synthetic_db, allowlist):
    srv = build_server(synthetic_db.db_path, allowlist, 500)
    out = _call(srv, "query_ae_results", {"sql": "DROP TABLE gold_ae_results"})
    assert out["error"].startswith("gate_2")
    assert "rows" not in out


def test_metadata_tool_invokes_through_fastmcp(synthetic_db, allowlist):
    srv = build_server(synthetic_db.db_path, allowlist, 500)
    out = _call(srv, "list_available_dimensions")
    assert "dimensions" in out
    assert any(d["name"] == "product_code" for d in out["dimensions"])


def test_query_result_is_json_serializable(synthetic_db, allowlist):
    """The MCP transport serializes tool output to JSON — no numpy/Decimal leaks."""
    res = query_ae_results_impl(
        "SELECT product_code, actual_deaths_count, exposure_count, expected_deaths_count "
        "FROM gold_ae_results LIMIT 10",
        db_path=synthetic_db.db_path, allowlist=allowlist)
    assert res["row_count"] == 10
    json.dumps(res)  # must not raise


def test_dimensions_and_error_are_json_serializable(synthetic_db, allowlist):
    json.dumps(list_available_dimensions_impl(db_path=synthetic_db.db_path, allowlist=allowlist))
    json.dumps(get_study_run_summary_impl("nope", db_path=synthetic_db.db_path))  # not_found error
