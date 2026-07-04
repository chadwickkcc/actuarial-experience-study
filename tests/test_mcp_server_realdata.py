"""End-to-end MCP server smoke against a copy of the production DB (Session 18).

Skips when the production DB is absent. Exercises all five tools on real Gold
data, plus an adversarial rejection, proving the server works against the live
schema (run ed193b59-c5d6-48cd-b5e6-43d33464dff8).
"""
from __future__ import annotations

import duckdb
import pytest

from src.utils.db_init import init_database
from src.utils.sql_boundary import load_allowlist
from src.ai.mcp_server.server import (
    get_study_run_summary_impl,
    get_tev_run_summary_impl,
    list_available_dimensions_impl,
    query_ae_results_impl,
    query_tev_results_impl,
)


@pytest.fixture(scope="module")
def real_db(prod_db):
    """Prod copy with the AI Gold tables added (the copy predates them)."""
    init_database(str(prod_db))  # idempotent; adds the AI tables to the copy
    return prod_db


@pytest.fixture(scope="module")
def allowlist():
    return load_allowlist("config/ai_config.yaml")


def _first(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchone()
    finally:
        con.close()


def test_query_ae_results_real(real_db, allowlist):
    out = query_ae_results_impl(
        "SELECT product_code, ae_count FROM gold_ae_results LIMIT 5",
        db_path=real_db, allowlist=allowlist)
    assert out["columns"] == ["product_code", "ae_count"]
    assert out["row_count"] >= 1


def test_query_tev_results_real(real_db, allowlist):
    out = query_tev_results_impl(
        "SELECT product_code, tev FROM gold_tev_results LIMIT 5",
        db_path=real_db, allowlist=allowlist)
    assert "error" not in out
    assert out["columns"] == ["product_code", "tev"]


def test_list_dimensions_real(real_db, allowlist):
    out = list_available_dimensions_impl(db_path=real_db, allowlist=allowlist)
    by_name = {d["name"]: d["values"] for d in out["dimensions"]}
    assert by_name["product_code"]  # non-empty on real data


def test_get_study_run_summary_real(real_db):
    row = _first(real_db, "SELECT run_id FROM gold_study_runs LIMIT 1")
    if row is None:
        pytest.skip("no study runs in the production DB")
    out = get_study_run_summary_impl(row[0], db_path=real_db)
    assert out["run_id"] == row[0]
    assert "status" in out
    assert "policy_id" not in out  # metadata only — no PII


def test_get_tev_run_summary_real(real_db):
    row = _first(real_db, "SELECT tev_run_id FROM gold_tev_run_log LIMIT 1")
    if row is None:
        pytest.skip("no TEV runs in the production DB")
    out = get_tev_run_summary_impl(row[0], db_path=real_db)
    assert out["tev_run_id"] == row[0]
    assert "assumption_set_id" in out


def test_adversarial_rejected_on_real_db(real_db, allowlist):
    out = query_ae_results_impl(
        "DROP TABLE gold_ae_results", db_path=real_db, allowlist=allowlist)
    assert out["error"].startswith("gate_2")
