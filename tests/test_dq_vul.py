"""Unit tests for the VUL DQ pipeline.

Covers:
    - Clean data: DQ score >= 85%, all 4 checks present, no critical failure
    - DQ-VUL-01: sub-account allocations not summing to 1.0
    - DQ-VUL-02: separate_account_total_value != sum of sub-account fund_values
    - DQ-VUL-03 (HALT): negative separate_account_total_value
    - DQ-VUL-04: unknown fund ID in sub_account_allocations

Note: VUL DQ checks do NOT filter by _etl_run_id. They check all rows in
silver_vul_policies. The study_run_id is used only for writing DQ run summary
and quarantine records, not for filtering the silver table.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import duckdb
import pytest

from src.data_quality.runner import DQCriticalFailure, run_dq_checks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# `prod_db` is provided by tests/conftest.py — a session-scoped *copy* of the production
# DB, so tests never mutate data/experience_study.duckdb (run_dq_checks persists rows).


@pytest.fixture(scope="module")
def prod_etl_run_id(prod_db: Path) -> str:
    """Return the most recent _etl_run_id present in silver_vul_policies."""
    conn = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = conn.execute(
            "SELECT _etl_run_id FROM silver_vul_policies ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip("No ETL data in silver_vul_policies — run VUL pipeline first")
    return row[0]


@pytest.fixture()
def bad_db(tmp_path: Path, prod_db: Path) -> Path:
    """Copy the production DB to a temp location for mutation tests."""
    dest = tmp_path / "vul_bad.duckdb"
    shutil.copy2(prod_db, dest)
    return dest


def _get_vul_etl_run_id(db_path: Path) -> str:
    """Fetch the _etl_run_id from silver_vul_policies."""
    conn = duckdb.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT _etl_run_id FROM silver_vul_policies LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Clean-data tests
# ---------------------------------------------------------------------------


class TestCleanDataVUL:
    """DQ checks on unmodified production VUL data."""

    def test_vul_dq_score_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.dq_score_pct >= 85.0, (
            f"VUL DQ score {result.dq_score_pct:.2f}% is below 85% threshold"
        )

    def test_vul_no_critical_failure(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_vul_success_flag(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.success

    def test_vul_total_records_count(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.total_records == 800, (
            f"Expected 800 VUL records, got {result.total_records}"
        )

    def test_vul_all_4_checks_present(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        check_ids = {cr.check_id for cr in result.check_results}
        assert check_ids == {"DQ-VUL-01", "DQ-VUL-02", "DQ-VUL-03", "DQ-VUL-04"}

    def test_vul_halt_check_passes_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-VUL-03 (HALT) must pass on clean data."""
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        for cr in result.check_results:
            if cr.check_id == "DQ-VUL-03":
                assert cr.passed, (
                    f"Halt check DQ-VUL-03 failed on clean data: {cr.fail_count} records"
                )

    def test_vul_dq_summary_written(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """run_dq_checks must write a row to gold_dq_run_summary."""
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        conn = duckdb.connect(str(prod_db), read_only=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_run_summary WHERE dq_run_id = ?",
                [result.dq_run_id],
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == 1, "No row written to gold_dq_run_summary"


# ---------------------------------------------------------------------------
# DQ-VUL-01: sub-account allocations must sum to 1.0 (within 0.1%)
# ---------------------------------------------------------------------------


class TestDQVUL01:
    """Verify DQ-VUL-01 fires when sub-account allocations don't sum to 1.0."""

    def test_vul01_fails_with_bad_allocation(self, bad_db: Path) -> None:
        """Seed one record with alloc_pct summing to 0.85 (not 1.0)."""
        etl_run_id = _get_vul_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        # Corrupt the allocation to sum to 0.85
        bad_alloc = json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.50, "fund_value": 50000.0},
            {"fund_id": "BALANCED",     "alloc_pct": 0.35, "fund_value": 35000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies SET sub_account_allocations = ? "
            "WHERE policy_id = ?",
            [bad_alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr01 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-01")
        assert not cr01.passed
        assert cr01.fail_count >= 1

    def test_vul01_sample_records_populated(self, bad_db: Path) -> None:
        """Failing check should have sample records showing the bad allocation."""
        etl_run_id = _get_vul_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        bad_alloc = json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.60, "fund_value": 60000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies SET sub_account_allocations = ? "
            "WHERE policy_id = ?",
            [bad_alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr01 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-01")
        assert cr01.fail_count >= 1
        assert len(cr01.sample_records) >= 1


# ---------------------------------------------------------------------------
# DQ-VUL-02: separate_account_total_value == sum of sub-account fund_values
# ---------------------------------------------------------------------------


class TestDQVUL02:
    """Verify DQ-VUL-02 fires when SA total does not match sub-account sum."""

    def test_vul02_fails_with_mismatched_total(self, bad_db: Path) -> None:
        """Seed a record where SA total is 100k but fund values sum to 50k."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        # Fund values sum to 50k but SA total set to 200k
        alloc = json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.60, "fund_value": 30000.0},
            {"fund_id": "BALANCED",     "alloc_pct": 0.40, "fund_value": 20000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies "
            "SET sub_account_allocations = ?, separate_account_total_value = 200000.0 "
            "WHERE policy_id = ?",
            [alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr02 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-02")
        assert not cr02.passed
        assert cr02.fail_count >= 1

    def test_vul02_passes_when_totals_match(self, bad_db: Path) -> None:
        """When SA total equals fund-value sum, DQ-VUL-02 must pass."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        alloc = json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.60, "fund_value": 60000.0},
            {"fund_id": "BALANCED",     "alloc_pct": 0.40, "fund_value": 40000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies "
            "SET sub_account_allocations = ?, separate_account_total_value = 100000.0 "
            "WHERE policy_id = ?",
            [alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr02 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-02")
        assert cr02.passed


# ---------------------------------------------------------------------------
# DQ-VUL-03 (HALT): separate_account_total_value >= 0
# ---------------------------------------------------------------------------


class TestDQVUL03Halt:
    """Verify DQ-VUL-03 halts when SA value is negative."""

    def test_vul03_raises_on_halt_on_critical(self, bad_db: Path) -> None:
        """Seeding a negative SA value must raise DQCriticalFailure."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_vul_policies SET separate_account_total_value = -1000.0 "
            "WHERE policy_id = ?",
            [pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        with pytest.raises(DQCriticalFailure) as exc_info:
            run_dq_checks("VUL", bad_db, run_id, halt_on_critical=True)
        assert exc_info.value.check_id == "DQ-VUL-03"

    def test_vul03_critical_failure_flag(self, bad_db: Path) -> None:
        """With halt_on_critical=False, critical_failure flag must be True."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_vul_policies SET separate_account_total_value = -5000.0 "
            "WHERE policy_id = ?",
            [pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        assert result.critical_failure

    def test_vul03_fail_count_correct(self, bad_db: Path) -> None:
        """fail_count must equal the number of negative SA records."""
        conn = duckdb.connect(str(bad_db))
        # Corrupt two records
        pids = conn.execute(
            "SELECT policy_id FROM silver_vul_policies LIMIT 2"
        ).fetchall()
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_vul_policies SET separate_account_total_value = -999.0 "
                "WHERE policy_id = ?",
                [pid],
            )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr03 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-03")
        assert cr03.fail_count >= 2

    def test_vul03_check_id_in_exception(self, bad_db: Path) -> None:
        """DQCriticalFailure exception must reference DQ-VUL-03."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_vul_policies SET separate_account_total_value = -1.0 "
            "WHERE policy_id = ?",
            [pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        try:
            run_dq_checks("VUL", bad_db, run_id, halt_on_critical=True)
            pytest.fail("Expected DQCriticalFailure to be raised")
        except DQCriticalFailure as exc:
            assert exc.check_id == "DQ-VUL-03"
            assert exc.fail_count >= 1


# ---------------------------------------------------------------------------
# DQ-VUL-04: All fund IDs must exist in master fund list
# ---------------------------------------------------------------------------


class TestDQVUL04:
    """Verify DQ-VUL-04 fires when an unknown fund ID appears."""

    def test_vul04_fails_with_unknown_fund_id(self, bad_db: Path) -> None:
        """Seed a sub_account_allocations with an unknown fund ID."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        bad_alloc = json.dumps([
            {"fund_id": "EQ_LARGE_CAP",    "alloc_pct": 0.70, "fund_value": 70000.0},
            {"fund_id": "UNKNOWN_FUND_XYZ","alloc_pct": 0.30, "fund_value": 30000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies SET sub_account_allocations = ? "
            "WHERE policy_id = ?",
            [bad_alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        cr04 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-04")
        assert not cr04.passed
        assert cr04.fail_count >= 1

    def test_vul04_severity_is_warn(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-VUL-04 must be WARN severity (not a halting check)."""
        result = run_dq_checks("VUL", prod_db, prod_etl_run_id, halt_on_critical=False)
        cr04 = next(cr for cr in result.check_results if cr.check_id == "DQ-VUL-04")
        assert cr04.severity == "WARN"

    def test_vul04_does_not_set_critical_failure(self, bad_db: Path) -> None:
        """An unknown fund ID must not trigger critical_failure."""
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_vul_policies "
            "WHERE sub_account_allocations IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        bad_alloc = json.dumps([
            {"fund_id": "MYSTERY_FUND", "alloc_pct": 1.0, "fund_value": 50000.0},
        ])
        conn.execute(
            "UPDATE silver_vul_policies SET sub_account_allocations = ? "
            "WHERE policy_id = ?",
            [bad_alloc, pid],
        )
        conn.close()

        run_id = _get_vul_etl_run_id(bad_db)
        result = run_dq_checks("VUL", bad_db, run_id, halt_on_critical=False)
        assert not result.critical_failure
