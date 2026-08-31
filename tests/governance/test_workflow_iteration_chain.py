"""The workflow-iteration log must actually be hash-chained (review B/M-9).

``gold_workflow_iterations`` was registered in ``_VERIFIABLE_CHAINS`` and shown in
the UI's "Verify integrity" list, but its writer did a plain INSERT — ``seq`` /
``prev_hash`` / ``entry_hash`` stayed NULL. ``verify_chain`` skips unhashed rows,
so it checked ZERO rows and returned ok=True, and the page rendered a green
"intact ✓" over a log with no integrity protection at all. Walkthrough §8.2 clicks
that button live.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pytest

from src.governance.audit import log_workflow_iteration
from src.governance.audit import verify_chain


def _log(db: Path, n: int = 3, set_id: str | None = None) -> str:
    set_id = set_id or str(uuid.uuid4())
    session = str(uuid.uuid4())
    for i in range(1, n + 1):
        log_workflow_iteration(
            db_path=db,
            workflow_session_id=session,
            iteration_number=i,
            assumption_set_id=set_id,
            stage=2,
            action="SAVED",
            actuary_id="a.analyst",
            actuary_comment=f"iteration {i}",
        )
    return set_id


def test_iterations_are_hash_chained(gov_env):
    db = Path(gov_env["db"])
    _log(db, 3)
    con = duckdb.connect(str(db), read_only=True)
    try:
        total, hashed = con.execute(
            "SELECT COUNT(*), COUNT(entry_hash) FROM gold_workflow_iterations"
        ).fetchone()
    finally:
        con.close()
    assert total == 3
    assert hashed == 3, "workflow iterations must carry an entry_hash"


def test_verify_chain_actually_checks_the_rows(gov_env):
    db = Path(gov_env["db"])
    _log(db, 3)
    res = verify_chain("gold_workflow_iterations", db_path=str(db))
    assert res.ok
    assert res.rows_checked == 3, (
        "a green tick after checking zero rows is worse than no check"
    )


def test_tampered_iteration_is_detected(gov_env):
    db = Path(gov_env["db"])
    _log(db, 3)
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "UPDATE gold_workflow_iterations SET actuary_comment = ? WHERE seq = ?",
            ["FORGED — never happened", 2],
        )
    finally:
        con.close()
    res = verify_chain("gold_workflow_iterations", db_path=str(db))
    assert not res.ok, "a tampered iteration row must be detected"
    assert res.first_divergence_seq == 2


def test_deleted_iteration_is_detected(gov_env):
    db = Path(gov_env["db"])
    _log(db, 3)
    con = duckdb.connect(str(db))
    try:
        con.execute("DELETE FROM gold_workflow_iterations WHERE seq = 2")
    finally:
        con.close()
    res = verify_chain("gold_workflow_iterations", db_path=str(db))
    assert not res.ok, "a deleted middle row must break the chain"
