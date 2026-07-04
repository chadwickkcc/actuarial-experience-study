"""DQ checks for Variable Universal Life (silver_vul_policies).

All check functions have signature:
    check_dq_vul_XX(conn, context) -> tuple[DQCheckResult, list[str]]

where the list contains ALL failing policy_ids (for quarantine insertion).
context keys:
    study_start_date  str  'YYYY-MM-DD'
    study_end_date    str  'YYYY-MM-DD'
    study_run_id      str  UUID
"""

from __future__ import annotations

import json
from typing import Any

import duckdb

from src.utils.types import DQCheckResult

# DQ-VUL-03 is ERROR_HALT (separate_account_total_value < 0)
HALT_CHECK_IDS: frozenset[str] = frozenset({"DQ-VUL-03"})


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
            passed=(len(rows) == 0),
            fail_count=len(rows),
            sample_records=sample,
        ),
        failing_ids,
    )


# ---------------------------------------------------------------------------
# DQ-VUL-01: Sub-account allocations sum to 100% (within 0.1%)
# ---------------------------------------------------------------------------


def check_dq_vul_01(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-VUL-01: sub_account_allocations JSON alloc_pct values sum to 1.0 ± 0.001."""
    etl_run_id = context["study_run_id"]
    # Load all records with non-null sub_account_allocations
    cols, rows = _fetch(
        conn,
        f"SELECT policy_id, sub_account_allocations "
        f"FROM silver_vul_policies "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND sub_account_allocations IS NOT NULL",
    )

    failing: list[tuple] = []
    for policy_id, alloc_str in rows:
        try:
            items = json.loads(alloc_str)
            total = sum(float(item.get("alloc_pct", 0)) for item in items)
            if abs(total - 1.0) > 0.001:
                failing.append((policy_id, alloc_str[:80], round(total, 6)))
        except (json.JSONDecodeError, TypeError, KeyError):
            failing.append((policy_id, str(alloc_str)[:80], "JSON_PARSE_ERROR"))

    result_cols = ["policy_id", "sub_account_allocations_sample", "alloc_sum"]
    return _build_result(
        "DQ-VUL-01",
        "sub_account_allocations alloc_pct values must sum to 1.0 (within 0.1%)",
        "ERROR",
        result_cols,
        failing,
    )


# ---------------------------------------------------------------------------
# DQ-VUL-02: separate_account_total_value == sum of sub-account values
# ---------------------------------------------------------------------------


def check_dq_vul_02(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-VUL-02: separate_account_total_value matches sum of sub-account fund_values."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT policy_id, separate_account_total_value, sub_account_allocations "
        f"FROM silver_vul_policies "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND sub_account_allocations IS NOT NULL",
    )

    failing: list[tuple] = []
    for policy_id, sa_total, alloc_str in rows:
        try:
            items = json.loads(alloc_str)
            fv_sum = sum(float(item.get("fund_value", 0)) for item in items)
            if abs(fv_sum - float(sa_total or 0)) > max(1.0, abs(float(sa_total or 0)) * 0.01):
                failing.append((policy_id, round(float(sa_total or 0), 2), round(fv_sum, 2)))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass  # JSON parse errors caught by DQ-VUL-01

    result_cols = ["policy_id", "separate_account_total_value", "sub_account_sum"]
    return _build_result(
        "DQ-VUL-02",
        "separate_account_total_value must equal sum of sub-account fund_values (within rounding)",
        "ERROR",
        result_cols,
        failing,
    )


# ---------------------------------------------------------------------------
# DQ-VUL-03: separate_account_total_value >= 0 (HALT)
# ---------------------------------------------------------------------------


def check_dq_vul_03(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-VUL-03 (HALT): separate_account_total_value must be non-negative."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT policy_id, separate_account_total_value "
        f"FROM silver_vul_policies "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND separate_account_total_value < 0",
    )
    return _build_result(
        "DQ-VUL-03",
        "separate_account_total_value must be >= 0",
        "ERROR_HALT",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# DQ-VUL-04: All fund IDs in sub-account allocations exist in master fund list
# ---------------------------------------------------------------------------

_MASTER_FUND_IDS = frozenset({
    "EQ_LARGE_CAP", "EQ_INTL", "BALANCED", "BOND_INTMED",
    "MONEY_MARKET", "EQ_SMALL_CAP", "EQ_MID_CAP",
    "BOND_SHORT", "BOND_LONG", "REIT", "COMMODITIES",
})


def check_dq_vul_04(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-VUL-04: All fund IDs in sub_account_allocations must be in master fund list."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT policy_id, sub_account_allocations "
        f"FROM silver_vul_policies "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND sub_account_allocations IS NOT NULL",
    )

    failing: list[tuple] = []
    for policy_id, alloc_str in rows:
        try:
            items = json.loads(alloc_str)
            unknown = [
                item["fund_id"] for item in items
                if item.get("fund_id") not in _MASTER_FUND_IDS
            ]
            if unknown:
                failing.append((policy_id, ", ".join(unknown)))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    result_cols = ["policy_id", "unknown_fund_ids"]
    return _build_result(
        "DQ-VUL-04",
        "All fund IDs in sub_account_allocations must exist in master fund table",
        "WARN",
        result_cols,
        failing,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


ALL_CHECKS = [
    check_dq_vul_01,
    check_dq_vul_02,
    check_dq_vul_03,
    check_dq_vul_04,
]


def run_all_checks(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> list[tuple[DQCheckResult, list[str]]]:
    """Run all VUL DQ checks and return results."""
    return [check(conn, context) for check in ALL_CHECKS]
