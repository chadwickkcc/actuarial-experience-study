"""AI Gold table creation tests (Session 18; Tech Spec §D.2/§D.3)."""
from __future__ import annotations

import duckdb

from src.utils.db_init import init_database

_EXPECTED = {
    "gold_ai_eval_results": {
        "eval_run_id", "eval_ts", "model_string", "prompt_template_hashes",
        "tool_schema_version", "execution_accuracy", "gate_integrity",
        "refusal_correctness", "intent_routing_acc", "numeric_traceability",
        "n_golden", "n_adversarial", "est_cost_usd", "actual_cost_usd",
        "per_question",
    },
    "gold_ai_audit_log": {
        "audit_id", "entry_ts", "source", "session_id", "turn_index", "provider",
        "model_string", "intent", "intent_reason", "prompt_template_hashes",
        "user_message", "retrieved_context_ref", "generated_sql",
        "sql_gate_outcome", "sql_gate_detail", "result_row_count", "response_text",
        "traceability_passed", "untraceable_nums", "faithfulness_score", "blocked",
        "block_reason", "input_tokens", "output_tokens", "est_cost_usd", "latency_ms",
    },
}


def _columns(con, table) -> set:
    return {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name = ?",
            [table],
        ).fetchall()
    }


def test_init_creates_both_new_ai_tables(tmp_path):
    db = tmp_path / "t.duckdb"
    init_database(str(db))
    con = duckdb.connect(str(db))
    try:
        tables = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()
        }
        # the Session-15 registry table is present and not duplicated
        assert "gold_ai_model_registry" in tables
        for table, cols in _EXPECTED.items():
            assert table in tables
            assert _columns(con, table) == cols
    finally:
        con.close()


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "t.duckdb"
    init_database(str(db))
    init_database(str(db))  # second call must not raise
    con = duckdb.connect(str(db))
    try:
        for table in _EXPECTED:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        con.close()
