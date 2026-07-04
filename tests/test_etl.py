"""Unit tests for the Term Life ETL pipeline (src/ingestion/pipeline.py)."""

import uuid
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingestion.pipeline import load_mapping_config, run_etl_pipeline
from src.utils.db_init import init_database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAPPING_CONFIG = Path("config/products/term.yaml")

MINIMAL_ROWS = [
    {
        "policy_id": "TRM-0000001",
        "product_code": "TERM",
        "plan_code": "T20",
        "issue_date": "2015-01-15",
        "date_of_birth": "1980-01-15",
        "issue_age_anb": "35",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "face_amount": "500000",
        "premium_mode": "ANNUAL",
        "annual_premium": "1250.00",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "level_period_years": "20",
        "plt_premium_year_1": "",
        "plt_structure_code": "",
        "premium_jump_ratio": "",
        "conversion_flag": "False",
        "ci_rider_flag": "True",
        "ci_rider_sum_assured": "250000.00",
        "ci_rider_premium": "75.00",
        "distribution_channel": "CAREER",
        "issue_state": "CA",
    },
    {
        "policy_id": "TRM-0000002",
        "product_code": "TERM",
        "plan_code": "T10",
        "issue_date": "2016-06-01",
        "date_of_birth": "1975-06-01",
        "issue_age_anb": "41",
        "gender": "F",
        "smoker_status": "SM",
        "risk_class": "STD_SM",
        "face_amount": "250000",
        "premium_mode": "MONTHLY",
        "annual_premium": "800.00",
        "reinsurance_flag": "False",
        "status_code": "LAPSE",
        "termination_date": "2020-03-15",
        "termination_cause_code": "LAPSE",
        "level_period_years": "10",
        "plt_premium_year_1": "",
        "plt_structure_code": "",
        "premium_jump_ratio": "",
        "conversion_flag": "False",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "distribution_channel": "INDEPENDENT",
        "issue_state": "NY",
    },
    {
        "policy_id": "TRM-0000003",
        "product_code": "TERM",
        "plan_code": "T20",
        "issue_date": "2017-03-10",
        "date_of_birth": "1982-03-10",
        "issue_age_anb": "35",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "STD_NS",
        "face_amount": "750000",
        "premium_mode": "ANNUAL",
        "annual_premium": "2000.00",
        "reinsurance_flag": "True",
        "status_code": "DEATH",
        "termination_date": "2021-07-20",
        "termination_cause_code": "DEATH_BENEFIT_CLAIM",
        "level_period_years": "20",
        "plt_premium_year_1": "",
        "plt_structure_code": "",
        "premium_jump_ratio": "",
        "conversion_flag": "False",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "distribution_channel": "CAREER",
        "issue_state": "TX",
    },
    {
        "policy_id": "TRM-0000004",
        "product_code": "TERM",
        "plan_code": "T20",
        "issue_date": "2014-05-01",
        "date_of_birth": "1979-05-01",
        "issue_age_anb": "35",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "SUPER_PREF",
        "face_amount": "1000000",
        "premium_mode": "ANNUAL",
        "annual_premium": "2500.00",
        "reinsurance_flag": "True",
        "status_code": "CONVERSION",      # should → CONV after translation
        "termination_date": "2019-05-01",
        "termination_cause_code": "CONVERSION",
        "level_period_years": "20",
        "plt_premium_year_1": "",
        "plt_structure_code": "",
        "premium_jump_ratio": "",
        "conversion_flag": "True",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "distribution_channel": "BANK",
        "issue_state": "FL",
    },
    {
        "policy_id": "TRM-0000005",
        "product_code": "TERM",
        "plan_code": "T10",
        "issue_date": "2015-09-01",
        "date_of_birth": "1972-09-01",
        "issue_age_anb": "43",
        "gender": "F",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "face_amount": "300000",
        "premium_mode": "QUARTERLY",
        "annual_premium": "900.00",
        "reinsurance_flag": "False",
        "status_code": "CI_CLAIM",
        "termination_date": "2021-01-10",
        "termination_cause_code": "CI_ACCELERATED_BENEFIT",
        "level_period_years": "10",
        "plt_premium_year_1": "",
        "plt_structure_code": "",
        "premium_jump_ratio": "",
        "conversion_flag": "False",
        "ci_rider_flag": "True",
        "ci_rider_sum_assured": "150000.00",
        "ci_rider_premium": "45.00",
        "distribution_channel": "DIRECT",
        "issue_state": "WA",
    },
]


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    """Write MINIMAL_ROWS to a temporary CSV and return its path."""
    df = pd.DataFrame(MINIMAL_ROWS)
    csv_path = tmp_path / "term_test.csv"
    df.to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Initialise a fresh DuckDB for each test and return its path."""
    db_path = tmp_path / "test.duckdb"
    init_database(db_path)
    return db_path


@pytest.fixture
def etl_result(mini_csv: Path, test_db: Path):
    """Run the ETL pipeline once and return (ETLResult, db_path)."""
    run_id = str(uuid.uuid4())
    result = run_etl_pipeline(
        product_code="TERM",
        source_path=mini_csv,
        mapping_config_path=MAPPING_CONFIG,
        db_path=test_db,
        run_id=run_id,
    )
    return result, test_db


def _query(db_path: Path, sql: str):
    """Execute a SQL query against the test DB and return fetchall rows."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# load_mapping_config tests
# ---------------------------------------------------------------------------

class TestLoadMappingConfig:
    def test_loads_successfully(self):
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert isinstance(cfg, dict)

    def test_required_keys_present(self):
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert "field_mappings" in cfg
        assert "target_table" in cfg

    def test_field_mappings_is_list(self):
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert isinstance(cfg["field_mappings"], list)
        assert len(cfg["field_mappings"]) > 0

    def test_code_translations_present(self):
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert "code_translations" in cfg

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_mapping_config(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# ETL run-level tests
# ---------------------------------------------------------------------------

class TestETLSuccess:
    def test_returns_success(self, etl_result):
        result, _ = etl_result
        assert result.success is True

    def test_no_errors(self, etl_result):
        result, _ = etl_result
        assert result.error_count == 0

    def test_records_ingested(self, etl_result):
        result, _ = etl_result
        assert result.records_ingested == 5

    def test_records_conformed(self, etl_result):
        result, _ = etl_result
        assert result.records_conformed == 5

    def test_duration_positive(self, etl_result):
        result, _ = etl_result
        assert result.duration_sec > 0

    def test_run_id_is_uuid(self, etl_result):
        result, _ = etl_result
        # Should be parseable as UUID
        uuid.UUID(result.run_id)


# ---------------------------------------------------------------------------
# Bronze table tests
# ---------------------------------------------------------------------------

class TestBronzeTable:
    def test_row_count(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM bronze_term_policies")
        assert rows[0][0] == 5

    def test_load_ts_populated(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT _load_ts FROM bronze_term_policies LIMIT 1")
        assert rows[0][0] is not None

    def test_source_file_populated(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT _source_file FROM bronze_term_policies LIMIT 1")
        assert rows[0][0] is not None and len(rows[0][0]) > 0

    def test_product_code_metadata(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT DISTINCT _product_code FROM bronze_term_policies")
        assert rows[0][0] == "TERM"

    def test_row_hash_is_64_chars(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT _row_hash FROM bronze_term_policies LIMIT 5")
        for (h,) in rows:
            assert len(h) == 64

    def test_bronze_id_is_36_chars(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT _bronze_id FROM bronze_term_policies LIMIT 5")
        for (bid,) in rows:
            assert len(bid) == 36

    def test_row_hashes_unique(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(DISTINCT _row_hash) FROM bronze_term_policies")
        assert rows[0][0] == 5


# ---------------------------------------------------------------------------
# Silver table tests
# ---------------------------------------------------------------------------

class TestSilverTable:
    def test_row_count(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies")
        assert rows[0][0] == 5

    def test_issue_date_is_date_type(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT issue_date FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        val = rows[0][0]
        assert isinstance(val, date)
        assert val == date(2015, 1, 15)

    def test_date_of_birth_is_date(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT date_of_birth FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert isinstance(rows[0][0], date)

    def test_face_amount_is_double(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT face_amount FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == pytest.approx(500000.0)

    def test_annual_premium_is_double(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT annual_premium FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == pytest.approx(1250.0)

    def test_ci_rider_flag_true_is_boolean(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT ci_rider_flag FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] is True

    def test_ci_rider_flag_false_is_boolean(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT ci_rider_flag FROM silver_term_policies WHERE policy_id = 'TRM-0000002'")
        assert rows[0][0] is False

    def test_reinsurance_flag_true(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT reinsurance_flag FROM silver_term_policies WHERE policy_id = 'TRM-0000003'")
        assert rows[0][0] is True

    def test_reinsurance_flag_false(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT reinsurance_flag FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] is False

    def test_issue_age_is_integer(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT issue_age_anb FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == 35

    def test_level_period_years_is_integer(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT level_period_years FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == 20

    def test_status_code_conversion_translated(self, etl_result):
        """CONVERSION in source CSV must become CONV in silver (per code translation)."""
        _, db = etl_result
        rows = _query(db, "SELECT status_code FROM silver_term_policies WHERE policy_id = 'TRM-0000004'")
        assert rows[0][0] == "CONV"

    def test_status_code_if_passthrough(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT status_code FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == "IF"

    def test_status_code_lapse_passthrough(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT status_code FROM silver_term_policies WHERE policy_id = 'TRM-0000002'")
        assert rows[0][0] == "LAPSE"

    def test_termination_cause_lapse_passthrough(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT termination_cause_code FROM silver_term_policies WHERE policy_id = 'TRM-0000002'")
        assert rows[0][0] == "LAPSE"

    def test_termination_cause_death_passthrough(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT termination_cause_code FROM silver_term_policies WHERE policy_id = 'TRM-0000003'")
        assert rows[0][0] == "DEATH_BENEFIT_CLAIM"

    def test_termination_cause_ci_passthrough(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT termination_cause_code FROM silver_term_policies WHERE policy_id = 'TRM-0000005'")
        assert rows[0][0] == "CI_ACCELERATED_BENEFIT"

    def test_termination_date_null_for_if(self, etl_result):
        """IF policies must have NULL termination_date."""
        _, db = etl_result
        rows = _query(db, "SELECT termination_date FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] is None

    def test_termination_cause_null_for_if(self, etl_result):
        """IF policies must have NULL termination_cause_code."""
        _, db = etl_result
        rows = _query(db, "SELECT termination_cause_code FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] is None

    def test_ci_rider_sum_assured_null_for_no_rider(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT ci_rider_sum_assured FROM silver_term_policies WHERE policy_id = 'TRM-0000002'")
        assert rows[0][0] is None

    def test_ci_rider_sum_assured_populated(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT ci_rider_sum_assured FROM silver_term_policies WHERE policy_id = 'TRM-0000001'")
        assert rows[0][0] == pytest.approx(250000.0)

    def test_no_nulls_in_policy_id(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE policy_id IS NULL")
        assert rows[0][0] == 0

    def test_no_nulls_in_product_code(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE product_code IS NULL")
        assert rows[0][0] == 0

    def test_no_nulls_in_face_amount(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE face_amount IS NULL")
        assert rows[0][0] == 0

    def test_no_nulls_in_issue_date(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE issue_date IS NULL")
        assert rows[0][0] == 0

    def test_no_nulls_in_reinsurance_flag(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE reinsurance_flag IS NULL")
        assert rows[0][0] == 0

    def test_no_nulls_in_ci_rider_flag(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE ci_rider_flag IS NULL")
        assert rows[0][0] == 0

    def test_etl_run_id_populated(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE _etl_run_id IS NULL")
        assert rows[0][0] == 0

    def test_source_bronze_id_populated(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE _source_bronze_id IS NULL")
        assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# Policy events tests
# ---------------------------------------------------------------------------

class TestPolicyEvents:
    def test_total_event_count(self, etl_result):
        """5 ISSUE events + 4 termination events (row 1 is IF, rows 2-5 terminated) = 9."""
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_policy_events")
        assert rows[0][0] == 9

    def test_issue_event_count(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_policy_events WHERE event_type = 'ISSUE'")
        assert rows[0][0] == 5

    def test_issue_event_date_matches_issue_date(self, etl_result):
        """ISSUE event_date must equal the policy's issue_date."""
        _, db = etl_result
        rows = _query(
            db,
            "SELECT e.event_date, s.issue_date "
            "FROM silver_policy_events e "
            "JOIN silver_term_policies s ON e.policy_id = s.policy_id "
            "WHERE e.event_type = 'ISSUE'"
        )
        for event_date, issue_date in rows:
            assert event_date == issue_date

    def test_lapse_event_exists(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT event_type FROM silver_policy_events WHERE policy_id = 'TRM-0000002'")
        types = {r[0] for r in rows}
        assert "LAPSE" in types

    def test_death_event_exists(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT event_type FROM silver_policy_events WHERE policy_id = 'TRM-0000003'")
        types = {r[0] for r in rows}
        assert "DEATH" in types

    def test_conversion_event_exists(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT event_type FROM silver_policy_events WHERE policy_id = 'TRM-0000004'")
        types = {r[0] for r in rows}
        assert "CONVERSION" in types

    def test_ci_claim_event_exists(self, etl_result):
        _, db = etl_result
        rows = _query(db, "SELECT event_type FROM silver_policy_events WHERE policy_id = 'TRM-0000005'")
        types = {r[0] for r in rows}
        assert "CI_CLAIM" in types

    def test_no_termination_event_for_if_policy(self, etl_result):
        """IF policy TRM-0000001 must only have an ISSUE event."""
        _, db = etl_result
        rows = _query(
            db,
            "SELECT event_type FROM silver_policy_events WHERE policy_id = 'TRM-0000001'"
        )
        types = [r[0] for r in rows]
        assert types == ["ISSUE"]

    def test_death_claim_amount_equals_face_amount(self, etl_result):
        _, db = etl_result
        rows = _query(
            db,
            "SELECT claim_amount, face_amount_before "
            "FROM silver_policy_events WHERE policy_id = 'TRM-0000003' AND event_type = 'DEATH'"
        )
        claim, face = rows[0]
        assert claim == pytest.approx(face)

    def test_ci_claim_amount_equals_ci_sum_assured(self, etl_result):
        _, db = etl_result
        rows = _query(
            db,
            "SELECT claim_amount FROM silver_policy_events "
            "WHERE policy_id = 'TRM-0000005' AND event_type = 'CI_CLAIM'"
        )
        assert rows[0][0] == pytest.approx(150000.0)

    def test_policy_year_one_for_issue_events(self, etl_result):
        _, db = etl_result
        rows = _query(
            db, "SELECT policy_year FROM silver_policy_events WHERE event_type = 'ISSUE'"
        )
        for (py,) in rows:
            assert py == 1


# ---------------------------------------------------------------------------
# Duplicate policy_id detection
# ---------------------------------------------------------------------------

class TestDuplicatePolicyIdDetection:
    def test_duplicate_logs_warning(self, tmp_path: Path, test_db: Path):
        """Duplicate policy_id in source CSV triggers a warning and keeps first row only."""
        dup_rows = [MINIMAL_ROWS[0], MINIMAL_ROWS[0]]  # same policy_id twice
        df = pd.DataFrame(dup_rows)
        csv_path = tmp_path / "dup_test.csv"
        df.to_csv(csv_path, index=False)

        result = run_etl_pipeline(
            product_code="TERM",
            source_path=csv_path,
            mapping_config_path=MAPPING_CONFIG,
            db_path=test_db,
            run_id=str(uuid.uuid4()),
        )
        assert result.success is True
        assert any("Duplicate" in w for w in result.warnings)

    def test_duplicate_deduplicates_to_one_silver_row(self, tmp_path: Path, test_db: Path):
        """After deduplication, only one row per duplicate policy_id lands in silver."""
        dup_rows = [MINIMAL_ROWS[0], MINIMAL_ROWS[0]]
        df = pd.DataFrame(dup_rows)
        csv_path = tmp_path / "dup_test2.csv"
        df.to_csv(csv_path, index=False)

        run_etl_pipeline(
            product_code="TERM",
            source_path=csv_path,
            mapping_config_path=MAPPING_CONFIG,
            db_path=test_db,
            run_id=str(uuid.uuid4()),
        )
        rows = _query(test_db, "SELECT COUNT(*) FROM silver_term_policies")
        assert rows[0][0] == 1


# ---------------------------------------------------------------------------
# Full synthetic data smoke test
# ---------------------------------------------------------------------------

class TestFullSyntheticData:
    """Smoke-test against the actual 3,200-row synthetic dataset."""

    @pytest.fixture(scope="class")
    def full_etl(self, tmp_path_factory):
        db_path = tmp_path_factory.mktemp("full_db") / "full.duckdb"
        init_database(db_path)
        result = run_etl_pipeline(
            product_code="TERM",
            source_path=Path("synthetic_data/output/term_policies.csv"),
            mapping_config_path=MAPPING_CONFIG,
            db_path=db_path,
            run_id=str(uuid.uuid4()),
        )
        return result, db_path

    def test_success(self, full_etl):
        result, _ = full_etl
        assert result.success is True

    def test_bronze_3200_rows(self, full_etl):
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM bronze_term_policies")
        assert rows[0][0] == 3200

    def test_silver_3200_rows(self, full_etl):
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies")
        assert rows[0][0] == 3200

    def test_events_at_least_3200(self, full_etl):
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM silver_policy_events")
        assert rows[0][0] >= 3200

    def test_no_conversion_in_status_code(self, full_etl):
        """The raw 'CONVERSION' value must have been translated to 'CONV'."""
        _, db = full_etl
        rows = _query(
            db,
            "SELECT COUNT(*) FROM silver_term_policies WHERE status_code = 'CONVERSION'"
        )
        assert rows[0][0] == 0

    def test_conv_present_in_status_code(self, full_etl):
        """Translated 'CONV' records should exist (there are ~39 conversions)."""
        _, db = full_etl
        rows = _query(
            db,
            "SELECT COUNT(*) FROM silver_term_policies WHERE status_code = 'CONV'"
        )
        assert rows[0][0] > 0

    def test_no_null_policy_ids(self, full_etl):
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE policy_id IS NULL")
        assert rows[0][0] == 0

    def test_no_null_face_amounts(self, full_etl):
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE face_amount IS NULL")
        assert rows[0][0] == 0

    def test_ci_rider_flags_all_boolean(self, full_etl):
        """No NULL ci_rider_flag values in the full dataset."""
        _, db = full_etl
        rows = _query(db, "SELECT COUNT(*) FROM silver_term_policies WHERE ci_rider_flag IS NULL")
        assert rows[0][0] == 0
