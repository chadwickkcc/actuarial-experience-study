"""Tests for src/tev/assumption_set.py.

Covers: DecrementMultiplier, AssumptionSet serialisation, create_from_ae_run,
load/save round-trips, get_multiplier lookup, and multiplier sanity checks.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import pytest
import yaml

from src.tev.assumption_set import (
    AssumptionSet,
    DecrementMultiplier,
    _compute_ci_bounds,
    _credibility_z,
    _parse_duration_band,
    _policy_year_to_band,
    _safe_ae,
    create_assumption_set_from_ae_run,
    get_multiplier,
    load_assumption_set,
    save_assumption_set,
)
from src.utils.types import AssumptionSetStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_PATH = Path("data/experience_study.duckdb")
TEV_CFG = Path("config/tev_config.yaml")
YAML_DIR = Path("data/assumption_sets")


def _latest_complete_run_id(default: str = "291fca51-f9db-41d1-8fa8-0a5d22c3c445") -> str:
    """Resolve the latest completed study run id with A/E results.

    The DB is rebuilt by the pipeline (new run_ids each run), so a hardcoded study_run_id
    goes stale and yields empty A/E cells. Resolve it dynamically; fall back to the constant
    when the DB is absent (the DB-backed tests skip in that case).
    """
    if not DB_PATH.exists():
        return default
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            row = conn.execute(
                "SELECT run_id FROM gold_study_runs WHERE status = 'COMPLETE' "
                "ORDER BY run_ts DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] else default
    except Exception:
        return default


STUDY_RUN_ID = _latest_complete_run_id()  # latest 6-product run, resolved at import


@pytest.fixture(scope="module", autouse=True)
def _isolate_db_path(tmp_path_factory):
    """Redirect this module's DB_PATH to an isolated mirrored copy so the integration tests
    (which insert into gold_assumption_sets) never write to the real production DB."""
    global DB_PATH
    if not DB_PATH.exists():
        yield
        return
    import shutil
    base = tmp_path_factory.mktemp("iso_aset")
    (base / "data").mkdir()
    shutil.copy2(DB_PATH, base / "data" / "experience_study.duckdb")
    shutil.copytree(Path("config"), base / "config")
    orig = DB_PATH
    DB_PATH = base / "data" / "experience_study.duckdb"
    try:
        yield
    finally:
        DB_PATH = orig


def _make_minimal_assumption_set(aset_id: Optional[str] = None) -> AssumptionSet:
    """Return a minimal AssumptionSet for unit testing."""
    mults = [
        DecrementMultiplier(
            product="TERM",
            gender="M",
            risk_class="STD_NS",
            duration_band=[6, 10],
            multiplier=0.92,
            credibility_z=0.85,
            credibility_lower=0.85,
            credibility_upper=0.99,
        ),
        DecrementMultiplier(
            product="TERM",
            gender="F",
            risk_class="STD_NS",
            duration_band=[1, 5],
            multiplier=0.88,
            credibility_z=0.70,
            credibility_lower=0.80,
            credibility_upper=0.96,
        ),
    ]
    return AssumptionSet(
        id=aset_id or str(uuid.uuid4()),
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(),
        author_id="test_actuary",
        basis="best-estimate",
        source_study_run_id=STUDY_RUN_ID,
        rdr=0.09,
        earned_rate_ga=0.05,
        earned_rate_sa=0.06,
        tax_rate=0.21,
        expense_inflation=0.025,
        rc_pct_reserve={"TERM": 0.03, "WL": 0.045, "UL": 0.06,
                         "ULSG": 0.08, "VUL": 0.035, "DA": 0.045},
        acquisition_per_policy=350.0,
        maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=mults,
        lapse_multipliers=[],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={
            "jump_band_lt_2x": 0.30,
            "jump_band_2x_5x": 0.55,
            "jump_band_5x_8x": 0.70,
            "jump_band_gt_8x": 0.88,
        },
    )


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_parse_duration_band_single(self):
        assert _parse_duration_band("1") == [1, 1]

    def test_parse_duration_band_range(self):
        assert _parse_duration_band("6-10") == [6, 10]
        assert _parse_duration_band("11-15") == [11, 15]

    def test_parse_duration_band_open(self):
        assert _parse_duration_band("26+") == [26, 999]

    def test_policy_year_to_band_year_1(self):
        assert _policy_year_to_band(1) == [1, 1]

    def test_policy_year_to_band_mid_range(self):
        assert _policy_year_to_band(8) == [6, 10]

    def test_policy_year_to_band_long(self):
        assert _policy_year_to_band(30) == [26, 999]

    def test_credibility_z_zero_claims(self):
        assert _credibility_z(0) == 0.0

    def test_credibility_z_full_credibility(self):
        z = _credibility_z(1082.0, threshold=1082.0)
        assert abs(z - 1.0) < 1e-9

    def test_credibility_z_partial(self):
        z = _credibility_z(270.5, threshold=1082.0)
        assert abs(z - 0.5) < 1e-4

    def test_credibility_z_buhlmann(self):
        # n = K -> Z = sqrt(n / (n + K)) = sqrt(0.5)
        z = _credibility_z(1082.0, method="BUHLMANN", threshold=1082.0)
        assert abs(z - 0.5 ** 0.5) < 1e-9

    def test_credibility_z_buhlmann_below_lf(self):
        # For n < K, Buhlmann Z is strictly below LF Z.
        n = 500.0
        z_lf = _credibility_z(n, method="LF", threshold=1082.0)
        z_b = _credibility_z(n, method="BUHLMANN", threshold=1082.0)
        assert z_b < z_lf

    def test_credibility_z_buhlmann_zero_claims(self):
        assert _credibility_z(0, method="BUHLMANN", threshold=1082.0) == 0.0

    def test_safe_ae_no_expected(self):
        assert _safe_ae(0, 0) == 1.0

    def test_safe_ae_clamped_high(self):
        assert _safe_ae(100, 1) == 10.0

    def test_safe_ae_clamped_low(self):
        assert _safe_ae(0, 100) == 0.1

    def test_compute_ci_bounds_zero_actual(self):
        lo, hi = _compute_ci_bounds(1.0, 0)
        assert lo >= 0.0
        assert hi > lo

    def test_compute_ci_bounds_positive(self):
        lo, hi = _compute_ci_bounds(0.95, 100)
        assert 0 < lo < 0.95 < hi
        assert hi - lo < 0.5   # reasonable width for 100 claims


# ---------------------------------------------------------------------------
# Unit tests: DecrementMultiplier
# ---------------------------------------------------------------------------

class TestDecrementMultiplier:
    def test_matches_exact(self):
        m = DecrementMultiplier("TERM", "M", "STD_NS", [6, 10], 0.92,
                                0.85, 0.85, 0.99)
        assert m.matches("TERM", "M", "STD_NS", 8)
        assert not m.matches("TERM", "F", "STD_NS", 8)
        assert not m.matches("TERM", "M", "STD_NS", 11)

    def test_to_dict_roundtrip(self):
        m = DecrementMultiplier("WL", "F", "PREF_NS", [2, 5], 0.88,
                                0.70, 0.80, 0.96, "Board override")
        d = m.to_dict()
        m2 = DecrementMultiplier.from_dict(d)
        assert m2.product == "WL"
        assert m2.multiplier == 0.88
        assert m2.duration_band == [2, 5]
        assert m2.override_rationale == "Board override"

    def test_boundary_inclusive(self):
        m = DecrementMultiplier("TERM", "M", "STD_NS", [6, 10], 0.92,
                                0.85, 0.85, 0.99)
        assert m.matches("TERM", "M", "STD_NS", 6)
        assert m.matches("TERM", "M", "STD_NS", 10)
        assert not m.matches("TERM", "M", "STD_NS", 5)
        assert not m.matches("TERM", "M", "STD_NS", 11)


# ---------------------------------------------------------------------------
# Unit tests: AssumptionSet
# ---------------------------------------------------------------------------

class TestAssumptionSet:
    def test_to_yaml_dict_structure(self):
        aset = _make_minimal_assumption_set()
        d = aset.to_yaml_dict()
        assert "assumption_set" in d
        inner = d["assumption_set"]
        assert inner["basis"] == "best-estimate"
        assert "mortality" in inner
        assert "lapse" in inner
        assert "economic" in inner
        assert inner["economic"]["rdr"] == 0.09

    def test_yaml_roundtrip(self, tmp_path):
        aset = _make_minimal_assumption_set()
        yaml_path = tmp_path / f"{aset.id}.yaml"
        aset.save_yaml(yaml_path)
        with open(yaml_path) as fh:
            d = yaml.safe_load(fh)
        aset2 = AssumptionSet.from_yaml_dict(d)
        assert aset2.id == aset.id
        assert aset2.rdr == aset.rdr
        assert len(aset2.mortality_multipliers) == 2
        assert aset2.mortality_multipliers[0].multiplier == 0.92

    def test_status_roundtrip(self, tmp_path):
        aset = _make_minimal_assumption_set()
        aset.status = AssumptionSetStatus.APPROVED
        yaml_path = tmp_path / f"{aset.id}.yaml"
        aset.save_yaml(yaml_path)
        with open(yaml_path) as fh:
            d = yaml.safe_load(fh)
        aset2 = AssumptionSet.from_yaml_dict(d)
        assert aset2.status == AssumptionSetStatus.APPROVED

    def test_shock_lapse_preserved(self, tmp_path):
        aset = _make_minimal_assumption_set()
        yaml_path = tmp_path / f"{aset.id}.yaml"
        aset.save_yaml(yaml_path)
        with open(yaml_path) as fh:
            d = yaml.safe_load(fh)
        aset2 = AssumptionSet.from_yaml_dict(d)
        assert aset2.shock_lapse_plt["jump_band_lt_2x"] == 0.30
        assert aset2.shock_lapse_plt["jump_band_gt_8x"] == 0.88


# ---------------------------------------------------------------------------
# Unit tests: get_multiplier
# ---------------------------------------------------------------------------

class TestGetMultiplier:
    def test_exact_match(self):
        aset = _make_minimal_assumption_set()
        mult = get_multiplier(aset, "mortality", "TERM", "M", "STD_NS", 8)
        assert mult == pytest.approx(0.92, abs=1e-6)

    def test_boundary_match(self):
        aset = _make_minimal_assumption_set()
        mult = get_multiplier(aset, "mortality", "TERM", "M", "STD_NS", 6)
        assert mult == pytest.approx(0.92, abs=1e-6)
        mult2 = get_multiplier(aset, "mortality", "TERM", "M", "STD_NS", 10)
        assert mult2 == pytest.approx(0.92, abs=1e-6)

    def test_fallback_to_one(self):
        aset = _make_minimal_assumption_set()
        mult = get_multiplier(aset, "mortality", "WL", "M", "PREF_NS", 5)
        assert mult == 1.0

    def test_no_match_returns_one(self):
        aset = _make_minimal_assumption_set()
        mult = get_multiplier(aset, "lapse", "TERM", "M", "STD_NS", 3)
        assert mult == 1.0   # empty lapse_multipliers

    def test_different_decrement_type(self):
        aset = _make_minimal_assumption_set()
        aset.lapse_multipliers = [
            DecrementMultiplier("TERM", "M", "STD_NS", [1, 10], 1.05,
                                0.50, 0.90, 1.20)
        ]
        mult = get_multiplier(aset, "lapse", "TERM", "M", "STD_NS", 5)
        assert mult == pytest.approx(1.05, abs=1e-6)


# ---------------------------------------------------------------------------
# Integration tests: create_assumption_set_from_ae_run
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not DB_PATH.exists(), reason="DB not available")
class TestCreateAssumptionSetFromAERun:
    def test_creates_yaml_file(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert Path(aset.yaml_file_path).exists()

    def test_status_is_proposed(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert aset.status == AssumptionSetStatus.PROPOSED

    def test_mortality_multipliers_populated(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert len(aset.mortality_multipliers) > 0

    def test_lapse_multipliers_populated(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert len(aset.lapse_multipliers) > 0

    def test_multipliers_in_sane_range(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        all_mults = (
            aset.mortality_multipliers
            + aset.lapse_multipliers
            + aset.surrender_multipliers
            + aset.ci_incidence_multipliers
        )
        for m in all_mults:
            assert 0.1 <= m.multiplier <= 10.0, (
                f"Multiplier {m.multiplier} out of range for "
                f"{m.product}/{m.gender}/{m.risk_class}"
            )

    def test_credibility_bounds_non_zero(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        # At least some cells must have non-zero credibility bounds
        all_mults = aset.mortality_multipliers + aset.lapse_multipliers
        non_zero_upper = [m for m in all_mults if m.credibility_upper > 0]
        assert len(non_zero_upper) > 0

    def test_ci_multipliers_exist(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert len(aset.ci_incidence_multipliers) > 0

    def test_economic_params_from_config(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        assert aset.rdr == pytest.approx(0.09)
        assert aset.tax_rate == pytest.approx(0.21)
        assert aset.earned_rate_ga == pytest.approx(0.05)

    def test_inserted_into_db(self, tmp_path):
        aset = create_assumption_set_from_ae_run(
            study_run_id=STUDY_RUN_ID,
            author_id="test",
            db_path=DB_PATH,
            tev_config_path=TEV_CFG,
            output_yaml_dir=tmp_path,
        )
        con = duckdb.connect(str(DB_PATH))
        row = con.execute(
            "SELECT assumption_set_id FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset.id]
        ).fetchone()
        con.close()
        assert row is not None


# ---------------------------------------------------------------------------
# Integration tests: save_assumption_set / load_assumption_set
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not DB_PATH.exists(), reason="DB not available")
class TestSaveLoadAssumptionSet:
    def test_save_and_load_roundtrip(self, tmp_path):
        aset = _make_minimal_assumption_set()
        aset.yaml_file_path = str(tmp_path / f"{aset.id}.yaml")
        saved_id = save_assumption_set(aset, DB_PATH)
        assert saved_id == aset.id

        loaded = load_assumption_set(saved_id, DB_PATH)
        assert loaded.id == aset.id
        assert loaded.rdr == pytest.approx(0.09)
        assert len(loaded.mortality_multipliers) == 2
        assert loaded.mortality_multipliers[0].multiplier == pytest.approx(0.92)

        # Cleanup
        con = duckdb.connect(str(DB_PATH))
        con.execute("DELETE FROM gold_assumption_sets WHERE assumption_set_id = ?",
                    [aset.id])
        con.close()

    def test_load_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            load_assumption_set("00000000-0000-0000-0000-000000000000", DB_PATH)

    def test_save_is_idempotent(self, tmp_path):
        aset = _make_minimal_assumption_set()
        aset.yaml_file_path = str(tmp_path / f"{aset.id}.yaml")
        save_assumption_set(aset, DB_PATH)
        save_assumption_set(aset, DB_PATH)   # second save should not raise

        con = duckdb.connect(str(DB_PATH))
        count = con.execute(
            "SELECT COUNT(*) FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset.id]
        ).fetchone()[0]
        con.close()
        assert count == 1

        # Cleanup
        con = duckdb.connect(str(DB_PATH))
        con.execute("DELETE FROM gold_assumption_sets WHERE assumption_set_id = ?",
                    [aset.id])
        con.close()
