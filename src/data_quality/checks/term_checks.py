"""DQ checks for Term Life (silver_term_policies).

All check functions have signature:
    check_dq_tl_XX(conn, context) -> tuple[DQCheckResult, list[str]]

where the list contains ALL failing policy_ids (for quarantine insertion).
context keys:
    study_start_date  str  'YYYY-MM-DD'
    study_end_date    str  'YYYY-MM-DD'
    study_run_id      str  UUID
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import duckdb

from src.utils.types import DQCheckResult

# Checks whose failure halts the pipeline
HALT_CHECK_IDS: frozenset[str] = frozenset(
    {"DQ-TL-01", "DQ-TL-03", "DQ-TL-05", "DQ-TL-06", "DQ-TL-10", "DQ-TL-14"}
)

VALID_RISK_CLASSES: list[str] = [
    "SUPER_PREF",
    "PREF_NS",
    "STD_NS",
    "PREF_SM",
    "STD_SM",
]

VALID_CI_ILLNESS_CODES: list[str] = [
    "CI-001",
    "CI-002",
    "CI-003",
    "CI-004",
    "CI-005",
    "CI-006",
    "CI-007",
    "CI-008",
    "CI-009",
    "CI-010",
]


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
    sample = [
        {c: _to_str(v) for c, v in zip(cols, r)} for r in rows[:10]
    ]
    # First column is always policy_id
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
# Individual check functions (DQ-TL-01 through DQ-TL-16)
# ---------------------------------------------------------------------------


def check_dq_tl_01(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-01 (HALT): issue_date <= termination_date <= study_end."""
    study_end = context["study_end_date"]
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, issue_date, termination_date
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND termination_date IS NOT NULL
          AND (
              issue_date > termination_date
              OR CAST(termination_date AS DATE) > DATE '{study_end}'
          )
        """,
    )
    return _build_result(
        "DQ-TL-01",
        "issue_date ≤ termination_date ≤ study end",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_tl_02(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-02 (WARN): date_of_birth + issue_age_anb = issue_date year ±1."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, date_of_birth, issue_date, issue_age_anb
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND ABS(YEAR(issue_date) - YEAR(date_of_birth) - issue_age_anb) > 1
        """,
    )
    return _build_result(
        "DQ-TL-02",
        "date_of_birth + issue_age_anb ≈ issue_date year ±1",
        "WARN",
        cols,
        rows,
    )


def check_dq_tl_03(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-03 (HALT): face_amount > 0 for all records."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, face_amount
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND (face_amount IS NULL OR face_amount <= 0)
        """,
    )
    return _build_result(
        "DQ-TL-03",
        "face_amount > 0 for all records",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_tl_04(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-04 (ERROR): issue_age_anb between 18 and 85."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, issue_age_anb
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND (issue_age_anb < 18 OR issue_age_anb > 85)
        """,
    )
    return _build_result(
        "DQ-TL-04",
        "issue_age_anb between 18 and 85",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_05(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-05 (HALT): termination_cause_code is null iff status_code = 'IF'."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, status_code, termination_cause_code
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND (
              (status_code = 'IF' AND termination_cause_code IS NOT NULL)
              OR (status_code != 'IF' AND termination_cause_code IS NULL)
          )
        """,
    )
    return _build_result(
        "DQ-TL-05",
        "termination_cause_code null iff status_code = IF",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_tl_06(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-06 (HALT): Death records — termination_date >= issue_date."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, issue_date, termination_date
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND termination_cause_code = 'DEATH_BENEFIT_CLAIM'
          AND termination_date < issue_date
        """,
    )
    return _build_result(
        "DQ-TL-06",
        "Death: termination_date ≥ issue_date",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_tl_07(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-07 (ERROR): Death status consistency — DEATH status ↔ DEATH_BENEFIT_CLAIM."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, status_code, termination_cause_code
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND (
              (status_code = 'DEATH' AND termination_cause_code != 'DEATH_BENEFIT_CLAIM')
              OR (termination_cause_code = 'DEATH_BENEFIT_CLAIM' AND status_code != 'DEATH')
          )
        """,
    )
    return _build_result(
        "DQ-TL-07",
        "Death: status_code = DEATH ↔ DEATH_BENEFIT_CLAIM",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_08(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-08 (WARN): premium_jump_ratio >= 1.0 for PLT records."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, premium_jump_ratio
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND premium_jump_ratio IS NOT NULL
          AND premium_jump_ratio < 1.0
        """,
    )
    return _build_result(
        "DQ-TL-08",
        "premium_jump_ratio ≥ 1.0 for level-term products",
        "WARN",
        cols,
        rows,
    )


def check_dq_tl_09(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-09 (ERROR): PLT flag set for policies with duration > level_period_years."""
    study_end = context["study_end_date"]
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, issue_date, level_period_years, plt_structure_code,
               ROUND(
                   DATEDIFF('day', issue_date,
                       CASE WHEN status_code = 'IF' THEN DATE '{study_end}'
                            ELSE termination_date END
                   ) / 365.25, 2
               ) AS max_duration_yrs
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND DATEDIFF('day', issue_date,
                  CASE WHEN status_code = 'IF' THEN DATE '{study_end}'
                       ELSE termination_date END
              ) / 365.25 > level_period_years
          AND plt_structure_code IS NULL
        """,
    )
    return _build_result(
        "DQ-TL-09",
        "PLT structure set for duration > level_period_years",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_10(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-10 (HALT): policy_id is unique within the current ETL run."""
    etl_run_id = context.get("study_run_id", "")
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, COUNT(*) AS duplicate_count
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
        GROUP BY policy_id
        HAVING COUNT(*) > 1
        """,
    )
    return _build_result(
        "DQ-TL-10",
        "policy_id is unique",
        "ERROR_HALT",
        cols,
        rows,
    )


def check_dq_tl_11(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-11 (ERROR): gender in {M, F, U}."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, gender
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND gender NOT IN ('M', 'F', 'U')
        """,
    )
    return _build_result(
        "DQ-TL-11",
        "gender ∈ {M, F, U}",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_12(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-12 (ERROR): smoker_status in {NS, SM, U}."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, smoker_status
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND smoker_status NOT IN ('NS', 'SM', 'U')
        """,
    )
    return _build_result(
        "DQ-TL-12",
        "smoker_status ∈ {NS, SM, U}",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_13(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-13 (ERROR): risk_class in configured valid class list."""
    valid_sql = ", ".join(f"'{c}'" for c in VALID_RISK_CLASSES)
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, risk_class
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND risk_class NOT IN ({valid_sql})
        """,
    )
    return _build_result(
        "DQ-TL-13",
        f"risk_class ∈ {{{', '.join(VALID_RISK_CLASSES)}}}",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_14(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-14 (HALT): In-force reconciliation per calendar year.

    Verifies BEG_IF + NEW_ISSUES - DECREMENTS = END_IF on count and face amount
    for each year in the study window.  Writes results to gold_inforce_reconciliation.
    Tolerance: count must be exact (diff = 0); amount diff ≤ 0.01% of end_if_amount.
    """
    study_start = context["study_start_date"]
    study_end = context["study_end_date"]
    study_run_id = context["study_run_id"]

    start_year = int(study_start[:4])
    end_year = int(study_end[:4])

    failing_years: list[int] = []
    sample_records: list[dict] = []
    now_ts = datetime.utcnow()

    for year in range(start_year, end_year + 1):
        yr_start = f"{year}-01-01"
        yr_end = f"{year}-12-31"

        row = conn.execute(
            f"""
            SELECT
                -- Counts
                COUNT(CASE WHEN issue_date < DATE '{yr_start}'
                           AND (termination_date IS NULL
                                OR termination_date >= DATE '{yr_start}')
                           THEN 1 END)                                         AS beg_if_count,
                COUNT(CASE WHEN issue_date >= DATE '{yr_start}'
                           AND issue_date <= DATE '{yr_end}'
                           THEN 1 END)                                         AS new_issues_count,
                COUNT(CASE WHEN termination_cause_code = 'DEATH_BENEFIT_CLAIM'
                           AND termination_date >= DATE '{yr_start}'
                           AND termination_date <= DATE '{yr_end}'
                           THEN 1 END)                                         AS deaths_count,
                COUNT(CASE WHEN termination_cause_code = 'LAPSE'
                           AND termination_date >= DATE '{yr_start}'
                           AND termination_date <= DATE '{yr_end}'
                           THEN 1 END)                                         AS lapses_count,
                COUNT(CASE WHEN termination_cause_code NOT IN
                                ('DEATH_BENEFIT_CLAIM', 'LAPSE')
                           AND termination_date >= DATE '{yr_start}'
                           AND termination_date <= DATE '{yr_end}'
                           THEN 1 END)                                         AS other_decrements,
                COUNT(CASE WHEN issue_date <= DATE '{yr_end}'
                           AND (termination_date IS NULL
                                OR termination_date > DATE '{yr_end}')
                           THEN 1 END)                                         AS end_if_count,
                -- Amounts
                SUM(CASE WHEN issue_date < DATE '{yr_start}'
                          AND (termination_date IS NULL
                               OR termination_date >= DATE '{yr_start}')
                          THEN face_amount ELSE 0 END)                         AS beg_if_amount,
                SUM(CASE WHEN issue_date >= DATE '{yr_start}'
                          AND issue_date <= DATE '{yr_end}'
                          THEN face_amount ELSE 0 END)                         AS new_issues_amount,
                SUM(CASE WHEN termination_cause_code = 'DEATH_BENEFIT_CLAIM'
                          AND termination_date >= DATE '{yr_start}'
                          AND termination_date <= DATE '{yr_end}'
                          THEN face_amount ELSE 0 END)                         AS deaths_amount,
                SUM(CASE WHEN termination_cause_code = 'LAPSE'
                          AND termination_date >= DATE '{yr_start}'
                          AND termination_date <= DATE '{yr_end}'
                          THEN face_amount ELSE 0 END)                         AS lapses_amount,
                SUM(CASE WHEN termination_cause_code NOT IN
                               ('DEATH_BENEFIT_CLAIM', 'LAPSE')
                          AND termination_date >= DATE '{yr_start}'
                          AND termination_date <= DATE '{yr_end}'
                          THEN face_amount ELSE 0 END)                         AS other_amount,
                SUM(CASE WHEN issue_date <= DATE '{yr_end}'
                          AND (termination_date IS NULL
                               OR termination_date > DATE '{yr_end}')
                          THEN face_amount ELSE 0 END)                         AS end_if_amount
            FROM silver_term_policies
            WHERE _etl_run_id = '{study_run_id}'
            """
        ).fetchone()

        (
            beg_if, new_issues, deaths, lapses, other, end_if,
            beg_if_amt, new_issues_amt, deaths_amt, lapses_amt, other_amt, end_if_amt,
        ) = row

        # SUM(...) returns NULL over an empty result set (a run/year with no matching rows);
        # coalesce the amount aggregates to 0 so the reconciliation arithmetic is NULL-safe.
        beg_if_amt     = beg_if_amt or 0.0
        new_issues_amt = new_issues_amt or 0.0
        deaths_amt     = deaths_amt or 0.0
        lapses_amt     = lapses_amt or 0.0
        other_amt      = other_amt or 0.0
        end_if_amt     = end_if_amt or 0.0

        decrements = deaths + lapses + other
        decrements_amt = deaths_amt + lapses_amt + other_amt

        recon_diff_count = beg_if + new_issues - decrements - end_if
        recon_diff_amount = (beg_if_amt + new_issues_amt - decrements_amt - end_if_amt)
        recon_diff_amount_pct = (
            abs(recon_diff_amount) / end_if_amt if end_if_amt and end_if_amt > 0 else 0.0
        )

        recon_passes = recon_diff_count == 0 and recon_diff_amount_pct <= 0.0001

        # Write year record to gold_inforce_reconciliation
        conn.execute(
            """
            INSERT INTO gold_inforce_reconciliation VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                str(uuid.uuid4()), study_run_id, "TERM", year,
                int(beg_if), int(new_issues), int(deaths), int(lapses),
                0,  # surrenders_count — Term has no surrenders
                int(other), int(end_if), int(recon_diff_count),
                float(beg_if_amt), float(new_issues_amt),
                float(deaths_amt), float(lapses_amt),
                0.0,   # surrenders_amount
                float(other_amt), float(end_if_amt),
                float(recon_diff_amount),
                recon_passes,
            ],
        )

        if not recon_passes:
            failing_years.append(year)
            sample_records.append(
                {
                    "year": year,
                    "beg_if": beg_if,
                    "new_issues": new_issues,
                    "decrements": decrements,
                    "end_if": end_if,
                    "recon_diff_count": recon_diff_count,
                    "recon_diff_amount_pct": f"{recon_diff_amount_pct:.6%}",
                }
            )

    total_years = end_year - start_year + 1
    return (
        DQCheckResult(
            check_id="DQ-TL-14",
            description="In-force reconciliation: BEG_IF + NEW_ISSUES - DECREMENTS = END_IF",
            severity="ERROR_HALT",
            passed=len(failing_years) == 0,
            fail_count=len(failing_years),
            sample_records=sample_records[:10],
        ),
        [],  # reconciliation is year-level — no individual policy_ids to quarantine
    )


def check_dq_tl_15(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-15 (ERROR): ci_rider_sum_assured <= face_amount."""
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, ci_rider_sum_assured, face_amount
        FROM silver_term_policies
        WHERE _etl_run_id = '{etl_run_id}'
          AND ci_rider_flag = TRUE
          AND ci_rider_sum_assured IS NOT NULL
          AND ci_rider_sum_assured > face_amount
        """,
    )
    return _build_result(
        "DQ-TL-15",
        "ci_rider_sum_assured ≤ face_amount",
        "ERROR",
        cols,
        rows,
    )


def check_dq_tl_16(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> tuple[DQCheckResult, list[str]]:
    """DQ-TL-16 (ERROR): CI claim illness_code in configured valid code list."""
    valid_sql = ", ".join(f"'{c}'" for c in VALID_CI_ILLNESS_CODES)
    etl_run_id = context["study_run_id"]
    cols, rows = _fetch(
        conn,
        f"""
        SELECT policy_id, illness_code
        FROM silver_policy_events
        WHERE _etl_run_id = '{etl_run_id}'
          AND product_code = 'TERM'
          AND event_type = 'CI_CLAIM'
          AND (illness_code IS NULL OR illness_code NOT IN ({valid_sql}))
        """,
    )
    return _build_result(
        "DQ-TL-16",
        f"CI claim illness_code ∈ valid codes",
        "ERROR",
        cols,
        rows,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    check_dq_tl_01,
    check_dq_tl_02,
    check_dq_tl_03,
    check_dq_tl_04,
    check_dq_tl_05,
    check_dq_tl_06,
    check_dq_tl_07,
    check_dq_tl_08,
    check_dq_tl_09,
    check_dq_tl_10,
    check_dq_tl_11,
    check_dq_tl_12,
    check_dq_tl_13,
    check_dq_tl_14,
    check_dq_tl_15,
    check_dq_tl_16,
]


def run_all_checks(
    conn: duckdb.DuckDBPyConnection, context: dict
) -> list[tuple[DQCheckResult, list[str]]]:
    """Run all 16 Term Life DQ checks and return results."""
    return [fn(conn, context) for fn in _ALL_CHECKS]
