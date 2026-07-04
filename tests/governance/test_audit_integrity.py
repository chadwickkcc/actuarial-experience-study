"""Tests for the Session-26 audit trail & tamper-evidence layer.

Realises the §I.3 acceptance for FR-4-19…22 / NFR-G-04: the new hash-chained
``gold_ae_governance_events`` log + the §G.5 additive hash columns on the Phase-2
logs; ``record_ae_event`` / ``submit_study_run``; ``verify_chain`` (passes on an
untouched log, fails on a constructed tamper with the correct
``first_divergence_seq``); and ``unified_audit_query`` / ``artifact_timeline``
spanning the three physically-separate governance logs.

Uses the shared ``gov_env`` fixture (a temp DB with the four seeded users).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
import yaml

from src.governance import audit
from src.governance.audit import (
    artifact_timeline,
    record_ae_event,
    submit_study_run,
    unified_audit_query,
    verify_chain,
)
from src.governance.users import get_user_by_username
from src.governance.workflow import record_signoff
from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    save_assumption_set,
)
from src.utils.db_init import init_database
from src.utils.types import (
    ArtifactType,
    AssumptionSetStatus,
    AuditFilter,
    Decision,
    IntegrityResult,
    Role,
    User,
)

try:  # AI audit log writer (Phase 3); import is offline-safe.
    from src.ai.chatbot.audit import write_audit_row
except Exception:  # pragma: no cover
    write_audit_row = None


_ATTEST = "I attest that I have reviewed this artifact."


# --------------------------------------------------------------------------
# seeding helpers
# --------------------------------------------------------------------------
def _append_signoff(db: str, **overrides) -> str:
    content = {
        "signoff_id": str(uuid.uuid4()),
        "artifact_type": "ASSUMPTION_SET",
        "artifact_id": "set-1",
        "artifact_version": 1,
        "chain_level": 1,
        "required_role": "junior_actuary",
        "actor_user_id": "u-1",
        "actor_role": "junior_actuary",
        "decision": "APPROVE",
        "comment": "ok",
        "attestation_text": _ATTEST,
        "delta_tev": None,
        "required_final_level": 3,
        "signoff_ts": datetime.utcnow(),
    }
    content.update(overrides)
    return audit.append_event("gold_governance_signoffs", content, db_path=db)


def _cols(db: str, table: str) -> list[str]:
    con = duckdb.connect(db, read_only=True)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",
                [table],
            ).fetchall()
        ]
    finally:
        con.close()


# --------------------------------------------------------------------------
# DDL / migration
# --------------------------------------------------------------------------
def test_ae_events_table_created(gov_env):
    cols = _cols(gov_env["db"], "gold_ae_governance_events")
    assert cols == [
        "event_id", "seq", "event_type", "study_run_id", "actor_user_id",
        "detail", "event_ts", "prev_hash", "entry_hash",
    ]


def test_ae_event_columns_lock(gov_env):
    """The physical column order matches the audit module's constant (drift lock)."""
    assert _cols(gov_env["db"], "gold_ae_governance_events") == audit._AE_EVENT_COLUMNS


def test_phase2_hash_columns_migrated_and_idempotent(gov_env):
    db = gov_env["db"]
    for table in ("gold_workflow_iterations", "gold_assumption_approvals"):
        cols = _cols(db, table)
        assert {"seq", "prev_hash", "entry_hash"} <= set(cols)
    # Re-running init_database is a no-op (idempotent migrations).
    init_database(db)
    assert {"seq", "prev_hash", "entry_hash"} <= set(_cols(db, "gold_workflow_iterations"))


# --------------------------------------------------------------------------
# writers + registry
# --------------------------------------------------------------------------
def test_append_event_registry_includes_ae_events(gov_env):
    db = gov_env["db"]
    h = audit.append_event(
        "gold_ae_governance_events",
        {
            "event_id": str(uuid.uuid4()),
            "event_type": "STUDY_RUN_SUBMITTED",
            "study_run_id": "run-1",
            "actor_user_id": "u-1",
            "detail": None,
            "event_ts": datetime.utcnow(),
        },
        db_path=db,
    )
    assert len(h) == 64
    with pytest.raises(ValueError):
        audit.append_event("gold_ae_results", {}, db_path=db)


def test_record_ae_event_roundtrip(gov_env):
    db = gov_env["db"]
    h1 = record_ae_event("STUDY_RUN_SUBMITTED", "run-1", "u-1", "submitted", db_path=db)
    h2 = submit_study_run("run-1", "u-2", db_path=db)  # second event, same run
    con = duckdb.connect(db, read_only=True)
    try:
        rows = con.execute(
            "SELECT seq, prev_hash, entry_hash FROM gold_ae_governance_events ORDER BY seq"
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == "" and rows[0][2] == h1
    assert rows[1][1] == h1 and rows[1][2] == h2       # chain links
    assert all(len(r[2]) == 64 for r in rows)


# --------------------------------------------------------------------------
# verify_chain
# --------------------------------------------------------------------------
def test_verify_chain_ok_untouched(gov_env):
    db = gov_env["db"]
    for _ in range(3):
        _append_signoff(db)
    record_ae_event("STUDY_RUN_SUBMITTED", "run-1", "u-1", db_path=db)
    record_ae_event("STUDY_RUN_APPROVED", "run-1", "u-2", db_path=db)

    r1 = verify_chain("gold_governance_signoffs", db_path=db)
    r2 = verify_chain("gold_ae_governance_events", db_path=db)
    assert r1 == IntegrityResult("gold_governance_signoffs", True, None, 3)
    assert r2 == IntegrityResult("gold_ae_governance_events", True, None, 2)


def test_verify_chain_empty_and_unhashed_phase2(gov_env):
    db = gov_env["db"]
    # Nothing written yet: every hash-chained log verifies clean at 0 rows.
    for table in audit._VERIFIABLE_CHAINS:
        res = verify_chain(table, db_path=db)
        assert res.ok and res.first_divergence_seq is None and res.rows_checked == 0


def test_verify_chain_ignores_unhashed_phase2_rows(gov_env):
    """A plain Phase-2 row (NULL hashes) is skipped; the chain begins at first hash."""
    db = gov_env["db"]
    con = duckdb.connect(db)
    try:
        con.execute(
            "INSERT INTO gold_workflow_iterations "
            "(iteration_id, workflow_session_id, iteration_number, assumption_set_id, "
            " stage, action, actuary_id, iteration_ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [str(uuid.uuid4()), "sess-1", 1, "set-1", 2, "SAVED", "actuary-1",
             datetime.utcnow()],
        )
    finally:
        con.close()
    res = verify_chain("gold_workflow_iterations", db_path=db)
    assert res.ok and res.rows_checked == 0


def test_verify_chain_detects_business_column_tamper(gov_env):
    db = gov_env["db"]
    for i in range(3):
        record_ae_event("STUDY_RUN_SUBMITTED", "run-1", f"u-{i}", f"detail-{i}", db_path=db)
    con = duckdb.connect(db)
    try:
        con.execute("UPDATE gold_ae_governance_events SET detail='TAMPERED' WHERE seq=2")
    finally:
        con.close()
    res = verify_chain("gold_ae_governance_events", db_path=db)
    assert res.ok is False
    assert res.first_divergence_seq == 2
    assert res.rows_checked == 2  # stops at the first failing row


def test_verify_chain_detects_broken_linkage(gov_env):
    db = gov_env["db"]
    for i in range(3):
        record_ae_event("STUDY_RUN_SUBMITTED", "run-1", f"u-{i}", None, db_path=db)
    con = duckdb.connect(db)
    try:
        con.execute("UPDATE gold_ae_governance_events SET prev_hash=? WHERE seq=3", ["d" * 64])
    finally:
        con.close()
    res = verify_chain("gold_ae_governance_events", db_path=db)
    assert res.ok is False
    assert res.first_divergence_seq == 3


def test_verify_chain_rejects_unknown_table(gov_env):
    with pytest.raises(ValueError):
        verify_chain("gold_ae_results", db_path=gov_env["db"])


def test_verify_chain_float_and_tzaware_roundtrip(gov_env):
    """The N-row verifier honours the same normalisation as the single-row write."""
    db = gov_env["db"]
    ts = datetime(2026, 6, 29, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    _append_signoff(db, delta_tev=0.0123456789, signoff_ts=ts, required_final_level=2)
    _append_signoff(db, delta_tev=-4_480_000.5)
    res = verify_chain("gold_governance_signoffs", db_path=db)
    assert res.ok and res.rows_checked == 2


# --------------------------------------------------------------------------
# unified_audit_query + artifact_timeline
# --------------------------------------------------------------------------
def _seed_ai_row(db: str, **kw) -> None:
    assert write_audit_row is not None
    row = {
        "source": "CHATBOT",
        "session_id": "sess-1",
        "intent": "FACTUAL_LOOKUP",
        "model_string": "mock-model",
        "response_text": "WL mortality A/E is 0.5718.",
        "blocked": False,
    }
    row.update(kw)
    write_audit_row(row, db_path=db)


def test_unified_audit_query_spans_three_logs(gov_env):
    db = gov_env["db"]
    _append_signoff(db)                                        # SIGNOFF log
    record_ae_event("STUDY_RUN_SUBMITTED", "run-1", "u-1", db_path=db)  # AE_EVENT log
    _seed_ai_row(db)                                           # AI log

    events = unified_audit_query(AuditFilter(), db_path=db)
    sources = {e["source"] for e in events}
    assert {"SIGNOFF", "AE_EVENT", "AI"} <= sources
    # common event shape present on every row
    for e in events:
        for key in ("ts", "actor", "role", "artifact_type", "artifact_id", "action",
                    "detail", "source"):
            assert key in e


def test_unified_audit_query_resolves_display_name(gov_env):
    db = gov_env["db"]
    user = get_user_by_username("s.senior", db)
    _append_signoff(db, actor_user_id=user.user_id, actor_role="senior_actuary")
    events = unified_audit_query(AuditFilter(artifact_id="set-1"), db_path=db)
    sign = [e for e in events if e["source"] == "SIGNOFF"][0]
    assert sign["actor"] == user.display_name
    assert sign["role"] == "senior_actuary"


def test_audit_filter_dimensions(gov_env):
    db = gov_env["db"]
    record_ae_event("STUDY_RUN_SUBMITTED", "run-1", "u-A", db_path=db)
    record_ae_event("STUDY_RUN_APPROVED", "run-2", "u-B", db_path=db)
    _append_signoff(db, actor_role="senior_actuary")

    # actor
    assert {e["actor_user_id"] for e in
            unified_audit_query(AuditFilter(actor_user_id="u-A"), db_path=db)} == {"u-A"}
    # action
    acts = unified_audit_query(AuditFilter(action="STUDY_RUN_APPROVED"), db_path=db)
    assert acts and all(e["action"] == "STUDY_RUN_APPROVED" for e in acts)
    # role (on the signoff row)
    roles = unified_audit_query(AuditFilter(role=Role.SENIOR_ACTUARY), db_path=db)
    assert roles and all(e["role"] == "senior_actuary" for e in roles)
    assert unified_audit_query(AuditFilter(role=Role.CHIEF_ACTUARY), db_path=db) == []
    # date: today includes, tomorrow excludes. Events store naive UTC timestamps,
    # so compare against the UTC date to avoid a tz-boundary flake (local date can
    # be a day ahead of UTC, e.g. before UTC midnight in GMT+8).
    today = datetime.utcnow().date()
    assert unified_audit_query(AuditFilter(date_from=today), db_path=db)
    assert unified_audit_query(
        AuditFilter(date_from=today + timedelta(days=1)), db_path=db
    ) == []


def test_artifact_timeline_chronology(gov_env):
    db = gov_env["db"]
    record_ae_event("STUDY_RUN_SUBMITTED", "run-1", "u-1", db_path=db)
    record_ae_event("STUDY_RUN_APPROVED", "run-1", "u-2", db_path=db)
    record_ae_event("STUDY_RUN_SUBMITTED", "run-OTHER", "u-9", db_path=db)  # unrelated

    tl = artifact_timeline(ArtifactType.STUDY_RUN, "run-1", db_path=db)
    assert [e["action"] for e in tl] == ["STUDY_RUN_SUBMITTED", "STUDY_RUN_APPROVED"]
    assert all(e["artifact_id"] == "run-1" for e in tl)  # unrelated run excluded


def test_artifact_timeline_and_filter_are_case_insensitive(gov_env):
    """UAT 4.2: an uppercase/mixed-case id matches the lowercase stored id.

    Governance ids are stored lowercase (UUIDs); a user entering an uppercased id
    in the Governance & Audit page must still resolve the artifact's history rather
    than falsely reporting "No history recorded".
    """
    db = gov_env["db"]
    stored = "e290621f-c1d1-4a08-bfa4-8e64b209e813"  # lowercase, as DuckDB stores it
    record_ae_event("STUDY_RUN_SUBMITTED", stored, "u-1", db_path=db)
    record_ae_event("STUDY_RUN_APPROVED", stored, "u-2", db_path=db)

    baseline = artifact_timeline(ArtifactType.STUDY_RUN, stored, db_path=db)
    assert [e["action"] for e in baseline] == ["STUDY_RUN_SUBMITTED", "STUDY_RUN_APPROVED"]

    # Uppercase and mixed-case inputs return the identical timeline.
    assert artifact_timeline(ArtifactType.STUDY_RUN, stored.upper(), db_path=db) == baseline
    assert (
        artifact_timeline(ArtifactType.STUDY_RUN, "E290621F-c1d1-4A08-bfa4-8E64b209e813", db_path=db)
        == baseline
    )

    # The Section-A unified filter is case-insensitive on artifact_id too.
    upper_rows = unified_audit_query(AuditFilter(artifact_id=stored.upper()), db_path=db)
    assert upper_rows and all(r["artifact_id"] == stored for r in upper_rows)


# --------------------------------------------------------------------------
# forward-safety of the Phase-2 verify column lists + append-only deletion
# --------------------------------------------------------------------------
def test_phase2_column_constants_match_physical(gov_env):
    """The verify registry's Phase-2 column lists equal the real table columns.

    A future hash-chaining retrofit hashes over exactly these columns, so a DDL
    column added without updating the constant would silently corrupt the chain.
    """
    db = gov_env["db"]
    for table, const in (
        ("gold_workflow_iterations", audit._WORKFLOW_ITER_COLUMNS),
        ("gold_assumption_approvals", audit._ASSUMPTION_APPROVAL_COLUMNS),
    ):
        assert _cols(db, table) == const


def test_verify_chain_detects_deleted_middle_row(gov_env):
    """Deleting a middle row (append-only violation) breaks the linkage."""
    db = gov_env["db"]
    for i in range(3):
        record_ae_event("STUDY_RUN_SUBMITTED", "run-1", f"u-{i}", None, db_path=db)
    con = duckdb.connect(db)
    try:
        con.execute("DELETE FROM gold_ae_governance_events WHERE seq=2")
    finally:
        con.close()
    res = verify_chain("gold_ae_governance_events", db_path=db)
    assert res.ok is False and res.first_divergence_seq == 3


# --------------------------------------------------------------------------
# natural-hook event emission (guarded, from record_signoff)
# --------------------------------------------------------------------------
_PERMISSIONS = {
    "analyst":        ["propose", "view"],
    "junior_actuary": ["sign_off", "view", "export"],
    "senior_actuary": ["sign_off", "view", "export"],
    "chief_actuary":  ["sign_off", "view", "export"],
}


def _u(db: str, username: str) -> User:
    user = get_user_by_username(username, db)
    assert user is not None
    return user


def _chain_config(path: Path, chain: list[str]) -> str:
    cfg = {
        "permissions": _PERMISSIONS,
        "approval_chain": [
            {"level": i + 1, "required_role": r} for i, r in enumerate(chain)
        ],
        "segregation": {"allow_multi_level_signoff": False},
        "materiality": {
            "delta_tev_threshold": 0.01,
            "final_level_below_threshold": "senior_actuary",
        },
        "attestation_text": _ATTEST,
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def _ae_events(db: str) -> list[tuple]:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT event_type, study_run_id, actor_user_id FROM gold_ae_governance_events "
            "ORDER BY seq"
        ).fetchall()
    finally:
        con.close()


def _seed_set(db: str, author: str) -> str:
    set_id = str(uuid.uuid4())
    aset = AssumptionSet(
        id=set_id, version=1, status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(), author_id=author,
        basis="best-estimate", source_study_run_id="run-1",
        rdr=0.09, earned_rate_ga=0.05, earned_rate_sa=0.06, tax_rate=0.21,
        expense_inflation=0.025, rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0, maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=[
            DecrementMultiplier(
                product="TERM", gender="M", risk_class="STD_NS",
                duration_band=[1, 5], multiplier=1.0, credibility_z=0.8,
                credibility_lower=0.7, credibility_upper=1.1, override_rationale="",
            )
        ],
        lapse_multipliers=[], surrender_multipliers=[], ci_incidence_multipliers=[],
        premium_persistency=[], shock_lapse_plt={},
        yaml_file_path=str(Path(db).parent / "assumption_sets" / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def test_study_run_approve_emits_ae_event(gov_env, tmp_path):
    """A completing study-run APPROVE records a STUDY_RUN_APPROVED A/E event."""
    db = gov_env["db"]
    cfg = _chain_config(tmp_path / "single_chief.yaml", ["chief_actuary"])
    run_id = "run-approve"
    record_signoff(
        _u(db, "c.chief"), ArtifactType.STUDY_RUN, run_id, None,
        Decision.APPROVE, "fit for assumption-setting", db_path=db, config_path=cfg,
    )
    events = _ae_events(db)
    assert [(e[0], e[1]) for e in events] == [("STUDY_RUN_APPROVED", run_id)]
    # chain still verifies; the event is surfaced in the unified stream
    assert verify_chain("gold_ae_governance_events", db_path=db).ok
    stream = unified_audit_query(AuditFilter(artifact_id=run_id), db_path=db)
    assert {"SIGNOFF", "AE_EVENT"} <= {e["source"] for e in stream}


def test_study_run_return_emits_ae_event(gov_env, tmp_path):
    """A study-run RETURN records a STUDY_RUN_RETURNED A/E event."""
    db = gov_env["db"]
    cfg = _chain_config(tmp_path / "single_chief.yaml", ["chief_actuary"])
    run_id = "run-return"
    record_signoff(
        _u(db, "c.chief"), ArtifactType.STUDY_RUN, run_id, None,
        Decision.RETURN, "needs rework", db_path=db, config_path=cfg,
    )
    assert [(e[0], e[1]) for e in _ae_events(db)] == [("STUDY_RUN_RETURNED", run_id)]


def test_assumption_set_signoff_emits_no_ae_event(gov_env, tmp_path):
    """The assumption-set sign-off path is unchanged — it writes no A/E event."""
    db = gov_env["db"]
    cfg = _chain_config(tmp_path / "single_chief.yaml", ["chief_actuary"])
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(
        _u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
        Decision.APPROVE, "final", db_path=db, config_path=cfg, delta_tev=0.005,
    )
    assert _ae_events(db) == []
