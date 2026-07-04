"""Phase 4 UAT — Test 3.3 (A/E study-run approval, FR-4-14) — engine harness.

Study-run approval has no dedicated UI in this build (the Stage-4 page approves
assumption sets, not study runs), so this drives the governance engine directly,
exactly as the governed flow would: submit the run, then run the full
junior->senior->chief sign-off chain, asserting the run is "not yet fit" until every
required level has APPROVED and "fit for assumption-setting" thereafter
(``is_study_run_fit``). It also checks the sequential-order guard, the pending-approvals
routing, the compliance-pack fit gate, and proposer != approver segregation (FR-4-05).

NON-DESTRUCTIVE: it copies the live data/experience_study.duckdb into a temp dir and
runs every engine call against the COPY (``db_path=`` override). The live DB is never
opened writable.

Usage (from the project root, with the venv active):
    .venv/bin/python scripts/uat_section3_3_runner.py

Exit code 0 iff all checks PASS.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# Make ``src`` importable when run as a standalone script.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb  # noqa: E402

from src.governance.audit import submit_study_run  # noqa: E402
from src.governance.reporting import export_compliance_pack  # noqa: E402
from src.governance.rbac import PermissionDenied  # noqa: E402
from src.governance.users import get_user_by_username  # noqa: E402
from src.governance.workflow import (  # noqa: E402
    SegregationViolation,
    is_study_run_fit,
    next_required_level,
    pending_approvals,
    record_signoff,
)
from src.utils.types import ArtifactType, Decision  # noqa: E402

LIVE_DB = ROOT_DIR / "data" / "experience_study.duckdb"
CONFIG = str(ROOT_DIR / "config" / "governance_config.yaml")

_results: list[tuple[str, bool, str]] = []


def record(row: str, ok: bool, detail: str) -> None:
    _results.append((row, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {row}: {detail}")


def _copy_db(work: Path, tag: str) -> str:
    dst = work / f"copy_{tag}.duckdb"
    shutil.copy2(LIVE_DB, dst)
    return str(dst)


def _reset_run_governance(db: str, run_id: str) -> None:
    """Clear a run's governance state on the COPY so each scenario starts from a
    clean, un-submitted precondition regardless of prior live sign-offs."""
    con = duckdb.connect(db)
    try:
        con.execute("DELETE FROM gold_ae_governance_events WHERE study_run_id = ?", [run_id])
        con.execute(
            "DELETE FROM gold_governance_signoffs "
            "WHERE artifact_type = 'STUDY_RUN' AND artifact_id = ?",
            [run_id],
        )
    finally:
        con.close()


def _latest_complete_run(db: str) -> str:
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status = 'COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit("No COMPLETE study run in the DB — run a study first.")
    return row[0]


def _users(db: str) -> dict:
    who = {u: get_user_by_username(u, db_path=db) for u in ("a.analyst", "j.junior", "s.senior", "c.chief")}
    for u, obj in who.items():
        assert obj is not None, f"seeded user {u} missing (seed governance users first)"
    by_role = {
        "junior_actuary": who["j.junior"],
        "senior_actuary": who["s.senior"],
        "chief_actuary": who["c.chief"],
    }
    return {"who": who, "by_role": by_role}


def scenario_main(work: Path) -> None:
    """3.3: submit -> not fit -> full chain -> fit, + order guard, pending, export gate."""
    db = _copy_db(work, "main")
    run_id = _latest_complete_run(db)
    _reset_run_governance(db, run_id)
    U = _users(db)
    analyst = U["who"]["a.analyst"]
    packs = work / "packs"
    packs.mkdir(exist_ok=True)

    submit_study_run(run_id, analyst.user_id, detail="UAT 3.3 submit", db_path=db)

    # 3.3-A — un-approved run is "not yet fit".
    fit0 = is_study_run_fit(run_id, db_path=db, config_path=CONFIG)
    record("3.3-A (not yet fit before sign-off)", fit0 is False, f"is_study_run_fit={fit0}")

    # 3.3-B — sequential order / role-for-level: senior cannot sign before junior.
    try:
        record_signoff(U["who"]["s.senior"], ArtifactType.STUDY_RUN, run_id, None,
                       Decision.APPROVE, "attempt out of order", db_path=db, config_path=CONFIG)
        record("3.3-B (out-of-order rejected)", False, "senior-before-junior was NOT rejected")
    except PermissionDenied as e:
        record("3.3-B (out-of-order rejected)", True, f"PermissionDenied: {e}")

    # 3.3-C — full chain in order; fit stays False until the last required level.
    ok_chain, steps, guard = True, [], 0
    while True:
        lvl = next_required_level(ArtifactType.STUDY_RUN, run_id, db_path=db, config_path=CONFIG)
        if lvl is None:
            break
        guard += 1
        assert guard <= 5, "chain did not terminate"
        signer = U["by_role"][lvl.required_role.value]
        record_signoff(signer, ArtifactType.STUDY_RUN, run_id, None, Decision.APPROVE,
                       f"L{lvl.level} approve ({signer.username})", db_path=db, config_path=CONFIG)
        fit_mid = is_study_run_fit(run_id, db_path=db, config_path=CONFIG)
        nxt = next_required_level(ArtifactType.STUDY_RUN, run_id, db_path=db, config_path=CONFIG)
        expect_fit = nxt is None
        ok_chain = ok_chain and (fit_mid is expect_fit)
        steps.append(f"L{lvl.level}/{lvl.required_role.value}->{signer.username}: fit={fit_mid} (exp {expect_fit})")
    record("3.3-C (chain order + interim fit)", ok_chain, " | ".join(steps))

    # 3.3-D — after all-APPROVE the run is "fit for assumption-setting".
    fitN = is_study_run_fit(run_id, db_path=db, config_path=CONFIG)
    record("3.3-D (fit after full chain)", fitN is True, f"is_study_run_fit={fitN}")

    # 3.3-E — compliance-pack gate: a fit run now exports (before completion it raised
    # "not yet fit"), confirming the fit state gates the downstream artifact (FR-4-24).
    try:
        path = export_compliance_pack(ArtifactType.STUDY_RUN, run_id, "html",
                                      db_path=db, config_path=CONFIG, output_dir=str(packs))
        record("3.3-E (fit run exports compliance pack)", bool(path) and Path(path).exists(), f"wrote {path}")
    except Exception as e:  # noqa: BLE001
        record("3.3-E (fit run exports compliance pack)", False, f"{type(e).__name__}: {e}")


def scenario_pending(work: Path) -> None:
    """3.3-F: pending-approvals routes a submitted+level-1-signed run to the next role only."""
    db = _copy_db(work, "pending")
    run_id = _latest_complete_run(db)
    _reset_run_governance(db, run_id)
    U = _users(db)
    submit_study_run(run_id, U["who"]["a.analyst"].user_id, detail="pending probe", db_path=db)
    record_signoff(U["who"]["j.junior"], ArtifactType.STUDY_RUN, run_id, None,
                   Decision.APPROVE, "L1 approve", db_path=db, config_path=CONFIG)
    pend_senior = pending_approvals(U["who"]["s.senior"], db_path=db, config_path=CONFIG)
    pend_chief = pending_approvals(U["who"]["c.chief"], db_path=db, config_path=CONFIG)
    in_senior = any(run_id in str(p.values()) for p in pend_senior)
    in_chief = any(run_id in str(p.values()) for p in pend_chief)
    record("3.3-F (pending queue routes to next role)", in_senior and not in_chief,
           f"senior_sees_run={in_senior} chief_sees_run={in_chief}")


def scenario_segregation(work: Path) -> None:
    """3.3-G (FR-4-05): the run's submitter (proposer) cannot also sign it off."""
    db = _copy_db(work, "segregation")
    run_id = _latest_complete_run(db)
    _reset_run_governance(db, run_id)
    U = _users(db)
    junior = U["who"]["j.junior"]
    submit_study_run(run_id, junior.user_id, detail="self-approve probe", db_path=db)
    try:
        record_signoff(junior, ArtifactType.STUDY_RUN, run_id, None,
                       Decision.APPROVE, "self approve attempt", db_path=db, config_path=CONFIG)
        record("3.3-G (submitter cannot sign — segregation)", False, "self-approval was NOT blocked")
    except SegregationViolation as e:
        record("3.3-G (submitter cannot sign — segregation)", True, f"SegregationViolation: {e}")


def _summary() -> int:
    print("\n================ Test 3.3 summary ================")
    for row, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {row}")
    n_pass = sum(1 for _, ok, _ in _results if ok)
    print("  ------------------------------------------------")
    print(f"  {n_pass}/{len(_results)} checks PASS")
    return 0 if n_pass == len(_results) else 1


def run() -> int:
    if not LIVE_DB.exists():
        raise SystemExit(f"Live DB not found at {LIVE_DB}")
    with tempfile.TemporaryDirectory(prefix="uat_s33_") as tmp:
        work = Path(tmp)
        print(f"Working on isolated copies under {work} (live DB never written)")
        scenario_main(work)
        scenario_pending(work)
        scenario_segregation(work)
        return _summary()


if __name__ == "__main__":
    sys.exit(run())
