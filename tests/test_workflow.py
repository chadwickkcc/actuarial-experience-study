"""Tests for src/tev/workflow.py — envelope-aware workflow logging.

Covers:
- log_workflow_iteration() writes envelope_run_flag correctly
- get_workflow_iterations() returns envelope_run_flag (no optimiser columns)
- record_governance_approval() stores envelope_tev_min/max/percentile
- Absence of optimiser columns in DB schema
- No adoption path: workflow functions do not accept or return EnvelopeResult/AssumptionSet
"""
from __future__ import annotations

import ast
import json
import tempfile
import uuid
from pathlib import Path

import duckdb
import pytest

from src.tev.workflow import (
    get_next_iteration_number,
    get_workflow_iterations,
    log_workflow_iteration,
    record_governance_approval,
    transition_assumption_set_status,
)
from src.utils.db_init import init_database


# ---------------------------------------------------------------------------
# Fixture: isolated in-memory DB with correct schema
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
                rdr, earned_rate_ga, earned_rate_sa, tax_rate, expense_inflation,
                created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """, [
            aset_id, 1, status, "2024-01-01", "actuary_1", "best-estimate",
            str(uuid.uuid4()), "",
            0.09, 0.05, 0.06, 0.21, 0.025,
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
            "SELECT iteration_id FROM gold_workflow_iterations WHERE iteration_id = ?",
            [iteration_id]
        ).fetchone()
        con.close()
        assert row is not None

    def test_envelope_run_flag_true(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        iteration_id = log_workflow_iteration(
            db_path=tmp_db,
            workflow_session_id=session_id,
            iteration_number=1,
            assumption_set_id=aset_id,
            stage=3,
            action="ENVELOPE_RUN",
            actuary_id="ACTUARY_1",
            envelope_run_flag=True,
            total_tev=5_000_000.0,
        )
        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT envelope_run_flag FROM gold_workflow_iterations WHERE iteration_id = ?",
            [iteration_id]
        ).fetchone()
        con.close()
        assert row[0] is True

    def test_envelope_run_flag_default_false(self, tmp_db):
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
        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT envelope_run_flag FROM gold_workflow_iterations WHERE iteration_id = ?",
            [iteration_id]
        ).fetchone()
        con.close()
        assert row[0] is False

    def test_no_optimiser_columns_in_schema(self, tmp_db):
        con = duckdb.connect(str(tmp_db), read_only=True)
        cols = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'gold_workflow_iterations'"
        ).fetchall()
        con.close()
        col_names = {r[0] for r in cols}
        assert "optimiser_run_flag" not in col_names
        assert "optimiser_suggestion_adopted" not in col_names

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

    def test_includes_envelope_run_flag_key(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        log_workflow_iteration(
            db_path=tmp_db, workflow_session_id=session_id, iteration_number=1,
            assumption_set_id=aset_id, stage=3, action="ENVELOPE_RUN", actuary_id="A1",
            envelope_run_flag=True,
        )
        rows = get_workflow_iterations(tmp_db, session_id)
        assert "envelope_run_flag" in rows[0]
        assert rows[0]["envelope_run_flag"] is True

    def test_does_not_include_optimiser_keys(self, tmp_db):
        session_id = str(uuid.uuid4())
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        log_workflow_iteration(
            db_path=tmp_db, workflow_session_id=session_id, iteration_number=1,
            assumption_set_id=aset_id, stage=2, action="SAVED", actuary_id="A1",
        )
        rows = get_workflow_iterations(tmp_db, session_id)
        assert "optimiser_run_flag" not in rows[0]
        assert "optimiser_suggestion_adopted" not in rows[0]

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
# record_governance_approval
# ---------------------------------------------------------------------------

class TestRecordGovernanceApproval:
    def _call(
        self,
        db_path: Path,
        aset_id: str,
        decision: str = "APPROVE",
        envelope_run_flag: bool = True,
        envelope_tev_min: float | None = 4_800_000.0,
        envelope_tev_max: float | None = 5_200_000.0,
        envelope_percentile: float | None = 0.62,
    ) -> str:
        return record_governance_approval(
            db_path=db_path,
            assumption_set_id=aset_id,
            workflow_session_id=str(uuid.uuid4()),
            source_study_run_id=str(uuid.uuid4()),
            tev_baseline_run_id=str(uuid.uuid4()),
            proposer_id="ACTUARY_1",
            reviewer_id="ACTUARY_2",
            reviewer_decision=decision,
            reviewer_comment="LGTM",
            total_iterations=3,
            envelope_run_flag=envelope_run_flag,
            baseline_tev=5_000_000.0,
            delta_tev_vs_prior=50_000.0,
            max_sensitivity_delta=200_000.0,
            iteration_history=[],
            envelope_tev_min=envelope_tev_min,
            envelope_tev_max=envelope_tev_max,
            proposed_envelope_percentile=envelope_percentile,
        )

    def test_returns_approval_id(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        approval_id = self._call(tmp_db, aset_id)
        uuid.UUID(approval_id)  # must be valid UUID

    def test_envelope_fields_stored_correctly(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        self._call(tmp_db, aset_id, envelope_tev_min=4_800_000.0,
                   envelope_tev_max=5_200_000.0, envelope_percentile=0.62)

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute("""
            SELECT envelope_run_flag, envelope_tev_min, envelope_tev_max,
                   proposed_envelope_percentile
            FROM gold_assumption_approvals WHERE assumption_set_id = ?
        """, [aset_id]).fetchone()
        con.close()

        assert row[0] is True
        assert row[1] == pytest.approx(4_800_000.0)
        assert row[2] == pytest.approx(5_200_000.0)
        assert row[3] == pytest.approx(0.62)

    def test_envelope_fields_none_when_not_run(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        self._call(tmp_db, aset_id, envelope_run_flag=False,
                   envelope_tev_min=None, envelope_tev_max=None, envelope_percentile=None)

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute("""
            SELECT envelope_run_flag, envelope_tev_min, envelope_tev_max,
                   proposed_envelope_percentile
            FROM gold_assumption_approvals WHERE assumption_set_id = ?
        """, [aset_id]).fetchone()
        con.close()

        assert row[0] is False
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None

    def test_no_optimiser_columns_in_approval_schema(self, tmp_db):
        con = duckdb.connect(str(tmp_db), read_only=True)
        cols = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'gold_assumption_approvals'"
        ).fetchall()
        con.close()
        col_names = {r[0] for r in cols}
        assert "optimiser_used_flag" not in col_names
        assert "optimiser_adopted_flag" not in col_names

    def test_return_decision_does_not_set_approved_ts(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        self._call(tmp_db, aset_id, decision="RETURN")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT approved_ts FROM gold_assumption_approvals WHERE assumption_set_id = ?",
            [aset_id]
        ).fetchone()
        con.close()
        assert row[0] is None

    def test_approve_decision_sets_approved_ts(self, tmp_db):
        aset_id = str(uuid.uuid4())
        _insert_assumption_set(tmp_db, aset_id)
        self._call(tmp_db, aset_id, decision="APPROVE")

        con = duckdb.connect(str(tmp_db), read_only=True)
        row = con.execute(
            "SELECT approved_ts FROM gold_assumption_approvals WHERE assumption_set_id = ?",
            [aset_id]
        ).fetchone()
        con.close()
        assert row[0] is not None


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
        from src.tev.workflow import LockedStatusTransition

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


# ---------------------------------------------------------------------------
# Architectural invariant: workflow functions have no adoption path
# ---------------------------------------------------------------------------

class TestWorkflowNoAdoptionPath:
    def test_workflow_module_has_no_envelope_result_param(self):
        """workflow.py must not import or use EnvelopeResult as a function parameter."""
        workflow_src = Path("src/tev/workflow.py").read_text(encoding="utf-8")
        tree = ast.parse(workflow_src)
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for arg in node.args.args:
                    if arg.annotation and "EnvelopeResult" in ast.unparse(arg.annotation):
                        violations.append(node.name)
        assert violations == [], (
            f"workflow.py functions with EnvelopeResult param: {violations}"
        )

    def test_workflow_module_has_no_assumption_set_return(self):
        """workflow.py must not return AssumptionSet from any function."""
        workflow_src = Path("src/tev/workflow.py").read_text(encoding="utf-8")
        tree = ast.parse(workflow_src)
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.returns and "AssumptionSet" in ast.unparse(node.returns):
                    violations.append(node.name)
        assert violations == [], (
            f"workflow.py functions returning AssumptionSet: {violations}"
        )
