"""experience_study_data MCP server (Session 18; Tech Spec v2.0.1 §E.6).

A read-only MCP server exposing Gold-layer results to LLM clients — the single
governed data surface for all AI access (FR-3B-09..16). It enforces its own
constraints rather than trusting callers:

* the two dedicated ``query_*`` tools plus the generic ``query_results(table,
  sql)`` tool (the 2026-06-27 governed-maximum widening) route every statement
  through the §7.2 hardened SQL boundary (``execute_safe_select``), so gates 1–5
  (parse, SELECT-only, allowlist, row-cap, read-only execution) run **server-side
  regardless of caller** (FR-3B-10) — each query is scoped to *just one* Gold
  table so it cannot read any other table (the AE tool cannot read the TEV table;
  the generic tool reads only the single named widened table). The widened set is
  PII-free results/summary tables only (reconciliation, DQ summary, model points,
  AI model registry, assumption sets, proposed factors) — never a table carrying
  policy_id and never Silver/Bronze;
* the three metadata tools return manifest/dimension metadata only — never PII
  and never policy rows (FR-3B-13);
* no write-capable connection is ever opened (FR-3B-11);
* the server runs over **stdio only** and binds no network interface
  (FR-3B-12).

Tools never raise: gate rejections and errors come back as structured objects,
never stack traces (FR-3B-15). The metadata tools read the manifest tables
(``gold_study_runs`` / ``gold_tev_run_log``) — which are intentionally *not* on
the chatbot allowlist — through a read-only, **parameterized** (``?``) query;
this is the documented, fixed-shape, PII-free metadata read path (no string
interpolation anywhere, FR-3A-02).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import duckdb
import yaml

from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.sql_boundary import (
    SQLBoundaryError,
    execute_safe_select,
    load_allowlist,
)
from src.utils.types import SQLGateOutcome

# Tool-schema version (FR-3B-16): recorded in eval-harness results so accuracy
# measurements are tied to the tool surface they ran against. Bumped to 2.0 for
# the 2026-06-27 governed-maximum data-surface widening (the generic
# ``query_results`` tool over the additional PII-free Gold tables; FR-3B-09
# in-place amendment).
TOOL_SCHEMA_VERSION = "2.0"

SERVER_NAME = "experience_study_data"
_STDIO_TRANSPORT = "stdio"

# Imported from src.utils (not a bare data/ literal) so the FR-3A-09 write-path
# guard does not mistake this read-only default for a write target.
_DEFAULT_DB = Path(DEFAULT_DB_PATH)
_DEFAULT_AI_CONFIG = Path("config/ai_config.yaml")
_DEFAULT_ROW_CAP = 500

# The two original query tools are each pinned to exactly one Gold table.
_AE_TABLE = "gold_ae_results"
_TEV_TABLE = "gold_tev_results"

# Additional PII-free Gold results/summary tables reachable through the generic
# ``query_results(table, sql)`` tool (governed-maximum widening, 2026-06-27).
# Each query is still scoped to a SINGLE table server-side (defence-in-depth: no
# cross-table read), allowlist-gated, row-capped and read-only. NO PII table is
# listed (no gold_dq_quarantine / gold_exposure_segments — both carry policy_id —
# and no Silver/Bronze).
_EXTRA_QUERYABLE_TABLES = (
    "gold_inforce_reconciliation",
    "gold_dq_run_summary",
    "gold_model_points",
    "gold_ai_model_registry",
    "gold_assumption_sets",
    "gold_ai_proposed_factors",
)

#: Every table the AI may query (the two originals + the widened set). The chatbot
#: pipeline imports this to route a validated single-table SELECT to its tool, and
#: a test asserts it equals the configured allowlist keys (no drift).
QUERYABLE_TABLES = (_AE_TABLE, _TEV_TABLE, *_EXTRA_QUERYABLE_TABLES)

# Static, no-interpolation dimensions probe: the categorical segmentation
# columns of the A/E fact table. DISTINCT over a bounded scan (LIMIT keeps it
# gate-4 compliant); per-column distinct values are derived in pandas. The
# literal LIMIT equals the default row cap, so the boundary admits it.
_DIMENSIONS_SQL = """
SELECT DISTINCT product_code, plan_code, gender, smoker_status, risk_class,
       issue_age_band, attained_age_band, duration_band, calendar_year,
       is_plt_flag, premium_jump_ratio_band, distribution_channel, illness_code
FROM gold_ae_results
LIMIT 500
"""

# Fixed-shape, PII-free manifest reads (parameterized; never interpolated).
_STUDY_SUMMARY_SQL = (
    "SELECT run_id, run_ts, product_codes, study_start_date, study_end_date, "
    "exposure_method, mortality_table, lapse_table, ci_table, credibility_method, "
    "data_snapshot_hash, config_hash, code_version, run_duration_sec, status "
    "FROM gold_study_runs WHERE run_id = ?"
)
_TEV_SUMMARY_SQL = (
    "SELECT tev_run_id, assumption_set_id, sensitivity_id, run_ts, model_point_hash, "
    "config_hash, code_version, projection_years, run_duration_sec, status, "
    "total_anw, total_pvfp, total_pvcoc, total_vif, total_tev, delta_tev_vs_prior, "
    "prior_tev_run_id "
    "FROM gold_tev_run_log WHERE tev_run_id = ?"
)


def _jsonable(value: Any) -> Any:
    """Coerce a DuckDB/pandas scalar to a JSON-serializable Python value."""
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, bool):
        return value
    if hasattr(value, "isoformat"):  # date / datetime / Timestamp
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        try:
            item = value.item()
            return None if isinstance(item, float) and math.isnan(item) else item
        except (ValueError, TypeError):  # pragma: no cover - defensive
            return str(value)
    return value


def _error(error: str, message: str) -> dict:
    """A structured, human-readable error object (FR-3B-15)."""
    return {"error": error, "message": message}


def _run_query(
    sql: str,
    *,
    table: str,
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int,
) -> dict:
    """Validate + execute a SELECT scoped to a single Gold table.

    The allowlist is narrowed to ``{table}`` so any reference to another table
    fails gate 3. Returns ``{columns, rows, row_count}`` on PASS, else a
    structured error. Never raises (FR-3B-15).
    """
    if table not in allowlist:
        return _error("server_misconfigured", f"Table {table!r} not in allowlist.")
    scoped = {table: allowlist[table]}
    try:
        validation, df = execute_safe_select(db_path, sql, scoped, row_cap)
    except SQLBoundaryError as err:  # boundary misuse (e.g. cannot open RO conn)
        return _error("boundary_error", str(err))
    except Exception:  # noqa: BLE001 - never leak a stack trace to the caller
        return _error("internal_error", "Query execution failed.")

    if validation.outcome is not SQLGateOutcome.PASS or df is None:
        return _error(
            validation.gate_failed or validation.outcome.value,
            validation.detail or "Query rejected by the SQL boundary.",
        )

    columns = [str(c) for c in df.columns]
    rows = [[_jsonable(v) for v in rec] for rec in df.itertuples(index=False, name=None)]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def query_ae_results_impl(
    sql: str,
    *,
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int = _DEFAULT_ROW_CAP,
) -> dict:
    """Read-only SELECT against the Gold A/E fact table (FR-3B-09)."""
    return _run_query(sql, table=_AE_TABLE, db_path=db_path, allowlist=allowlist, row_cap=row_cap)


def query_tev_results_impl(
    sql: str,
    *,
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int = _DEFAULT_ROW_CAP,
) -> dict:
    """Read-only SELECT against the Gold TEV results table (FR-3B-09)."""
    return _run_query(sql, table=_TEV_TABLE, db_path=db_path, allowlist=allowlist, row_cap=row_cap)


def query_results_impl(
    table: str,
    sql: str,
    *,
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int = _DEFAULT_ROW_CAP,
) -> dict:
    """Read-only SELECT against one of the widened PII-free Gold tables (FR-3B-09).

    ``table`` must be in :data:`QUERYABLE_TABLES`; the query is then scoped to that
    single table (so it cannot read any other table, even an allowlisted one) and
    run through the same gates as the original query tools. Returns ``{columns,
    rows, row_count}`` on PASS, else a structured error. Never raises (FR-3B-15).
    """
    if table not in QUERYABLE_TABLES:
        return _error("table_not_queryable", f"Table {table!r} is not queryable.")
    return _run_query(sql, table=table, db_path=db_path, allowlist=allowlist, row_cap=row_cap)


def list_available_dimensions_impl(
    *,
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int = _DEFAULT_ROW_CAP,
) -> dict:
    """Available segmentation dimensions, metadata only (FR-3B-09/13).

    Distinct values are derived in pandas from a bounded (``LIMIT 500``) DISTINCT
    scan via the boundary, so no policy rows and no PII are returned. Because the
    scan is row-capped, the value lists are a representative sample, not an
    exhaustive enumeration — they are a UI/agent hint, not a query result.
    """
    if _AE_TABLE not in allowlist:
        return _error("server_misconfigured", f"Table {_AE_TABLE!r} not in allowlist.")
    scoped = {_AE_TABLE: allowlist[_AE_TABLE]}
    try:
        validation, df = execute_safe_select(db_path, _DIMENSIONS_SQL, scoped, max(row_cap, 500))
    except SQLBoundaryError as err:
        return _error("boundary_error", str(err))
    except Exception:  # noqa: BLE001
        return _error("internal_error", "Dimension lookup failed.")

    if validation.outcome is not SQLGateOutcome.PASS or df is None:
        return _error(
            validation.gate_failed or validation.outcome.value,
            validation.detail or "Dimension query rejected by the SQL boundary.",
        )

    dimensions = []
    for col in df.columns:
        values = sorted(
            {_jsonable(v) for v in df[col].dropna().tolist()},
            key=lambda x: (str(type(x)), str(x)),
        )
        dimensions.append({"name": str(col), "values": values})
    return {"dimensions": dimensions}


def _read_manifest(sql: str, key: str, db_path: Path) -> Optional[tuple]:
    """Run a fixed, parameterized manifest SELECT on a read-only connection."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        cur = con.execute(sql, [key])
        columns = [d[0] for d in cur.description]
        row = cur.fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return columns, row


def get_study_run_summary_impl(run_id: str, *, db_path: Path) -> dict:
    """Study run manifest (metadata only; no policy data) (FR-3B-09)."""
    try:
        result = _read_manifest(_STUDY_SUMMARY_SQL, run_id, db_path)
    except Exception:  # noqa: BLE001
        return _error("internal_error", "Could not read the study run manifest.")
    if result is None:
        return _error("not_found", f"No study run with run_id {run_id!r}.")
    columns, row = result
    return {col: _jsonable(val) for col, val in zip(columns, row)}


def get_tev_run_summary_impl(tev_run_id: str, *, db_path: Path) -> dict:
    """TEV run manifest including assumption set ID (metadata only) (FR-3B-09)."""
    try:
        result = _read_manifest(_TEV_SUMMARY_SQL, tev_run_id, db_path)
    except Exception:  # noqa: BLE001
        return _error("internal_error", "Could not read the TEV run manifest.")
    if result is None:
        return _error("not_found", f"No TEV run with tev_run_id {tev_run_id!r}.")
    columns, row = result
    return {col: _jsonable(val) for col, val in zip(columns, row)}


def _load_row_cap(config_path: Path) -> int:
    """Read ``chatbot.sql_row_cap`` from ai_config.yaml (default 500)."""
    config_path = Path(config_path)
    if not config_path.exists():
        return _DEFAULT_ROW_CAP
    with config_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    try:
        return int(cfg["chatbot"]["sql_row_cap"])
    except (KeyError, TypeError, ValueError):
        return _DEFAULT_ROW_CAP


def build_server(
    db_path: Path,
    allowlist: dict[str, set[str]],
    row_cap: int = _DEFAULT_ROW_CAP,
):
    """Build the FastMCP server, registering the five tools as thin closures.

    The five core ``*_impl`` functions hold all logic and are directly testable
    with explicit keywords; the registered tools simply bind ``db_path`` /
    ``allowlist`` / ``row_cap``. (FastMCP registration boilerplate is per the
    FastMCP docs so the spec does not rot against library versions.)
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(SERVER_NAME)

    @server.tool()
    def query_ae_results(sql: str) -> dict:
        """Run a read-only SELECT against the Gold A/E results fact table."""
        return query_ae_results_impl(sql, db_path=db_path, allowlist=allowlist, row_cap=row_cap)

    @server.tool()
    def query_tev_results(sql: str) -> dict:
        """Run a read-only SELECT against the Gold TEV results table."""
        return query_tev_results_impl(sql, db_path=db_path, allowlist=allowlist, row_cap=row_cap)

    @server.tool()
    def query_results(table: str, sql: str) -> dict:
        """Run a read-only SELECT against one widened PII-free Gold table.

        ``table`` must be one of the queryable Gold tables (reconciliation, DQ
        summary, model points, AI model registry, assumption sets, proposed
        factors); the query is scoped server-side to that single table.
        """
        return query_results_impl(
            table, sql, db_path=db_path, allowlist=allowlist, row_cap=row_cap
        )

    @server.tool()
    def list_available_dimensions() -> dict:
        """List the available A/E segmentation dimensions (metadata only)."""
        return list_available_dimensions_impl(db_path=db_path, allowlist=allowlist, row_cap=row_cap)

    @server.tool()
    def get_study_run_summary(run_id: str) -> dict:
        """Return the manifest metadata for a study run."""
        return get_study_run_summary_impl(run_id, db_path=db_path)

    @server.tool()
    def get_tev_run_summary(tev_run_id: str) -> dict:
        """Return the manifest metadata for a TEV run."""
        return get_tev_run_summary_impl(tev_run_id, db_path=db_path)

    return server


def serve(server) -> None:
    """Run an already-built server over stdio only — never binds a network
    interface (FR-3B-12)."""
    server.run(transport=_STDIO_TRANSPORT)


def run(
    db_path: Path = _DEFAULT_DB,
    config_path: Path = _DEFAULT_AI_CONFIG,
) -> None:  # pragma: no cover - process entry point
    """Build and serve the experience_study_data server over stdio."""
    allowlist = load_allowlist(config_path)
    row_cap = _load_row_cap(config_path)
    serve(build_server(Path(db_path), allowlist, row_cap))


if __name__ == "__main__":  # pragma: no cover
    run()
