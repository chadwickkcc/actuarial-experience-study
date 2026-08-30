"""Workflow iteration logging and status transitions for the assumption workflow.

Provides helpers to log every editor save, submit and sign-off action into the
DuckDB audit tables, and to move an assumption set through its status lifecycle.

Tables written:
    gold_workflow_iterations   — every significant action in the workflow
    gold_assumption_sets       — status transitions (PROPOSED → STAGE3_APPROVED → APPROVED)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import duckdb


# ---------------------------------------------------------------------------
# Workflow iteration logging
# ---------------------------------------------------------------------------

def log_workflow_iteration(
    db_path: Path,
    workflow_session_id: str,
    iteration_number: int,
    assumption_set_id: str,
    stage: int,
    action: str,
    actuary_id: str,
    actuary_comment: str = "",
) -> str:
    """Insert a row into gold_workflow_iterations.

    Args:
        db_path:                  DuckDB path.
        workflow_session_id:      UUID identifying this workflow session.
        iteration_number:         Monotonically increasing counter within the session.
        assumption_set_id:        UUID of the assumption set being worked on.
        stage:                    2 (edit) or 3 (submit) or 4 (governance).
        action:                   One of SAVED, RETURNED_TO_S2, SUBMITTED_S4, APPROVED.
        actuary_id:               Identifier of the actuary performing the action.
        actuary_comment:          Free-text comment (optional).

    Returns:
        The new iteration_id (UUID string).
    """
    iteration_id = str(uuid.uuid4())
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            INSERT INTO gold_workflow_iterations (
                iteration_id, workflow_session_id, iteration_number,
                assumption_set_id, stage, action,
                actuary_id, actuary_comment, iteration_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
        """, [
            iteration_id,
            workflow_session_id,
            iteration_number,
            assumption_set_id,
            stage,
            action,
            actuary_id,
            actuary_comment,
            datetime.utcnow(),
        ])
    finally:
        con.close()
    return iteration_id


# ---------------------------------------------------------------------------
# Assumption set status transitions
# ---------------------------------------------------------------------------

class LockedStatusTransition(Exception):
    """Raised when a caller tries to move an APPROVED (locked) set to a non-terminal state.

    Once an assumption set completes the sign-off chain it is APPROVED and
    immutable; the only onward move is SUPERSEDED (via the lineage publish path).
    A re-submit (or any other caller) must never silently revert it to
    STAGE3_APPROVED / PROPOSED and unlock it while leaving the stale
    approved_by / approved_ts in place. See the governance audit (2026-07-04).
    """


def transition_assumption_set_status(
    db_path: Path,
    assumption_set_id: str,
    new_status: str,
    approved_by: str | None = None,
) -> None:
    """Update the status of an assumption set in gold_assumption_sets.

    Args:
        db_path:             DuckDB path.
        assumption_set_id:   UUID of the assumption set to update.
        new_status:          One of PROPOSED, STAGE3_APPROVED, APPROVED, SUPERSEDED.
        approved_by:         Actuary ID who approved (only for APPROVED status).

    Raises:
        LockedStatusTransition: if the set is already APPROVED and ``new_status`` is
            anything other than APPROVED (idempotent) or SUPERSEDED — this guards
            against silently unlocking a completed, locked assumption set.
    """
    con = duckdb.connect(str(db_path))
    try:
        current = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [assumption_set_id],
        ).fetchone()
        if (
            current is not None
            and current[0] == "APPROVED"
            and new_status not in ("APPROVED", "SUPERSEDED")
        ):
            raise LockedStatusTransition(
                f"assumption set {assumption_set_id!r} is APPROVED (locked) and cannot be "
                f"transitioned to {new_status!r}; only SUPERSEDED is permitted"
            )
        if new_status == "APPROVED" and approved_by:
            con.execute(
                "UPDATE gold_assumption_sets SET status = ?, approved_by = ?, "
                "approved_ts = ? WHERE assumption_set_id = ?",
                [new_status, approved_by, datetime.utcnow(), assumption_set_id],
            )
        else:
            con.execute(
                "UPDATE gold_assumption_sets SET status = ? WHERE assumption_set_id = ?",
                [new_status, assumption_set_id],
            )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Query helpers for session continuity
# ---------------------------------------------------------------------------

def get_workflow_iterations(
    db_path: Path,
    workflow_session_id: str,
) -> list[dict]:
    """Return all iterations for a workflow session, ordered by iteration_number."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute("""
            SELECT iteration_id, iteration_number, stage, action, actuary_id,
                   actuary_comment, iteration_ts
            FROM gold_workflow_iterations
            WHERE workflow_session_id = ?
            ORDER BY iteration_number
        """, [workflow_session_id]).fetchall()
        cols = ["iteration_id", "iteration_number", "stage", "action", "actuary_id",
                "actuary_comment", "iteration_ts"]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        con.close()


def get_next_iteration_number(db_path: Path, workflow_session_id: str) -> int:
    """Return the next iteration number for a workflow session."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT COALESCE(MAX(iteration_number), 0) + 1 "
            "FROM gold_workflow_iterations WHERE workflow_session_id = ?",
            [workflow_session_id],
        ).fetchone()
        return int(row[0]) if row else 1
    finally:
        con.close()
