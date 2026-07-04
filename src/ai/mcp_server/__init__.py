"""experience_study_data MCP server — read-only data tools (§7.8; Session 18).

Read-only, stdio-only MCP server over the Gold layer: the single governed data
surface for all AI access. The five tools and their server-side gate enforcement
live in :mod:`src.ai.mcp_server.server`.
"""
from src.ai.mcp_server.server import (
    TOOL_SCHEMA_VERSION,
    build_server,
    get_study_run_summary_impl,
    get_tev_run_summary_impl,
    list_available_dimensions_impl,
    query_ae_results_impl,
    query_tev_results_impl,
    run,
    serve,
)

__all__ = [
    "TOOL_SCHEMA_VERSION",
    "build_server",
    "serve",
    "run",
    "query_ae_results_impl",
    "query_tev_results_impl",
    "list_available_dimensions_impl",
    "get_study_run_summary_impl",
    "get_tev_run_summary_impl",
]
