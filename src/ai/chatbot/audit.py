"""Append-only per-turn audit logging to ``gold_ai_audit_log`` (Session 21).

Realises FR-3B-47 / NFR-A-07: every chatbot turn (and Skill/MCP call) is recorded
with the full Tech-Spec §D.3 field set so any answer can be deterministically
reconstructed (the §D.3 hashes-plus-dynamic-parts reconciliation of FR-3B-41).

The INSERT is a *static, parameterized* statement (``?`` placeholders) on a
writable connection — the same controlled-write pattern as
``src/ai/glm/registry.py`` (permitted under FR-3A-02 no-interpolation and
FR-3A-09: ``gold_ai_audit_log`` is one of the three AI Gold tables). The chatbot
pipeline stays DB-free (FR-3B-25); it emits a ``{"event": "turn", ...}`` event and
this injected sink performs the only write.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import duckdb

from src.utils.db_init import DEFAULT_DB_PATH

# Column order matches the §D.3 DDL exactly (26 columns).
_COLUMNS = [
    "audit_id", "entry_ts", "source", "session_id", "turn_index", "provider",
    "model_string", "intent", "intent_reason", "prompt_template_hashes",
    "user_message", "retrieved_context_ref", "generated_sql", "sql_gate_outcome",
    "sql_gate_detail", "result_row_count", "response_text", "traceability_passed",
    "untraceable_nums", "faithfulness_score", "blocked", "block_reason",
    "input_tokens", "output_tokens", "est_cost_usd", "latency_ms",
]

# JSON-encoded columns (dict/list -> JSON string for storage).
_JSON_COLUMNS = {"prompt_template_hashes", "retrieved_context_ref", "untraceable_nums"}

# Static, implicitly-concatenated statement (no f-string / % / .format() / '+'
# building) so the FR-3A-02 interpolation guard stays green — the column list and
# placeholder count match _COLUMNS (26) exactly.
_INSERT_SQL = (
    "INSERT INTO gold_ai_audit_log ("
    "audit_id, entry_ts, source, session_id, turn_index, provider, "
    "model_string, intent, intent_reason, prompt_template_hashes, "
    "user_message, retrieved_context_ref, generated_sql, sql_gate_outcome, "
    "sql_gate_detail, result_row_count, response_text, traceability_passed, "
    "untraceable_nums, faithfulness_score, blocked, block_reason, "
    "input_tokens, output_tokens, est_cost_usd, latency_ms"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
    "?, ?, ?, ?)"
)
assert _INSERT_SQL.count("?") == len(_COLUMNS)  # placeholders match columns


def _coerce(column: str, value):
    """Serialize a single field for the parameterized INSERT."""
    if value is None:
        return None
    if column in _JSON_COLUMNS:
        return value if isinstance(value, str) else json.dumps(value, default=str)
    return value


def write_audit_row(row: dict, db_path: Path = Path(DEFAULT_DB_PATH)) -> str:
    """Append one row to ``gold_ai_audit_log`` (append-only, FR-3B-47).

    ``row`` is keyed by the §D.3 column names; missing keys default to NULL.
    ``audit_id`` and ``entry_ts`` are generated when absent. Returns the
    ``audit_id`` written.
    """
    enriched = dict(row)
    enriched.setdefault("audit_id", str(uuid.uuid4()))
    enriched.setdefault("entry_ts", datetime.utcnow())
    enriched.setdefault("source", "CHATBOT")
    values = [_coerce(col, enriched.get(col)) for col in _COLUMNS]

    con = duckdb.connect(str(db_path))
    try:
        con.execute(_INSERT_SQL, values)
    finally:
        con.close()
    return enriched["audit_id"]


def make_db_audit_sink(
    db_path: Path = Path(DEFAULT_DB_PATH),
) -> Callable[[dict], None]:
    """Return an audit sink that writes a ``gold_ai_audit_log`` row per turn.

    The chatbot emits ordered ``intent`` / ``sql_validation`` / ``data_access``
    events (used for ordering tests) and one final ``turn`` event carrying the
    full field set. This sink writes only on the ``turn`` event; the others are
    ignored (they are reconstructable from the persisted row).
    """
    target = Path(db_path)

    def _sink(event: dict) -> None:
        if event.get("event") != "turn":
            return
        payload = {k: v for k, v in event.items() if k != "event"}
        write_audit_row(payload, target)

    return _sink
