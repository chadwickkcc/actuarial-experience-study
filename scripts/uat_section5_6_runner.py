"""Phase 4 UAT — Test 5.6 (Compliance pack — study run, FR-4-24) — engine harness.

A study-run compliance pack can only be exported once the run is "fit for
assumption-setting" (its governance sign-off chain is complete). There is no UI to
approve a *study run* in this build (Stage 4 approves assumption sets, not runs), so a
freshly rebuilt run is never fit and the UI export correctly refuses it — that refusal
is Test 5.7, not a bug. This harness makes a run fit the governed way (submit + full
junior->senior->chief chain) and then exports the pack, asserting the HTML carries the
sign-offs/attestations, the audit excerpt, and the supporting-report links (5.6). It also
confirms the pre-fit export is refused (5.7).

NON-DESTRUCTIVE: everything runs against a temp COPY of data/experience_study.duckdb
(``db_path=`` override); the live DB is never opened writable.

Usage (from the project root, venv active):
    .venv/bin/python scripts/uat_section5_6_runner.py

Exit code 0 iff all checks PASS.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb  # noqa: E402

from src.governance.audit import submit_study_run  # noqa: E402
from src.governance.reporting import export_compliance_pack  # noqa: E402
from src.governance.users import get_user_by_username  # noqa: E402
from src.governance.workflow import (  # noqa: E402
    is_study_run_fit,
    next_required_level,
    record_signoff,
)
from src.utils.types import ArtifactType, Decision  # noqa: E402

LIVE_DB = ROOT_DIR / "data" / "experience_study.duckdb"
CONFIG = str(ROOT_DIR / "config" / "governance_config.yaml")

_results: list[tuple[str, bool, str]] = []


def record(row: str, ok: bool, detail: str) -> None:
    _results.append((row, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {row}: {detail}")


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


def run() -> int:
    if not LIVE_DB.exists():
        raise SystemExit(f"Live DB not found at {LIVE_DB}")

    # Baseline of the live governance-events log; the harness must not change it.
    _lcon = duckdb.connect(str(LIVE_DB), read_only=True)
    try:
        live_events_before = _lcon.execute(
            "SELECT COUNT(*) FROM gold_ae_governance_events"
        ).fetchone()[0]
    finally:
        _lcon.close()

    with tempfile.TemporaryDirectory(prefix="uat_s56_") as tmp:
        work = Path(tmp)
        db = str(work / "copy.duckdb")
        shutil.copy2(LIVE_DB, db)
        packs = work / "packs"
        packs.mkdir()
        print(f"Working on an isolated copy under {work} (live DB never written)")

        run_id = _latest_complete_run(db)
        # Clean precondition on the COPY: clear any prior governance state for the run
        # so the "not yet fit / pre-fit export refused" checks hold regardless of live.
        _rcon = duckdb.connect(db)
        try:
            _rcon.execute("DELETE FROM gold_ae_governance_events WHERE study_run_id = ?", [run_id])
            _rcon.execute(
                "DELETE FROM gold_governance_signoffs "
                "WHERE artifact_type = 'STUDY_RUN' AND artifact_id = ?",
                [run_id],
            )
        finally:
            _rcon.close()
        who = {u: get_user_by_username(u, db_path=db)
               for u in ("a.analyst", "j.junior", "s.senior", "c.chief")}
        for u, obj in who.items():
            assert obj is not None, f"seeded user {u} missing"
        by_role = {
            "junior_actuary": who["j.junior"],
            "senior_actuary": who["s.senior"],
            "chief_actuary": who["c.chief"],
        }

        # --- 5.7 (guard): a not-yet-fit run refuses export --------------------
        submit_study_run(run_id, who["a.analyst"].user_id, detail="UAT 5.6 submit", db_path=db)
        try:
            export_compliance_pack(ArtifactType.STUDY_RUN, run_id, "html",
                                   db_path=db, config_path=CONFIG, output_dir=str(packs))
            record("5.7 (pre-fit export refused)", False, "export did NOT refuse a not-fit run")
        except ValueError as e:
            record("5.7 (pre-fit export refused)", True, f"ValueError: {e}")

        # --- make the run fit via the full governed chain ---------------------
        guard = 0
        while True:
            lvl = next_required_level(ArtifactType.STUDY_RUN, run_id, db_path=db, config_path=CONFIG)
            if lvl is None:
                break
            guard += 1
            assert guard <= 5, "chain did not terminate"
            signer = by_role[lvl.required_role.value]
            record_signoff(signer, ArtifactType.STUDY_RUN, run_id, None, Decision.APPROVE,
                           f"L{lvl.level} approve ({signer.username})", db_path=db, config_path=CONFIG)
        fit = is_study_run_fit(run_id, db_path=db, config_path=CONFIG)
        record("5.6-pre (run fit after full chain)", fit is True, f"is_study_run_fit={fit}")

        # --- 5.6: export the study-run compliance pack ------------------------
        try:
            path = export_compliance_pack(ArtifactType.STUDY_RUN, run_id, "html",
                                          db_path=db, config_path=CONFIG, output_dir=str(packs))
        except Exception as e:  # noqa: BLE001
            record("5.6 (fit run exports pack)", False, f"{type(e).__name__}: {e}")
            return _summary()
        html = Path(path).read_text(encoding="utf-8")
        record("5.6 (fit run exports pack)", bool(path) and Path(path).exists(), f"wrote {Path(path).name}")

        # --- content assertions: sign-offs/attestations + audit + reports -----
        signers_present = all(n in html for n in ("J. Junior", "S. Senior", "C. Chief"))
        record("5.6-a (three sign-offs shown)", signers_present,
               f"junior/senior/chief all present={signers_present}")

        attest_present = "I attest that I have reviewed this artifact" in html
        record("5.6-b (attestations present)", attest_present, f"attestation text present={attest_present}")

        audit_present = "Audit Trail" in html and "STUDY_RUN_SUBMITTED" in html
        record("5.6-c (audit excerpt present)", audit_present,
               f"audit section + submit event present={audit_present}")

        reports_present = f"working_actuary_{run_id[:8]}" in html or "Supporting Reports" in html
        record("5.6-d (supporting report links)", reports_present,
               f"report links present={reports_present}")

        # --- live DB untouched (sanity) --------------------------------------
        con = duckdb.connect(str(LIVE_DB), read_only=True)
        live_events_after = con.execute("SELECT COUNT(*) FROM gold_ae_governance_events").fetchone()[0]
        con.close()
        record("5.6-e (live DB untouched)", live_events_after == live_events_before,
               f"live gold_ae_governance_events rows={live_events_after} (unchanged from {live_events_before})")

        return _summary()


def _summary() -> int:
    print("\n================ Test 5.6 summary ================")
    for row, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {row}")
    n_pass = sum(1 for _, ok, _ in _results if ok)
    print("  ------------------------------------------------")
    print(f"  {n_pass}/{len(_results)} checks PASS")
    return 0 if n_pass == len(_results) else 1


if __name__ == "__main__":
    sys.exit(run())
