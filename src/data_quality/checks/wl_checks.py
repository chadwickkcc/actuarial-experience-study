"""DQ checks for Whole Life (silver_wl_policies).

All check functions have signature:
    check_dq_wl_XX(conn, context) -> tuple[DQCheckResult, list[str]]

where the list contains ALL failing policy_ids (for quarantine insertion).
context keys:
    study_start_date  str  'YYYY-MM-DD'
    study_end_date    str  'YYYY-MM-DD'
    study_run_id      str  UUID
"""

from __future__ import annotations

from typing import Any

import duckdb

from src.utils.types import DQCheckResult

# Checks whose failure halts the pipeline
HALT_CHECK_IDS: frozenset[str] = frozenset({"DQ-WL-01", "DQ-WL-03"})


# ---------------------------------------------------------------------------
# Helpers (mirrors term_checks.py pattern)
# ---------------------------------------------------------------------------


def _fetch(
    conn: duckdb.DuckDBPyConnection, sql: str
) -> tuple[list[str], list[tuple]]:
    """Return (column_names, rows) for a SELECT query."""
    cursor = conn.execute(sql)
    cols = [d[0] for d in cursor.description]
    return cols, cursor.fetchall()


def _to_str(value: Any) -> str:
    """Convert a value to a JSON-safe string."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_result(
    check_id: str,
    description: str,
    severity: str,
    cols: list[str],
    rows: list[tuple],
) -> tuple[DQCheckResult, list[str]]:
    """Assemble DQCheckResult and list of all failing policy_ids."""
    sample = [{c: _to_str(v) for c, v in zip(cols, r)} for r in rows[:10]]
    failing_ids = [str(r[0]) for r in rows]
    return (
        DQCheckResult(
            check_id=check_id,
            description=description,
            severity=severity,
            passed=len(rows) == 0,
            fail_count=len(rows),
            sample_records=sample,
        ),
        failing_ids,
    )


# ---------------------------------------------------------------------------
# Individual check functions (DQ-WL-01 through DQ-WL-04)
# ---------------------------------------------------------------------------


def check_dq_wl_01(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-WL-01 (HALT): guaranteed_cash_value >= 0; for active IF policies <= face_amount."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, status_code, face_amount, guaranteed_cash_value
        FROM silver_wl_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND (
              guaranteed_cash_value < 0
              OR (status_code = 'IF' AND guaranteed_cash_value > face_amount)
          )
        """,
    )
    return _build_result(
        "DQ-WL-01",
        "guaranteed_cash_value >= 0; for IF policies <= face_amount",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_wl_02(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-WL-02 (WARN): policy_loan_balance <= guaranteed_cash_value."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, guaranteed_cash_value, policy_loan_balance
        FROM silver_wl_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND policy_loan_balance > guaranteed_cash_value
        """,
    )
    return _build_result(
        "DQ-WL-02",
        "policy_loan_balance <= guaranteed_cash_value",
        "WARN",
        cols,
        rows,
    )


def check_dq_wl_03(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-WL-03 (HALT): non_forfeiture_status RPU/ETT implies termination_cause_code != LAPSE."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, non_forfeiture_status, termination_cause_code
        FROM silver_wl_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND non_forfeiture_status IN ('RPU', 'ETT')
          AND termination_cause_code = 'LAPSE'
        """,
    )
    return _build_result(
        "DQ-WL-03",
        "non_forfeiture_status RPU/ETT implies termination_cause_code != LAPSE",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_wl_04(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-WL-04 (WARN): For par WL policies, dividend_on_deposit_bal >= 0."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, participating_flag, dividend_on_deposit_bal
        FROM silver_wl_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND participating_flag = TRUE
          AND dividend_on_deposit_bal < 0
        """,
    )
    return _build_result(
        "DQ-WL-04",
        "For par WL: dividend_on_deposit_balance >= 0",
        "WARN",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# Check suite runner
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    check_dq_wl_01,
    check_dq_wl_02,
    check_dq_wl_03,
    check_dq_wl_04,
]


def run_all_checks(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> list[tuple[DQCheckResult, list[str]]]:
    """Run all DQ-WL checks and return results in order.

    Args:
        conn:    Active DuckDB connection.
        context: Dict with study_start_date, study_end_date, study_run_id.

    Returns:
        List of (DQCheckResult, failing_policy_ids) tuples.
    """
    return [check(conn, context) for check in _ALL_CHECKS]
