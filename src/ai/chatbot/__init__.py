"""Guarded conversational chatbot — pipeline, session, traceability (§7.10).

Session 20 builds the seven-stage pipeline + guardrails around the Session-19
numeric post-check (``traceability.verify_traceability``). Session 21 adds RAG
commentary, the faithfulness judge, full audit-log writes, and the AI Analyst UI.
"""
from src.ai.chatbot.audit import make_db_audit_sink, write_audit_row
from src.ai.chatbot.context import (
    assemble_rag_context,
    resolve_rag_artifacts,
    trim_history,
)
from src.ai.chatbot.mcp_client import InProcessMCPClient, MCPClient
from src.ai.chatbot.pipeline import (
    SlotFillError,
    assemble_response,
    classify_intent,
    execute_via_mcp,
    fill_numeric_slots,
    generate_commentary_plan,
    generate_query_plan,
    generate_sql,
    handle_turn,
    load_few_shots,
    score_faithfulness,
    validate_sql,
)
from src.ai.chatbot.session import SessionState, record_call
from src.ai.chatbot.traceability import verify_traceability

__all__ = [
    "SessionState",
    "record_call",
    "InProcessMCPClient",
    "MCPClient",
    "classify_intent",
    "generate_query_plan",
    "generate_commentary_plan",
    "score_faithfulness",
    "generate_sql",
    "validate_sql",
    "execute_via_mcp",
    "fill_numeric_slots",
    "assemble_response",
    "handle_turn",
    "load_few_shots",
    "SlotFillError",
    "trim_history",
    "assemble_rag_context",
    "resolve_rag_artifacts",
    "verify_traceability",
    "write_audit_row",
    "make_db_audit_sink",
]
