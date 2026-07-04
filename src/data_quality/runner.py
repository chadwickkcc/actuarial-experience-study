"""DQ pipeline runner — executes check suites and manages quarantine records.

Public API (per experience_study_technical_spec_v1.2.md Section B.3):
    run_dq_checks(product_code, db_path, study_run_id, halt_on_critical) -> DQResult
    override_quarantine_record(quarantine_id, actuary_id, justification, db_path) -> bool
    DQCriticalFailure (exception class)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import duckdb
import yaml

from src.utils.types import DQCheckResult, DQResult


# Snake-case or mixed-case identifier (must contain at least one underscore OR
# at least one uppercase letter inside, e.g. ``AV_eom``). Prevents picking up
# plain English words like "policies", "between", "forward".
_FIELD_NAME_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*_[A-Za-z0-9_]*\b")


def _extract_failing_field(description: str, check_id: str) -> str:
    """Best-effort extraction of the data field referenced by a DQ check description.

    Picks the first snake_case (or mixed-case-with-underscore) identifier in the
    description (e.g. ``shadow_account_funding_ratio`` from "ULSG IF policies
    with shadow_account_funding_ratio < 1.0 …", or ``AV_eom`` from
    "AV roll-forward: AV_eom within plausible range …"). Falls back to
    ``check_id`` when no identifier is found. Truncated to 50 chars to fit the
    ``gold_dq_quarantine.failing_field`` column.

    Per-policy values are not extracted — that would require querying each
    failing row individually. The audit trail uses
    ``check_description`` + this approximate field name for review.
    """
    if not description:
        return check_id[:50]
    m = _FIELD_NAME_RE.search(description)
    if m:
        return m.group(0)[:50]
    return check_id[:50]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DQCriticalFailure(Exception):
    """Raised when a DQ check with severity ERROR_HALT fails."""

    def __init__(self, check_id: str, fail_count: int, description: str) -> None:
        self.check_id = check_id
        self.fail_count = fail_count
        super().__init__(
            f"Critical DQ failure: {check_id} ({fail_count} records) — {description}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_study_dates(db_path: Path) -> tuple[str, str]:
    """Read study_start_date and study_end_date from study_config.yaml.

    Searches upward from db_path for the project root containing
    config/study_config.yaml.
    """
    p = db_path.resolve().parent
    while p != p.parent:
        cfg_path = p / "config" / "study_config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text())
            return str(cfg["study_start_date"]), str(cfg["study_end_date"])
        p = p.parent
    # Fallback defaults matching the project spec
    return "2016-01-01", "2023-12-31"


def _insert_quarantine_records(
    conn: duckdb.DuckDBPyConnection,
    dq_run_id: str,
    study_run_id: str,
    product_code: str,
    check_result: DQCheckResult,
    failing_ids: list[str],
) -> None:
    """Insert one quarantine row per failing policy_id for non-halt checks."""
    if not failing_ids:
        return
    now_ts = datetime.utcnow()
    failing_field = _extract_failing_field(
        check_result.description, check_result.check_id
    )
    failing_value = f"{check_result.check_id}:{check_result.severity} — see check_description"
    rows = [
        (
            str(uuid.uuid4()),
            dq_run_id,
            study_run_id,
            pid,
            product_code,
            check_result.check_id,
            check_result.description,
            failing_field,
            failing_value,
            now_ts,
            False,  # actuary_override_flag
            None,   # override_ts
            None,   # override_justification
            None,   # override_actuary_id
        )
        for pid in failing_ids
    ]
    conn.executemany(
        """
        INSERT INTO gold_dq_quarantine VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )


def _write_dq_summary(
    conn: duckdb.DuckDBPyConnection,
    dq_run_id: str,
    study_run_id: str,
    product_code: str,
    total_records: int,
    records_passed: int,
    records_quarantined: int,
    records_halted: int,
    dq_score_pct: float,
    critical_failure: bool,
    check_results: list[DQCheckResult],
) -> None:
    """Write one row to gold_dq_run_summary."""
    check_json = json.dumps(
        [
            {
                "check_id": r.check_id,
                "description": r.description,
                "status": "PASS" if r.passed else "FAIL",
                "fail_count": r.fail_count,
                "severity": r.severity,
            }
            for r in check_results
        ]
    )
    conn.execute(
        """
        INSERT INTO gold_dq_run_summary VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            dq_run_id,
            study_run_id,
            product_code,
            datetime.utcnow(),
            total_records,
            records_passed,
            records_quarantined,
            records_halted,
            dq_score_pct,
            critical_failure,
            check_json,
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_dq_checks(
    product_code: str,
    db_path: Path,
    study_run_id: str,
    halt_on_critical: bool = True,
) -> DQResult:
    """Execute all DQ checks for a product against its Silver table.

    Critical (ERROR_HALT) checks that fail:
        - Set DQResult.critical_failure = True
        - Raise DQCriticalFailure if halt_on_critical=True

    Non-critical (ERROR/WARN) checks that fail:
        - Insert failing records into gold_dq_quarantine
        - Continue execution

    Args:
        product_code:       Product to check ("TERM", "WL", …)
        db_path:            Path to DuckDB file
        study_run_id:       UUID of the current study run
        halt_on_critical:   Raise on first halt-level failure if True

    Returns:
        DQResult with all check results and aggregate score

    Raises:
        DQCriticalFailure: if a halting check fails and halt_on_critical=True
        ValueError: if product_code is not supported
    """
    product_code = product_code.upper()

    # Normalise UL variants to shared check module
    _check_product = product_code
    if product_code in ("ULSG", "IUL"):
        _check_product = "UL"
    if product_code in ("DA_FIXED", "DA_FIA", "DA_VA"):
        _check_product = "DA"

    _SUPPORTED = {"TERM", "WL", "UL", "ULSG", "IUL", "VUL", "DA", "DA_FIXED", "DA_FIA", "DA_VA"}
    if product_code not in _SUPPORTED:
        raise ValueError(f"DQ checks not yet implemented for product: {product_code}")

    study_start, study_end = _load_study_dates(db_path)
    context = {
        "study_start_date": study_start,
        "study_end_date": study_end,
        "study_run_id": study_run_id,
    }

    dq_run_id = str(uuid.uuid4())
    conn = duckdb.connect(str(db_path))

    try:
        # Import product-specific checks
        if _check_product == "TERM":
            from src.data_quality.checks.term_checks import (
                HALT_CHECK_IDS,
                run_all_checks,
            )
            silver_table = "silver_term_policies"
        elif _check_product == "WL":
            from src.data_quality.checks.wl_checks import (
                HALT_CHECK_IDS,
                run_all_checks,
            )
            silver_table = "silver_wl_policies"
        elif _check_product == "VUL":
            from src.data_quality.checks.vul_checks import (
                HALT_CHECK_IDS,
                run_all_checks,
            )
            silver_table = "silver_vul_policies"
        elif _check_product == "DA":
            from src.data_quality.checks.annuity_checks import (
                HALT_CHECK_IDS,
                run_all_checks,
            )
            silver_table = "silver_annuity_contracts"
        else:  # UL / ULSG / IUL
            from src.data_quality.checks.ul_checks import (
                HALT_CHECK_IDS,
                run_all_checks,
            )
            silver_table = "silver_ul_policies"

        # DA uses contract_id as primary key; all others use policy_id
        pk_col = "contract_id" if _check_product == "DA" else "policy_id"

        # Total records for the current ETL run only.
        # UL variants share silver_ul_policies, so filter by product_code to avoid
        # counting sibling-product rows (e.g. ULSG rows when running UL DQ).
        if product_code in ("UL", "ULSG", "IUL"):
            total_records: int = conn.execute(
                f"SELECT COUNT(*) FROM {silver_table}"
                " WHERE _etl_run_id = ? AND product_code = ?",
                [study_run_id, product_code],
            ).fetchone()[0]
        else:
            total_records: int = conn.execute(
                f"SELECT COUNT(*) FROM {silver_table} WHERE _etl_run_id = ?",
                [study_run_id],
            ).fetchone()[0]

        all_results: list[DQCheckResult] = []
        quarantine_ids: set[str] = set()
        halt_ids: set[str] = set()
        critical_failure = False
        first_halt: DQCheckResult | None = None

        for check_result, failing_ids in run_all_checks(conn, context):
            all_results.append(check_result)

            if not check_result.passed:
                is_halt = check_result.check_id in HALT_CHECK_IDS

                if is_halt:
                    critical_failure = True
                    halt_ids.update(failing_ids)
                    if first_halt is None:
                        first_halt = check_result
                else:
                    # Skip policies already approved by an actuary in any prior run.
                    # An actuary override is a standing approval — do not re-quarantine.
                    approved_ids: set[str] = {
                        r[0]
                        for r in conn.execute(
                            "SELECT DISTINCT policy_id FROM gold_dq_quarantine"
                            " WHERE actuary_override_flag = TRUE"
                        ).fetchall()
                    }
                    new_failing = [pid for pid in failing_ids if pid not in approved_ids]

                    # Quarantine non-halt failures (excluding approved policies)
                    if new_failing:
                        _insert_quarantine_records(
                            conn,
                            dq_run_id,
                            study_run_id,
                            product_code,
                            check_result,
                            new_failing,
                        )
                        quarantine_ids.update(new_failing)

        records_quarantined = len(quarantine_ids)
        records_halted = len(halt_ids)
        all_failing = quarantine_ids | halt_ids
        records_passed = total_records - len(all_failing)
        dq_score_pct = (records_passed / total_records * 100.0) if total_records > 0 else 100.0

        _write_dq_summary(
            conn,
            dq_run_id,
            study_run_id,
            product_code,
            total_records,
            records_passed,
            records_quarantined,
            records_halted,
            dq_score_pct,
            critical_failure,
            all_results,
        )

        result = DQResult(
            dq_run_id=dq_run_id,
            study_run_id=study_run_id,
            product_code=product_code,
            total_records=total_records,
            records_passed=records_passed,
            records_quarantined=records_quarantined,
            critical_failure=critical_failure,
            dq_score_pct=dq_score_pct,
            check_results=all_results,
            success=not critical_failure,
        )

        # Raise after writing summary so the summary is always persisted
        if critical_failure and halt_on_critical and first_halt is not None:
            raise DQCriticalFailure(
                first_halt.check_id,
                first_halt.fail_count,
                first_halt.description,
            )

        return result

    finally:
        conn.close()


def override_quarantine_record(
    quarantine_id: str,
    actuary_id: str,
    justification: str,
    db_path: Path,
) -> bool:
    """Mark a quarantined record as overridden by an actuary.

    Updates gold_dq_quarantine.actuary_override_flag = True and records
    the actuary_id, justification, and timestamp.

    Returns:
        True if the record was found and updated; False otherwise.
    """
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            """
            UPDATE gold_dq_quarantine
            SET actuary_override_flag  = TRUE,
                override_ts            = ?,
                override_justification = ?,
                override_actuary_id    = ?
            WHERE quarantine_id = ?
            """,
            [datetime.utcnow(), justification, actuary_id, quarantine_id],
        )
        rows_affected = conn.execute(
            "SELECT COUNT(*) FROM gold_dq_quarantine WHERE quarantine_id = ? AND actuary_override_flag = TRUE",
            [quarantine_id],
        ).fetchone()[0]
        return rows_affected > 0
    finally:
        conn.close()
