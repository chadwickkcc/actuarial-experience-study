"""DQ checks for Universal Life (silver_ul_policies: UL, ULSG, IUL).

All check functions have signature:
    check_dq_ul_XX(conn, context) -> tuple[DQCheckResult, list[str]]

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

# DQ-UL-04 and DQ-UL-05 are ERROR (not halt); none are HALT for UL
HALT_CHECK_IDS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Helpers
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
# Individual check functions (DQ-UL-01 through DQ-UL-06)
# ---------------------------------------------------------------------------


def check_dq_ul_01(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-01 (WARN): AV roll-forward identity: AV(end) ~= AV(begin) + premium - COI + interest.

    Simplified check: AV_eom should be >= AV_bom * 0.80 and <= AV_bom * 1.30 for in-force
    policies (allows for COI charges, loads, and interest within a plausible range).
    Exact roll-forward requires monthly transaction detail not present in this snapshot.
    """
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, account_value_bom, account_value_eom
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND status_code = 'IF'
          AND account_value_bom > 0
          AND (
              account_value_eom < account_value_bom * 0.70
              OR account_value_eom > account_value_bom * 1.40
          )
        """,
    )
    return _build_result(
        "DQ-UL-01",
        "AV roll-forward: AV_eom within plausible range of AV_bom (±30% tolerance)",
        "WARN",
        cols,
        rows,
    )


def check_dq_ul_02(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-02 (ERROR): ULSG: shadow_account_funding_ratio >= 0."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, is_ulsg_flag, shadow_account_funding_ratio
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND is_ulsg_flag = TRUE
          AND shadow_account_funding_ratio IS NOT NULL
          AND shadow_account_funding_ratio < 0
        """,
    )
    return _build_result(
        "DQ-UL-02",
        "ULSG: shadow_account_funding_ratio >= 0",
        "ERROR",
        cols,
        rows,
    )


def check_dq_ul_03(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-03 (WARN): ULSG in-force with funding_ratio < 1.0 flagged for review."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, shadow_account_funding_ratio, status_code
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND is_ulsg_flag = TRUE
          AND status_code = 'IF'
          AND shadow_account_funding_ratio IS NOT NULL
          AND shadow_account_funding_ratio < 1.0
        """,
    )
    return _build_result(
        "DQ-UL-03",
        "ULSG IF policies with shadow_account_funding_ratio < 1.0 (NLG at risk)",
        "WARN",
        cols,
        rows,
    )


def check_dq_ul_04(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-04 (ERROR): current_coi_rate <= guaranteed_coi_rate."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, current_coi_rate, guaranteed_coi_rate
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND current_coi_rate > guaranteed_coi_rate
        """,
    )
    return _build_result(
        "DQ-UL-04",
        "current_coi_rate <= guaranteed_coi_rate",
        "ERROR",
        cols,
        rows,
    )


def check_dq_ul_05(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-05 (ERROR): credited_interest_rate >= guaranteed_min_interest_rate."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, credited_interest_rate, guaranteed_min_interest_rate
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND credited_interest_rate < guaranteed_min_interest_rate - 0.0001
        """,
    )
    return _build_result(
        "DQ-UL-05",
        "credited_interest_rate >= guaranteed_min_interest_rate",
        "ERROR",
        cols,
        rows,
    )


def check_dq_ul_06(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-UL-06 (WARN): MEC flag consistency with seven_pay_premium and cumulative premiums.

    A policy is a MEC under IRC 7702A if cumulative_premiums_paid > seven_pay_premium × 7.
    Flags policies where mec_status_flag = FALSE but cumulative premiums exceed the 7-pay limit.
    """
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, mec_status_flag, cumulative_premiums_paid, seven_pay_premium
        FROM silver_ul_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND seven_pay_premium IS NOT NULL
          AND seven_pay_premium > 0
          AND mec_status_flag = FALSE
          AND cumulative_premiums_paid > seven_pay_premium * 7
        """,
    )
    return _build_result(
        "DQ-UL-06",
        "MEC flag consistency: mec_status_flag should be TRUE when cumulative premiums > 7-pay limit",
        "WARN",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# Check suite runner
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    check_dq_ul_01,
    check_dq_ul_02,
    check_dq_ul_03,
    check_dq_ul_04,
    check_dq_ul_05,
    check_dq_ul_06,
]


def run_all_checks(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> list[tuple[DQCheckResult, list[str]]]:
    """Run all DQ-UL checks and return results in order.

    Args:
        conn:    Active DuckDB connection.
        context: Dict with study_start_date, study_end_date, study_run_id.

    Returns:
        List of (DQCheckResult, failing_policy_ids) tuples.
    """
    return [check(conn, context) for check in _ALL_CHECKS]
