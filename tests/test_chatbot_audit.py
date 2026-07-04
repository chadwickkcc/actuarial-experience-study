"""Per-turn audit logging to gold_ai_audit_log (Session 21; FR-3B-47 / NFR-A-07).

Every turn writes one append-only row with the full §D.3 field set via a static
parameterized INSERT; the row is deterministically reconstructable (prompt-template
hashes + dynamic parts); a mid-session model switch is logged; the faithfulness
score is logged. Scripted provider, tmp DB — keys unset, no network.
"""
from __future__ import annotations

import copy
import json

import duckdb

from datetime import datetime

from src.ai.chatbot.audit import _COLUMNS, _INSERT_SQL, make_db_audit_sink, write_audit_row
from src.ai.chatbot.pipeline import handle_turn
from src.ai.chatbot.session import SessionState
from src.utils.db_init import init_database
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)

_MODEL = "claude-sonnet-4-6"
_AE = {"columns": ["ae_count"], "rows": [[0.92]], "row_count": 1}
_SQL = "SELECT ae_count FROM gold_ae_results WHERE product_code='TERM' LIMIT 500"


def _db(tmp_path):
    db = tmp_path / "audit.duckdb"
    init_database(str(db))
    return db


def _factual_provider():
    return ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP", "one figure"),
        sqlgen_reply(_SQL, "A/E is {{col:ae_count}}."),
    )


def test_insert_column_list_matches_columns_in_order():
    """The hand-written INSERT column list must match _COLUMNS exactly, or values
    land in the wrong columns silently. Parse the column list out of the SQL."""
    inner = _INSERT_SQL.split("(", 1)[1].split(") VALUES")[0]
    cols = [c.strip() for c in inner.split(",")]
    assert cols == _COLUMNS


def test_full_row_roundtrips_every_column_in_alignment(tmp_path):
    """Write a row with a distinct value per column; read each back to prove the
    INSERT/_COLUMNS/_coerce mapping is aligned end-to-end (no off-by-one)."""
    db = _db(tmp_path)
    row = {
        "audit_id": "aid-align-1", "entry_ts": datetime(2026, 6, 20, 12, 0, 0),
        "source": "CHATBOT", "session_id": "sess-x", "turn_index": 7,
        "provider": "scripted", "model_string": "m-align",
        "intent": "EXPLORATORY", "intent_reason": "because",
        "prompt_template_hashes": {"routing.md": "h1"}, "user_message": "umsg",
        "retrieved_context_ref": {"run_ids": ["r9"]}, "generated_sql": "SELECT 1",
        "sql_gate_outcome": "PASS", "sql_gate_detail": "ok", "result_row_count": 3,
        "response_text": "resp", "traceability_passed": True,
        "untraceable_nums": ["9"], "faithfulness_score": 4, "blocked": False,
        "block_reason": "br", "input_tokens": 10, "output_tokens": 20,
        "est_cost_usd": 0.5, "latency_ms": 12.5,
    }
    write_audit_row(row, db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        got = con.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM gold_ai_audit_log "
            "WHERE audit_id = 'aid-align-1'"
        ).fetchone()
    finally:
        con.close()
    by_col = dict(zip(_COLUMNS, got))
    assert by_col["session_id"] == "sess-x"
    assert by_col["turn_index"] == 7
    assert by_col["intent"] == "EXPLORATORY"
    assert by_col["model_string"] == "m-align"
    assert by_col["result_row_count"] == 3
    assert by_col["faithfulness_score"] == 4
    assert by_col["blocked"] is False
    assert by_col["input_tokens"] == 10 and by_col["output_tokens"] == 20
    assert abs(by_col["est_cost_usd"] - 0.5) < 1e-9
    assert abs(by_col["latency_ms"] - 12.5) < 1e-9
    assert json.loads(by_col["prompt_template_hashes"]) == {"routing.md": "h1"}
    assert json.loads(by_col["retrieved_context_ref"]) == {"run_ids": ["r9"]}
    assert json.loads(by_col["untraceable_nums"]) == ["9"]


def test_refusal_turn_is_audited(tmp_path):
    """An OUT_OF_SCOPE refusal (not blocked) is still logged with its intent."""
    db = _db(tmp_path)
    provider = ScriptedProvider(routing_reply("OUT_OF_SCOPE", "general knowledge"))
    handle_turn("capital of France?", SessionState(session_id="r", model_key=_MODEL),
                llm_cfg(), StubMCP(ae=_AE), allowlist(), chatbot_cfg=chatbot_cfg(),
                provider=provider, audit=make_db_audit_sink(db))
    con = duckdb.connect(str(db), read_only=True)
    try:
        intent, blocked, reason, sql = con.execute(
            "SELECT intent, blocked, block_reason, generated_sql FROM gold_ai_audit_log"
        ).fetchone()
    finally:
        con.close()
    assert intent == "OUT_OF_SCOPE"
    assert blocked is False and reason == "refusal"
    assert sql is None


def test_write_audit_row_roundtrips_with_json_fields(tmp_path):
    db = _db(tmp_path)
    row = {
        "session_id": "s", "turn_index": 0, "intent": "FACTUAL_LOOKUP",
        "prompt_template_hashes": {"routing.md": "abc"},
        "untraceable_nums": ["999"], "user_message": "q", "blocked": True,
        "input_tokens": 12, "output_tokens": 24, "est_cost_usd": 0.001,
    }
    audit_id = write_audit_row(row, db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        got = con.execute(
            "SELECT audit_id, source, intent, prompt_template_hashes, "
            "untraceable_nums, blocked FROM gold_ai_audit_log WHERE audit_id = ?",
            [audit_id],
        ).fetchone()
    finally:
        con.close()
    assert got[0] == audit_id
    assert got[1] == "CHATBOT"                       # default source
    assert got[2] == "FACTUAL_LOOKUP"
    assert json.loads(got[3]) == {"routing.md": "abc"}   # JSON-encoded dict
    assert json.loads(got[4]) == ["999"]
    assert got[5] is True


def test_turn_event_carries_full_field_set(tmp_path):
    events: list[dict] = []
    handle_turn(
        "Term A/E?", SessionState(session_id="s1", model_key=_MODEL), llm_cfg(),
        StubMCP(ae=_AE), allowlist(), chatbot_cfg=chatbot_cfg(),
        provider=_factual_provider(), audit=events.append,
    )
    turn = next(e for e in events if e.get("event") == "turn")
    # Every §D.3 column (except the auto audit_id/entry_ts) is present in the event.
    for col in _COLUMNS:
        if col in ("audit_id", "entry_ts"):
            continue
        assert col in turn, f"missing audit field: {col}"
    assert turn["intent"] == "FACTUAL_LOOKUP"
    assert turn["traceability_passed"] is True
    assert turn["result_row_count"] == 1
    assert turn["input_tokens"] > 0 and turn["output_tokens"] > 0


def test_handle_turn_writes_one_row_via_db_sink(tmp_path):
    db = _db(tmp_path)
    handle_turn(
        "Term A/E?", SessionState(session_id="sX", model_key=_MODEL), llm_cfg(),
        StubMCP(ae=_AE), allowlist(), chatbot_cfg=chatbot_cfg(),
        provider=_factual_provider(), audit=make_db_audit_sink(db),
    )
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT session_id, intent, generated_sql, traceability_passed, "
            "prompt_template_hashes FROM gold_ai_audit_log"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    assert rows[0][0] == "sX" and rows[0][1] == "FACTUAL_LOOKUP"
    assert "gold_ae_results" in rows[0][2]
    assert rows[0][3] is True
    # Deterministic reconstruction: the templates used are referenced by hash.
    hashes = json.loads(rows[0][4])
    assert "routing.md" in hashes and "sql_generation.md" in hashes


def test_model_switch_mid_session_is_logged(tmp_path):
    db = _db(tmp_path)
    state = SessionState(session_id="sw", model_key=_MODEL)
    sink = make_db_audit_sink(db)
    handle_turn("Term A/E?", state, llm_cfg(), StubMCP(ae=_AE), allowlist(),
                chatbot_cfg=chatbot_cfg(), provider=_factual_provider(), audit=sink)
    state.model_key = "claude-opus-4-8"  # mid-session switch (FR-3B-45)
    handle_turn("Term A/E again?", state, llm_cfg(), StubMCP(ae=_AE), allowlist(),
                chatbot_cfg=chatbot_cfg(), provider=_factual_provider(), audit=sink)
    con = duckdb.connect(str(db), read_only=True)
    try:
        models = [r[0] for r in con.execute(
            "SELECT model_string FROM gold_ai_audit_log ORDER BY turn_index"
        ).fetchall()]
    finally:
        con.close()
    assert models == ["claude-sonnet-4-6", "claude-opus-4-8"]


def test_budget_stop_turn_is_audited_with_null_intent(tmp_path):
    """A pre-routing budget hard-stop still writes an audit row (intent NULL)."""
    db = _db(tmp_path)
    cfg = copy.deepcopy(chatbot_cfg())
    cfg["session_token_budget"] = 1  # force the hard-stop on entry
    state = SessionState(session_id="bud", model_key=_MODEL, tokens_used=10)
    result = handle_turn("anything", state, llm_cfg(), StubMCP(ae=_AE), allowlist(),
                         chatbot_cfg=cfg, provider=_factual_provider(),
                         audit=make_db_audit_sink(db))
    assert result.blocked is True and result.block_reason == "budget_exhausted"
    con = duckdb.connect(str(db), read_only=True)
    try:
        intent, blocked, reason, model = con.execute(
            "SELECT intent, blocked, block_reason, model_string FROM gold_ai_audit_log"
        ).fetchone()
    finally:
        con.close()
    assert intent is None and blocked is True
    assert reason == "budget_exhausted"
    assert model == _MODEL  # falls back to the session model when no call was made


def test_blocked_turn_is_audited(tmp_path):
    db = _db(tmp_path)
    # Off-allowlist SQL is gate-rejected; the blocked turn must still be logged.
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply("DROP TABLE gold_ae_results", "x {{col:ae_count}}"),
    )
    handle_turn("drop it", SessionState(session_id="b", model_key=_MODEL), llm_cfg(),
                StubMCP(ae=_AE), allowlist(), chatbot_cfg=chatbot_cfg(),
                provider=provider, audit=make_db_audit_sink(db))
    con = duckdb.connect(str(db), read_only=True)
    try:
        blocked, reason, outcome = con.execute(
            "SELECT blocked, block_reason, sql_gate_outcome FROM gold_ai_audit_log"
        ).fetchone()
    finally:
        con.close()
    assert blocked is True
    assert outcome == "REJECT_NOT_SELECT"
