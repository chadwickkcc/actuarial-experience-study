"""Tests for AI-provenance helpers (Session 17; FR-3A-30 / §D.4).

record_ai_provenance and find_ai_proposal_for_set live in src/assumptions/ (not src/ai/)
because they are part of the sanctioned human edit path that writes the Phase 2
gold_assumption_sets table.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from src.utils.db_init import init_database
from src.assumptions.assumption_set import (
    record_ai_provenance,
    find_ai_proposal_for_set,
    _insert_assumption_set_metadata,
    AssumptionSet,
)
from src.utils.types import AssumptionSetStatus

ARTIFACT_ROOT = "tests/_artifacts"


def _fresh_db(tmp_path):
    db = tmp_path / "prov.duckdb"
    init_database(str(db))
    return db


def _insert_assumption_row(db, aset_id):
    con = duckdb.connect(str(db))
    try:
        con.execute(
            """
            INSERT INTO gold_assumption_sets (
                assumption_set_id, version, status, effective_date, author_id,
                basis, source_study_run_id, yaml_file_path, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [aset_id, 1, "PROPOSED", "2024-01-01", "tester", "best-estimate",
             "RUN1", "/tmp/none.yaml", datetime.utcnow()],
        )
    finally:
        con.close()


def test_d4_columns_present_after_init(tmp_path):
    db = _fresh_db(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'gold_assumption_sets'"
        ).fetchall()}
    finally:
        con.close()
    assert "ai_proposed_value" in cols
    assert "ai_model_id" in cols


def test_init_is_idempotent_for_columns(tmp_path):
    db = _fresh_db(tmp_path)
    # Second init must not raise (column already exists).
    init_database(str(db))


def test_record_ai_provenance_sets_columns(tmp_path):
    db = _fresh_db(tmp_path)
    _insert_assumption_row(db, "A-PROV-1")

    record_ai_provenance(db, "A-PROV-1", 0.934, "model-xyz")

    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute(
            "SELECT ai_proposed_value, ai_model_id FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            ["A-PROV-1"],
        ).fetchone()
    finally:
        con.close()
    assert abs(row[0] - 0.934) < 1e-9
    assert row[1] == "model-xyz"


def test_record_ai_provenance_unknown_id_raises(tmp_path):
    db = _fresh_db(tmp_path)
    with pytest.raises(ValueError):
        record_ai_provenance(db, "does-not-exist", 1.0, "m")


def test_find_ai_proposal_for_set(tmp_path):
    db = _fresh_db(tmp_path)
    con = duckdb.connect(str(db))
    try:
        con.execute(
            """
            INSERT INTO gold_ai_model_registry (
                model_id, run_id, model_type, decrement, product_code, fit_ts,
                converged, n_cells, artifact_path, data_snapshot_hash, config_hash,
                code_version, seed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ["M1", "RUN1", "GLM", "MORTALITY", "TERM", datetime.utcnow(),
             True, 24, "/tmp/m1.pkl", "snap", "cfg", "0.17", 42],
        )
    finally:
        con.close()

    found = find_ai_proposal_for_set(db, "RUN1")
    assert found is not None
    assert found["model_id"] == "M1"
    assert found["decrement"] == "MORTALITY"
    assert found["product_code"] == "TERM"

    assert find_ai_proposal_for_set(db, "OTHER_RUN") is None


def test_find_ai_proposal_matches_the_decrement_and_product_being_edited(tmp_path):
    """Filtered lookup returns the model for that pair, not the latest fit overall.

    Without filters the latest GLM of *any* product wins, so an actuary editing WL
    mortality could be offered a TERM CI model just because it was fitted last.
    """
    db = _fresh_db(tmp_path)
    con = duckdb.connect(str(db))
    try:
        for model_id, dec, prod, ts in (
            ("WL_MORT", "MORTALITY", "WL", datetime(2026, 1, 1)),
            ("TERM_CI", "CI_INCIDENCE", "TERM", datetime(2026, 1, 2)),   # fitted last
        ):
            con.execute(
                """
                INSERT INTO gold_ai_model_registry (
                    model_id, run_id, model_type, decrement, product_code, fit_ts,
                    converged, n_cells, artifact_path, data_snapshot_hash, config_hash,
                    code_version, seed
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [model_id, "RUN1", "GLM", dec, prod, ts, True, 10, "/tmp/x.pkl",
                 "snap", "cfg", "0.17", 42],
            )
    finally:
        con.close()

    assert find_ai_proposal_for_set(db, "RUN1")["model_id"] == "TERM_CI"
    matched = find_ai_proposal_for_set(db, "RUN1", decrement="MORTALITY", product_code="WL")
    assert matched["model_id"] == "WL_MORT"
    assert find_ai_proposal_for_set(db, "RUN1", decrement="LAPSE", product_code="WL") is None


def _minimal_aset(aset_id):
    return AssumptionSet(
        id=aset_id, version=1, status=AssumptionSetStatus.PROPOSED,
        effective_date="2024-01-01", author_id="t", basis="best-estimate",
        source_study_run_id="RUN1", mortality_multipliers=[], lapse_multipliers=[],
        surrender_multipliers=[], ci_incidence_multipliers=[], premium_persistency=[],
        shock_lapse_plt={}, yaml_file_path="/tmp/none.yaml",
    )


def test_provenance_survives_a_plain_resave(tmp_path):
    """A later metadata re-save (Stage 2 plain save) must NOT wipe AI provenance."""
    db = _fresh_db(tmp_path)
    aset = _minimal_aset("A-RESAVE-1")

    _insert_assumption_set_metadata(db, aset)          # first save
    record_ai_provenance(db, aset.id, 0.917, "model-keep")
    _insert_assumption_set_metadata(db, aset)          # re-save without adopting

    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute(
            "SELECT ai_proposed_value, ai_model_id FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [aset.id],
        ).fetchone()
    finally:
        con.close()
    assert abs(row[0] - 0.917) < 1e-9, "provenance value was wiped on re-save"
    assert row[1] == "model-keep", "provenance model_id was wiped on re-save"


# --------------------------------------------------------------------------
# Step 2 page: the adopt box offers the model for the pair the actuary names
# --------------------------------------------------------------------------
_LIVE_DB = Path("data/experience_study.duckdb")


def _live_set_with_wl_mortality_glm():
    """(assumption_set_id) of a live set whose source run has a WL mortality GLM."""
    if not _LIVE_DB.exists():
        return None
    con = duckdb.connect(str(_LIVE_DB), read_only=True)
    try:
        row = con.execute(
            "SELECT s.assumption_set_id FROM gold_assumption_sets s "
            "JOIN gold_ai_model_registry r ON r.run_id = s.source_study_run_id "
            "WHERE r.model_type = 'GLM' AND r.converged "
            "AND r.decrement = 'MORTALITY' AND r.product_code = 'WL' LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def test_step2_adopt_box_matches_the_edited_decrement_and_product():
    set_id = _live_set_with_wl_mortality_glm()
    if set_id is None:
        pytest.skip("live DB has no assumption set with a WL mortality GLM")
    from unittest.mock import patch

    from streamlit.testing.v1 import AppTest
    from src.utils.types import Role, User

    analyst = User(user_id="t-analyst", username="a.analyst", display_name="A. Analyst",
                   role=Role.ANALYST, active=True)
    with patch("src.governance.auth.current_user", return_value=analyst):
        at = AppTest.from_file("ui/views/21_assumption_step2.py", default_timeout=60)
        at.session_state["active_assumption_set_id"] = set_id
        at.run()
        assert not at.exception, list(at.exception)

        at.selectbox(key="s2_ai_decrement").set_value("Mortality")
        at.selectbox(key="s2_ai_product").set_value("WL")
        at.run()
        captions = " ".join(c.value for c in at.caption)
        assert "GLM proposal for Mortality / WL" in captions
        assert not at.checkbox(key="s2_adopt_ai").disabled

        at.selectbox(key="s2_ai_product").set_value("IUL")   # no IUL mortality GLM
        at.run()
        captions = " ".join(c.value for c in at.caption)
        assert "No GLM proposal was fitted for Mortality / IUL" in captions
        assert at.checkbox(key="s2_adopt_ai").disabled
