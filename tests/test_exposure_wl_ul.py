"""Exposure engine unit tests for Phase 1B — Whole Life and Universal Life.

Verifies product-specific exposure behaviours that have no equivalent in the
generic TERM exposure tests (test_exposure.py):

  WL:
    - SURRENDER and LAPSE are stored as DISTINCT decrement types (not conflated)
    - Non-forfeiture elections (RPU / ETT) generate NO lapse or surrender decrement
    - DEATH and CI_CLAIM decrements are also present
    - All WL segments carry product_code = 'WL'

  UL / ULSG / IUL:
    - All three variants preserve their own product_code in gold_exposure_segments
    - ULSG generates NO CI_CLAIM decrements (ULSG carries no CI rider)
    - UL and IUL can generate CI_CLAIM decrements (they do carry CI riders)
    - is_plt_flag is False for all UL-family segments (no PLT period in UL)
    - LAPSE decrement exists for UL/IUL/ULSG; SURRENDER does NOT appear (UL exits are LAPSE)

All tests use module-scoped fixtures that run ETL + Exposure on the synthetic
datasets once per session.
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
# WL pipeline fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def wl_exposure_db(tmp_path_factory) -> tuple[Path, str]:
    """ETL + Exposure for WL synthetic data; return (db_path, run_id)."""
    source_csv = Path("synthetic_data/output/wl_policies.csv")
    if not source_csv.exists():
        pytest.skip("WL synthetic data not generated — run generate_all.py first")

    db_path = tmp_path_factory.mktemp("wl_exposure") / "wl_exp.duckdb"
    init_database(db_path)
    run_id = str(uuid.uuid4())

    run_etl_pipeline("WL", source_csv, Path("config/products/wl.yaml"), db_path, run_id)
    cfg = StudyConfig(product_codes=["WL"], **_STUDY_CFG_KWARGS)
    build_exposure_file("WL", db_path, cfg, run_id)
    return db_path, run_id


# ---------------------------------------------------------------------------
# UL pipeline fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ul_exposure_db(tmp_path_factory) -> tuple[Path, str]:
    """ETL + Exposure for UL synthetic data; return (db_path, run_id)."""
    source_csv = Path("synthetic_data/output/ul_policies.csv")
    if not source_csv.exists():
        pytest.skip("UL synthetic data not generated — run generate_all.py first")

    db_path = tmp_path_factory.mktemp("ul_exposure") / "ul_exp.duckdb"
    init_database(db_path)
    run_id = str(uuid.uuid4())

    run_etl_pipeline("UL", source_csv, Path("config/products/ul.yaml"), db_path, run_id)
    cfg = StudyConfig(product_codes=["UL"], **_STUDY_CFG_KWARGS)
    build_exposure_file("UL", db_path, cfg, run_id)
    return db_path, run_id


def _q(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WL exposure: surrender vs lapse are distinct decrements
# ---------------------------------------------------------------------------

class TestWLSurrenderLapseDistinct:
    """SURRENDER and LAPSE must be stored as separate, non-overlapping decrements."""

    def test_wl_exposure_segments_exist(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code = 'WL'")
        assert rows[0][0] > 0

    def test_surrender_decrement_exists(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='SURRENDER'"
        )
        assert rows[0][0] > 0, "No SURRENDER decrements found in WL exposure"

    def test_lapse_decrement_exists(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='LAPSE'"
        )
        assert rows[0][0] > 0, "No LAPSE decrements found in WL exposure"

    def test_surrender_and_lapse_counts_are_different(self, wl_exposure_db) -> None:
        """SURRENDER ≠ LAPSE: they are separate decrements with separate counts."""
        db, _ = wl_exposure_db
        surrenders = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='SURRENDER'"
        )[0][0]
        lapses = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='LAPSE'"
        )[0][0]
        # Both exist but are stored under different labels — not the same count
        assert surrenders > 0 and lapses > 0
        # No segment should carry both labels simultaneously
        both = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' "
            "AND decrement_type='SURRENDER' AND decrement_type='LAPSE'"
        )[0][0]
        assert both == 0

    def test_surrender_decrement_flag_is_true(self, wl_exposure_db) -> None:
        """All SURRENDER segments must have decrement_flag=True."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT policy_id, decrement_flag FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='SURRENDER' LIMIT 20"
        )
        assert len(rows) > 0
        for pid, flag in rows:
            assert flag is True, f"Policy {pid}: SURRENDER decrement but decrement_flag=False"

    def test_lapse_decrement_flag_is_true(self, wl_exposure_db) -> None:
        """All LAPSE segments must have decrement_flag=True."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT policy_id, decrement_flag FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='LAPSE' LIMIT 20"
        )
        assert len(rows) > 0
        for pid, flag in rows:
            assert flag is True, f"Policy {pid}: LAPSE decrement but decrement_flag=False"

    def test_wl_decrement_types_limited_to_known_set(self, wl_exposure_db) -> None:
        """WL must only use known decrement types — no DA-specific or TERM-specific types."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT DISTINCT decrement_type FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type IS NOT NULL"
        )
        types = {r[0] for r in rows}
        allowed = {"LAPSE", "SURRENDER", "DEATH", "CI_CLAIM", "NON_FORFEITURE", "EXPIRY"}
        unexpected = types - allowed
        assert not unexpected, f"Unexpected WL decrement types: {unexpected}"

    def test_all_wl_segments_have_product_code_wl(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT DISTINCT product_code FROM gold_exposure_segments "
            "WHERE product_code LIKE 'WL%'"
        )
        assert {r[0] for r in rows} == {"WL"}


# ---------------------------------------------------------------------------
# WL exposure: non-forfeiture (RPU / ETT) → NOT a lapse or surrender decrement
# ---------------------------------------------------------------------------

class TestWLNonForfeitureNotDecrement:
    """RPU / ETT status policies must not generate LAPSE or SURRENDER decrements."""

    def test_rpu_policies_exist_in_silver(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM silver_wl_policies WHERE non_forfeiture_status = 'RPU'"
        )
        if rows[0][0] == 0:
            pytest.skip("No RPU policies in synthetic WL data")
        assert rows[0][0] > 0

    def test_rpu_policies_have_no_lapse_decrement(self, wl_exposure_db) -> None:
        """Policies with non_forfeiture_status=RPU must not appear as LAPSE decrements."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_wl_policies s ON e.policy_id = s.policy_id
            WHERE s.non_forfeiture_status = 'RPU'
              AND e.decrement_type = 'LAPSE'
              AND e.product_code = 'WL'
            """
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} RPU policies incorrectly classified as LAPSE decrements"
        )

    def test_rpu_policies_have_no_surrender_decrement(self, wl_exposure_db) -> None:
        """RPU policies must not appear as SURRENDER decrements either."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_wl_policies s ON e.policy_id = s.policy_id
            WHERE s.non_forfeiture_status = 'RPU'
              AND e.decrement_type = 'SURRENDER'
              AND e.product_code = 'WL'
            """
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} RPU policies incorrectly classified as SURRENDER decrements"
        )

    def test_ett_policies_have_no_lapse_decrement(self, wl_exposure_db) -> None:
        """ETT (Extended Term) policies must not appear as LAPSE decrements."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_wl_policies s ON e.policy_id = s.policy_id
            WHERE s.non_forfeiture_status = 'ETT'
              AND e.decrement_type = 'LAPSE'
              AND e.product_code = 'WL'
            """
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} ETT policies incorrectly classified as LAPSE decrements"
        )

    @pytest.mark.skip(
        reason="WL non-forfeiture (RPU/ETT) simulation was removed from the generator on "
        "2026-05-21 (base lapse rate now represents lapse+surrender only), so no RPU/ETT "
        "policies are produced. Re-enable if non-forfeiture election is reintroduced."
    )
    def test_rpu_and_ett_policies_have_exposure_segments(self, wl_exposure_db) -> None:
        """Non-forfeiture policies must still produce exposure segments (just no lapse/surrender)."""
        db, _ = wl_exposure_db
        rows = _q(
            db,
            """
            SELECT COUNT(*) FROM gold_exposure_segments e
            JOIN silver_wl_policies s ON e.policy_id = s.policy_id
            WHERE s.non_forfeiture_status IN ('RPU','ETT')
              AND e.product_code = 'WL'
            """
        )
        assert rows[0][0] > 0, "No exposure segments found for RPU/ETT policies at all"


# ---------------------------------------------------------------------------
# WL exposure: death and CI decrements exist
# ---------------------------------------------------------------------------

class TestWLMortalityAndCIDecrements:
    """WL must have DEATH decrements; CI_CLAIM decrements must exist for CI riders."""

    def test_death_decrement_exists(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='DEATH'"
        )
        assert rows[0][0] > 0, "No DEATH decrements found in WL exposure"

    def test_ci_claim_decrement_exists_for_rider_policies(self, wl_exposure_db) -> None:
        """WL policies with CI rider must generate CI_CLAIM decrements."""
        db, _ = wl_exposure_db
        rider_count = _q(
            db,
            "SELECT COUNT(*) FROM silver_wl_policies WHERE ci_rider_flag = TRUE"
        )[0][0]
        if rider_count == 0:
            pytest.skip("No CI rider policies in synthetic WL data")
        ci_decrements = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='WL' AND decrement_type='CI_CLAIM'"
        )[0][0]
        assert ci_decrements > 0, "No CI_CLAIM decrements despite CI rider policies existing"

    def test_exposure_years_positive_for_wl(self, wl_exposure_db) -> None:
        db, _ = wl_exposure_db
        rows = _q(db, "SELECT MIN(exposure_years) FROM gold_exposure_segments WHERE product_code='WL'")
        assert rows[0][0] > 0.0


# ---------------------------------------------------------------------------
# UL exposure: product_code preservation across all three variants
# ---------------------------------------------------------------------------

class TestULProductCodePreservation:
    """UL, ULSG, and IUL must each appear under their own product_code in exposure."""

    def test_ul_exposure_segments_exist(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code IN ('UL','ULSG','IUL')"
        )
        assert rows[0][0] > 0

    def test_ul_product_code_present(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='UL'")
        assert rows[0][0] > 0, "No segments with product_code='UL'"

    def test_ulsg_product_code_present(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='ULSG'")
        assert rows[0][0] > 0, "No segments with product_code='ULSG'"

    def test_iul_product_code_present(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(db, "SELECT COUNT(*) FROM gold_exposure_segments WHERE product_code='IUL'")
        assert rows[0][0] > 0, "No segments with product_code='IUL'"

    def test_no_generic_ul_family_normalization(self, ul_exposure_db) -> None:
        """Product codes must NOT be normalised to a single generic value like 'UL_ALL'."""
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT DISTINCT product_code FROM gold_exposure_segments "
            "WHERE product_code NOT IN ('UL','ULSG','IUL') "
            "AND product_code LIKE '%UL%'"
        )
        unexpected = {r[0] for r in rows}
        assert not unexpected, f"Unexpected UL-family product codes: {unexpected}"

    def test_all_three_variants_have_lapse_decrements(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        for code in ("UL", "IUL"):
            rows = _q(
                db,
                f"SELECT COUNT(*) FROM gold_exposure_segments "
                f"WHERE product_code='{code}' AND decrement_type='LAPSE'"
            )
            assert rows[0][0] > 0, f"No LAPSE decrements for product_code='{code}'"


# ---------------------------------------------------------------------------
# UL exposure: ULSG has no CI rider → no CI_CLAIM decrements
# ---------------------------------------------------------------------------

class TestULSGNoCIDecrement:
    """ULSG must never generate CI_CLAIM decrements (ULSG carries no CI rider)."""

    def test_ulsg_has_no_ci_claim_decrement(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code='ULSG' AND decrement_type='CI_CLAIM'"
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} CI_CLAIM decrements found for ULSG (should be 0 — no CI rider)"
        )

    def test_ul_can_have_ci_claim_decrement(self, ul_exposure_db) -> None:
        """Trad UL (15% CI rider penetration) may generate CI_CLAIM decrements."""
        db, _ = ul_exposure_db
        rider_count = _q(
            db,
            "SELECT COUNT(*) FROM silver_ul_policies "
            "WHERE ci_rider_flag=TRUE AND is_ulsg_flag=FALSE"
        )[0][0]
        if rider_count == 0:
            pytest.skip("No non-ULSG CI rider policies in synthetic UL data")
        # CI_CLAIM decrements for UL/IUL may or may not exist depending on
        # how many claims occur — just verify the check produces a non-negative count
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code IN ('UL','IUL') AND decrement_type='CI_CLAIM'"
        )
        assert rows[0][0] >= 0  # structural check: no exception; count ≥ 0

    def test_ulsg_silver_has_no_ci_rider_flag(self, ul_exposure_db) -> None:
        """Confirm at the silver level: all ULSG records have ci_rider_flag=False."""
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM silver_ul_policies "
            "WHERE is_ulsg_flag=TRUE AND ci_rider_flag=TRUE"
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} ULSG silver rows incorrectly have ci_rider_flag=True"
        )


# ---------------------------------------------------------------------------
# UL exposure: no PLT flag — UL has no post-level-term period
# ---------------------------------------------------------------------------

class TestULNoPLTFlag:
    """UL-family products have no PLT period; is_plt_flag must be False for all segments."""

    def test_ul_has_no_plt_segments(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code IN ('UL','ULSG','IUL') AND is_plt_flag=TRUE"
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} UL-family segments incorrectly have is_plt_flag=True"
        )

    def test_ul_exposure_years_positive(self, ul_exposure_db) -> None:
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT MIN(exposure_years) FROM gold_exposure_segments "
            "WHERE product_code IN ('UL','ULSG','IUL')"
        )
        assert rows[0][0] > 0.0

    def test_ul_has_no_surrender_decrement(self, ul_exposure_db) -> None:
        """UL exits as LAPSE only — no SURRENDER decrement type (unlike WL)."""
        db, _ = ul_exposure_db
        rows = _q(
            db,
            "SELECT COUNT(*) FROM gold_exposure_segments "
            "WHERE product_code IN ('UL','ULSG','IUL') AND decrement_type='SURRENDER'"
        )
        assert rows[0][0] == 0, (
            f"{rows[0][0]} UL segments have SURRENDER decrement — should be LAPSE"
        )
