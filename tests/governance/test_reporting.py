"""Tests for governance reporting & compliance export (Session 27, §H.8 / FR-4-23/24/25).

Builds real APPROVED artifacts through the Session-25 sign-off chain on the shared
``gov_env`` DB, then asserts ``dashboard_data`` returns the four sections,
``export_compliance_pack`` assembles a correct HTML pack for both an approved
assumption set (with a 2-version lineage + rationale) and an approved study run,
non-APPROVED artifacts are refused, ``fmt='pdf'`` is a deferred surface, and
``retention_policy`` normalises the config block.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pytest
import yaml

from src.governance.audit import submit_study_run
from src.governance.lineage import approve_and_supersede, lineage_root
from src.governance.reporting import (
    _supporting_reports,
    dashboard_data,
    export_compliance_pack,
    retention_policy,
)
from src.governance.users import get_user_by_username
from src.governance.workflow import record_signoff
from src.utils.db_init import init_database
from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    save_assumption_set,
)
from src.utils.types import ArtifactType, AssumptionSetStatus, Decision, User

_PERMISSIONS = {
    "analyst":        ["propose", "view"],
    "junior_actuary": ["sign_off", "view", "export"],
    "senior_actuary": ["sign_off", "view", "export"],
    "chief_actuary":  ["sign_off", "view", "export"],
}
_CHAIN = ["junior_actuary", "senior_actuary", "chief_actuary"]
_ATTEST = "I attest that I have reviewed this artifact and it is fit for its stated purpose."


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _write_chain_config(path: Path) -> str:
    cfg = {
        "permissions": _PERMISSIONS,
        "approval_chain": [
            {"level": i + 1, "required_role": role} for i, role in enumerate(_CHAIN)
        ],
        "segregation": {"allow_multi_level_signoff": False},
        "materiality": {
            "delta_tev_threshold": 0.01,
            "final_level_below_threshold": "senior_actuary",
        },
        "attestation_text": _ATTEST,
        "retention": {"hard_delete": False, "archive_after_days": 3650},
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def _u(db: str, username: str) -> User:
    user = get_user_by_username(username, db)
    assert user is not None, f"seeded user {username} missing"
    return user


def _seed_set(
    db: str,
    *,
    author: str = "a.analyst",
    status: AssumptionSetStatus = AssumptionSetStatus.PROPOSED,
    version: int = 1,
    multiplier: float = 1.0,
    rationale: str = "",
    source_run: str = "run-1",
) -> str:
    set_id = str(uuid.uuid4())
    yaml_dir = Path(db).parent / "assumption_sets"
    aset = AssumptionSet(
        id=set_id, version=version, status=status,
        effective_date=date.today().isoformat(), author_id=author,
        basis="best-estimate", source_study_run_id=source_run,
        rdr=0.09, earned_rate_ga=0.05, earned_rate_sa=0.06, tax_rate=0.21,
        expense_inflation=0.025, rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0, maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=[
            DecrementMultiplier(
                product="TERM", gender="M", risk_class="STD_NS",
                duration_band=[1, 5], multiplier=multiplier, credibility_z=0.8,
                credibility_lower=0.7, credibility_upper=1.1,
                override_rationale=rationale,
            )
        ],
        lapse_multipliers=[], surrender_multipliers=[],
        ci_incidence_multipliers=[], premium_persistency=[], shock_lapse_plt={},
        yaml_file_path=str(yaml_dir / f"{set_id}.yaml"),
    )
    save_assumption_set(aset, Path(db))
    return set_id


def _set_parent(db: str, child_id: str, parent_id: str, version: int) -> None:
    con = duckdb.connect(db)
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET parent_set_id = ?, version = ? "
            "WHERE assumption_set_id = ?",
            [parent_id, version, child_id],
        )
    finally:
        con.close()


def _status(db: str, set_id: str) -> str:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [set_id],
        ).fetchone()[0]
    finally:
        con.close()


def _approve_assumption_set(db: str, cfg: str, set_id: str, version: int) -> None:
    for uname in ("j.junior", "s.senior", "c.chief"):
        record_signoff(
            _u(db, uname), ArtifactType.ASSUMPTION_SET, set_id, version,
            Decision.APPROVE, f"reviewed by {uname}",
            db_path=db, config_path=cfg, delta_tev=0.05,
        )


def _approve_study_run(db: str, cfg: str, run_id: str) -> None:
    for uname in ("j.junior", "s.senior", "c.chief"):
        record_signoff(
            _u(db, uname), ArtifactType.STUDY_RUN, run_id, None,
            Decision.APPROVE, f"fit — {uname}", db_path=db, config_path=cfg,
        )


@pytest.fixture()
def cfg(gov_env, tmp_path) -> str:
    return _write_chain_config(tmp_path / "gov_chain.yaml")


@pytest.fixture()
def approved_lineage(gov_env, cfg) -> tuple:
    """A 2-version lineage whose child (v2) is APPROVED, with a changed cell."""
    db = gov_env["db"]
    root = _seed_set(db, multiplier=1.0, status=AssumptionSetStatus.PROPOSED)
    child = _seed_set(db, multiplier=1.15, rationale="Emerging deterioration",
                      status=AssumptionSetStatus.PROPOSED)
    _set_parent(db, child, root, version=2)
    _approve_assumption_set(db, cfg, child, version=2)
    assert _status(db, child) == "APPROVED"
    return root, child


# --------------------------------------------------------------------------- #
# retention_policy (FR-4-25)                                                   #
# --------------------------------------------------------------------------- #

def test_retention_policy_from_dict():
    out = retention_policy({"retention": {"hard_delete": False, "archive_after_days": 3650}})
    assert out == {"hard_delete": False, "archive_after_days": 3650}


def test_retention_policy_defaults_when_absent():
    out = retention_policy({})
    assert out["hard_delete"] is False
    assert out["archive_after_days"] == 3650


def test_retention_policy_from_config_file(cfg):
    out = retention_policy(config_path=cfg)
    assert out["hard_delete"] is False


# --------------------------------------------------------------------------- #
# dashboard_data (FR-4-23)                                                     #
# --------------------------------------------------------------------------- #

def test_dashboard_data_shapes(gov_env, cfg, approved_lineage):
    db = gov_env["db"]
    _root, child = approved_lineage
    pending_set = _seed_set(db, status=AssumptionSetStatus.PROPOSED)  # no sign-offs yet
    _approve_study_run(db, cfg, "studyrun-fit")

    data = dashboard_data(db_path=db, config_path=cfg)

    assert set(data) == {
        "sets_by_state", "live_set_per_lineage", "pending_approvals", "recent_activity",
    }
    approved_ids = {e["assumption_set_id"] for e in data["sets_by_state"]["APPROVED"]}
    assert child in approved_ids
    # The un-signed PROPOSED set is awaiting the junior level, globally.
    pend_ids = {p["artifact_id"] for p in data["pending_approvals"]}
    assert pending_set in pend_ids
    assert any(p["required_role"] == "junior_actuary" for p in data["pending_approvals"])
    # Live-set-per-lineage and recent activity are populated.
    assert data["live_set_per_lineage"]  # at least the lineages we seeded
    assert data["recent_activity"], "sign-off events should surface as recent activity"


# --------------------------------------------------------------------------- #
# export_compliance_pack (FR-4-24)                                             #
# --------------------------------------------------------------------------- #

def test_export_pack_assumption_set(gov_env, cfg, approved_lineage, tmp_path):
    db = gov_env["db"]
    _root, child = approved_lineage
    out_dir = tmp_path / "packs"
    path = export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, fmt="html",
        db_path=db, config_path=cfg, output_dir=out_dir,
    )
    p = Path(path)
    assert p.exists() and p.suffix == ".html"
    html = p.read_text(encoding="utf-8")
    assert child in html
    assert "APPROVED" in html
    assert _ATTEST in html                       # attestation on file
    assert "Emerging deterioration" in html      # per-change rationale vs parent
    assert "reviewed by c.chief" in html or "C. Chief" in html  # sign-off row
    assert "source_study_run_id" in html or "run-1" in html     # reproducibility stamp
    assert "Version Lineage" in html             # lineage section rendered


def test_export_pack_study_run(gov_env, cfg, tmp_path):
    db = gov_env["db"]
    _approve_study_run(db, cfg, "studyrun-export")
    path = export_compliance_pack(
        ArtifactType.STUDY_RUN, "studyrun-export", fmt="html",
        db_path=db, config_path=cfg, output_dir=tmp_path,
    )
    html = Path(path).read_text(encoding="utf-8")
    assert "studyrun-export" in html
    assert "STUDY_RUN" in html
    assert _ATTEST in html
    assert "fit — c.chief" in html or "C. Chief" in html


def test_export_accepts_string_artifact_type(gov_env, cfg, approved_lineage, tmp_path):
    db = gov_env["db"]
    _root, child = approved_lineage
    path = export_compliance_pack(
        "ASSUMPTION_SET", child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )
    assert Path(path).exists()


def test_export_non_approved_assumption_set_raises(gov_env, cfg, tmp_path):
    db = gov_env["db"]
    proposed = _seed_set(db, status=AssumptionSetStatus.PROPOSED)
    with pytest.raises(ValueError):
        export_compliance_pack(
            ArtifactType.ASSUMPTION_SET, proposed,
            db_path=db, config_path=cfg, output_dir=tmp_path,
        )


def test_export_unfit_study_run_raises(gov_env, cfg, tmp_path):
    db = gov_env["db"]
    with pytest.raises(ValueError):
        export_compliance_pack(
            ArtifactType.STUDY_RUN, "never-submitted",
            db_path=db, config_path=cfg, output_dir=tmp_path,
        )


def test_export_pdf_deferred(gov_env, cfg, approved_lineage, tmp_path):
    db = gov_env["db"]
    _root, child = approved_lineage
    with pytest.raises(NotImplementedError):
        export_compliance_pack(
            ArtifactType.ASSUMPTION_SET, child, fmt="pdf",
            db_path=db, config_path=cfg, output_dir=tmp_path,
        )


def test_export_bad_fmt_raises(gov_env, cfg, approved_lineage, tmp_path):
    db = gov_env["db"]
    _root, child = approved_lineage
    with pytest.raises(ValueError):
        export_compliance_pack(
            ArtifactType.ASSUMPTION_SET, child, fmt="xml",
            db_path=db, config_path=cfg, output_dir=tmp_path,
        )


# --------------------------------------------------------------------------- #
# Post-build review — strengthening tests (Session 27)                        #
# --------------------------------------------------------------------------- #

def test_pack_rationale_dimension_is_readable(gov_env, cfg, approved_lineage, tmp_path):
    """The changed-cell dimension renders as `k=v, …`, never a raw Python dict."""
    db = gov_env["db"]
    _root, child = approved_lineage
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "product=TERM" in html                    # readable form
    assert "{'product'" not in html                  # no raw dict literal
    assert "&#39;product&#39;" not in html            # nor its HTML-escaped form


def test_pack_includes_audit_excerpt_and_reproducibility(gov_env, cfg, approved_lineage, tmp_path):
    """The pack renders real audit-excerpt rows and a reproducibility stamp (not just headers)."""
    db = gov_env["db"]
    _root, child = approved_lineage
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    # audit excerpt: the sign-off events surface as SIGNOFF-source rows
    assert "SIGNOFF" in html
    assert "APPROVE" in html
    # reproducibility stamp fields with real values
    assert "source_study_run_id" in html and "run-1" in html
    assert "version" in html


def test_pack_rationale_failure_marker(gov_env, cfg, tmp_path):
    """If the parent version can't be loaded for the diff, the pack surfaces that
    honestly — never a false 'no changes'."""
    db = gov_env["db"]
    child = _seed_set(db, multiplier=1.2, rationale="x")
    _set_parent(db, child, "missing-parent-id", version=2)  # parent yaml does not exist
    _approve_assumption_set(db, cfg, child, version=2)
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "comparison unavailable" in html
    assert "No cell-level changes" not in html


def test_pack_root_set_no_parent_no_crash(gov_env, cfg, tmp_path):
    """A directly-approved root set (no parent) exports with an empty rationale section."""
    db = gov_env["db"]
    root = _seed_set(db, multiplier=1.0)
    _approve_assumption_set(db, cfg, root, version=1)
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, root, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "No cell-level changes" in html
    assert "comparison unavailable" not in html


def test_study_run_pack_has_no_lineage_section(gov_env, cfg, tmp_path):
    db = gov_env["db"]
    _approve_study_run(db, cfg, "run-nolineage")
    html = Path(export_compliance_pack(
        ArtifactType.STUDY_RUN, "run-nolineage", db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Version Lineage" not in html
    assert "Per-Change Rationale" not in html


def test_dashboard_empty_db(tmp_path, cfg):
    """dashboard_data on a fresh DB (no sets/runs) returns four empty sections, no crash."""
    db = str(tmp_path / "empty.duckdb")
    init_database(db)
    data = dashboard_data(db_path=db, config_path=cfg)
    assert all(v == [] for v in data["sets_by_state"].values())
    assert data["live_set_per_lineage"] == []
    assert data["pending_approvals"] == []
    assert data["recent_activity"] == []


def test_dashboard_live_set_and_supersession(gov_env, cfg):
    """approve_and_supersede populates the SUPERSEDED bucket + a live set for today (FR-4-08/09)."""
    db = gov_env["db"]
    root = _seed_set(db, multiplier=1.0)
    child = _seed_set(db, multiplier=1.1)
    _set_parent(db, child, root, version=2)
    approve_and_supersede(root, date(2020, 1, 1), date(2023, 12, 31), db_path=db)
    approve_and_supersede(child, date(2024, 1, 1), date(2030, 12, 31), db_path=db)  # contains today

    data = dashboard_data(db_path=db, config_path=cfg)
    superseded_ids = {e["assumption_set_id"] for e in data["sets_by_state"]["SUPERSEDED"]}
    approved_ids = {e["assumption_set_id"] for e in data["sets_by_state"]["APPROVED"]}
    assert root in superseded_ids
    assert child in approved_ids
    live = {row["lineage_root"]: row["live_set_id"] for row in data["live_set_per_lineage"]}
    assert live[lineage_root(root, db_path=db)] == child   # live set = current version


# --------------------------------------------------------------------------- #
# Compliance-pack warning banners (governance-output audit, 2026-07-04)        #
# --------------------------------------------------------------------------- #

def test_pack_warns_when_approved_but_not_effective(gov_env, cfg, approved_lineage, tmp_path):
    """A chain-APPROVED but never-published set (no effective range) warns it isn't live."""
    db = gov_env["db"]
    _root, child = approved_lineage  # approved via the chain only → no effective dates
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Not yet effective" in html


def test_pack_no_effective_warning_once_published(gov_env, cfg, tmp_path):
    """Publishing (approve_and_supersede sets the effective range) clears the banner."""
    db = gov_env["db"]
    s = _seed_set(db, multiplier=1.0, source_run="run-1")
    approve_and_supersede(s, date(2024, 1, 1), date(2030, 12, 31), db_path=db)
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, s, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Not yet effective" not in html


def test_pack_warns_when_source_run_unfit(gov_env, cfg, approved_lineage, tmp_path):
    """The source study run ('run-1') is not governance-approved → warn in the pack."""
    db = gov_env["db"]
    _root, child = approved_lineage
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Source study run not governance-approved" in html


def test_pack_no_source_warning_when_source_run_fit(gov_env, cfg, tmp_path):
    """A set whose source run has completed its own sign-off chain gets no source warning."""
    db = gov_env["db"]
    run = "fit-source-run"
    _approve_study_run(db, cfg, run)            # source run is now "fit"
    s = _seed_set(db, source_run=run)
    _approve_assumption_set(db, cfg, s, version=1)
    html = Path(export_compliance_pack(
        ArtifactType.ASSUMPTION_SET, s, db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Source study run not governance-approved" not in html


def test_pack_require_fit_source_run_raises(gov_env, cfg, approved_lineage, tmp_path):
    """With compliance.require_fit_source_run: true, an unfit source run is refused."""
    db = gov_env["db"]
    _root, child = approved_lineage  # source run 'run-1' is unfit
    strict = tmp_path / "strict.yaml"
    data = yaml.safe_load(Path(cfg).read_text(encoding="utf-8"))
    data["compliance"] = {"require_fit_source_run": True}
    strict.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        export_compliance_pack(
            ArtifactType.ASSUMPTION_SET, child, db_path=db, config_path=str(strict),
            output_dir=tmp_path,
        )


def test_study_run_pack_has_no_assumption_set_banners(gov_env, cfg, tmp_path):
    """A STUDY_RUN pack never renders the assumption-set-only warning banners.

    Guards the cross-branch template contract: not_yet_effective / source_run_unfit
    are always defined (False) for the study-run context, so the {% if %} blocks are
    inert and the pack does not crash or show an irrelevant warning.
    """
    db = gov_env["db"]
    _approve_study_run(db, cfg, "run-no-banner")
    html = Path(export_compliance_pack(
        ArtifactType.STUDY_RUN, "run-no-banner", db_path=db, config_path=cfg, output_dir=tmp_path,
    )).read_text(encoding="utf-8")
    assert "Not yet effective" not in html
    assert "Source study run not governance-approved" not in html


def test_dashboard_global_pending_spans_roles(gov_env, cfg):
    """The global pending queue lists artifacts awaiting *different* roles (not one)."""
    db = gov_env["db"]
    awaiting_junior = _seed_set(db, status=AssumptionSetStatus.PROPOSED)   # 0 sign-offs → junior
    awaiting_senior = _seed_set(db, status=AssumptionSetStatus.PROPOSED)
    record_signoff(  # one junior sign-off advances this one to the senior level
        _u(db, "j.junior"), ArtifactType.ASSUMPTION_SET, awaiting_senior, 1,
        Decision.APPROVE, "junior ok", db_path=db, config_path=cfg, delta_tev=0.05,
    )
    pending = {p["artifact_id"]: p["required_role"] for p in dashboard_data(db_path=db, config_path=cfg)["pending_approvals"]}
    assert pending.get(awaiting_junior) == "junior_actuary"
    assert pending.get(awaiting_senior) == "senior_actuary"


def test_dashboard_pending_includes_submitted_unsigned_study_run(gov_env, cfg):
    """A study run submitted for approval but not yet signed surfaces on the queue."""
    db = gov_env["db"]
    submit_study_run("run-submitted", _u(db, "a.analyst").user_id, db_path=db)
    pending = {p["artifact_id"]: p for p in dashboard_data(db_path=db, config_path=cfg)["pending_approvals"]}
    assert "run-submitted" in pending
    assert pending["run-submitted"]["required_role"] == "junior_actuary"


def test_supporting_reports_dedupes_tev_links(gov_env, tmp_path):
    """Multiple TEV runs for one set collapse to a single impact-report reference."""
    db = gov_env["db"]
    aset = _seed_set(db)
    con = duckdb.connect(db)
    try:
        for i in range(3):
            con.execute(
                "INSERT INTO gold_tev_run_log "
                "(tev_run_id, assumption_set_id, run_ts, model_point_hash, config_hash, "
                " code_version, projection_years, status) "
                "VALUES (?, ?, now(), 'h', 'h', 'v', 60, 'COMPLETE')",
                [f"tev-{i}", aset],
            )
    finally:
        con.close()
    reports = _supporting_reports(db, None, aset)
    tev = [r for r in reports if "TEV" in r["label"]]
    assert len(tev) == 1
    assert "3 run(s)" in tev[0]["label"]


def test_retention_hard_delete_true_coerced(cfg):
    assert retention_policy({"retention": {"hard_delete": True, "archive_after_days": 100}}) == {
        "hard_delete": True, "archive_after_days": 100,
    }
