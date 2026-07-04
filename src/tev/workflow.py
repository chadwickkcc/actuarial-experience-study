"""Workflow iteration and approval logging for the TEV four-stage workflow.

Provides helpers to log every Stage 2 save, Stage 3 run, envelope analysis,
and Stage 4 governance sign-off into the DuckDB audit tables.

Tables written:
    gold_workflow_iterations   — every significant action in the workflow
    gold_assumption_approvals  — final Stage 4 sign-off record
    gold_assumption_sets       — status transitions (PROPOSED → STAGE3_APPROVED → APPROVED)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    tev_baseline_run_id: str | None = None,
    total_tev: float | None = None,
    delta_tev_vs_prior: float | None = None,
    envelope_run_flag: bool = False,
) -> str:
    """Insert a row into gold_workflow_iterations.

    Args:
        db_path:                  DuckDB path.
        workflow_session_id:      UUID identifying this workflow session.
        iteration_number:         Monotonically increasing counter within the session.
        assumption_set_id:        UUID of the assumption set being worked on.
        stage:                    2 (edit) or 3 (TEV run) or 4 (governance).
        action:                   One of SAVED, RAN_TEV, APPROVED_S3, RETURNED_TO_S2,
                                  ENVELOPE_RUN, SUBMITTED_S4.
        actuary_id:               Identifier of the actuary performing the action.
        actuary_comment:          Free-text comment (optional).
        tev_baseline_run_id:      UUID of the most recent TEV baseline run (if any).
        total_tev:                Total TEV at time of action.
        delta_tev_vs_prior:       ΔTEV vs the prior iteration (if applicable).
        envelope_run_flag:        True if the credibility envelope was computed this iteration.

    Returns:
        The new iteration_id (UUID string).
    """
    iteration_id = str(uuid.uuid4())
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            INSERT INTO gold_workflow_iterations (
                iteration_id, workflow_session_id, iteration_number,
                assumption_set_id, tev_baseline_run_id, stage, action,
                actuary_id, actuary_comment, total_tev, delta_tev_vs_prior,
                envelope_run_flag, iteration_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            iteration_id,
            workflow_session_id,
            iteration_number,
            assumption_set_id,
            tev_baseline_run_id,
            stage,
            action,
            actuary_id,
            actuary_comment,
            total_tev,
            delta_tev_vs_prior,
            envelope_run_flag,
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

    Once an assumption set completes the Stage-4 sign-off chain it is APPROVED and
    immutable; the only onward move is SUPERSEDED (via the lineage publish path). A
    re-run of the Stage-3 "Submit for sign-off" step (or any other caller) must never
    silently revert it to STAGE3_APPROVED / PROPOSED and unlock it while leaving the
    stale approved_by / approved_ts in place. See the governance audit (2026-07-04).
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
# Stage 4 governance sign-off
# ---------------------------------------------------------------------------

def record_governance_approval(
    db_path: Path,
    assumption_set_id: str,
    workflow_session_id: str,
    source_study_run_id: str,
    tev_baseline_run_id: str,
    proposer_id: str,
    reviewer_id: str,
    reviewer_decision: str,
    reviewer_comment: str,
    total_iterations: int,
    envelope_run_flag: bool,
    baseline_tev: float,
    delta_tev_vs_prior: float | None,
    max_sensitivity_delta: float | None,
    iteration_history: list[dict],
    envelope_tev_min: float | None = None,
    envelope_tev_max: float | None = None,
    proposed_envelope_percentile: float | None = None,
) -> str:
    """Insert a row into gold_assumption_approvals.

    Args:
        db_path:                     DuckDB path.
        assumption_set_id:           UUID of the assumption set being reviewed.
        workflow_session_id:         UUID of the workflow session.
        source_study_run_id:         UUID of the source experience study run.
        tev_baseline_run_id:         UUID of the TEV baseline run.
        proposer_id:                 Actuary who proposed the assumption set.
        reviewer_id:                 Reviewer who signs off.
        reviewer_decision:           APPROVE or RETURN.
        reviewer_comment:            Mandatory free-text comment from reviewer.
        total_iterations:            Number of Stage 2 → Stage 3 iterations.
        envelope_run_flag:           Whether the envelope analyser was run.
        baseline_tev:                Total TEV of the current assumption set.
        delta_tev_vs_prior:          ΔTEV vs the prior APPROVED assumption set.
        max_sensitivity_delta:       Maximum |ΔTEV| across all 11 sensitivities.
        iteration_history:           List of dicts summarising each iteration.
        envelope_tev_min:            TEV_min from envelope analysis (if run).
        envelope_tev_max:            TEV_max from envelope analysis (if run).
        proposed_envelope_percentile: Percentile of proposed within envelope (None if
                                     width below materiality floor or envelope not run).

    Returns:
        The new approval_id (UUID string).
    """
    approval_id = str(uuid.uuid4())
    approved_ts = datetime.utcnow() if reviewer_decision == "APPROVE" else None

    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "DELETE FROM gold_assumption_approvals WHERE assumption_set_id = ?",
            [assumption_set_id],
        )
        con.execute("""
            INSERT INTO gold_assumption_approvals (
                approval_id, assumption_set_id, workflow_session_id,
                source_study_run_id, tev_baseline_run_id,
                proposer_id, reviewer_id, reviewer_decision, reviewer_comment,
                total_iterations, envelope_run_flag,
                envelope_tev_min, envelope_tev_max, proposed_envelope_percentile,
                baseline_tev, delta_tev_vs_prior, max_sensitivity_delta,
                proposed_ts, approved_ts, iteration_history
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            approval_id,
            assumption_set_id,
            workflow_session_id,
            source_study_run_id,
            tev_baseline_run_id,
            proposer_id,
            reviewer_id,
            reviewer_decision,
            reviewer_comment,
            total_iterations,
            envelope_run_flag,
            envelope_tev_min,
            envelope_tev_max,
            proposed_envelope_percentile,
            baseline_tev,
            delta_tev_vs_prior,
            max_sensitivity_delta,
            datetime.utcnow(),
            approved_ts,
            json.dumps(iteration_history),
        ])
    finally:
        con.close()
    return approval_id


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
                   actuary_comment, total_tev, delta_tev_vs_prior,
                   envelope_run_flag, iteration_ts
            FROM gold_workflow_iterations
            WHERE workflow_session_id = ?
            ORDER BY iteration_number
        """, [workflow_session_id]).fetchall()
        cols = ["iteration_id", "iteration_number", "stage", "action", "actuary_id",
                "actuary_comment", "total_tev", "delta_tev_vs_prior",
                "envelope_run_flag", "iteration_ts"]
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
