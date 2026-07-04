"""App-side orchestration for the AI Analyst chatbot page (Session 21).

Mirrors ``ui/skills_logic.py``: it lives under ``ui/`` (not ``src/ai/``) so it may
read the Gold layer for the run selector and resolve the RAG grounding artifacts,
then drives the guarded chatbot pipeline (``src/ai/chatbot/handle_turn``). The
chatbot itself reaches the DB only through the in-process MCP client (FR-3B-25);
the only write is the injected ``gold_ai_audit_log`` sink (a sanctioned AI write).
All of this module's own Gold queries are read-only and parameterised.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.ai.chatbot import (
    InProcessMCPClient,
    handle_turn,
    load_few_shots,
    make_db_audit_sink,
    resolve_rag_artifacts,
)
from src.ai.chatbot.session import SessionState
from src.ai.llm.client import available_models, load_llm_config
from src.utils.sql_boundary import load_allowlist

from ui.config import CONFIG_DIR, DB_PATH, PROJECT_ROOT, REPORTS_DIR
from ui.skills_logic import assemble_commentary_facts

_AI_CONFIG = CONFIG_DIR / "ai_config.yaml"
_LLM_CONFIG = CONFIG_DIR / "llm_config.yaml"
_FEW_SHOTS = CONFIG_DIR / "chatbot_few_shots.yaml"


# --------------------------------------------------------------------------- #
# Config loaders                                                              #
# --------------------------------------------------------------------------- #

def llm_config() -> dict:
    """Parsed ``llm_config.yaml`` (drives ``complete`` + per-model pricing)."""
    return load_llm_config(_LLM_CONFIG)


def chatbot_config() -> dict:
    """The ``chatbot`` block of ``ai_config.yaml`` (limits, row cap, rag, judge)."""
    with _AI_CONFIG.open("r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("chatbot", {}) or {}


def gold_allowlist() -> dict:
    """The shared Gold table->columns allowlist (FR-3B-32)."""
    return load_allowlist(_AI_CONFIG)


def few_shots() -> list[dict]:
    """Curated Q->SQL few-shot pairs for the data path."""
    return load_few_shots(_FEW_SHOTS)


def available_analyst_models(config_dir: Path = CONFIG_DIR) -> list[dict]:
    """Configured models for the page dropdown; greyed when the API key is unset
    (FR-3B-04/43)."""
    return available_models(load_llm_config(Path(config_dir) / "llm_config.yaml"))


def default_model_key(config_dir: Path = CONFIG_DIR) -> str:
    """The configured default model (falls back to the first listed)."""
    cfg = load_llm_config(Path(config_dir) / "llm_config.yaml")
    models = available_models(cfg)
    return cfg.get("default_model") or (models[0]["model_id"] if models else "")


# --------------------------------------------------------------------------- #
# Run selector + RAG resolution (read-only)                                   #
# --------------------------------------------------------------------------- #

def list_study_runs(db_path: Path = DB_PATH, limit: int = 50) -> list[dict]:
    """Recent COMPLETE study runs for the page's run selector (read-only)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT run_id, run_ts, product_codes, status FROM gold_study_runs "
            "ORDER BY run_ts DESC NULLS LAST LIMIT ?",
            [int(limit)],
        ).fetchall()
    finally:
        con.close()
    out = []
    for run_id, run_ts, products, status in rows:
        out.append({
            "run_id": run_id,
            "label": f"{str(run_id)[:8]} · {str(run_ts)[:19]} · {products} · {status}",
        })
    return out


def resolve_rag_for_run(
    run_id: str,
    *,
    config_dir: Path = CONFIG_DIR,
    reports_dir: Path = REPORTS_DIR,
) -> dict:
    """Resolve the tool's own grounding artifacts for ``run_id`` (FR-3B-36).

    Reads ``chatbot.rag`` from ``ai_config.yaml`` for the methodology doc list and
    resolves every path against the project root; returns the artifact-paths dict
    consumed by ``assemble_rag_context``. Only files that exist are included.
    """
    rag_cfg = (chatbot_config().get("rag", {}) or {})
    cfg_reports = rag_cfg.get("reports_dir")
    reports_root = (PROJECT_ROOT / cfg_reports) if cfg_reports else Path(reports_dir)
    methodology = [
        str((PROJECT_ROOT / p)) for p in (rag_cfg.get("methodology_docs", []) or [])
    ]
    return resolve_rag_artifacts(
        [run_id], reports_dir=reports_root, methodology_paths=methodology
    )


# --------------------------------------------------------------------------- #
# Turn driver                                                                  #
# --------------------------------------------------------------------------- #

def build_mcp_client(db_path: Path = DB_PATH, row_cap: int = 500) -> InProcessMCPClient:
    """In-process MCP client (the chatbot's only DB path, FR-3B-25)."""
    return InProcessMCPClient(Path(db_path), gold_allowlist(), row_cap=row_cap)


def run_turn(
    user_msg: str,
    state: SessionState,
    *,
    run_id: Optional[str] = None,
    db_path: Path = DB_PATH,
    config_dir: Path = CONFIG_DIR,
    faithfulness: Optional[bool] = None,
    analyst_mode: Optional[bool] = None,
    multi_query: Optional[bool] = None,
):
    """Drive one guarded turn end-to-end with audit logging + RAG grounding.

    The DB audit sink (``gold_ai_audit_log``) is the only write; everything else
    is read-only via the MCP client. Commentary grounds in the selected run's own
    report + methodology artifacts, and is handed an app-assembled fact pack so the
    LLM narrates over real figures (round 3). ``faithfulness`` (when not ``None``)
    overrides the config ``faithfulness_llm_judge`` flag (FR-3B-46); ``analyst_mode``
    (when not ``None``) overrides the opt-in flag-not-block for numeric traceability
    (round 3) — both flag, never silently weaken the SQL gates.
    """
    cb_cfg = dict(chatbot_config())
    if faithfulness is not None:
        cb_cfg["faithfulness_llm_judge"] = bool(faithfulness)
    row_cap = int(cb_cfg.get("sql_row_cap", 500))
    mcp_client = build_mcp_client(db_path, row_cap=row_cap)
    rag_artifacts = resolve_rag_for_run(run_id, config_dir=config_dir) if run_id else {}
    facts = assemble_commentary_facts(db_path, run_id) if run_id else None
    return handle_turn(
        user_msg,
        state,
        llm_config(),
        mcp_client,
        gold_allowlist(),
        chatbot_cfg=cb_cfg,
        few_shots=few_shots(),
        audit=make_db_audit_sink(Path(db_path)),
        rag_run_ids=[run_id] if run_id else None,
        rag_artifact_paths=rag_artifacts or None,
        commentary_facts=facts,
        study_digest=facts,
        analyst_mode=analyst_mode,
        multi_query=multi_query,
    )


# --------------------------------------------------------------------------- #
# Conversation export (FR-3B-43) — banners preserved                          #
# --------------------------------------------------------------------------- #

def export_conversation_markdown(state: SessionState) -> str:
    """Render the conversation to Markdown for download (FR-3B-43).

    Every assistant turn's text is included verbatim, so the persistent
    "AI-drafted — pending actuary review" banner and any low-faithfulness warning
    survive the export.
    """
    lines = [
        "# AI Analyst conversation",
        "",
        f"- Session: `{state.session_id}`",
        f"- Model: `{state.model_key}`",
        f"- Tokens used: {state.tokens_used}",
        f"- Estimated cost: ${state.cost_estimate:.4f}",
        "",
        "---",
        "",
    ]
    for turn in state.turns:
        role = str(turn.get("role", "")).capitalize()
        content = str(turn.get("content", ""))
        lines.append(f"**{role}:**")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)
