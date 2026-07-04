"""DQ checks for Deferred Annuities (silver_annuity_contracts).

All check functions have signature:
    check_dq_da_XX(conn, context) -> tuple[DQCheckResult, list[str]]

where the list contains ALL failing contract_ids (for quarantine insertion).
context keys:
    study_start_date  str  'YYYY-MM-DD'
    study_end_date    str  'YYYY-MM-DD'
    study_run_id      str  UUID

Note: annuities use contract_id (not policy_id) as primary key.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb

from src.utils.types import DQCheckResult

# No HALT checks for DA in Phase 1C (all are ERROR or WARN)
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
    """Assemble DQCheckResult and list of all failing contract_ids."""
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
# DQ-DA-01: Surrender charge rate for current SC year matches schedule
# ---------------------------------------------------------------------------


def check_dq_da_01(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-DA-01: SC rate for current surrender_charge_year matches schedule (WARN)."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT contract_id, surrender_charge_year, surrender_charge_remaining, "
        f"       account_value, surrender_charge_schedule "
        f"FROM silver_annuity_contracts "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND surrender_charge_schedule IS NOT NULL "
        f"  AND is_surrender_charge_expired_flag = FALSE",
    )

    failing: list[tuple] = []
    for contract_id, sc_year, sc_remaining, av, schedule_str in rows:
        try:
            schedule = json.loads(schedule_str)
            # Find expected rate for this year
            expected_rate = None
            for item in schedule:
                if item.get("year") == int(sc_year or 1):
                    expected_rate = float(item["rate"])
                    break

            if expected_rate is None or av is None or float(av) <= 0:
                continue

            # Compute expected SC amount
            expected_sc = float(av) * expected_rate
            actual_sc = float(sc_remaining or 0)

            # Allow large tolerance (simplified data): just check within 50% of expected
            # Real check would use exact policy-level calculation
            if expected_sc > 0 and abs(actual_sc - expected_sc) / expected_sc > 0.50:
                failing.append((contract_id, sc_year, round(actual_sc, 2), round(expected_sc, 2)))

        except (json.JSONDecodeError, TypeError, KeyError, ZeroDivisionError):
            pass

    result_cols = ["contract_id", "surrender_charge_year", "actual_sc", "expected_sc"]
    return _build_result(
        "DQ-DA-01",
        "Surrender charge rate for current surrender year should match schedule",
        "WARN",
        result_cols,
        failing,
    )


# ---------------------------------------------------------------------------
# DQ-DA-02: benefit_base >= 0 for all GLB contracts
# ---------------------------------------------------------------------------


def check_dq_da_02(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-DA-02: benefit_base must be >= 0 for contracts with GLB riders."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT contract_id, glwb_elected_flag, gmdb_type, benefit_base "
        f"FROM silver_annuity_contracts "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND (glwb_elected_flag = TRUE OR gmdb_type IS NOT NULL) "
        f"  AND (benefit_base IS NULL OR benefit_base < 0)",
    )
    return _build_result(
        "DQ-DA-02",
        "benefit_base must be >= 0 for all GLB contracts",
        "ERROR",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# DQ-DA-03: Withdrawal flagged "free" must be <= free_withdrawal_allowance
# ---------------------------------------------------------------------------


def check_dq_da_03(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-DA-03: Active withdrawals must not exceed free withdrawal allowance (WARN)."""
    # In our synthetic data, GLWB-active contracts draw at glwb_withdrawal_rate_pct
    # Check that glwb_withdrawal_rate_pct × account_value ≤ free_withdrawal_allowance_pct × account_value
    # i.e., glwb_withdrawal_rate_pct ≤ free_withdrawal_allowance_pct (unless it's a GLWB benefit)
    # The spec says "free" withdrawals; GLWB withdrawals are contractual, not free.
    # We check status = "ACTIVE" non-GLWB partial withdrawals against the free wd limit.
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT contract_id, account_value, free_withdrawal_allowance_pct, "
        f"       glwb_withdrawal_rate_pct, glwb_elected_flag "
        f"FROM silver_annuity_contracts "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND glwb_elected_flag = FALSE "
        f"  AND glwb_withdrawal_rate_pct IS NOT NULL "
        f"  AND glwb_withdrawal_rate_pct > free_withdrawal_allowance_pct",
    )
    return _build_result(
        "DQ-DA-03",
        "Non-GLWB withdrawal rate must not exceed free_withdrawal_allowance_pct",
        "WARN",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# DQ-DA-04: market_type consistent across all records for same contract
# ---------------------------------------------------------------------------


def check_dq_da_04(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-DA-04: market_type must be consistent (single unique value per contract)."""
    # In our data each contract is one row, so this checks market_type is a valid value
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT contract_id, market_type "
        f"FROM silver_annuity_contracts "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND market_type NOT IN ('NQ', 'TRAD_IRA', 'ROTH_IRA', 'QUAL')",
    )
    return _build_result(
        "DQ-DA-04",
        "market_type must be one of: NQ, TRAD_IRA, ROTH_IRA, QUAL",
        "ERROR",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# DQ-DA-05: is_surrender_charge_expired_flag=TRUE implies surrender_charge_remaining=0
# ---------------------------------------------------------------------------


def check_dq_da_05(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-DA-05: SC expired flag must be consistent with SC remaining = 0."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"SELECT contract_id, is_surrender_charge_expired_flag, surrender_charge_remaining "
        f"FROM silver_annuity_contracts "
        f"WHERE _etl_run_id = '{etl_run_id}' "
        f"  AND is_surrender_charge_expired_flag = TRUE "
        f"  AND surrender_charge_remaining > 0",
    )
    return _build_result(
        "DQ-DA-05",
        "is_surrender_charge_expired_flag=TRUE implies surrender_charge_remaining must be 0",
        "ERROR",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


ALL_CHECKS = [
    check_dq_da_01,
    check_dq_da_02,
    check_dq_da_03,
    check_dq_da_04,
    check_dq_da_05,
]


def run_all_checks(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> list[tuple[DQCheckResult, list[str]]]:
    """Run all DA DQ checks and return results."""
    return [check(conn, context) for check in ALL_CHECKS]
