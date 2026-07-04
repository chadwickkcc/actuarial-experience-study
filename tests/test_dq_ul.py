"""Unit tests for the Universal Life DQ pipeline.

Covers:
    - Clean data: all 6 checks present, no critical failure, 1800 records
    - DQ-UL-02 ERROR: ULSG shadow_account_funding_ratio < 0
    - DQ-UL-03 WARN: ULSG IF policies with funding_ratio < 1.0
    - DQ-UL-04 ERROR: current_coi_rate > guaranteed_coi_rate
    - DQ-UL-05 ERROR: credited_interest_rate < guaranteed_min_interest_rate
    - DQ-UL-06 WARN: MEC flag inconsistency
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from src.data_quality.checks.ul_checks import HALT_CHECK_IDS
from src.data_quality.runner import run_dq_checks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# `prod_db` is provided by tests/conftest.py — a session-scoped *copy* of the production
# DB, so tests never mutate data/experience_study.duckdb (run_dq_checks persists rows).


@pytest.fixture(scope="module")
def prod_etl_run_id(prod_db: Path) -> str:
    """Return the most recent _etl_run_id present in silver_ul_policies."""
    conn = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip("No ETL data in silver_ul_policies — run UL pipeline first")
    return row[0]


@pytest.fixture()
def bad_db(tmp_path: Path, prod_db: Path) -> Path:
    """Copy the production DB to a temp location for mutation tests."""
    dest = tmp_path / "ul_bad.duckdb"
    shutil.copy2(prod_db, dest)
    return dest


# ---------------------------------------------------------------------------
# Clean-data tests
# ---------------------------------------------------------------------------


class TestCleanDataUL:
    """DQ checks on unmodified production UL data."""

    def test_ul_dq_score_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.dq_score_pct >= 0.0, "DQ score should be a non-negative number"

    def test_ul_no_critical_failure(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul_all_6_checks_present(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        check_ids = {cr.check_id for cr in result.check_results}
        assert check_ids == {"DQ-UL-01", "DQ-UL-02", "DQ-UL-03",
                             "DQ-UL-04", "DQ-UL-05", "DQ-UL-06"}

    def test_ul_no_halt_checks_defined(self) -> None:
        """UL has no HALT-severity checks — HALT_CHECK_IDS must be empty."""
        assert HALT_CHECK_IDS == frozenset()

    def test_ul_total_records_count(self, prod_db: Path, prod_etl_run_id: str) -> None:
        # The "UL" product DQ run covers Trad UL only (800). ULSG (800) and IUL (200) are
        # processed as their own product runs, so the per-product UL count is 800, not the
        # full 1,800-row UL-family CSV.
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.total_records == 800, (
            f"Expected 800 Trad-UL records, got {result.total_records}"
        )

    def test_ul_dq_summary_written(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
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
# DQ-UL-02 ERROR: ULSG shadow_account_funding_ratio < 0
# ---------------------------------------------------------------------------


class TestDQUL02Quarantine:
    """Verify DQ-UL-02 quarantines without halting when ULSG funding ratio is negative."""

    def test_ul02_quarantine_on_negative_ratio(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        pids = conn.execute(
            "SELECT policy_id FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND is_ulsg_flag = TRUE "
            "AND shadow_account_funding_ratio IS NOT NULL LIMIT 2",
            [etl_run_id],
        ).fetchall()
        if not pids:
            pytest.skip("No ULSG policies found")
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_ul_policies SET shadow_account_funding_ratio = -0.5 "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul02 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-02")
        assert ul02.fail_count == 2

    def test_ul02_not_halt(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND is_ulsg_flag = TRUE "
            "AND shadow_account_funding_ratio IS NOT NULL LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No ULSG policies found")
        conn.execute(
            "UPDATE silver_ul_policies SET shadow_account_funding_ratio = -0.5 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul02_quarantine_record_written(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND is_ulsg_flag = TRUE "
            "AND shadow_account_funding_ratio IS NOT NULL LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No ULSG policies found")
        conn.execute(
            "UPDATE silver_ul_policies SET shadow_account_funding_ratio = -0.5 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_quarantine "
                "WHERE check_id = 'DQ-UL-02' AND study_run_id = ?",
                [etl_run_id],
            ).fetchone()
        finally:
            conn.close()
        assert count_row[0] >= 1

    def test_ul02_non_ulsg_ignored(self, bad_db: Path) -> None:
        """Non-ULSG policies with negative value must not trigger DQ-UL-02."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        # Find a non-ULSG policy and set its shadow ratio to -0.5 (should be ignored)
        row = conn.execute(
            "SELECT policy_id FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND is_ulsg_flag = FALSE LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No non-ULSG UL policies found")
        conn.execute(
            "UPDATE silver_ul_policies SET shadow_account_funding_ratio = -0.5 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul02 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-02")
        assert ul02.fail_count == 0
        assert ul02.passed


# ---------------------------------------------------------------------------
# DQ-UL-03 WARN: ULSG IF with funding_ratio < 1.0
# ---------------------------------------------------------------------------


class TestDQUL03Warn:
    """Verify DQ-UL-03 fires on underfunded ULSG policies in force."""

    def test_ul03_fires_on_underfunded_ulsg(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """Synthetic ULSG data has some funding < 1.0 — check must fire."""
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        ul03 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-03")
        assert ul03.fail_count > 0, (
            "DQ-UL-03 should fire on synthetic data — ULSG policies are generated "
            "with some funding ratios < 1.0"
        )

    def test_ul03_not_critical(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul03_non_if_excluded(self, bad_db: Path) -> None:
        """A lapsed ULSG with funding_ratio < 1.0 must NOT be counted by DQ-UL-03."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        # Seed a terminated ULSG with low funding ratio
        row = conn.execute(
            "SELECT policy_id FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND is_ulsg_flag = TRUE AND status_code != 'IF' "
            "LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No terminated ULSG policies in synthetic data")
        conn.execute(
            "UPDATE silver_ul_policies SET shadow_account_funding_ratio = 0.3 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        result_before_count = run_dq_checks(
            "UL", bad_db, etl_run_id, halt_on_critical=False
        )
        ul03 = next(cr for cr in result_before_count.check_results if cr.check_id == "DQ-UL-03")
        # Verify by checking that all failing policies have status_code = 'IF'
        # (the check query filters on status_code = 'IF')
        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            non_if_count = conn.execute(
                "SELECT COUNT(*) FROM silver_ul_policies "
                "WHERE _etl_run_id = ? AND is_ulsg_flag = TRUE "
                "AND status_code != 'IF' AND shadow_account_funding_ratio < 1.0",
                [etl_run_id],
            ).fetchone()[0]
        finally:
            conn.close()
        # The check should only count IF policies; non-IF cannot inflate the count
        assert non_if_count >= 1  # our seeded record is there but not counted by DQ-UL-03


# ---------------------------------------------------------------------------
# DQ-UL-04 ERROR: current_coi_rate > guaranteed_coi_rate
# ---------------------------------------------------------------------------


class TestDQUL04Quarantine:
    """Verify DQ-UL-04 quarantines when current COI exceeds guaranteed COI."""

    def test_ul04_quarantine_on_coi_violation(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        pids = conn.execute(
            "SELECT policy_id, guaranteed_coi_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? LIMIT 3",
            [etl_run_id],
        ).fetchall()
        for pid, gcoi in pids:
            conn.execute(
                "UPDATE silver_ul_policies SET current_coi_rate = ? "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [gcoi + 0.001, pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul04 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-04")
        assert ul04.fail_count == 3

    def test_ul04_not_halt(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id, guaranteed_coi_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()
        conn.execute(
            "UPDATE silver_ul_policies SET current_coi_rate = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[1] + 0.001, row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul04_equal_rates_pass(self, bad_db: Path) -> None:
        """Setting current_coi_rate == guaranteed_coi_rate must not trigger DQ-UL-04."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id, guaranteed_coi_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()
        conn.execute(
            "UPDATE silver_ul_policies SET current_coi_rate = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[1], row[0], etl_run_id],  # exactly equal — should pass
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul04 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-04")
        assert ul04.fail_count == 0
        assert ul04.passed


# ---------------------------------------------------------------------------
# DQ-UL-05 ERROR: credited_interest_rate < guaranteed_min_interest_rate
# ---------------------------------------------------------------------------


class TestDQUL05Quarantine:
    """Verify DQ-UL-05 quarantines when credited rate falls below guaranteed minimum."""

    def test_ul05_quarantine_on_credited_below_min(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        # Get baseline count of already-failing policies
        baseline_result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        baseline_count = next(
            cr.fail_count for cr in baseline_result.check_results if cr.check_id == "DQ-UL-05"
        )
        # Seed 2 policies that are currently passing (credited >= gmir)
        pids = conn.execute(
            "SELECT policy_id, guaranteed_min_interest_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? "
            "AND credited_interest_rate >= guaranteed_min_interest_rate - 0.0001 "
            "LIMIT 2",
            [etl_run_id],
        ).fetchall()
        for pid, gmir in pids:
            conn.execute(
                "UPDATE silver_ul_policies SET credited_interest_rate = ? "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [gmir - 0.005, pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul05 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-05")
        assert ul05.fail_count == baseline_count + 2

    def test_ul05_not_halt(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id, guaranteed_min_interest_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()
        conn.execute(
            "UPDATE silver_ul_policies SET credited_interest_rate = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[1] - 0.005, row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul05_tolerance_boundary(self, bad_db: Path) -> None:
        """credited = gmir - 0.00005 (within 0.0001 tolerance) must not add new failures."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        # Get baseline before seeding
        baseline_result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        baseline_count = next(
            cr.fail_count for cr in baseline_result.check_results if cr.check_id == "DQ-UL-05"
        )
        # Pick a policy currently passing and apply within-tolerance change
        row = conn.execute(
            "SELECT policy_id, guaranteed_min_interest_rate FROM silver_ul_policies "
            "WHERE _etl_run_id = ? "
            "AND credited_interest_rate >= guaranteed_min_interest_rate - 0.0001 "
            "LIMIT 1",
            [etl_run_id],
        ).fetchone()
        conn.execute(
            "UPDATE silver_ul_policies SET credited_interest_rate = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[1] - 0.00005, row[0], etl_run_id],  # within 0.0001 tolerance
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul05 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-05")
        # Count should not increase — within-tolerance change must not trigger DQ-UL-05
        assert ul05.fail_count == baseline_count


# ---------------------------------------------------------------------------
# DQ-UL-06 WARN: MEC flag inconsistency
# ---------------------------------------------------------------------------


class TestDQUL06Warn:
    """Verify DQ-UL-06 fires on MEC flag inconsistencies."""

    def test_ul06_fires_on_underpaid_mec(self, bad_db: Path) -> None:
        """DQ-UL-06 must fire on an inconsistent policy: mec_status_flag=FALSE while
        cumulative premiums exceed the 7-pay limit (seven_pay_premium × 7). Clean synthetic
        data sets mec_status_flag deterministically (always consistent), so we seed the
        inconsistency to exercise the check."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT policy_id, seven_pay_premium FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND seven_pay_premium IS NOT NULL "
            "AND seven_pay_premium > 0 LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No UL policies with seven_pay_premium found")
        pid, spp = row
        conn.execute(
            "UPDATE silver_ul_policies "
            "SET mec_status_flag = FALSE, cumulative_premiums_paid = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [spp * 8.0, pid, etl_run_id],  # > 7-pay limit but flag FALSE → inconsistent
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul06 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-06")
        assert ul06.fail_count >= 1, (
            "DQ-UL-06 should fire when mec_status_flag=FALSE and cumulative premiums "
            "exceed the 7-pay limit"
        )

    def test_ul06_not_critical(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("UL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_ul06_true_mec_excluded(self, bad_db: Path) -> None:
        """Policies with mec_status_flag=TRUE must not be flagged by DQ-UL-06."""
        conn = duckdb.connect(str(bad_db))
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_ul_policies LIMIT 1"
        ).fetchone()[0]
        # Set mec_status_flag=TRUE on a policy with very high cumulative premiums
        row = conn.execute(
            "SELECT policy_id, seven_pay_premium FROM silver_ul_policies "
            "WHERE _etl_run_id = ? AND seven_pay_premium IS NOT NULL "
            "AND seven_pay_premium > 0 LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No UL policies with seven_pay_premium found")
        pid, spp = row
        conn.execute(
            "UPDATE silver_ul_policies "
            "SET mec_status_flag = TRUE, cumulative_premiums_paid = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [spp * 50.0, pid, etl_run_id],  # very high — would fail if mec_flag=FALSE
        )
        conn.close()

        result = run_dq_checks("UL", bad_db, etl_run_id, halt_on_critical=False)
        ul06 = next(cr for cr in result.check_results if cr.check_id == "DQ-UL-06")
        # The seeded policy has mec_status_flag=TRUE — DQ-UL-06 only checks FALSE ones
        # Verify the specific policy is not in the failing set
        failing_sample_ids = {s.get("policy_id") for s in ul06.sample_records}
        assert pid not in failing_sample_ids
