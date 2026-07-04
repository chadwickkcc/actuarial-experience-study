"""Unit tests for the Whole Life DQ pipeline.

Covers:
    - Clean data: DQ score > 95%, all 4 checks present, no critical failure
    - DQ-WL-01 HALT: guaranteed_cash_value < 0 or (IF and GCV > face)
    - DQ-WL-02 WARN: policy_loan_balance > guaranteed_cash_value
    - DQ-WL-03 HALT: non_forfeiture_status RPU/ETT and termination_cause_code = LAPSE
    - DQ-WL-04 WARN: par policies with dividend_on_deposit_bal < 0

Note: WL/UL DQ checks filter by _etl_run_id = study_run_id, so seeded tests must
pass the actual _etl_run_id from the bad DB as the study_run_id argument.
"""

from __future__ import annotations

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
    """Return the most recent _etl_run_id present in silver_wl_policies."""
    conn = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = conn.execute(
            "SELECT _etl_run_id FROM silver_wl_policies ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip("No ETL data in silver_wl_policies — run WL pipeline first")
    return row[0]


@pytest.fixture()
def bad_db(tmp_path: Path, prod_db: Path) -> Path:
    """Copy the production DB to a temp location for mutation tests."""
    dest = tmp_path / "wl_bad.duckdb"
    shutil.copy2(prod_db, dest)
    return dest


def _get_wl_etl_run_id(db_path: Path) -> str:
    """Fetch the _etl_run_id used in silver_wl_policies (needed as study_run_id for DQ)."""
    conn = duckdb.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT _etl_run_id FROM silver_wl_policies LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Clean-data tests
# ---------------------------------------------------------------------------


class TestCleanDataWL:
    """DQ checks on unmodified production WL data."""

    def test_wl_dq_score_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.dq_score_pct > 95.0, (
            f"WL DQ score {result.dq_score_pct:.2f}% is below 95% threshold"
        )

    def test_wl_no_critical_failure(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_wl_success_flag(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.success

    def test_wl_total_records_count(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.total_records == 2800, (
            f"Expected 2800 WL records, got {result.total_records}"
        )

    def test_wl_all_4_checks_present(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        check_ids = {cr.check_id for cr in result.check_results}
        assert check_ids == {"DQ-WL-01", "DQ-WL-02", "DQ-WL-03", "DQ-WL-04"}

    def test_wl_halt_checks_pass_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-WL-01 and DQ-WL-03 (HALT checks) must pass on clean data."""
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
        for cr in result.check_results:
            if cr.check_id in ("DQ-WL-01", "DQ-WL-03"):
                assert cr.passed, (
                    f"Halt check {cr.check_id} failed on clean data: {cr.fail_count} records"
                )

    def test_wl_dq_summary_written(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """run_dq_checks must write a row to gold_dq_run_summary."""
        result = run_dq_checks("WL", prod_db, prod_etl_run_id, halt_on_critical=False)
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
# DQ-WL-01 HALT: guaranteed_cash_value < 0 OR (IF AND GCV > face_amount)
# ---------------------------------------------------------------------------


class TestDQWL01Halt:
    """Verify DQ-WL-01 halts when GCV is negative or exceeds face for IF policies."""

    def test_wl01_raises_on_halt_on_critical(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND status_code = 'IF' LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = -500.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        with pytest.raises(DQCriticalFailure) as exc_info:
            run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=True)
        assert exc_info.value.check_id == "DQ-WL-01"

    def test_wl01_critical_failure_flag(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND status_code = 'IF' LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = -500.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        assert result.critical_failure is True

    def test_wl01_fail_count_matches_seeded(self, bad_db: Path) -> None:
        """Seed 2 negative GCV + 1 GCV > face; confirm DQ-WL-01 finds exactly 3."""
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        # 2 negative GCV
        pids_neg = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND status_code = 'IF' LIMIT 2",
            [etl_run_id],
        ).fetchall()
        for (pid,) in pids_neg:
            conn.execute(
                "UPDATE silver_wl_policies SET guaranteed_cash_value = -500.0 "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        # 1 GCV > face (pick a policy not already seeded negative)
        neg_pids = tuple(r[0] for r in pids_neg)
        row_exceed = conn.execute(
            f"""
            SELECT policy_id, face_amount FROM silver_wl_policies
            WHERE _etl_run_id = ? AND status_code = 'IF'
              AND policy_id NOT IN ({','.join("?" for _ in neg_pids)})
            LIMIT 1
            """,
            [etl_run_id, *neg_pids],
        ).fetchone()
        pid_exceed, face = row_exceed
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [face * 2.0, pid_exceed, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl01 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-01")
        assert wl01.fail_count == 3

    def test_wl01_summary_persisted_after_halt(self, bad_db: Path) -> None:
        """Summary must be persisted even when DQCriticalFailure is raised."""
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = -500.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        try:
            run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=True)
        except DQCriticalFailure:
            pass

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_run_summary WHERE study_run_id = ?",
                [etl_run_id],
            ).fetchone()
        finally:
            conn.close()
        assert row[0] >= 1, "DQ summary not written before raising DQCriticalFailure"

    def test_wl01_gcv_negative_detected(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = -1.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl01 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-01")
        assert wl01.fail_count >= 1

    def test_wl01_if_gcv_exceeds_face_detected(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        row = conn.execute(
            "SELECT policy_id, face_amount FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND status_code = 'IF' LIMIT 1",
            [etl_run_id],
        ).fetchone()
        pid, face = row
        conn.execute(
            "UPDATE silver_wl_policies SET guaranteed_cash_value = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [face * 2.0, pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl01 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-01")
        assert wl01.fail_count >= 1


# ---------------------------------------------------------------------------
# DQ-WL-02 WARN: policy_loan_balance > guaranteed_cash_value
# ---------------------------------------------------------------------------


class TestDQWL02Quarantine:
    """Verify DQ-WL-02 quarantines without halting when loan exceeds GCV."""

    def test_wl02_quarantine_on_excess_loan(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pids = conn.execute(
            "SELECT policy_id, guaranteed_cash_value FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND guaranteed_cash_value > 0 LIMIT 3",
            [etl_run_id],
        ).fetchall()
        for pid, gcv in pids:
            conn.execute(
                "UPDATE silver_wl_policies SET policy_loan_balance = ? "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [gcv + 1000.0, pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl02 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-02")
        assert wl02.fail_count == 3

    def test_wl02_not_halt(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        row = conn.execute(
            "SELECT policy_id, guaranteed_cash_value FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND guaranteed_cash_value > 0 LIMIT 1",
            [etl_run_id],
        ).fetchone()
        conn.execute(
            "UPDATE silver_wl_policies SET policy_loan_balance = ? "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[1] + 1000.0, row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_wl02_quarantine_records_written(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pids = conn.execute(
            "SELECT policy_id, guaranteed_cash_value FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND guaranteed_cash_value > 0 LIMIT 3",
            [etl_run_id],
        ).fetchall()
        for pid, gcv in pids:
            conn.execute(
                "UPDATE silver_wl_policies SET policy_loan_balance = ? "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [gcv + 1000.0, pid, etl_run_id],
            )
        conn.close()

        run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)

        conn = duckdb.connect(str(bad_db), read_only=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM gold_dq_quarantine "
                "WHERE check_id = 'DQ-WL-02' AND study_run_id = ?",
                [etl_run_id],
            ).fetchone()
        finally:
            conn.close()
        assert row[0] >= 3


# ---------------------------------------------------------------------------
# DQ-WL-03 HALT: non_forfeiture_status RPU/ETT and termination_cause_code = LAPSE
# ---------------------------------------------------------------------------


class TestDQWL03Halt:
    """Verify DQ-WL-03 halts when RPU/ETT policies show LAPSE cause."""

    def test_wl03_raises_on_rpu_lapse(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pids = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? LIMIT 2",
            [etl_run_id],
        ).fetchall()
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_wl_policies "
                "SET non_forfeiture_status = 'RPU', termination_cause_code = 'LAPSE' "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        conn.close()

        with pytest.raises(DQCriticalFailure) as exc_info:
            run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=True)
        assert exc_info.value.check_id == "DQ-WL-03"

    def test_wl03_critical_failure_flag(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies "
            "SET non_forfeiture_status = 'RPU', termination_cause_code = 'LAPSE' "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        assert result.critical_failure is True

    def test_wl03_rpu_detected(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies "
            "SET non_forfeiture_status = 'RPU', termination_cause_code = 'LAPSE' "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl03 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-03")
        assert wl03.fail_count >= 1

    def test_wl03_ett_detected(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies "
            "SET non_forfeiture_status = 'ETT', termination_cause_code = 'LAPSE' "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl03 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-03")
        assert wl03.fail_count >= 1

    def test_wl03_rpu_without_lapse_passes(self, bad_db: Path) -> None:
        """RPU with SURRENDER (not LAPSE) must not be flagged."""
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pid = conn.execute(
            "SELECT policy_id FROM silver_wl_policies WHERE _etl_run_id = ? LIMIT 1",
            [etl_run_id],
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_wl_policies "
            "SET non_forfeiture_status = 'RPU', termination_cause_code = 'SURRENDER' "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [pid, etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl03 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-03")
        assert wl03.fail_count == 0
        assert wl03.passed


# ---------------------------------------------------------------------------
# DQ-WL-04 WARN: par policies with dividend_on_deposit_bal < 0
# ---------------------------------------------------------------------------


class TestDQWL04Quarantine:
    """Verify DQ-WL-04 quarantines negative dividend balances on par policies."""

    def test_wl04_quarantine_on_negative_dividend(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        pids = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND participating_flag = TRUE LIMIT 2",
            [etl_run_id],
        ).fetchall()
        if not pids:
            pytest.skip("No participating WL policies found")
        for (pid,) in pids:
            conn.execute(
                "UPDATE silver_wl_policies SET dividend_on_deposit_bal = -100.0 "
                "WHERE policy_id = ? AND _etl_run_id = ?",
                [pid, etl_run_id],
            )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl04 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-04")
        assert wl04.fail_count == 2

    def test_wl04_not_halt(self, bad_db: Path) -> None:
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        row = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND participating_flag = TRUE LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No participating WL policies found")
        conn.execute(
            "UPDATE silver_wl_policies SET dividend_on_deposit_bal = -100.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_wl04_nonpar_ignored(self, bad_db: Path) -> None:
        """Negative dividend_on_deposit_bal on non-par policies must NOT be flagged."""
        etl_run_id = _get_wl_etl_run_id(bad_db)
        conn = duckdb.connect(str(bad_db))
        row = conn.execute(
            "SELECT policy_id FROM silver_wl_policies "
            "WHERE _etl_run_id = ? AND participating_flag = FALSE LIMIT 1",
            [etl_run_id],
        ).fetchone()
        if row is None:
            pytest.skip("No non-participating WL policies found")
        conn.execute(
            "UPDATE silver_wl_policies SET dividend_on_deposit_bal = -100.0 "
            "WHERE policy_id = ? AND _etl_run_id = ?",
            [row[0], etl_run_id],
        )
        conn.close()

        result = run_dq_checks("WL", bad_db, etl_run_id, halt_on_critical=False)
        wl04 = next(cr for cr in result.check_results if cr.check_id == "DQ-WL-04")
        assert wl04.fail_count == 0
        assert wl04.passed
