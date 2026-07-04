"""Hardened SQL boundary for the AI layer (Tech Spec v2.0.1 §E.2).

The single gateway through which all dynamically-constructed SQL in the AI layer
passes (FR-3A-01/02). Lives in ``src/utils/`` so non-AI code may also adopt it.
The MCP server (§E.6) and chatbot (§E.7) reach the database only through
:func:`execute_safe_select`.

Five gates (FR-3B-31):
  1. parse      — exactly one parseable statement, else REJECT_PARSE.
  2. select     — root is a SELECT; any DDL/DML/PRAGMA/ATTACH/SET/transaction
                  control rejects → REJECT_NOT_SELECT.
  3. allowlist  — every table is an allowlist key; every column resolves to an
                  allowed column for its table; ``SELECT *`` is *expanded* to the
                  allowlisted subset (never physical columns) → REJECT_ALLOWLIST.
  4. row cap    — LIMIT <= row_cap, or fully aggregated → REJECT_ROWCAP.
  5. execute    — only on PASS, run on a read-only DuckDB connection.

Rejected user SQL is *returned* as :class:`SQLValidationResult`; it is never
raised. :class:`SQLBoundaryError` is reserved for misuse of the boundary API
itself (e.g. being forced to open a writable connection).
"""
from __future__ import annotations  # Python 3.9 union/builtin-generic compat

from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import sqlglot
import yaml
from sqlglot import exp

from src.utils.types import SQLGateOutcome, SQLValidationResult


# Expression types that must never appear anywhere in a candidate statement.
# Any hit fails gate 2 (REJECT_NOT_SELECT). exp.Command captures dialect
# fall-throughs such as SET / ATTACH / CALL / COPY / transaction control.
_FORBIDDEN_NODES: tuple[type, ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Pragma,
    exp.Set,
    exp.SetItem,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.Attach,
)


class SQLBoundaryError(Exception):
    """Raised only for misuse of the boundary API itself, never for rejected
    user SQL — rejections are returned as SQLValidationResult, not raised."""


def load_allowlist(allowlist_path: Path) -> dict[str, set[str]]:
    """Load the Gold-only table->columns allowlist from ``ai_config.yaml``.

    The allowlist lives under the ``chatbot.allowlist`` block (Tech Spec §F.1).
    Returns ``{table_name: {permitted column names}}``. The same allowlist object
    is shared by the chatbot and the MCP server (FR-3B-32).

    Args:
        allowlist_path: Path to ``config/ai_config.yaml``.

    Returns:
        Mapping of table name to the set of permitted column names.

    Raises:
        SQLBoundaryError: if the file is missing or the allowlist block is absent
            or malformed (misuse of the boundary configuration).
    """
    allowlist_path = Path(allowlist_path)
    if not allowlist_path.exists():
        raise SQLBoundaryError(f"AI config not found: {allowlist_path}")

    with allowlist_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    try:
        raw = cfg["chatbot"]["allowlist"]
    except (KeyError, TypeError) as err:
        raise SQLBoundaryError(
            "ai_config.yaml is missing the chatbot.allowlist block"
        ) from err

    if not isinstance(raw, dict) or not raw:
        raise SQLBoundaryError("chatbot.allowlist must be a non-empty mapping")

    allowlist: dict[str, set[str]] = {}
    for table, columns in raw.items():
        if not isinstance(columns, (list, tuple)):
            raise SQLBoundaryError(
                f"allowlist columns for {table!r} must be a list"
            )
        allowlist[str(table)] = {str(c) for c in columns}
    return allowlist


def _reject(
    sql: str, outcome: SQLGateOutcome, gate: str, detail: str
) -> SQLValidationResult:
    """Build a rejection result (helper for the gate functions)."""
    return SQLValidationResult(
        outcome=outcome, sql=sql, gate_failed=gate, detail=detail
    )


def _qualifier_map(
    statement: exp.Expression, cte_names: set[str]
) -> dict[str, str]:
    """Map every physical table alias/name used in the query to its real name.

    e.g. ``FROM gold_ae_results a`` yields ``{"a": "gold_ae_results",
    "gold_ae_results": "gold_ae_results"}``. CTE names are excluded.
    """
    qmap: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if table.name in cte_names:
            continue
        real = table.name
        qmap[real] = real
        alias = table.alias
        if alias:
            qmap[alias] = real
    return qmap


def _allowed_columns_for_qualifier(
    qualifier: str, qmap: dict[str, str], allowlist: dict[str, set[str]]
) -> Optional[set[str]]:
    """Allowed column set for a table alias/name, or None if unresolvable."""
    real = qmap.get(qualifier)
    if real is None or real not in allowlist:
        return None
    return allowlist[real]


def _gate3_allowlist(
    statement: exp.Expression,
    allowlist: dict[str, set[str]],
    original_sql: str,
) -> Optional[SQLValidationResult]:
    """Gate 3: enforce the table/column allowlist and expand ``*`` in place.

    CTE names are transparent: a ``WITH x AS (...)`` block is validated by the
    allowlist checks on its own body, so references to ``x`` are not subject to
    the physical-table allowlist. Returns a rejection result on failure, or
    ``None`` on success (the statement is mutated: stars expanded).
    """
    cte_names = {c.alias for c in statement.find_all(exp.CTE) if c.alias}
    qmap = _qualifier_map(statement, cte_names)

    # Every referenced *physical* table must be an allowlist key.
    for table in statement.find_all(exp.Table):
        if table.name in cte_names:
            continue
        if table.name not in allowlist:
            return _reject(
                original_sql,
                SQLGateOutcome.REJECT_ALLOWLIST,
                "gate_3_allowlist",
                f"table not on allowlist: {table.name}",
            )

    # Union of allowlisted columns over every physical table referenced anywhere
    # (used to resolve unqualified columns).
    real_tables = {
        t.name for t in statement.find_all(exp.Table) if t.name not in cte_names
    }
    permitted_union: set[str] = set().union(
        *(allowlist[t] for t in real_tables)
    ) if real_tables else set()

    # Expand stars per SELECT scope (a star binds to its own FROM/JOINs only).
    for select in statement.find_all(exp.Select):
        scope_tables = _scope_real_tables(select, cte_names)
        new_projections: list[exp.Expression] = []
        for proj in select.expressions:
            star_table = _star_qualifier(proj)
            if star_table is _NOT_A_STAR:
                new_projections.append(proj)
            elif star_table is None:
                # Bare ``*``. When this scope's FROM is only CTEs/derived tables,
                # leave it unexpanded — the underlying bodies are allowlisted, so
                # no off-allowlist physical column can surface.
                if not scope_tables:
                    new_projections.append(proj)
                else:
                    new_projections.extend(
                        _expand_bare_star(scope_tables, allowlist)
                    )
            elif star_table in cte_names:
                new_projections.append(proj)  # CTE.* — body already validated
            else:
                cols = _allowed_columns_for_qualifier(star_table, qmap, allowlist)
                if cols is None:
                    return _reject(
                        original_sql,
                        SQLGateOutcome.REJECT_ALLOWLIST,
                        "gate_3_allowlist",
                        f"`*` over off-allowlist table: {star_table}",
                    )
                new_projections.extend(
                    exp.column(c, table=star_table) for c in sorted(cols)
                )
        select.set("expressions", new_projections)

    # Every remaining column must resolve to an allowed column.
    for col in statement.find_all(exp.Column):
        name = col.name
        qualifier = col.table  # '' when unqualified
        if qualifier in cte_names:
            continue  # CTE output column; the CTE body was validated
        if qualifier:
            cols = _allowed_columns_for_qualifier(qualifier, qmap, allowlist)
            if cols is None or name not in cols:
                return _reject(
                    original_sql,
                    SQLGateOutcome.REJECT_ALLOWLIST,
                    "gate_3_allowlist",
                    f"column not on allowlist: {qualifier}.{name}",
                )
        else:
            # Unqualified — allowed if permitted by ANY physical table. When CTEs
            # are present a name may be a CTE-exposed alias; the body is already
            # validated, so accept it rather than over-reject.
            if name not in permitted_union and not cte_names:
                return _reject(
                    original_sql,
                    SQLGateOutcome.REJECT_ALLOWLIST,
                    "gate_3_allowlist",
                    f"column not on allowlist: {name}",
                )
    return None


def _scope_real_tables(select: exp.Select, cte_names: set[str]) -> list[str]:
    """Physical tables in this SELECT's own FROM/JOIN scope (not nested ones)."""
    res: list[str] = []
    for table in select.find_all(exp.Table):
        if table.name in cte_names:
            continue
        ancestor = table.parent
        while ancestor is not None and not isinstance(ancestor, exp.Select):
            ancestor = ancestor.parent
        if ancestor is select:
            res.append(table.name)
    return sorted(set(res))


# Sentinel distinguishing "not a star projection" from "bare star" (None).
_NOT_A_STAR = object()


def _star_qualifier(proj: exp.Expression):
    """Classify a projection expression.

    Returns ``_NOT_A_STAR`` if it is not a star, ``None`` for a bare ``*``, or
    the qualifier string for ``t.*``.
    """
    if isinstance(proj, exp.Star):
        return None
    if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
        return proj.table or None
    return _NOT_A_STAR


def _expand_bare_star(
    scope_tables: list[str],
    allowlist: dict[str, set[str]],
) -> list[exp.Expression]:
    """Expand a bare ``*`` to allowlisted columns across the scope's tables.

    All ``scope_tables`` are physical and already confirmed allowlisted by the
    caller. Columns are qualified only when more than one table is in scope.
    """
    columns: list[exp.Expression] = []
    qualify = len(scope_tables) > 1
    for table in scope_tables:
        for c in sorted(allowlist[table]):
            columns.append(exp.column(c, table=table) if qualify else exp.column(c))
    return columns


def _limit_value(statement: exp.Expression) -> Optional[int]:
    """Integer LIMIT on the outermost statement, or None if absent/non-literal."""
    limit = statement.args.get("limit")
    if limit is None:
        return None
    expr = limit.expression if isinstance(limit, exp.Limit) else limit
    if isinstance(expr, exp.Literal) and expr.is_int:
        return int(expr.name)
    return None


def _is_fully_aggregated(statement: exp.Expression) -> bool:
    """True if the outermost SELECT is a single-row aggregate.

    "Only aggregate functions in projection, no ungrouped non-aggregate columns"
    (Tech Spec §E.2 gate 4): no GROUP BY, at least one aggregate, and no bare
    column sitting outside an aggregate in the projection.
    """
    if not isinstance(statement, exp.Select):
        return False
    if statement.args.get("group") is not None:
        return False

    projections = statement.expressions
    if not projections:
        return False

    has_aggregate = False
    for proj in projections:
        if list(proj.find_all(exp.AggFunc)):
            has_aggregate = True
            # A column outside the aggregate (e.g. SUM(x) + y) breaks the
            # single-row guarantee.
            for column in proj.find_all(exp.Column):
                if not _within_aggregate(column, proj):
                    return False
        else:
            # A bare non-aggregate projection (column / literal expression that
            # is not constant) means the result is row-per-input.
            if list(proj.find_all(exp.Column)):
                return False
    return has_aggregate


def _within_aggregate(column: exp.Column, root: exp.Expression) -> bool:
    """True if ``column`` is nested inside an aggregate function within ``root``."""
    node = column.parent
    while node is not None and node is not root.parent:
        if isinstance(node, exp.AggFunc):
            return True
        node = node.parent
    return False


def validate_select(
    sql: str,
    allowlist: dict[str, set[str]],
    row_cap: int = 500,
) -> SQLValidationResult:
    """Run gates 1-4 of FR-3B-31 WITHOUT executing. Pure — no DB access.

    Args:
        sql:       Candidate SQL text.
        allowlist: ``{table: {columns}}`` from :func:`load_allowlist`.
        row_cap:   Maximum rows a bare (non-aggregated) scan may return.

    Returns:
        ``SQLValidationResult(outcome=PASS, sql=normalized_sql)`` on success,
        where ``normalized_sql`` is the sqlglot-roundtripped, star-expanded form.
        On rejection, ``outcome`` is the relevant ``REJECT_*`` value and ``sql``
        is the original input. Never raises on bad user SQL.
    """
    # ---- Gate 1: parse, exactly one statement ----
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as err:
        return _reject(sql, SQLGateOutcome.REJECT_PARSE, "gate_1_parse", str(err))

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return _reject(
            sql,
            SQLGateOutcome.REJECT_PARSE,
            "gate_1_parse",
            f"expected exactly one statement, found {len(statements)}",
        )
    statement = statements[0]

    # ---- Gate 2: SELECT only ----
    if not isinstance(statement, (exp.Select, exp.Union)):
        return _reject(
            sql,
            SQLGateOutcome.REJECT_NOT_SELECT,
            "gate_2_select",
            f"root expression is not a SELECT: {type(statement).__name__}",
        )
    if any(isinstance(node, _FORBIDDEN_NODES) for node in statement.walk()):
        return _reject(
            sql,
            SQLGateOutcome.REJECT_NOT_SELECT,
            "gate_2_select",
            "statement contains a forbidden (non-SELECT) construct",
        )

    # ---- Gate 3: allowlist + star expansion (mutates statement) ----
    rejection = _gate3_allowlist(statement, allowlist, sql)
    if rejection is not None:
        return rejection

    # ---- Gate 4: row cap ----
    limit = _limit_value(statement)
    capped = limit is not None and limit <= row_cap
    if not capped and not _is_fully_aggregated(statement):
        return _reject(
            sql,
            SQLGateOutcome.REJECT_ROWCAP,
            "gate_4_rowcap",
            f"scan without LIMIT <= {row_cap} or full aggregation",
        )

    return SQLValidationResult(
        outcome=SQLGateOutcome.PASS,
        sql=statement.sql(dialect="duckdb"),
    )


def execute_safe_select(
    db_path: Path,
    sql: str,
    allowlist: dict[str, set[str]],
    row_cap: int = 500,
) -> tuple[SQLValidationResult, Optional[pd.DataFrame]]:
    """Validate (gates 1-4) then, only on PASS, execute read-only (gate 5).

    Identifiers are validated against the allowlist, not interpolated; the
    validated, star-expanded statement is executed as-is on a connection opened
    with ``read_only=True``.

    Args:
        db_path:   Path to the DuckDB file.
        sql:       Candidate SQL text.
        allowlist: ``{table: {columns}}`` from :func:`load_allowlist`.
        row_cap:   Maximum rows a bare scan may return.

    Returns:
        ``(validation_result, dataframe_or_None)``. On a non-PASS outcome the
        dataframe is ``None`` and no execution occurs.

    Raises:
        SQLBoundaryError: if a read-only connection cannot be opened (which would
            require a writable/creating connection — forbidden here).
    """
    result = validate_select(sql, allowlist, row_cap=row_cap)
    if result.outcome is not SQLGateOutcome.PASS:
        return result, None

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as err:  # duckdb raises on read-only open of a missing DB
        raise SQLBoundaryError(
            f"refusing to open a writable/new connection to {db_path}: {err}"
        ) from err

    try:
        dataframe = conn.execute(result.sql).fetchdf()
    finally:
        conn.close()
    return result, dataframe
