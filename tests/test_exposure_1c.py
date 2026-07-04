"""Exposure engine unit tests for Phase 1C — VUL and Deferred Annuities.

Verifies that the exposure engine correctly translates product-specific
silver fields into gold_exposure_segments flags:

  VUL:  withdrawal_active_flag → is_plt_flag  (repurposed field)
  DA:   surrender_charge_year in [6,10] AND NOT expired → is_plt_flag
        contract_id aliased to policy_id in gold_exposure_segments
        decrement_type = 'SURRENDER' for DA surrenders (not 'LAPSE')

These tests run the full ETL + Exposure pipeline on the synthetic datasets
using a module-scoped temporary database, so the pipeline runs once per
product and all tests within a class share the result.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pytest

from src.exposure.engine import build_exposure_file
from src.ingestion.pipeline import run_etl_pipeline
from src.utils.db_init import init_database
from src.utils.types import (
    CredibilityMethod,
    ExposureMethod,
    StudyConfig,
)

_STUDY_CFG_KWARGS = dict(
    study_start_date=date(2016, 1, 1),
    study_end_date=date(2023, 12, 31),
    exposure_method=ExposureMethod.ANNUAL,
    mortality_table_path="config/reference_tables/mortality_2015vbt.parquet",
    lapse_table_path="config/reference_tables/lapse_benchmarks.parquet",
    ci_table_path="config/reference_tables/ci_incidence.parquet",
    credibility_method=CredibilityMethod.LIMITED_FLUCTUATION,
)


# ---------------------------------------------------------------------------
# VUL pipeline fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vul_exposure_db(tmp_path_factory) -> tuple[Path, str]:
    """ETL + Exposure for VUL synthetic data; return (db_path, run_id)."""
    source_csv = Path("synthetic_data/output/vul_policies.csv")
    if not source_csv.exists():
        pytest.skip("VUL synthetic data not generated — run generate_all.py first")

    db_path = tmp_path_factory.mktemp("vul_exposure") / "vul_exp.duckdb"
    init_database(db_path)
    run_id = str(uuid.uuid4())

    run_etl_pipeline("VUL", source_csv, Path("config/products/vul.yaml"), db_path, run_id)
    cfg = StudyConfig(product_codes=["VUL"], **_STUDY_CFG_KWARGS)
    build_exposure_file("VUL", db_path, cfg, run_id)
    return db_path, run_id


# ---------------------------------------------------------------------------
# DA pipeline fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def da_exposure_db(tmp_path_factory) -> tuple[Path, str]:
    """ETL + Exposure for DA synthetic data; return (db_path, run_id)."""
    source_csv = Path("synthetic_data/output/annuity_contracts.csv")
    if not source_csv.exists():
        pytest.skip("DA synthetic data not generated — run generate_all.py first")

    db_path = tmp_path_factory.mktemp("da_exposure") / "da_exp.duckdb"
    init_database(db_path)
    run_id = str(uuid.uuid4())

    run_etl_pipeline("DA", source_csv, Path("config/products/annuity.yaml"), db_path, run_id)
    cfg = StudyConfig(product_codes=["DA"], **_STUDY_CFG_KWARGS)
    build_exposure_file("DA", db_path, cfg, run_id)
    return db_path, run_id


def _q(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# VUL exposure tests — withdrawal_active_flag → is_plt_flag
# ---------------------------------------------------------------------------

class TestVULExposureFlags:
    """Verify VUL withdrawal_active_flag propagates correctly to is_plt_flag."""

    def test_vul_exposure_segments_exist(self, vul_exposure_db) -> None:
        db, run_id = vul_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code = 'VUL'")
        assert rows[0][0] > 0

    def test_withdrawal_active_true_produces_plt_true(self, vul_exposure_db) -> None:
        """Every segment of a withdrawal_active policy must have is_plt_flag=True."""
        db, _ = vul_exposure_db
        # Find a policy where all silver records have withdrawal_active_flag=True
        rows = _q(
            db,
            """
            SELECT e.policy_id, e.is_plt_flag
            FROM gold_exposure_segments e
            JOIN silver_vul_policies s ON e.policy_id = s.policy_id
            WHERE s.withdrawal_active_flag = TRUE
            LIMIT 50
            """
        )
        assert len(rows) > 0, "No segments found for withdrawal_active policies"
        for pid, plt in rows:
            assert plt is True, f"Policy {pid}: withdrawal_active=True but is_plt_flag=False"

    def test_withdrawal_active_false_produces_plt_false(self, vul_exposure_db) -> None:
        """Every segment of a non-withdrawal policy must have is_plt_flag=False."""
        db, _ = vul_exposure_db
        rows = _q(
            db,
            """
            SELECT e.policy_id, e.is_plt_flag
            FROM gold_exposure_segments e
            JOIN silver_vul_policies s ON e.policy_id = s.policy_id
            WHERE s.withdrawal_active_flag = FALSE
            LIMIT 50
            """
        )
        assert len(rows) > 0, "No segments found for non-withdrawal policies"
        for pid, plt in rows:
            assert plt is False, f"Policy {pid}: withdrawal_active=False but is_plt_flag=True"

    def test_is_plt_counts_match_withdrawal_active(self, vul_exposure_db) -> None:
        """Count of plt=True segments must equal count of segments for withdrawal_active policies."""
        db, _ = vul_exposure_db
        plt_true = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='VUL' AND is_plt_flag=TRUE"
        )[0][0]
        from_withdrawal = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_vul_policies s ON e.policy_id = s.policy_id
            WHERE s.withdrawal_active_flag = TRUE
            """
        )[0][0]
        assert plt_true == from_withdrawal

    def test_all_vul_segments_have_product_code_vul(self, vul_exposure_db) -> None:
        db, _ = vul_exposure_db
        rows = _q(
            db,
            "SELECT DISTINCT product_code FROM gold_exposure_segments WHERE product_code LIKE 'VUL%'"
        )
        codes = {r[0] for r in rows}
        assert codes == {"VUL"}

    def test_exposure_years_positive(self, vul_exposure_db) -> None:
        db, _ = vul_exposure_db
        rows = _q(db, "SELECT MIN(exposure_years) FROM gold_exposure_segments WHERE product_code='VUL'")
        assert rows[0][0] > 0.0

    def test_vul_segments_have_no_lapse_decrements(self, vul_exposure_db) -> None:
        """VUL surrenders must be stored as SURRENDER or LAPSE — not a DA-only code."""
        db, _ = vul_exposure_db
        rows = _q(
            db,
            """
            SELECT DISTINCT decrement_type FROM gold_exposure_segments
            WHERE product_code = 'VUL' AND decrement_type IS NOT NULL
            """
        )
        types = {r[0] for r in rows}
        # VUL should NOT have FULL_SURRENDER (DA-specific code)
        assert "FULL_SURRENDER" not in types


# ---------------------------------------------------------------------------
# DA exposure tests — SC expiry logic → is_plt_flag
# ---------------------------------------------------------------------------

class TestDAExposureSCExpiry:
    """Verify DA surrender-charge-expiry approach flag (is_plt_flag)."""

    def test_da_exposure_segments_exist(self, da_exposure_db) -> None:
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            """
        )
        assert rows[0][0] > 0

    def test_sc_year_in_approach_band_produces_plt_true(self, da_exposure_db) -> None:
        """Contracts with sc_year in [6,10] and not expired must have is_plt_flag=True."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT e.policy_id, e.is_plt_flag, a.surrender_charge_year, a.is_surrender_charge_expired_flag
            FROM gold_exposure_segments e
            JOIN silver_annuity_contracts a ON e.policy_id = a.contract_id
            WHERE a.surrender_charge_year >= 6
              AND a.surrender_charge_year <= 10
              AND a.is_surrender_charge_expired_flag = FALSE
              AND e.product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            LIMIT 50
            """
        )
        assert len(rows) > 0, "No segments found for SC-approaching contracts"
        for pid, plt, sc_yr, expired in rows:
            assert plt is True, (
                f"Contract {pid}: sc_year={sc_yr}, expired={expired} — expected is_plt_flag=True"
            )

    def test_sc_year_below_threshold_produces_plt_false(self, da_exposure_db) -> None:
        """Contracts with sc_year < 6 must have is_plt_flag=False."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT e.policy_id, e.is_plt_flag, a.surrender_charge_year
            FROM gold_exposure_segments e
            JOIN silver_annuity_contracts a ON e.policy_id = a.contract_id
            WHERE a.surrender_charge_year < 6
              AND a.is_surrender_charge_expired_flag = FALSE
              AND e.product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            LIMIT 50
            """
        )
        assert len(rows) > 0, "No segments found for sc_year < 6 contracts"
        for pid, plt, sc_yr in rows:
            assert plt is False, (
                f"Contract {pid}: sc_year={sc_yr} — expected is_plt_flag=False, got True"
            )

    def test_sc_expired_flag_produces_plt_false(self, da_exposure_db) -> None:
        """Contracts where sc_expired=True must have is_plt_flag=False regardless of sc_year."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT e.policy_id, e.is_plt_flag, a.surrender_charge_year, a.is_surrender_charge_expired_flag
            FROM gold_exposure_segments e
            JOIN silver_annuity_contracts a ON e.policy_id = a.contract_id
            WHERE a.is_surrender_charge_expired_flag = TRUE
              AND e.product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            LIMIT 50
            """
        )
        if len(rows) == 0:
            pytest.skip("No expired SC contracts in synthetic data")
        for pid, plt, sc_yr, expired in rows:
            assert plt is False, (
                f"Contract {pid}: sc_expired=True but is_plt_flag=True (sc_year={sc_yr})"
            )

    def test_plt_true_contracts_exist(self, da_exposure_db) -> None:
        """At least some DA segments must have is_plt_flag=True."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA') AND is_plt_flag = TRUE
            """
        )
        assert rows[0][0] > 0, "No DA segments with is_plt_flag=True found"

    def test_plt_false_contracts_exist(self, da_exposure_db) -> None:
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA') AND is_plt_flag = FALSE
            """
        )
        assert rows[0][0] > 0


# ---------------------------------------------------------------------------
# DA exposure tests — contract_id aliasing and product_code storage
# ---------------------------------------------------------------------------

class TestDAExposureIdentity:
    """Verify DA identity fields are stored correctly in gold_exposure_segments."""

    def test_policy_id_matches_contract_id(self, da_exposure_db) -> None:
        """policy_id in exposure must equal contract_id from silver_annuity_contracts."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_annuity_contracts a ON e.policy_id = a.contract_id
            WHERE e.product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            """
        )
        total_rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            """
        )[0][0]
        # All DA exposure rows must join back to silver on contract_id
        assert rows[0][0] == total_rows

    def test_policy_id_has_da_prefix(self, da_exposure_db) -> None:
        """DA contract IDs use DAF- or DAV- prefixes, not generic DA-."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT DISTINCT LEFT(policy_id, 3) AS prefix FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
            """
        )
        prefixes = {r[0] for r in rows}
        # All DA contracts use DAF- prefix (even FIA uses DAF-); no raw 'DA-' IDs
        assert "DAF" in prefixes or "DAV" in prefixes
        assert "DA-" not in {p[:3] for p in prefixes}

    def test_product_codes_are_subtypes_not_generic_da(self, da_exposure_db) -> None:
        """Exposure segments use specific sub-type codes, not the generic 'DA' code."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            "SELECT DISTINCT product_code FROM gold_exposure_segments WHERE product_code LIKE 'DA%'"
        )
        codes = {r[0] for r in rows}
        assert "DA" not in codes, f"Generic 'DA' product code found in exposure segments: {codes}"
        assert codes.issubset({"DA_FIXED", "DA_FIA", "DA_VA"})

    def test_da_fixed_segments_exist(self, da_exposure_db) -> None:
        db, _ = da_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='DA_FIXED'")
        assert rows[0][0] > 0

    def test_da_va_segments_exist(self, da_exposure_db) -> None:
        db, _ = da_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='DA_VA'")
        assert rows[0][0] > 0


# ---------------------------------------------------------------------------
# DA exposure tests — SURRENDER decrement type
# ---------------------------------------------------------------------------

class TestDAExposureDecrement:
    """Verify DA terminations are stored with SURRENDER decrement type."""

    def test_da_surrender_decrement_type_is_surrender(self, da_exposure_db) -> None:
        """Full DA surrenders must have decrement_type='SURRENDER', not 'LAPSE'."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT DISTINCT decrement_type FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
              AND decrement_type IS NOT NULL
            """
        )
        types = {r[0] for r in rows}
        assert "SURRENDER" in types, "No SURRENDER decrements found in DA exposure"
        assert "LAPSE" not in types, f"LAPSE decrement found in DA exposure — should be SURRENDER: {types}"

    def test_da_surrender_decrement_flag_is_true(self, da_exposure_db) -> None:
        """Segments with SURRENDER decrement_type must have decrement_flag=True."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT policy_id, decrement_flag FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
              AND decrement_type = 'SURRENDER'
            LIMIT 20
            """
        )
        assert len(rows) > 0
        for pid, flag in rows:
            assert flag is True, f"Contract {pid}: SURRENDER decrement but decrement_flag=False"

    def test_death_decrement_also_present(self, da_exposure_db) -> None:
        """Annuity owner deaths must appear as DEATH decrements."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
              AND decrement_type = 'DEATH'
            """
        )
        # Annuity owners die too — at least some DEATH decrements expected
        assert rows[0][0] > 0

    def test_no_lapse_decrement_in_da(self, da_exposure_db) -> None:
        """DA products should not use LAPSE as a decrement type."""
        db, _ = da_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments
            WHERE product_code IN ('DA_FIXED','DA_FIA','DA_VA')
              AND decrement_type = 'LAPSE'
            """
        )
        assert rows[0][0] == 0, "Unexpected LAPSE decrements found in DA exposure segments"
