"""Tests for AI-provenance helpers (Session 17; FR-3A-30 / §D.4).

record_ai_provenance and find_ai_proposal_for_set live in src/tev/ (not src/ai/)
because they are part of the sanctioned human edit path that writes the Phase 2
gold_assumption_sets table.
"""
from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from src.utils.db_init import init_database
from src.tev.assumption_set import (
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
                basis, source_study_run_id, yaml_file_path, created_ts,
                rdr, earned_rate_ga, earned_rate_sa, tax_rate, expense_inflation
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [aset_id, 1, "PROPOSED", "2024-01-01", "tester", "best-estimate",
             "RUN1", "/tmp/none.yaml", datetime.utcnow(),
             0.09, 0.05, 0.06, 0.21, 0.025],
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


def _minimal_aset(aset_id):
    return AssumptionSet(
        id=aset_id, version=1, status=AssumptionSetStatus.PROPOSED,
        effective_date="2024-01-01", author_id="t", basis="best-estimate",
        source_study_run_id="RUN1", rdr=0.09, earned_rate_ga=0.05, earned_rate_sa=0.06,
        tax_rate=0.21, expense_inflation=0.025, rc_pct_reserve={"TERM": 0.03},
        acquisition_per_policy=350.0, maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02, mortality_multipliers=[], lapse_multipliers=[],
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
