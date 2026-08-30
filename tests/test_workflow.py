"""Tests for src/assumptions/workflow.py — workflow logging + status transitions.

Covers:
- log_workflow_iteration() writes and returns a UUID row
- get_workflow_iterations() ordering + shape (no TEV/envelope columns)
- transition_assumption_set_status() incl. the APPROVED lock guard
- Absence of the retired TEV/envelope columns in the schema
"""
from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pytest

from src.assumptions.workflow import (
    get_next_iteration_number,
    get_workflow_iterations,
    log_workflow_iteration,
    transition_assumption_set_status,
)
from src.utils.db_init import init_database


# ---------------------------------------------------------------------------
# Fixture: isolated DB with correct schema
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Create a fresh DuckDB at tmp_path with the current schema."""
    db_path = tmp_path / "test_workflow.duckdb"
    init_database(db_path)
    return db_path


def _insert_assumption_set(db_path: Path, aset_id: str, status: str = "PROPOSED") -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            INSERT INTO gold_assumption_sets (
                assumption_set_id, version, status, effective_date,
                author_id, basis, source_study_run_id, yaml_file_path,
                created_ts
            ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """, [
            aset_id, 1, status, "2024-01-01", "actuary_1", "best-estimate",
            str(uuid.uuid4()), "",
        ])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# log_workflow_iteration
# ---------------------------------------------------------------------------

class TestLogWorkflowIteration:
    def test_inserts_row(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        iteration_id = log_workflow_iteration(
            db_path=tmp_db,
            workflow_session_id=session_id,
            iteration_number=1,
            assumption_set_id=aset_id,
            stage=2,
            action="SAVED",
            actuary_id="ACTUARY_1",
        )
        assert iteration_id  # non-empty UUID string

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT iteration_id, actuary_comment FROM gold_workflow_iterations "
            "WHERE iteration_id = ?",
            [iteration_id]
        ).fetchone()
        con.close()
        assert row is not None

    def test_comment_stored(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        iteration_id = log_workflow_iteration(
            db_path=tmp_db,
            workflow_session_id=session_id,
            iteration_number=1,
            assumption_set_id=aset_id,
            stage=3,
            action="SUBMITTED_S4",
            actuary_id="ACTUARY_1",
            actuary_comment="ready for sign-off",
        )
        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT action, actuary_comment FROM gold_workflow_iterations "
            "WHERE iteration_id = ?",
            [iteration_id]
        ).fetchone()
        con.close()
        assert row == ("SUBMITTED_S4", "ready for sign-off")

    def test_no_tev_columns_in_schema(self, tmp_db):
        """The TEV/envelope columns were retired in the demo refresh (P2)."""
        con = duckdb.connect(str(tmp_db), read_only=True)
        cols = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'gold_workflow_iterations'"
        ).fetchall()
        con.close()
        col_names = {r[0] for r in cols}
        for retired in (
            "tev_baseline_run_id", "total_tev", "delta_tev_vs_prior",
            "envelope_run_flag", "optimiser_run_flag",
        ):
            assert retired not in col_names

    def test_returns_uuid_string(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        iteration_id = log_workflow_iteration(
            db_path=tmp_db,
            workflow_session_id=session_id,
            iteration_number=1,
            assumption_set_id=aset_id,
            stage=2,
            action="SAVED",
            actuary_id="ACTUARY_1",
        )
        # Must be a valid UUID string
        uuid.UUID(iteration_id)


# ---------------------------------------------------------------------------
# get_workflow_iterations
# ---------------------------------------------------------------------------

class TestGetWorkflowIterations:
    def test_returns_list_of_dicts(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        log_workflow_iteration(
            db_path=tmp_db, workflow_session_id=session_id, iteration_number=1,
            assumption_set_id=aset_id, stage=2, action="SAVED", actuary_id="A1",
        )
        rows = get_workflow_iterations(tmp_db, session_id)
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert isinstance(rows[0], dict)

    def test_does_not_include_tev_keys(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        log_workflow_iteration(
            db_path=tmp_db, workflow_session_id=session_id, iteration_number=1,
            assumption_set_id=aset_id, stage=2, action="SAVED", actuary_id="A1",
        )
        rows = get_workflow_iterations(tmp_db, session_id)
        for retired in ("total_tev", "delta_tev_vs_prior", "envelope_run_flag"):
            assert retired not in rows[0]

    def test_ordered_by_iteration_number(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        for n in [3, 1, 2]:
            log_workflow_iteration(
                db_path=tmp_db, workflow_session_id=session_id, iteration_number=n,
                assumption_set_id=aset_id, stage=2, action="SAVED", actuary_id="A1",
            )
        rows = get_workflow_iterations(tmp_db, session_id)
        assert [r["iteration_number"] for r in rows] == [1, 2, 3]

    def test_empty_for_unknown_session(self, tmp_db):
        rows = get_workflow_iterations(tmp_db, str(uuid.uuid4()))
        assert rows == []


# ---------------------------------------------------------------------------
# get_next_iteration_number
# ---------------------------------------------------------------------------

class TestGetNextIterationNumber:
    def test_returns_1_for_new_session(self, tmp_db):
        assert get_next_iteration_number(tmp_db, str(uuid.uuid4())) == 1

    def test_increments_correctly(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        for n in [1, 2, 3]:
            log_workflow_iteration(
                db_path=tmp_db, workflow_session_id=session_id, iteration_number=n,
                assumption_set_id=aset_id, stage=2, action="SAVED", actuary_id="A1",
            )
        assert get_next_iteration_number(tmp_db, session_id) == 4


# ---------------------------------------------------------------------------
# Retired legacy approvals table
# ---------------------------------------------------------------------------

class TestLegacyApprovalsRetired:
    def test_gold_assumption_approvals_not_created(self, tmp_db):
        """The Phase-2 legacy summary table is retired (demo refresh P2)."""
        con = duckdb.connect(str(tmp_db), read_only=True)
        tables = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        con.close()
        assert "gold_assumption_approvals" not in tables

    def test_no_source_references_legacy_writer(self):
        """No src/ or ui/ code may write or read the retired approvals table."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rln", "--include=*.py", "--include=*.yaml", "--include=*.j2",
             "gold_assumption_approvals", "src", "ui", "config", "scripts"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        offenders = [
            line for line in result.stdout.splitlines()
            # the one legitimate mention: the migration script deleted in P3
            if not line.startswith("scripts/migrate_envelope_schema.py")
        ]
        assert offenders == [], f"references to retired table: {offenders}"


# ---------------------------------------------------------------------------
# transition_assumption_set_status
# ---------------------------------------------------------------------------

class TestTransitionAssumptionSetStatus:
    def test_status_changes(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id, status="PROPOSED")
        transition_assumption_set_status(tmp_db, aset_id, "STAGE3_APPROVED")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?", [aset_id]
        ).fetchone()
        con.close()
        assert row[0] == "STAGE3_APPROVED"

    def test_approved_status_sets_approved_by(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id, status="STAGE3_APPROVED")
        transition_assumption_set_status(tmp_db, aset_id, "APPROVED", approved_by="ACTUARY_2")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT status, approved_by FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset_id]
        ).fetchone()
        con.close()
        assert row[0] == "APPROVED"
        assert row[1] == "ACTUARY_2"

    def test_approved_set_cannot_be_unlocked_to_stage3(self, tmp_db):
        """Re-submitting a locked (APPROVED) set must not silently unlock it (audit 2026-07-04)."""
        from src.assumptions.workflow import LockedStatusTransition

        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id, status="STAGE3_APPROVED")
        transition_assumption_set_status(tmp_db, aset_id, "APPROVED", approved_by="c.chief")

        with pytest.raises(LockedStatusTransition):
            transition_assumption_set_status(tmp_db, aset_id, "STAGE3_APPROVED")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT status, approved_by FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset_id]
        ).fetchone()
        con.close()
        assert row[0] == "APPROVED"       # unchanged
        assert row[1] == "c.chief"        # stale approval not left on a reverted set

    def test_approved_set_may_be_superseded(self, tmp_db):
        """The one permitted onward move from APPROVED is SUPERSEDED (lineage publish)."""
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id, status="STAGE3_APPROVED")
        transition_assumption_set_status(tmp_db, aset_id, "APPROVED", approved_by="c.chief")
        transition_assumption_set_status(tmp_db, aset_id, "SUPERSEDED")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?", [aset_id]
        ).fetchone()
        con.close()
        assert row[0] == "SUPERSEDED"
