"""Tests for the hardened SQL boundary (Tech Spec v2.0.1 §E.2; FR-3A-01/02).

Validates each of the five gates plus the SELECT * expansion rule and the
read-only execution contract. These are the security gates that all AI-layer
data access must pass through.
"""
from pathlib import Path

import duckdb
import pytest

from src.utils.sql_boundary import (
    SQLBoundaryError,
    execute_safe_select,
    load_allowlist,
    validate_select,
)
from src.utils.types import SQLGateOutcome

_AI_CONFIG = Path("config/ai_config.yaml")


@pytest.fixture(scope="module")
def allowlist() -> dict:
    """The real Gold-only allowlist loaded from config/ai_config.yaml."""
    return load_allowlist(_AI_CONFIG)


@pytest.fixture(scope="module")
def tiny_db(tmp_path_factory) -> Path:
    """A transient DuckDB with a minimal gold_ae_results table for execution."""
    db = tmp_path_factory.mktemp("sqlb") / "tiny.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE gold_ae_results "
        "(product_code VARCHAR, ae_count DOUBLE, actual_deaths_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO gold_ae_results VALUES "
        "('TERM', 1.02, 10), ('WL', 0.98, 5), ('UL', 1.10, 7)"
    )
    conn.close()
    return db


# --------------------------------------------------------------------------- #
# load_allowlist                                                              #
# --------------------------------------------------------------------------- #

def test_load_allowlist_shape(allowlist):
    """Returns {table: {columns}} including the core A/E + TEV tables and the
    widened PII-free Gold surface (2026-06-27 governed-maximum amendment)."""
    # Core results tables always present.
    assert {"gold_ae_results", "gold_tev_results"} <= set(allowlist)
    # Widened PII-free results/summary + governance tables.
    assert {
        "gold_inforce_reconciliation", "gold_dq_run_summary", "gold_model_points",
        "gold_ai_model_registry", "gold_assumption_sets", "gold_ai_proposed_factors",
    } <= set(allowlist)
    assert isinstance(allowlist["gold_ae_results"], set)
    assert "ae_count" in allowlist["gold_ae_results"]
    assert "tev" in allowlist["gold_tev_results"]
    # No PII column anywhere, and no raw / policy-level table is a key.
    flat = set().union(*allowlist.values())
    for pii in ("policy_holder_name", "ssn", "date_of_birth", "policy_id",
                "author_id", "approved_by", "yaml_file_path"):
        assert pii not in flat
    assert "gold_dq_quarantine" not in allowlist        # carries policy_id
    assert "gold_exposure_segments" not in allowlist     # carries policy_id
    assert not any(t.startswith(("silver_", "bronze_")) for t in allowlist)


def test_load_allowlist_missing_block(tmp_path):
    """A config without chatbot.allowlist is boundary misuse → SQLBoundaryError."""
    bad = tmp_path / "ai_config.yaml"
    bad.write_text("chatbot:\n  sql_row_cap: 500\n", encoding="utf-8")
    with pytest.raises(SQLBoundaryError):
        load_allowlist(bad)


# --------------------------------------------------------------------------- #
# Gate 1 — parse                                                             #
# --------------------------------------------------------------------------- #

def test_gate1_multi_statement_rejected(allowlist):
    r = validate_select(
        "SELECT ae_count FROM gold_ae_results LIMIT 1; SELECT 1", allowlist
    )
    assert r.outcome is SQLGateOutcome.REJECT_PARSE


# --------------------------------------------------------------------------- #
# Gate 2 — SELECT only                                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE gold_ae_results",
        "DELETE FROM gold_ae_results",
        "UPDATE gold_ae_results SET ae_count = 0",
        "INSERT INTO gold_ae_results (ae_count) VALUES (1)",
        "CREATE TABLE x (a INT)",
        "ALTER TABLE gold_ae_results ADD COLUMN x INT",
        "PRAGMA database_list",
        "ATTACH 'evil.db' AS e",
        "SET memory_limit = '1GB'",
        "BEGIN TRANSACTION",
        "COMMIT",
    ],
)
def test_gate2_non_select_rejected(allowlist, sql):
    r = validate_select(sql, allowlist)
    assert r.outcome is SQLGateOutcome.REJECT_NOT_SELECT, sql


# --------------------------------------------------------------------------- #
# Gate 3 — allowlist + SELECT * expansion                                    #
# --------------------------------------------------------------------------- #

def test_gate3_off_allowlist_table_rejected(allowlist):
    r = validate_select(
        "SELECT * FROM silver_canonical_policies LIMIT 5", allowlist
    )
    assert r.outcome is SQLGateOutcome.REJECT_ALLOWLIST


def test_gate3_off_allowlist_column_rejected(allowlist):
    r = validate_select(
        "SELECT policy_holder_name FROM gold_ae_results LIMIT 5", allowlist
    )
    assert r.outcome is SQLGateOutcome.REJECT_ALLOWLIST


def test_gate3_star_expands_to_allowlisted_subset(allowlist):
    """Bare * is expanded to allowlisted columns — never physical/PII columns."""
    r = validate_select("SELECT * FROM gold_ae_results LIMIT 5", allowlist)
    assert r.outcome is SQLGateOutcome.PASS
    assert "*" not in r.sql
    assert "ae_count" in r.sql
    # The expansion uses the allowlist, so an off-allowlist/PII column can never
    # appear even though the physical table might contain one.
    assert "policy_holder_name" not in r.sql
    # A representative spread of allowlisted columns is present.
    assert all(c in r.sql for c in ("ae_count", "exposure_count", "product_code"))


def test_gate3_qualified_star_expands(allowlist):
    r = validate_select("SELECT g.* FROM gold_ae_results g LIMIT 5", allowlist)
    assert r.outcome is SQLGateOutcome.PASS
    assert "g.ae_count" in r.sql
    assert "*" not in r.sql


def test_gate3_join_two_gold_tables_pass(allowlist):
    sql = (
        "SELECT a.ae_count, t.tev FROM gold_ae_results a "
        "JOIN gold_tev_results t ON a.assumption_set_id = t.assumption_set_id "
        "LIMIT 5"
    )
    assert validate_select(sql, allowlist).outcome is SQLGateOutcome.PASS


def test_gate3_off_allowlist_table_in_union_rejected(allowlist):
    """An off-allowlist table cannot be smuggled in through a UNION branch."""
    sql = (
        "SELECT ae_count FROM gold_ae_results LIMIT 5 "
        "UNION SELECT secret FROM silver_canonical_policies"
    )
    assert validate_select(sql, allowlist).outcome is SQLGateOutcome.REJECT_ALLOWLIST


def test_gate3_off_allowlist_table_in_subquery_rejected(allowlist):
    """An off-allowlist table cannot be smuggled in through a subquery."""
    sql = (
        "SELECT ae_count FROM gold_ae_results "
        "WHERE assumption_set_id IN (SELECT id FROM silver_secret) LIMIT 5"
    )
    assert validate_select(sql, allowlist).outcome is SQLGateOutcome.REJECT_ALLOWLIST


def test_stacked_statement_via_comment_rejected(allowlist):
    """A trailing second statement is caught even when comment-obfuscated."""
    sql = "SELECT ae_count FROM gold_ae_results LIMIT 5; -- DROP TABLE x"
    assert validate_select(sql, allowlist).outcome is SQLGateOutcome.REJECT_PARSE


@pytest.mark.parametrize(
    "sql, outcome",
    [
        ("SELECT COUNT(*) FROM gold_ae_results", SQLGateOutcome.PASS),
        ("select ae_count from gold_ae_results limit 5", SQLGateOutcome.PASS),
        ("SELECT ae_count FROM gold_ae_results LIMIT 500", SQLGateOutcome.PASS),
        ("SELECT ae_count FROM gold_ae_results LIMIT 501", SQLGateOutcome.REJECT_ROWCAP),
        ("", SQLGateOutcome.REJECT_PARSE),
        ("   ", SQLGateOutcome.REJECT_PARSE),
    ],
)
def test_assorted_edge_cases(allowlist, sql, outcome):
    assert validate_select(sql, allowlist).outcome is outcome


# --------------------------------------------------------------------------- #
# Gate 4 — row cap                                                           #
# --------------------------------------------------------------------------- #

def test_gate4_bare_scan_without_limit_rejected(allowlist):
    r = validate_select("SELECT product_code FROM gold_ae_results", allowlist)
    assert r.outcome is SQLGateOutcome.REJECT_ROWCAP


def test_gate4_limit_over_cap_rejected(allowlist):
    r = validate_select(
        "SELECT ae_count FROM gold_ae_results LIMIT 100000", allowlist
    )
    assert r.outcome is SQLGateOutcome.REJECT_ROWCAP


def test_gate4_fully_aggregated_passes_without_limit(allowlist):
    r = validate_select(
        "SELECT SUM(actual_deaths_count) FROM gold_ae_results", allowlist
    )
    assert r.outcome is SQLGateOutcome.PASS


def test_gate4_group_by_without_limit_rejected(allowlist):
    """A GROUP BY can return many rows → needs a LIMIT (not 'fully aggregated')."""
    r = validate_select(
        "SELECT product_code, SUM(ae_count) FROM gold_ae_results "
        "GROUP BY product_code",
        allowlist,
    )
    assert r.outcome is SQLGateOutcome.REJECT_ROWCAP


def test_clean_select_passes(allowlist):
    r = validate_select(
        "SELECT product_code, ae_count FROM gold_ae_results LIMIT 10", allowlist
    )
    assert r.outcome is SQLGateOutcome.PASS
    assert r.gate_failed is None


# --------------------------------------------------------------------------- #
# Gate 5 — read-only execution                                              #
# --------------------------------------------------------------------------- #

def test_execute_safe_select_returns_dataframe(allowlist, tiny_db):
    result, df = execute_safe_select(
        tiny_db, "SELECT product_code, ae_count FROM gold_ae_results LIMIT 2",
        allowlist,
    )
    assert result.outcome is SQLGateOutcome.PASS
    assert df is not None
    assert list(df.columns) == ["product_code", "ae_count"]
    assert len(df) == 2


def test_execute_safe_select_write_blocked_not_executed(allowlist, tiny_db):
    """A write statement is rejected at the gate and never executed."""
    result, df = execute_safe_select(
        tiny_db, "DELETE FROM gold_ae_results", allowlist
    )
    assert result.outcome is SQLGateOutcome.REJECT_NOT_SELECT
    assert df is None
    # Confirm nothing was deleted.
    conn = duckdb.connect(str(tiny_db), read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM gold_ae_results").fetchone()[0] == 3
    finally:
        conn.close()


def test_execute_safe_select_raises_on_unopenable_readonly(allowlist, tmp_path):
    """A valid SELECT against a DB that cannot be opened read-only (which would
    require a writable/creating connection) raises SQLBoundaryError."""
    missing = tmp_path / "does_not_exist.duckdb"
    with pytest.raises(SQLBoundaryError):
        execute_safe_select(
            missing, "SELECT ae_count FROM gold_ae_results LIMIT 1", allowlist
        )


def test_execute_safe_select_opens_connection_read_only(
    allowlist, tiny_db, monkeypatch
):
    """The execution connection is always opened with read_only=True (gate 5)."""
    import src.utils.sql_boundary as sb

    captured = {}
    real_connect = sb.duckdb.connect

    def spy(database, *args, **kwargs):
        captured["read_only"] = kwargs.get("read_only")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sb.duckdb, "connect", spy)
    result, df = execute_safe_select(
        tiny_db, "SELECT ae_count FROM gold_ae_results LIMIT 1", allowlist
    )
    assert result.outcome is SQLGateOutcome.PASS
    assert captured["read_only"] is True


def test_validate_select_is_deterministic(allowlist):
    """Star expansion / normalization is stable across repeated calls."""
    sql = "SELECT * FROM gold_ae_results LIMIT 5"
    first = validate_select(sql, allowlist).sql
    second = validate_select(sql, allowlist).sql
    assert first == second
