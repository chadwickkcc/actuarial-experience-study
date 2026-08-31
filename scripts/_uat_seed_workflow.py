"""Headless demo-workflow seeding (demo refresh P8).

Drives one assumption set through the full governed lifecycle on the LIVE demo
DB — create (a.analyst) → save/submit → junior/senior/chief sign-offs → locked
APPROVED — so a freshly rebuilt DB ships with a completed example workflow:
the UAT harnesses (sections 2 / 3.7 / 4.4) have their preconditions, the
Lineage/Dashboard pages have content, and hash-chained sign-off rows exist for
the tamper-evidence demo. Part of the live-DB rebuild sequence
(reset → _uat_rerun → _uat_ai_fit → THIS). Idempotent-ish: skips when an
APPROVED set already exists.

Usage:  .venv/bin/python scripts/_uat_seed_workflow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

from src.assumptions.assumption_set import create_assumption_set_from_ae_run
from src.assumptions.workflow import (
    get_next_iteration_number,
    transition_assumption_set_status,
)
from src.governance.audit import log_workflow_iteration
from src.governance.users import get_user_by_username, seed_users_from_config
from src.governance.workflow import record_signoff
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import ArtifactType, Decision

GOV_CONFIG = str(ROOT / "config" / "governance_config.yaml")


def main() -> int:
    """Seed one approved assumption set on the live demo DB."""
    db = Path(DEFAULT_DB_PATH)
    seed_users_from_config()

    con = duckdb.connect(str(db), read_only=True)
    try:
        run = con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
        existing = con.execute(
            "SELECT COUNT(*) FROM gold_assumption_sets WHERE status='APPROVED'"
        ).fetchone()[0]
    finally:
        con.close()
    if run is None:
        print("No COMPLETE study run — run scripts/_uat_rerun.py first.")
        return 1
    if existing:
        print(f"An APPROVED assumption set already exists ({existing}) — nothing to seed.")
        return 0

    analyst = get_user_by_username("a.analyst", db_path=str(db))
    assert analyst is not None, "seed user a.analyst missing"

    print("Creating the demo assumption set (author a.analyst)…")
    aset = create_assumption_set_from_ae_run(
        study_run_id=run[0],
        author_id=analyst.username,
        db_path=db,
        output_yaml_dir=db.parent / "assumption_sets",
    )
    wf_session = aset.id  # one demo session keyed on the set id
    log_workflow_iteration(
        db_path=db, workflow_session_id=wf_session,
        iteration_number=get_next_iteration_number(db, wf_session),
        assumption_set_id=aset.id, stage=2, action="SAVED",
        actuary_id=analyst.username,
        actuary_comment="Demo seed: credibility-weighted A/E basis accepted as proposed.",
    )
    transition_assumption_set_status(db, aset.id, "STAGE3_APPROVED")
    log_workflow_iteration(
        db_path=db, workflow_session_id=wf_session,
        iteration_number=get_next_iteration_number(db, wf_session),
        assumption_set_id=aset.id, stage=3, action="SUBMITTED_S4",
        actuary_id=analyst.username,
        actuary_comment="Demo seed: submitted for governance sign-off.",
    )

    for uname, note in (
        ("j.junior", "Level 1 review complete — basis reconciles to the study."),
        ("s.senior", "Level 2 review complete — credibility treatment appropriate."),
        ("c.chief", "Final approval — assumption set adopted and locked."),
    ):
        signer = get_user_by_username(uname, db_path=str(db))
        assert signer is not None, f"seed user {uname} missing"
        rec = record_signoff(
            signer, ArtifactType.ASSUMPTION_SET, aset.id, aset.version,
            Decision.APPROVE, note, db_path=str(db), config_path=GOV_CONFIG,
        )
        print(f"  signed level {rec.chain_level} as {uname}")

    con = duckdb.connect(str(db), read_only=True)
    try:
        status = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset.id],
        ).fetchone()[0]
        n_signoffs = con.execute(
            "SELECT COUNT(*) FROM gold_governance_signoffs"
        ).fetchone()[0]
    finally:
        con.close()
    print(f"DONE: set {aset.id[:8]}… status={status}; "
          f"hash-chained sign-off rows={n_signoffs}")
    return 0 if status == "APPROVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
