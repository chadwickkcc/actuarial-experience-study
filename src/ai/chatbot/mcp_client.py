"""In-process MCP client for the chatbot (Session 20; Tech Spec v2.0.1 §E.6/§E.7).

The chatbot reaches the database **only** through the ``experience_study_data``
MCP server tools (FR-3B-25): it never opens its own DuckDB connection. In-app the
round trip is in-process — this client binds ``db_path`` / ``allowlist`` /
``row_cap`` to the server's five ``*_impl`` tools, which re-enforce the SQL gates
server-side regardless of caller (FR-3B-10). The same surface is what the
Session-22 eval harness drives.

An optional ``on_call`` hook receives a structured event on every tool call, so a
test can assert that intent classification is logged *before* any data access
(FR-3B-27) by feeding both the router audit sink and this hook the same recorder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol

from src.ai.mcp_server.server import (
    get_study_run_summary_impl,
    get_tev_run_summary_impl,
    list_available_dimensions_impl,
    query_ae_results_impl,
    query_results_impl,
    query_tev_results_impl,
)


class MCPClient(Protocol):
    """The data surface the chatbot pipeline depends on (read-only)."""

    def query_ae_results(self, sql: str) -> dict: ...
    def query_tev_results(self, sql: str) -> dict: ...
    def query_results(self, table: str, sql: str) -> dict: ...


class InProcessMCPClient:
    """Binds the five server tools for in-app use; opens no DB connection itself."""

    def __init__(
        self,
        db_path: Path,
        allowlist: dict[str, set[str]],
        row_cap: int = 500,
        on_call: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._allowlist = allowlist
        self._row_cap = row_cap
        self._on_call = on_call

    def _emit(self, tool: str, **detail) -> None:
        if self._on_call is not None:
            self._on_call({"event": "data_access", "tool": tool, **detail})

    def query_ae_results(self, sql: str) -> dict:
        """Route a read-only SELECT to the gated A/E tool (FR-3B-10)."""
        self._emit("query_ae_results", sql=sql)
        return query_ae_results_impl(
            sql, db_path=self._db_path, allowlist=self._allowlist, row_cap=self._row_cap
        )

    def query_tev_results(self, sql: str) -> dict:
        """Route a read-only SELECT to the gated TEV tool (FR-3B-10)."""
        self._emit("query_tev_results", sql=sql)
        return query_tev_results_impl(
            sql, db_path=self._db_path, allowlist=self._allowlist, row_cap=self._row_cap
        )

    def query_results(self, table: str, sql: str) -> dict:
        """Route a read-only SELECT to the gated generic tool for a widened table.

        ``table`` is one of the additional PII-free Gold tables (reconciliation,
        DQ summary, model points, AI model registry, assumption sets, proposed
        factors); the server scopes the query to that single table (FR-3B-10).
        """
        self._emit("query_results", table=table, sql=sql)
        return query_results_impl(
            table, sql, db_path=self._db_path, allowlist=self._allowlist, row_cap=self._row_cap
        )

    def list_available_dimensions(self) -> dict:
        """Available segmentation dimensions (metadata only)."""
        self._emit("list_available_dimensions")
        return list_available_dimensions_impl(
            db_path=self._db_path, allowlist=self._allowlist, row_cap=self._row_cap
        )

    def get_study_run_summary(self, run_id: str) -> dict:
        """Study run manifest (metadata only)."""
        self._emit("get_study_run_summary", run_id=run_id)
        return get_study_run_summary_impl(run_id, db_path=self._db_path)

    def get_tev_run_summary(self, tev_run_id: str) -> dict:
        """TEV run manifest (metadata only)."""
        self._emit("get_tev_run_summary", tev_run_id=tev_run_id)
        return get_tev_run_summary_impl(tev_run_id, db_path=self._db_path)
