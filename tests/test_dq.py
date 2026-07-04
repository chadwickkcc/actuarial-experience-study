"""Unit tests for the Term Life DQ pipeline.

Covers:
    - Clean data: DQ score > 95%, all 16 checks, reconciliation passes
    - Seeded bad records: HALT triggers for DQ-TL-01, DQ-TL-03;
      quarantine triggers for DQ-TL-15; all failures surfaced with
      halt_on_critical=False
    - override_quarantine_record updates the quarantine table correctly
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import duckdb
import pytest

from src.data_quality.runner import (
    DQCriticalFailure,
    override_quarantine_record,
    run_dq_checks,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# `prod_db` is provided by tests/conftest.py — a session-scoped *copy* of the production
# DB, so tests never mutate data/experience_study.duckdb (run_dq_checks persists rows).


@pytest.fixture(scope="module")
def prod_etl_run_id(prod_db: Path) -> str:
    """Return the most recent _etl_run_id present in silver_term_policies."""
    conn = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = conn.execute(
            "SELECT _etl_run_id FROM silver_term_policies ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip("No ETL data in silver_term_policies — run pipeline first")
    return row[0]


@pytest.fixture()
def bad_db(tmp_path: Path, prod_db: Path) -> Path:
    """Copy the production DB to a temp location for mutation tests (DQ tables cleared)."""
    dest = tmp_path / "bad_test.duckdb"
    shutil.copy2(prod_db, dest)
    _reset_dq_tables(dest)
    return dest


def _etl_run_id_of(db_path: Path) -> str:
    """Return the _etl_run_id present in a (copied) DB's silver_term_policies.

    DQ checks filter by _etl_run_id, so mutation tests must pass the run_id the data was
    loaded under — not a fresh UUID (which would match zero rows and surface no failures).
    """
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return conn.execute(
            "SELECT _etl_run_id FROM silver_term_policies ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()


def _reset_dq_tables(db_path: Path) -> None:
    """Clear DQ result tables in a copied DB so mutation tests start clean.

    The pipeline-populated DB already contains gold_dq_run_summary / gold_dq_quarantine rows
    under the study run_id; clearing them isolates each test's own DQ writes (silver is kept).
    """
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("DELETE FROM gold_dq_quarantine")
        conn.execute("DELETE FROM gold_dq_run_summary")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helper: insert a known good policy that can be mutated per test
# ---------------------------------------------------------------------------

def _get_first_term_policy(db_path: Path) -> dict:
    """Fetch one complete policy row from silver_term_policies."""
    conn = duckdb.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT * FROM silver_term_policies LIMIT 1"
        ).fetchdf().to_dict(orient="records")[0]
    finally:
        conn.close()
    return row


def _pick_ci_policy(db_path: Path) -> dict | None:
    """Fetch a policy with ci_rider_flag = TRUE."""
    conn = duckdb.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT * FROM silver_term_policies WHERE ci_rider_flag = TRUE LIMIT 1"
        ).fetchdf().to_dict(orient="records")
    finally:
        conn.close()
    return rows[0] if rows else None


def _pick_if_policy(db_path: Path) -> dict:
    """Fetch an in-force policy."""
    conn = duckdb.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT * FROM silver_term_policies WHERE status_code = 'IF' LIMIT 1"
        ).fetchdf().to_dict(orient="records")
    finally:
        conn.close()
    return rows[0]


def _pick_terminated_policy(db_path: Path) -> dict:
    """Fetch a terminated policy (not IF)."""
    conn = duckdb.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT * FROM silver_term_policies WHERE status_code != 'IF' LIMIT 1"
        ).fetchdf().to_dict(orient="records")
    finally:
        conn.close()
    return rows[0]


# ---------------------------------------------------------------------------
# Clean-data tests
# ---------------------------------------------------------------------------


class TestCleanData:
    """DQ checks on unmodified production data."""

    def test_dq_score_exceeds_95_pct(self, prod_db: Path) -> None:
        """Clean synthetic data must achieve DQ score > 95%."""
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        assert result.dq_score_pct > 95.0, (
            f"DQ score {result.dq_score_pct:.2f}% is below 95% threshold"
        )

    def test_no_critical_failure_on_clean_data(self, prod_db: Path) -> None:
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        assert not result.critical_failure

    def test_success_flag_true_on_clean_data(self, prod_db: Path) -> None:
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        assert result.success

    def test_total_records_count(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("TERM", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.total_records == 3200

    def test_all_16_checks_present(self, prod_db: Path) -> None:
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        check_ids = {cr.check_id for cr in result.check_results}
        expected = {f"DQ-TL-{i:02d}" for i in range(1, 17)}
        assert check_ids == expected

    def test_halt_checks_pass_on_clean_data(self, prod_db: Path) -> None:
        """The six halting checks must all pass on clean synthetic data."""
        halt_ids = {"DQ-TL-01", "DQ-TL-03", "DQ-TL-05", "DQ-TL-06", "DQ-TL-10", "DQ-TL-14"}
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        for cr in result.check_results:
            if cr.check_id in halt_ids:
                assert cr.passed, (
                    f"Halt check {cr.check_id} failed on clean data: {cr.fail_count} records"
                )

    def test_inforce_reconciliation_all_years_pass(self, prod_db: Path) -> None:
        """DQ-TL-14 (reconciliation) must pass for all 8 study years."""
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        recon_check = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-14")
        assert recon_check.passed, (
            f"Reconciliation failed for {recon_check.fail_count} year(s): "
            f"{recon_check.sample_records}"
        )
        assert recon_check.fail_count == 0

    def test_dq_run_summary_written_to_db(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """run_dq_checks must write exactly one row to gold_dq_run_summary."""
        run_id = prod_etl_run_id
        result = run_dq_checks("TERM", prod_db, run_id, halt_on_critical=False)

        conn = duckdb.connect(str(prod_db), read_only=True)
        try:
            row = conn.execute(
                "SELECT dq_run_id, total_records, dq_score_pct, critical_failure "
                "FROM gold_dq_run_summary WHERE dq_run_id = ?",
                [result.dq_run_id],
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "No row written to gold_dq_run_summary"
        assert row[0] == result.dq_run_id
        assert row[1] == 3200
        assert abs(row[2] - result.dq_score_pct) < 0.001
        assert row[3] == result.critical_failure

    def test_inforce_reconciliation_rows_in_db(self, prod_db: Path) -> None:
        """DQ-TL-14 must write 8 rows (one per year 2016–2023) to gold_inforce_reconciliation."""
        run_id = str(uuid.uuid4())
        run_dq_checks("TERM", prod_db, run_id, halt_on_critical=False)

        conn = duckdb.connect(str(prod_db), read_only=True)
        try:
            rows = conn.execute(
                "SELECT calendar_year, recon_diff_count, recon_passes "
                "FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ? ORDER BY calendar_year",
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 8, f"Expected 8 recon rows, got {len(rows)}"
        for year_row in rows:
            assert year_row[1] == 0, (
                f"Reconciliation count diff != 0 for year {year_row[0]}: diff={year_row[1]}"
            )
            assert year_row[2] is True, f"recon_passes=False for year {year_row[0]}"

    def test_result_fields_non_negative(self, prod_db: Path) -> None:
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        assert result.records_passed >= 0
        assert result.records_quarantined >= 0
        assert 0.0 <= result.dq_score_pct <= 100.0

    def test_records_passed_plus_failing_equals_total(self, prod_db: Path) -> None:
        result = run_dq_checks("TERM", prod_db, str(uuid.uuid4()), halt_on_critical=False)
        # records_passed = total - distinct_failing (quarantined union halt)
        assert result.records_passed <= result.total_records

    def test_invalid_product_raises_value_error(self, prod_db: Path) -> None:
        with pytest.raises(ValueError, match="DQ checks not yet implemented"):
            run_dq_checks("UNKNOWN_PRODUCT", prod_db, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# DQ-TL-01 HALT: issue_date > termination_date
# ---------------------------------------------------------------------------


class TestDQTL01Halt:
    """Verify DQ-TL-01 halts when issue_date > termination_date."""

    def test_halt_raises_when_issue_after_termination(self, bad_db: Path) -> None:
        policy = _pick_terminated_policy(bad_db)
        pid = policy["policy_id"]

        conn = duckdb.connect(str(bad_db))
        # Set termination_date to 1 day before issue_date
        conn.execute(
            "UPDATE silver_term_policies "
            "SET termination_date = DATE '2000-01-01' "
            "WHERE policy_id = ?",
            [pid],
        )
        conn.close()

        with pytest.raises(DQCriticalFailure) as exc_info:
            run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=True)
        assert exc_info.value.check_id == "DQ-TL-01"
        assert exc_info.value.fail_count >= 1

    def test_critical_failure_flag_set_without_halt(self, bad_db: Path) -> None:
        policy = _pick_terminated_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET termination_date = DATE '2000-01-01' "
            "WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        assert result.critical_failure is True
        assert result.success is False

    def test_dq_tl01_fail_count_matches_seeded_count(self, bad_db: Path) -> None:
        """Corrupt exactly 3 policies (one ETL run) and confirm DQ-TL-01 finds exactly 3."""
        conn = duckdb.connect(str(bad_db))
        # Pick one _etl_run_id so we corrupt exactly 3 rows, not N_runs × 3
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_term_policies LIMIT 1"
        ).fetchone()[0]
        pids = conn.execute(
            "SELECT policy_id FROM silver_term_policies "
            "WHERE status_code != 'IF' AND _etl_run_id = ? LIMIT 3",
            [etl_run_id],
        ).fetchall()
        conn.close()

        conn = duckdb.connect(str(bad_db))
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_term_policies SET termination_date = DATE '2000-01-01' "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        tl01 = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-01")
        assert tl01.fail_count == 3


# ---------------------------------------------------------------------------
# DQ-TL-03 HALT: face_amount = 0
# ---------------------------------------------------------------------------


class TestDQTL03Halt:
    """Verify DQ-TL-03 halts when face_amount <= 0."""

    def test_halt_raises_when_face_amount_zero(self, bad_db: Path) -> None:
        policy = _get_first_term_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET face_amount = 0.0 WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        with pytest.raises(DQCriticalFailure) as exc_info:
            run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=True)
        assert exc_info.value.check_id in ("DQ-TL-01", "DQ-TL-03")

    def test_halt_raises_when_face_amount_negative(self, bad_db: Path) -> None:
        policy = _get_first_term_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET face_amount = -1000.0 WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        with pytest.raises(DQCriticalFailure):
            run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=True)

    def test_tl03_check_finds_zero_face_amount(self, bad_db: Path) -> None:
        conn = duckdb.connect(str(bad_db))
        # Corrupt exactly 2 rows in one ETL run to keep fail_count predictable
        etl_run_id = conn.execute(
            "SELECT _etl_run_id FROM silver_term_policies LIMIT 1"
        ).fetchone()[0]
        pids = conn.execute(
            "SELECT policy_id FROM silver_term_policies WHERE _etl_run_id = ? LIMIT 2",
            [etl_run_id],
        ).fetchall()
        conn.close()

        conn = duckdb.connect(str(bad_db))
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_term_policies SET face_amount = 0.0 "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        tl03 = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-03")
        assert tl03.fail_count == 2
        assert not tl03.passed

    def test_critical_failure_written_to_summary_before_raise(self, bad_db: Path) -> None:
        """Summary must be persisted even when DQCriticalFailure is raised."""
        policy = _get_first_term_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET face_amount = 0.0 WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        try:
            run_dq_checks("TERM", bad_db, run_id, halt_on_critical=True)
        except DQCriticalFailure:
            pass

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_run_summary WHERE study_run_id = ?",
                [run_id],
            ).fetchone()
        finally:
            conn.close()

        assert rows[0] == 1, "DQ summary not written before raising DQCriticalFailure"


# ---------------------------------------------------------------------------
# DQ-TL-05 HALT: termination_cause_code null iff status_code = IF
# ---------------------------------------------------------------------------


class TestDQTL05Halt:
    """Verify DQ-TL-05 halts on cause/status inconsistency."""

    def test_if_policy_with_cause_code_triggers_halt(self, bad_db: Path) -> None:
        policy = _pick_if_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET termination_cause_code = 'LAPSE' "
            "WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        tl05 = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-05")
        assert not tl05.passed
        assert tl05.fail_count >= 1
        assert result.critical_failure

    def test_terminated_policy_with_null_cause_triggers_halt(self, bad_db: Path) -> None:
        policy = _pick_terminated_policy(bad_db)
        conn = duckdb.connect(str(bad_db))
        conn.execute(
            "UPDATE silver_term_policies SET termination_cause_code = NULL "
            "WHERE policy_id = ?",
            [policy["policy_id"]],
        )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        tl05 = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-05")
        assert not tl05.passed


# ---------------------------------------------------------------------------
# DQ-TL-15 ERROR (quarantine): ci_rider_sum_assured > face_amount
# ---------------------------------------------------------------------------


class TestDQTL15Quarantine:
    """Verify DQ-TL-15 quarantines records without halting."""

    def test_ci_rider_exceeds_face_triggers_quarantine(self, bad_db: Path) -> None:
        ci_policy = _pick_ci_policy(bad_db)
        if ci_policy is None:
            pytest.skip("No CI rider policies found")

        conn = duckdb.connect(str(bad_db))
        face = conn.execute(
            "SELECT face_amount FROM silver_term_policies WHERE policy_id = ?",
            [ci_policy["policy_id"]],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_term_policies SET ci_rider_sum_assured = ? "
            "WHERE policy_id = ?",
            [face + 1.0, ci_policy["policy_id"]],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("TERM", bad_db, run_id, halt_on_critical=True)

        tl15 = next(cr for cr in result.check_results if cr.check_id == "DQ-TL-15")
        assert not tl15.passed
        assert tl15.fail_count >= 1
        # Must NOT halt
        assert not result.critical_failure
        assert result.success

    def test_ci_rider_exceeds_face_written_to_quarantine(self, bad_db: Path) -> None:
        ci_policy = _pick_ci_policy(bad_db)
        if ci_policy is None:
            pytest.skip("No CI rider policies found")

        conn = duckdb.connect(str(bad_db))
        face = conn.execute(
            "SELECT face_amount FROM silver_term_policies WHERE policy_id = ?",
            [ci_policy["policy_id"]],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_term_policies SET ci_rider_sum_assured = ? "
            "WHERE policy_id = ?",
            [face * 2.0, ci_policy["policy_id"]],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("TERM", bad_db, run_id, halt_on_critical=True)

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_quarantine "
                "WHERE study_run_id = ? AND check_id = 'DQ-TL-15' "
                "AND policy_id = ?",
                [run_id, ci_policy["policy_id"]],
            ).fetchone()
        finally:
            conn.close()

        assert row[0] >= 1, "DQ-TL-15 failure not written to gold_dq_quarantine"

    def test_quarantined_record_not_in_halt_ids(self, bad_db: Path) -> None:
        """DQ-TL-15 failures must reduce records_quarantined, not records_halted."""
        ci_policy = _pick_ci_policy(bad_db)
        if ci_policy is None:
            pytest.skip("No CI rider policies found")

        conn = duckdb.connect(str(bad_db))
        face = conn.execute(
            "SELECT face_amount FROM silver_term_policies WHERE policy_id = ?",
            [ci_policy["policy_id"]],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_term_policies SET ci_rider_sum_assured = ? "
            "WHERE policy_id = ?",
            [face + 1.0, ci_policy["policy_id"]],
        )
        conn.close()

        result = run_dq_checks("TERM", bad_db, _etl_run_id_of(bad_db), halt_on_critical=False)
        assert result.records_quarantined >= 1


# ---------------------------------------------------------------------------
# Ten seeded bad records — all surfaced with halt_on_critical=False
# ---------------------------------------------------------------------------


class TestTenSeededBadRecords:
    """Seed 10 distinct violations and confirm all are surfaced together."""

    @pytest.fixture()
    def ten_bad_db(self, tmp_path: Path, prod_db: Path) -> Path:
        dest = tmp_path / "ten_bad.duckdb"
        shutil.copy2(prod_db, dest)
        _reset_dq_tables(dest)

        conn = duckdb.connect(str(dest))

        # 1-3: DQ-TL-01 — issue_date > termination_date (3 records)
        pids = conn.execute(
            "SELECT policy_id FROM silver_term_policies WHERE status_code != 'IF' LIMIT 3"
        ).fetchall()
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_term_policies SET termination_date = DATE '2000-01-01' "
                "WHERE policy_id = ?",
                [pid],
            )

        # 4-5: DQ-TL-03 — face_amount = 0 (2 records)
        pids2 = conn.execute(
            "SELECT policy_id FROM silver_term_policies "
            "WHERE policy_id NOT IN (SELECT policy_id FROM silver_term_policies WHERE status_code != 'IF' LIMIT 3) "
            "LIMIT 2"
        ).fetchall()
        for (pid,) in pids2:
            conn.execute(
                "UPDATE silver_term_policies SET face_amount = 0.0 WHERE policy_id = ?",
                [pid],
            )

        # 6-7: DQ-TL-04 — issue_age_anb out of range (2 records)
        pids3 = conn.execute(
            "SELECT policy_id FROM silver_term_policies "
            "WHERE policy_id NOT IN (SELECT policy_id FROM silver_term_policies WHERE status_code != 'IF' LIMIT 3) "
            "LIMIT 2 OFFSET 2"
        ).fetchall()
        for (pid,) in pids3:
            conn.execute(
                "UPDATE silver_term_policies SET issue_age_anb = 17 WHERE policy_id = ?",
                [pid],
            )

        # 8-10: DQ-TL-15 — ci_rider_sum_assured > face_amount (3 records, if available)
        ci_pids = conn.execute(
            "SELECT policy_id, face_amount FROM silver_term_policies "
            "WHERE ci_rider_flag = TRUE LIMIT 3"
        ).fetchall()
        for (pid, fa) in ci_pids:
            conn.execute(
                "UPDATE silver_term_policies SET ci_rider_sum_assured = ? "
                "WHERE policy_id = ?",
                [fa + 1.0, pid],
            )

        conn.close()
        return dest

    def test_all_failures_surfaced_without_halt(self, ten_bad_db: Path) -> None:
        result = run_dq_checks("TERM", ten_bad_db, _etl_run_id_of(ten_bad_db), halt_on_critical=False)

        results_by_id = {cr.check_id: cr for cr in result.check_results}

        tl01 = results_by_id["DQ-TL-01"]
        assert not tl01.passed, "DQ-TL-01 should fail"
        assert tl01.fail_count >= 3

        tl03 = results_by_id["DQ-TL-03"]
        assert not tl03.passed, "DQ-TL-03 should fail"
        assert tl03.fail_count >= 2

        tl04 = results_by_id["DQ-TL-04"]
        assert not tl04.passed, "DQ-TL-04 should fail"
        assert tl04.fail_count >= 2

        tl15 = results_by_id["DQ-TL-15"]
        assert not tl15.passed, "DQ-TL-15 should fail"
        assert tl15.fail_count >= 1

    def test_critical_failure_set_from_halt_checks(self, ten_bad_db: Path) -> None:
        result = run_dq_checks("TERM", ten_bad_db, _etl_run_id_of(ten_bad_db), halt_on_critical=False)
        assert result.critical_failure, "critical_failure should be True due to DQ-TL-01/03"

    def test_no_exception_when_halt_on_critical_false(self, ten_bad_db: Path) -> None:
        """Should complete without raising even with HALT-level failures."""
        result = run_dq_checks("TERM", ten_bad_db, _etl_run_id_of(ten_bad_db), halt_on_critical=False)
        assert result is not None

    def test_exception_raised_when_halt_on_critical_true(self, ten_bad_db: Path) -> None:
        with pytest.raises(DQCriticalFailure):
            run_dq_checks("TERM", ten_bad_db, _etl_run_id_of(ten_bad_db), halt_on_critical=True)

    def test_non_halt_failures_quarantined(self, ten_bad_db: Path) -> None:
        """DQ-TL-04 and DQ-TL-15 failures must appear in gold_dq_quarantine."""
        run_id = _etl_run_id_of(ten_bad_db)
        run_dq_checks("TERM", ten_bad_db, run_id, halt_on_critical=False)

        conn = duckdb.connect(str(ten_bad_db), read_only=True)
        try:
            rows = conn.execute(
                "SELECT check_id, COUNT(*) AS cnt FROM gold_dq_quarantine "
                "WHERE study_run_id = ? GROUP BY check_id ORDER BY check_id",
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        check_quarantine = {r[0]: r[1] for r in rows}
        assert "DQ-TL-04" in check_quarantine, "DQ-TL-04 failures not quarantined"
        assert "DQ-TL-15" in check_quarantine, "DQ-TL-15 failures not quarantined"
        assert check_quarantine["DQ-TL-04"] >= 2
        assert check_quarantine["DQ-TL-15"] >= 1


# ---------------------------------------------------------------------------
# override_quarantine_record
# ---------------------------------------------------------------------------


class TestOverrideQuarantine:
    """Verify override_quarantine_record updates the quarantine table."""

    @pytest.fixture()
    def db_with_quarantine(self, bad_db: Path) -> tuple[Path, str]:
        """Seed one DQ-TL-15 violation and return (db_path, quarantine_id)."""
        ci_policy = _pick_ci_policy(bad_db)
        if ci_policy is None:
            pytest.skip("No CI rider policies found")

        conn = duckdb.connect(str(bad_db))
        face = conn.execute(
            "SELECT face_amount FROM silver_term_policies WHERE policy_id = ?",
            [ci_policy["policy_id"]],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_term_policies SET ci_rider_sum_assured = ? WHERE policy_id = ?",
            [face + 1.0, ci_policy["policy_id"]],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        run_dq_checks("TERM", bad_db, run_id, halt_on_critical=False)

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            qid = conn.execute(
                "SELECT quarantine_id FROM gold_dq_quarantine "
                "WHERE study_run_id = ? AND check_id = 'DQ-TL-15' LIMIT 1",
                [run_id],
            ).fetchone()[0]
        finally:
            conn.close()

        return bad_db, qid

    def test_override_returns_true_for_valid_id(
        self, db_with_quarantine: tuple[Path, str]
    ) -> None:
        db_path, qid = db_with_quarantine
        result = override_quarantine_record(qid, "ACTUARY_001", "CI SA matches policy intent", db_path)
        assert result is True

    def test_override_returns_false_for_unknown_id(self, bad_db: Path) -> None:
        result = override_quarantine_record(str(uuid.uuid4()), "ACTUARY_001", "test", bad_db)
        assert result is False

    def test_override_sets_flag_and_fields(
        self, db_with_quarantine: tuple[Path, str]
    ) -> None:
        db_path, qid = db_with_quarantine
        override_quarantine_record(qid, "ACTUARY_001", "Reviewed and accepted", db_path)

        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            row = conn.execute(
                "SELECT actuary_override_flag, override_justification, override_actuary_id "
                "FROM gold_dq_quarantine WHERE quarantine_id = ?",
                [qid],
            ).fetchone()
        finally:
            conn.close()

        assert row[0] is True
        assert row[1] == "Reviewed and accepted"
        assert row[2] == "ACTUARY_001"
