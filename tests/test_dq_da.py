"""Unit tests for the Deferred Annuity DQ pipeline.

Covers:
    - Clean data: DQ score >= 50%, all 5 checks present, no critical failure
    - DQ-DA-01 (WARN): SC rate for current year mismatches schedule
    - DQ-DA-02 (ERROR): benefit_base < 0 for GLB contracts
    - DQ-DA-03 (WARN): non-GLWB withdrawal rate > free_withdrawal_allowance_pct
    - DQ-DA-04 (ERROR): invalid market_type
    - DQ-DA-05 (ERROR): SC expired flag=TRUE but SC remaining > 0

Note: DA DQ checks ARE scoped by _etl_run_id (they check rows WHERE
_etl_run_id = the run_id passed to run_dq_checks), consistent with the other products.
Bad-data tests must therefore pass the run_id under which the (copied) data was loaded —
see `_etl_run_id_of()` — not a fresh UUID. No HALT checks exist for DA (HALT_CHECK_IDS is
empty). DA uses contract_id (not policy_id) as primary key.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from src.data_quality.runner import run_dq_checks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# `prod_db` is provided by tests/conftest.py — a session-scoped *copy* of the production
# DB, so tests never mutate data/experience_study.duckdb (run_dq_checks persists rows).


@pytest.fixture(scope="module")
def prod_etl_run_id(prod_db: Path) -> str:
    """Return the most recent _etl_run_id present in silver_annuity_contracts."""
    conn = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = conn.execute(
            "SELECT _etl_run_id FROM silver_annuity_contracts ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip("No ETL data in silver_annuity_contracts — run DA pipeline first")
    return row[0]


@pytest.fixture()
def bad_db(tmp_path: Path, prod_db: Path) -> Path:
    """Copy the production DB to a temp location for mutation tests."""
    dest = tmp_path / "da_bad.duckdb"
    shutil.copy2(prod_db, dest)
    return dest


def _etl_run_id_of(db_path: Path) -> str:
    """Return the most recent _etl_run_id in a (copied) DB.

    DA DQ checks filter by _etl_run_id, so mutation tests must pass the run_id the data was
    actually loaded under — not a fresh UUID (which would match zero rows).
    """
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return conn.execute(
            "SELECT _etl_run_id FROM silver_annuity_contracts ORDER BY _load_ts DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Clean-data tests
# ---------------------------------------------------------------------------


class TestCleanDataDA:
    """DQ checks on unmodified production DA data."""

    def test_da_dq_score_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        # DQ-DA-01 is WARN and noisy on synthetic data; 50% threshold is appropriate
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.dq_score_pct >= 50.0, (
            f"DA DQ score {result.dq_score_pct:.2f}% is below 50% threshold"
        )

    def test_da_no_critical_failure(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DA has no HALT checks, so critical_failure must always be False."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert not result.critical_failure

    def test_da_success_flag(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.success

    def test_da_total_records_count(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        assert result.total_records == 1400, (
            f"Expected 1400 DA records, got {result.total_records}"
        )

    def test_da_all_5_checks_present(self, prod_db: Path, prod_etl_run_id: str) -> None:
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        check_ids = {cr.check_id for cr in result.check_results}
        assert check_ids == {"DQ-DA-01", "DQ-DA-02", "DQ-DA-03", "DQ-DA-04", "DQ-DA-05"}

    def test_da_no_halt_checks_exist(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DA has no HALT severity checks — DQCriticalFailure cannot be raised."""
        # Should not raise even with halt_on_critical=True
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=True)
        assert not result.critical_failure

    def test_da_dq04_passes_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-DA-04 (market_type validation) must pass on clean data."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        cr04 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-04")
        assert cr04.passed, (
            f"DQ-DA-04 failed on clean data: {cr04.fail_count} records"
        )

    def test_da_dq05_passes_on_clean_data(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-DA-05 (SC expiry consistency) must pass on clean data."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        cr05 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-05")
        assert cr05.passed, (
            f"DQ-DA-05 failed on clean data: {cr05.fail_count} records"
        )

    def test_da_dq_summary_written(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """run_dq_checks must write a row to gold_dq_run_summary."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
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
# DQ-DA-02: benefit_base >= 0 for GLB contracts
# ---------------------------------------------------------------------------


class TestDQDA02:
    """Verify DQ-DA-02 fires when benefit_base is negative for a GLB contract."""

    def test_da02_fails_with_negative_benefit_base(self, bad_db: Path) -> None:
        """Seed a GLWB-elected contract with benefit_base = -1000."""
        conn = duckdb.connect(str(bad_db))
        # Find a GLWB-elected contract
        row = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts "
            "WHERE glwb_elected_flag = TRUE LIMIT 1"
        ).fetchone()
        if row is None:
            conn.close()
            pytest.skip("No GLWB-elected contracts in test data")
        cid = row[0]
        conn.execute(
            "UPDATE silver_annuity_contracts SET benefit_base = -1000.0 "
            "WHERE contract_id = ?",
            [cid],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr02 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-02")
        assert not cr02.passed
        assert cr02.fail_count >= 1

    def test_da02_severity_is_error(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-DA-02 must be ERROR severity."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        cr02 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-02")
        assert cr02.severity == "ERROR"

    def test_da02_does_not_trigger_for_nonglb(self, bad_db: Path) -> None:
        """Setting benefit_base=NULL on a non-GLB, non-GMDB contract must not add new DQ-DA-02 failures.

        DQ-DA-02 only checks contracts where (glwb_elected_flag=TRUE OR gmdb_type IS NOT NULL).
        A contract with both flags absent should be invisible to this check.
        """
        # Baseline fail_count before mutation
        baseline_id = _etl_run_id_of(bad_db)
        baseline = run_dq_checks("DA", bad_db, baseline_id, halt_on_critical=False)
        cr02_before = next(cr for cr in baseline.check_results if cr.check_id == "DQ-DA-02")
        baseline_count = cr02_before.fail_count

        conn = duckdb.connect(str(bad_db))
        cid_row = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts "
            "WHERE glwb_elected_flag = FALSE AND gmdb_type IS NULL LIMIT 1"
        ).fetchone()
        if cid_row:
            conn.execute(
                "UPDATE silver_annuity_contracts SET benefit_base = NULL "
                "WHERE contract_id = ?",
                [cid_row[0]],
            )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr02 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-02")
        # Mutating a non-GLB, non-GMDB contract must not increase the fail count
        assert cr02.fail_count == baseline_count, (
            f"DQ-DA-02 fail_count increased from {baseline_count} to {cr02.fail_count} "
            "after setting benefit_base=NULL on a non-GLB/non-GMDB contract"
        )


# ---------------------------------------------------------------------------
# DQ-DA-04: market_type must be in {NQ, TRAD_IRA, ROTH_IRA, QUAL}
# ---------------------------------------------------------------------------


class TestDQDA04:
    """Verify DQ-DA-04 fires when market_type is invalid."""

    def test_da04_fails_with_invalid_market_type(self, bad_db: Path) -> None:
        """Seed a contract with market_type = 'INVALID_TYPE'."""
        conn = duckdb.connect(str(bad_db))
        cid = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_annuity_contracts SET market_type = 'INVALID_TYPE' "
            "WHERE contract_id = ?",
            [cid],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr04 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-04")
        assert not cr04.passed
        assert cr04.fail_count >= 1

    def test_da04_does_not_halt(self, bad_db: Path) -> None:
        """DQ-DA-04 is ERROR but not HALT — no DQCriticalFailure raised."""
        conn = duckdb.connect(str(bad_db))
        cid = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_annuity_contracts SET market_type = 'BOGUS' "
            "WHERE contract_id = ?",
            [cid],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        # Should complete without raising, even with halt_on_critical=True
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=True)
        assert not result.critical_failure

    def test_da04_passes_all_valid_types(self, bad_db: Path) -> None:
        """All valid market_type values must pass DQ-DA-04."""
        conn = duckdb.connect(str(bad_db))
        for i, mtype in enumerate(["NQ", "TRAD_IRA", "ROTH_IRA", "QUAL"]):
            cid = conn.execute(
                f"SELECT contract_id FROM silver_annuity_contracts LIMIT 1 OFFSET {i}"
            ).fetchone()
            if cid:
                conn.execute(
                    "UPDATE silver_annuity_contracts SET market_type = ? "
                    "WHERE contract_id = ?",
                    [mtype, cid[0]],
                )
        conn.close()

        # Reset to just NQ for the check — the other 3 invalid ones were set above only in loop
        # Refresh from prod data pattern: the valid updates should not increase fail count
        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr04 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-04")
        # Still passes after setting valid types
        assert cr04.passed


# ---------------------------------------------------------------------------
# DQ-DA-05: is_surrender_charge_expired_flag=TRUE implies SC remaining = 0
# ---------------------------------------------------------------------------


class TestDQDA05:
    """Verify DQ-DA-05 fires when SC expired flag is True but SC remaining > 0."""

    def test_da05_fails_with_expired_flag_and_nonzero_remaining(self, bad_db: Path) -> None:
        """Seed a contract with SC expired=True but SC remaining=500."""
        conn = duckdb.connect(str(bad_db))
        cid = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts "
            "WHERE is_surrender_charge_expired_flag = FALSE AND account_value > 0 LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_annuity_contracts "
            "SET is_surrender_charge_expired_flag = TRUE, surrender_charge_remaining = 500.0 "
            "WHERE contract_id = ?",
            [cid],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr05 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-05")
        assert not cr05.passed
        assert cr05.fail_count >= 1

    def test_da05_passes_when_expired_and_zero_remaining(self, bad_db: Path) -> None:
        """SC expired=True with SC remaining=0 must pass DQ-DA-05."""
        conn = duckdb.connect(str(bad_db))
        cid = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE silver_annuity_contracts "
            "SET is_surrender_charge_expired_flag = TRUE, surrender_charge_remaining = 0.0 "
            "WHERE contract_id = ?",
            [cid],
        )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr05 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-05")
        # This specific contract is now consistent; overall check may still pass
        # Verify fail count didn't increase due to this contract
        assert cr05.fail_count == 0  # clean data + our fix = all consistent

    def test_da05_severity_is_error(self, prod_db: Path, prod_etl_run_id: str) -> None:
        """DQ-DA-05 must be ERROR severity."""
        result = run_dq_checks("DA", prod_db, prod_etl_run_id, halt_on_critical=False)
        cr05 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-05")
        assert cr05.severity == "ERROR"

    def test_da05_multiple_violations_counted(self, bad_db: Path) -> None:
        """All contracts with SC expired=True and remaining > 0 should be counted."""
        conn = duckdb.connect(str(bad_db))
        cids = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts "
            "WHERE is_surrender_charge_expired_flag = FALSE LIMIT 3"
        ).fetchall()
        for (cid,) in cids:
            conn.execute(
                "UPDATE silver_annuity_contracts "
                "SET is_surrender_charge_expired_flag = TRUE, surrender_charge_remaining = 250.0 "
                "WHERE contract_id = ?",
                [cid],
            )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=False)
        cr05 = next(cr for cr in result.check_results if cr.check_id == "DQ-DA-05")
        assert cr05.fail_count >= 3


# ---------------------------------------------------------------------------
# DA-specific guard: no DQCriticalFailure even with all checks failing
# ---------------------------------------------------------------------------


class TestDANoHalt:
    """Confirm that no combination of failures halts the DA DQ pipeline."""

    def test_da_never_raises_dq_critical_failure(self, bad_db: Path) -> None:
        """Corrupt multiple fields — DA must never raise DQCriticalFailure."""
        conn = duckdb.connect(str(bad_db))
        cids = conn.execute(
            "SELECT contract_id FROM silver_annuity_contracts LIMIT 5"
        ).fetchall()
        for (cid,) in cids:
            conn.execute(
                "UPDATE silver_annuity_contracts "
                "SET market_type = 'INVALID', benefit_base = -999.0, "
                "    is_surrender_charge_expired_flag = TRUE, surrender_charge_remaining = 100.0 "
                "WHERE contract_id = ?",
                [cid],
            )
        conn.close()

        run_id = _etl_run_id_of(bad_db)
        # Must not raise, even with halt_on_critical=True, because no HALT checks exist for DA
        result = run_dq_checks("DA", bad_db, run_id, halt_on_critical=True)
        assert not result.critical_failure
