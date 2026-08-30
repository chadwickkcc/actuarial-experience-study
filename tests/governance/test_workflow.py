"""Tests for the Phase-4 configurable approval-chain engine (Session 25).

Realises the §I.3 acceptance for FR-4-12…18 and NFR-G-03/G-08: the hash-chained
``gold_governance_signoffs`` log + ``append_event``, ``load_chain`` /
``required_final_level`` / ``next_required_level``, sequential multi-level
``record_signoff`` (with materiality-driven final level and attestation), governed
``reopen``, A/E study-run approval, and the ``pending_approvals`` queue.

Uses the shared ``gov_env`` fixture (a temp DB with the four seeded users) plus a
locally-written governance config that carries the approval chain / materiality /
segregation blocks (the shared config only carries roles/permissions/users).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
import yaml

from src.governance import audit
from src.governance.rbac import PermissionDenied
from src.governance.users import get_user_by_username
from src.governance.workflow import (
    SegregationViolation,
    is_study_run_fit,
    load_chain,
    next_required_level,
    pending_approvals,
    record_signoff,
    reopen,
    required_final_level,
)
from src.assumptions.assumption_set import AssumptionSet, DecrementMultiplier
from src.assumptions.assumption_set import save_assumption_set
from src.utils.db_init import init_database
from src.utils.types import (
    ArtifactType,
    AssumptionSetStatus,
    Decision,
    User,
)

_PERMISSIONS = {
    "analyst":        ["propose", "view"],
    "junior_actuary": ["sign_off", "view", "export"],
    "senior_actuary": ["sign_off", "view", "export"],
    "chief_actuary":  ["sign_off", "view", "export"],
}

_DEFAULT_CHAIN = ["junior_actuary", "senior_actuary", "chief_actuary"]

_ATTEST = "I attest that I have reviewed this artifact and it is fit for its stated purpose."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_chain_config(
    path: Path,
    chain: list[str] = None,
    *,
    allow_multi: bool = False,
    threshold: float = 0.01,
    final_below: str = "senior_actuary",
) -> str:
    """Write a governance config (permissions + chain + materiality + segregation)."""
    chain = chain or _DEFAULT_CHAIN
    cfg = {
        "permissions": _PERMISSIONS,
        "approval_chain": [
            {"level": i + 1, "required_role": role} for i, role in enumerate(chain)
        ],
        "segregation": {"allow_multi_level_signoff": allow_multi},
        "materiality": {
            "max_multiplier_delta_threshold": threshold,
            "final_level_below_threshold": final_below,
        },
        "attestation_text": _ATTEST,
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)
    return str(path)


def _u(db: str, username: str) -> User:
    user = get_user_by_username(username, db)
    assert user is not None, f"seeded user {username} missing"
    return user


def _seed_set(
    db: str,
    *,
    author: str,
    status: AssumptionSetStatus = AssumptionSetStatus.PROPOSED,
    set_id: str | None = None,
    version: int = 1,
    source_run: str = "run-1",
) -> str:
    """Seed a real YAML-backed assumption set with a chosen author/status; return id."""
    set_id = set_id or str(uuid.uuid4())
    yaml_dir = Path(db).parent / "assumption_sets"
    aset = AssumptionSet(
        id=set_id,
        version=version,
        status=status,
        effective_date=date.today().isoformat(),
        author_id=author,
        basis="best-estimate",
        source_study_run_id=source_run,
        mortality_multipliers=[
            DecrementMultiplier(
                product="TERM", gender="M", risk_class="STD_NS",
                duration_band=[1, 5], multiplier=1.0, credibility_z=0.8,
                credibility_lower=0.7, credibility_upper=1.1, override_rationale="",
            )
        ],
        lapse_multipliers=[],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={},
        yaml_file_path=str(yaml_dir / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _status(db: str, set_id: str) -> str:
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()
    finally:
        con.close()
    return row[0]


@pytest.fixture()
def cfg_path(gov_env, tmp_path) -> str:
    """The default junior→senior→chief governance config for the gov_env DB."""
    return _write_chain_config(tmp_path / "gov_full.yaml")


# ---------------------------------------------------------------------------
# Schema + append_event (§G.2 / §H.7 / FR-4-20)
# ---------------------------------------------------------------------------

def test_init_creates_signoffs_table_with_expected_columns(tmp_path):
    """init_database creates gold_governance_signoffs with the §G.2 columns, in order."""
    db = tmp_path / "schema.duckdb"
    init_database(str(db))
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name='gold_governance_signoffs' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        con.close()
    assert cols == audit._SIGNOFF_COLUMNS  # DDL ↔ writer column-alignment lock


def test_init_signoffs_idempotent(tmp_path):
    """Re-running init_database does not error or duplicate the table."""
    db = tmp_path / "idem.duckdb"
    init_database(str(db))
    init_database(str(db))  # must not raise


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
        "materiality_value": None,
        "required_final_level": 3,
        "signoff_ts": datetime.utcnow(),
    }
    content.update(overrides)
    return audit.append_event("gold_governance_signoffs", content, db_path=db)


def test_append_event_first_row_empty_prev_hash(gov_env):
    """The first hash-chained row gets seq=1 and an empty prev_hash."""
    _append_signoff(gov_env["db"])
    con = duckdb.connect(gov_env["db"], read_only=True)
    try:
        seq, prev_hash, entry_hash = con.execute(
            "SELECT seq, prev_hash, entry_hash FROM gold_governance_signoffs"
        ).fetchone()
    finally:
        con.close()
    assert seq == 1
    assert prev_hash == ""
    assert len(entry_hash) == 64


def test_append_event_links_prev_hash_and_increments_seq(gov_env):
    """Each appended row's prev_hash is the prior row's entry_hash; seq increments."""
    h1 = _append_signoff(gov_env["db"])
    h2 = _append_signoff(gov_env["db"])
    con = duckdb.connect(gov_env["db"], read_only=True)
    try:
        rows = con.execute(
            "SELECT seq, prev_hash, entry_hash FROM gold_governance_signoffs ORDER BY seq"
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][2] == h1 and rows[1][2] == h2
    assert rows[1][1] == h1  # second row's prev_hash links to the first's entry_hash


def test_append_event_entry_hash_recomputes_from_stored_columns(gov_env):
    """entry_hash recomputes from the stored business columns by the §G.2 rule."""
    _append_signoff(gov_env["db"])
    con = duckdb.connect(gov_env["db"], read_only=True)
    try:
        cols = [c for c in audit._SIGNOFF_COLUMNS]
        row = con.execute(
            f"SELECT {', '.join(cols)} FROM gold_governance_signoffs"
        ).fetchone()
    finally:
        con.close()
    stored = dict(zip(cols, row))
    canonical_cols = [c for c in cols if c not in ("prev_hash", "entry_hash")]
    canonical = audit._canonical_row({c: stored[c] for c in canonical_cols})
    recomputed = audit._entry_hash(canonical, stored["prev_hash"] or "")
    assert recomputed == stored["entry_hash"]


def test_append_event_rejects_unknown_table(gov_env):
    """append_event refuses a table not in the hash-chained registry."""
    with pytest.raises(ValueError):
        audit.append_event("gold_ae_results", {"x": 1}, db_path=gov_env["db"])


# ---------------------------------------------------------------------------
# Chain config + materiality (FR-4-12/14/16)
# ---------------------------------------------------------------------------

def test_load_chain_default(cfg_path):
    chain = load_chain(yaml.safe_load(Path(cfg_path).read_text()))
    assert [lvl.required_role.value for lvl in chain] == _DEFAULT_CHAIN
    assert [lvl.level for lvl in chain] == [1, 2, 3]


def test_required_final_level_study_run_full_chain(cfg_path):
    """A study run (materiality None) always requires the full chain (FR-4-14)."""
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    assert required_final_level(None, cfg) == 3


def test_required_final_level_material_forces_chief(cfg_path):
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    assert required_final_level(0.05, cfg) == 3  # > 0.01 -> chief level


def test_required_final_level_below_threshold_senior(cfg_path):
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    assert required_final_level(0.005, cfg) == 2  # <= 0.01 -> senior level


def test_required_final_level_at_threshold_is_below_branch(cfg_path):
    """A value exactly at the threshold is NOT material (FR-4-16: 'above' forces chief)."""
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    assert required_final_level(0.01, cfg) == 2  # == threshold -> below branch (senior)


def test_required_final_level_negative_delta_uses_magnitude(cfg_path):
    """A negative value is assessed on its magnitude."""
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    assert required_final_level(-0.05, cfg) == 3   # |−0.05| > 0.01 -> chief
    assert required_final_level(-0.005, cfg) == 2  # |−0.005| <= 0.01 -> senior


def test_required_final_level_role_not_in_chain_falls_back_to_final(gov_env, tmp_path):
    """A single-chief chain has no senior level; a below-threshold change falls back
    to the final (chief) level rather than raising (robustness)."""
    single = _write_chain_config(tmp_path / "single.yaml", ["chief_actuary"])
    cfg = yaml.safe_load(Path(single).read_text())
    assert required_final_level(0.005, cfg) == 1   # senior absent -> last level (chief)
    assert required_final_level(0.05, cfg) == 1     # chief present


# ---------------------------------------------------------------------------
# record_signoff — full chain, materiality, return, ordering (FR-4-13/15/16)
# ---------------------------------------------------------------------------

def test_full_chain_material_locks_set(gov_env, cfg_path):
    """A material change runs junior→senior→chief; the final APPROVE locks the set
    (FR-4-16)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    for uname in ("j.junior", "s.senior", "c.chief"):
        record_signoff(
            _u(db, uname), ArtifactType.ASSUMPTION_SET, set_id, 1,
            Decision.APPROVE, f"reviewed by {uname}",
            db_path=db, config_path=cfg_path, materiality_value=0.05,
        )
    assert _status(db, set_id) == "APPROVED"
    assert next_required_level(
        ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg_path
    ) is None
    con = duckdb.connect(db, read_only=True)
    try:
        n_signoffs = con.execute(
            "SELECT COUNT(*) FROM gold_governance_signoffs WHERE artifact_id = ?",
            [set_id],
        ).fetchone()[0]
        stored_mat = con.execute(
            "SELECT materiality_value FROM gold_governance_signoffs "
            "WHERE artifact_id = ? ORDER BY seq LIMIT 1",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()
    assert n_signoffs == 3
    assert stored_mat == pytest.approx(0.05)


def test_below_threshold_completes_at_senior(gov_env, cfg_path):
    """An immaterial change completes at senior; chief is never required (FR-4-16)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "jr", db_path=db, config_path=cfg_path, materiality_value=0.005)
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "sr", db_path=db, config_path=cfg_path, materiality_value=0.005)
    assert _status(db, set_id) == "APPROVED"
    assert next_required_level(
        ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg_path
    ) is None


def test_sequential_order_enforced(gov_env, cfg_path):
    """A later level cannot sign before the prior level (FR-4-13)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    with pytest.raises(PermissionDenied):
        record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "early", db_path=db, config_path=cfg_path, materiality_value=0.05)


def test_return_resets_to_proposed(gov_env, cfg_path):
    """A RETURN records the row and resets the set to PROPOSED (editable) (FR-4-13)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.RETURN, "needs work", db_path=db, config_path=cfg_path, materiality_value=0.05)
    assert _status(db, set_id) == "PROPOSED"
    # round reset: the next required level is back to level 1 (junior)
    nxt = next_required_level(ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg_path)
    assert nxt is not None and nxt.level == 1


def test_comment_mandatory(gov_env, cfg_path):
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    with pytest.raises(ValueError):
        record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "   ", db_path=db, config_path=cfg_path, materiality_value=0.05)


def test_analyst_cannot_sign_off(gov_env, cfg_path, caplog):
    """An analyst has no sign_off permission; the denial raises and is logged (FR-4-04)."""
    import logging
    db = gov_env["db"]
    set_id = _seed_set(db, author="someone.else")
    with caplog.at_level(logging.WARNING, logger="governance.rbac"):
        with pytest.raises(PermissionDenied):
            record_signoff(_u(db, "a.analyst"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                           Decision.APPROVE, "x", db_path=db, config_path=cfg_path, materiality_value=0.05)
    assert any("RBAC denied" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# A/E study-run approval (FR-4-14)
# ---------------------------------------------------------------------------

def test_study_run_runs_full_chain(gov_env, cfg_path):
    """A study run always runs the full chain; 'fit' is derived from sign-offs (FR-4-14)."""
    db = gov_env["db"]
    run_id = "studyrun-1"
    assert is_study_run_fit(run_id, db_path=db, config_path=cfg_path) is False
    for uname in ("j.junior", "s.senior"):
        record_signoff(_u(db, uname), ArtifactType.STUDY_RUN, run_id, None,
                       Decision.APPROVE, f"by {uname}", db_path=db, config_path=cfg_path)
        # not fit until the full chain completes
        assert is_study_run_fit(run_id, db_path=db, config_path=cfg_path) is False
    record_signoff(_u(db, "c.chief"), ArtifactType.STUDY_RUN, run_id, None,
                   Decision.APPROVE, "by chief", db_path=db, config_path=cfg_path)
    assert is_study_run_fit(run_id, db_path=db, config_path=cfg_path) is True


# ---------------------------------------------------------------------------
# Legacy single-reviewer reproduction (FR-4-12; NFR-G-08)
# ---------------------------------------------------------------------------

def test_single_chief_chain_reproduces_legacy(gov_env, tmp_path):
    """A single-chief_actuary chain reproduces the legacy single-reviewer sign-off."""
    db = gov_env["db"]
    single = _write_chain_config(tmp_path / "single.yaml", ["chief_actuary"])
    set_id = _seed_set(db, author="a.analyst")
    rec = record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                         Decision.APPROVE, "final sign-off", db_path=db, config_path=single)
    assert rec.chain_level == 1
    assert _status(db, set_id) == "APPROVED"


# ---------------------------------------------------------------------------
# Governed re-open (FR-4-18)
# ---------------------------------------------------------------------------

def test_reopen_creates_draft_child_and_preserves_original(gov_env, tmp_path):
    db = gov_env["db"]
    single = _write_chain_config(tmp_path / "single.yaml", ["chief_actuary"])
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "ok", db_path=db, config_path=single)
    assert _status(db, set_id) == "APPROVED"

    child = reopen(set_id, _u(db, "a.analyst"), "macro update warrants a revision", db_path=db)
    con = duckdb.connect(db, read_only=True)
    try:
        crow = con.execute(
            "SELECT status, parent_set_id, version FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [child],
        ).fetchone()
    finally:
        con.close()
    assert child != set_id
    assert crow[0] == "DRAFT"
    assert crow[1] == set_id          # parent link
    assert crow[2] == 2               # version incremented
    assert _status(db, set_id) == "APPROVED"   # original untouched


def test_reopen_requires_justification(gov_env):
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.APPROVED)
    with pytest.raises(ValueError):
        reopen(set_id, _u(db, "a.analyst"), "   ", db_path=db)


# ---------------------------------------------------------------------------
# Pending-approvals queue (FR-4-17)
# ---------------------------------------------------------------------------

def test_pending_approvals_filters_by_role(gov_env, cfg_path):
    """A PROPOSED set appears for the junior (its next level), not the senior."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    jr_pending = pending_approvals(_u(db, "j.junior"), db_path=db, config_path=cfg_path)
    sr_pending = pending_approvals(_u(db, "s.senior"), db_path=db, config_path=cfg_path)
    assert any(p["artifact_id"] == set_id for p in jr_pending)
    assert all(p["artifact_id"] != set_id for p in sr_pending)


def test_pending_approvals_includes_stage3_approved(gov_env, cfg_path):
    """The Phase-2 shell submits to the chain at STAGE3_APPROVED — it must be pending."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.STAGE3_APPROVED)
    jr_pending = pending_approvals(_u(db, "j.junior"), db_path=db, config_path=cfg_path)
    assert any(p["artifact_id"] == set_id for p in jr_pending)


# ---------------------------------------------------------------------------
# Robustness — completion guard, single-chief materiality, multi-round
# ---------------------------------------------------------------------------

def test_single_chief_below_threshold_completes_at_chief(gov_env, tmp_path):
    """A single-chief chain approves a below-threshold change at the chief level
    (the materiality fallback must not crash when senior is absent)."""
    db = gov_env["db"]
    single = _write_chain_config(tmp_path / "single.yaml", ["chief_actuary"])
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "final", db_path=db, config_path=single, materiality_value=0.005)
    assert _status(db, set_id) == "APPROVED"


def test_complete_chain_rejects_extra_signoff(gov_env, cfg_path):
    """Once the chain is complete, a further sign-off attempt is rejected."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    for uname in ("j.junior", "s.senior", "c.chief"):
        record_signoff(_u(db, uname), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, f"by {uname}", db_path=db, config_path=cfg_path, materiality_value=0.05)
    with pytest.raises(ValueError):
        record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                       Decision.APPROVE, "again", db_path=db, config_path=cfg_path, materiality_value=0.05)


def test_return_then_resubmit_reevaluates_materiality(gov_env, cfg_path):
    """A RETURN starts a new round; the materiality-required final level is
    re-evaluated from the new round's ΔTEV (it does not stick across rounds)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    # Round 1: immaterial (final level = senior); junior approves, senior returns.
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "jr r1", db_path=db, config_path=cfg_path, materiality_value=0.005)
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.RETURN, "needs rework", db_path=db, config_path=cfg_path, materiality_value=0.005)
    assert _status(db, set_id) == "PROPOSED"
    # Round 2: now material (final level = chief); junior + senior approve must NOT complete.
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "jr r2", db_path=db, config_path=cfg_path, materiality_value=0.05)
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "sr r2", db_path=db, config_path=cfg_path, materiality_value=0.05)
    assert _status(db, set_id) != "APPROVED"   # chief still required
    nxt = next_required_level(ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg_path)
    assert nxt is not None and nxt.required_role.value == "chief_actuary"
    record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "chief r2", db_path=db, config_path=cfg_path, materiality_value=0.05)
    assert _status(db, set_id) == "APPROVED"


def test_study_run_return_makes_it_not_fit(gov_env, cfg_path):
    """A RETURN on a study-run chain leaves it not fit for assumption-setting."""
    db = gov_env["db"]
    run_id = "studyrun-ret"
    record_signoff(_u(db, "j.junior"), ArtifactType.STUDY_RUN, run_id, None,
                   Decision.RETURN, "not fit", db_path=db, config_path=cfg_path)
    assert is_study_run_fit(run_id, db_path=db, config_path=cfg_path) is False


def test_required_final_level_fixed_at_first_signoff_of_round(gov_env, cfg_path):
    """The required final level is fixed at a round's first sign-off — a later sign-off
    with a different ΔTEV does not change it (no mid-chain materiality drift)."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "jr material", db_path=db, config_path=cfg_path, materiality_value=0.05)
    # Senior signs with an immaterial ΔTEV; the round must still require chief.
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "sr immaterial", db_path=db, config_path=cfg_path, materiality_value=0.001)
    assert _status(db, set_id) != "APPROVED"
    nxt = next_required_level(ArtifactType.ASSUMPTION_SET, set_id, db_path=db, config_path=cfg_path)
    assert nxt is not None and nxt.required_role.value == "chief_actuary"


# ---------------------------------------------------------------------------
# Hash-chain reproducibility & tamper detection (§G.2 / FR-4-20/21)
# ---------------------------------------------------------------------------

def _recompute_entry_hash(db: str) -> tuple[str, str]:
    """Recompute the single row's entry_hash from its stored columns; return
    (recomputed, stored)."""
    con = duckdb.connect(db, read_only=True)
    try:
        cols = list(audit._SIGNOFF_COLUMNS)
        row = con.execute(f"SELECT {', '.join(cols)} FROM gold_governance_signoffs").fetchone()
    finally:
        con.close()
    stored = dict(zip(cols, row))
    canon_cols = [c for c in cols if c not in ("prev_hash", "entry_hash")]
    canonical = audit._canonical_row({c: stored[c] for c in canon_cols})
    return audit._entry_hash(canonical, stored["prev_hash"] or ""), stored["entry_hash"]


def test_recompute_matches_with_float_and_tzaware_ts(gov_env):
    """entry_hash recomputes from stored columns even for a populated float materiality_value
    and a tz-aware signoff_ts (normalised to naive UTC before hashing + storing)."""
    ts = datetime(2026, 6, 29, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    _append_signoff(gov_env["db"], materiality_value=0.0123456789, signoff_ts=ts, required_final_level=2)
    recomputed, stored = _recompute_entry_hash(gov_env["db"])
    assert recomputed == stored


def test_tampered_row_recompute_mismatches(gov_env):
    """Mutating a stored business column makes the recomputed entry_hash diverge —
    the column-level basis of the Session-26 verify_chain tamper check (FR-4-21)."""
    _append_signoff(gov_env["db"])
    con = duckdb.connect(gov_env["db"])
    try:
        con.execute("UPDATE gold_governance_signoffs SET comment = 'TAMPERED'")
    finally:
        con.close()
    recomputed, stored = _recompute_entry_hash(gov_env["db"])
    assert recomputed != stored


# ---------------------------------------------------------------------------
# Re-open guards (FR-4-18)
# ---------------------------------------------------------------------------

def test_reopen_rejects_non_approved_set(gov_env):
    """Re-open applies to an APPROVED (locked) set; a PROPOSED set is rejected."""
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.PROPOSED)
    with pytest.raises(ValueError):
        reopen(set_id, _u(db, "a.analyst"), "reopen a non-approved set", db_path=db)


def test_reopen_records_justification_on_child(gov_env, tmp_path):
    """The mandatory re-open justification is recorded durably on the DRAFT child."""
    db = gov_env["db"]
    single = _write_chain_config(tmp_path / "single.yaml", ["chief_actuary"])
    set_id = _seed_set(db, author="a.analyst")
    record_signoff(_u(db, "c.chief"), ArtifactType.ASSUMPTION_SET, set_id, 1,
                   Decision.APPROVE, "ok", db_path=db, config_path=single)
    child = reopen(set_id, _u(db, "a.analyst"), "macro shift requires revision", db_path=db)
    con = duckdb.connect(db, read_only=True)
    try:
        desc = con.execute(
            "SELECT description FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [child],
        ).fetchone()[0]
    finally:
        con.close()
    assert desc is not None and "macro shift requires revision" in desc


# ---------------------------------------------------------------------------
# Materiality from lineage diff (demo refresh P2, FR-4-16 new basis)
# ---------------------------------------------------------------------------

def _seed_child_of(db: str, parent_id: str, *, author: str, multiplier: float) -> str:
    """Seed a v2 child of ``parent_id`` whose single mortality cell moved to
    ``multiplier`` (wide credibility bounds so saves are never blocked)."""
    child = _seed_set(db, author=author, version=2)
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET parent_set_id = ? WHERE assumption_set_id = ?",
            [parent_id, child],
        )
    finally:
        con.close()
    from src.assumptions.assumption_set import load_assumption_set
    aset = load_assumption_set(child, Path(db))
    aset.mortality_multipliers[0].multiplier = multiplier
    aset.mortality_multipliers[0].credibility_lower = 0.0
    aset.mortality_multipliers[0].credibility_upper = 5.0
    save_assumption_set(aset, Path(db))
    return child


def test_materiality_vs_prior_approved_none_for_root(gov_env):
    from src.governance.workflow import materiality_vs_prior_approved
    db = gov_env["db"]
    set_id = _seed_set(db, author="a.analyst")
    assert materiality_vs_prior_approved(set_id, db_path=db) is None


def test_materiality_vs_prior_approved_uses_lineage_diff(gov_env):
    """The metric is the max |Δ multiplier| between the set and its nearest
    APPROVED (or SUPERSEDED) ancestor."""
    from src.governance.workflow import materiality_vs_prior_approved
    db = gov_env["db"]
    parent = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.APPROVED)
    child = _seed_child_of(db, parent, author="a.analyst", multiplier=1.08)
    val = materiality_vs_prior_approved(child, db_path=db)
    assert val == pytest.approx(0.08)  # |1.08 - 1.00|


def test_record_signoff_autocomputes_materiality(gov_env, cfg_path):
    """With no explicit materiality_value, record_signoff computes it from the
    lineage: an immaterial child of an APPROVED parent completes at senior."""
    db = gov_env["db"]
    parent = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.APPROVED)
    child = _seed_child_of(db, parent, author="a.analyst", multiplier=1.005)  # |Δ|=0.005 ≤ 0.01
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, child, 2,
                   Decision.APPROVE, "jr", db_path=db, config_path=cfg_path)
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, child, 2,
                   Decision.APPROVE, "sr", db_path=db, config_path=cfg_path)
    assert _status(db, child) == "APPROVED"  # completed at senior (immaterial)
    con = duckdb.connect(db, read_only=True)
    try:
        stored = con.execute(
            "SELECT materiality_value FROM gold_governance_signoffs "
            "WHERE artifact_id = ? ORDER BY seq LIMIT 1",
            [child],
        ).fetchone()[0]
    finally:
        con.close()
    assert stored == pytest.approx(0.005)


def test_record_signoff_autocompute_material_requires_chief(gov_env, cfg_path):
    """A material auto-computed change (> threshold) still requires the chief."""
    db = gov_env["db"]
    parent = _seed_set(db, author="a.analyst", status=AssumptionSetStatus.APPROVED)
    child = _seed_child_of(db, parent, author="a.analyst", multiplier=1.30)  # |Δ|=0.30 > 0.01
    record_signoff(_u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, child, 2,
                   Decision.APPROVE, "jr", db_path=db, config_path=cfg_path)
    record_signoff(_u(db, "s.senior"), ArtifactType.ASSUMPTION_SET, child, 2,
                   Decision.APPROVE, "sr", db_path=db, config_path=cfg_path)
    assert _status(db, child) != "APPROVED"
    nxt = next_required_level(ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg_path)
    assert nxt is not None and nxt.required_role.value == "chief_actuary"
