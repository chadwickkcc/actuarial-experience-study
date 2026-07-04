"""Unit tests for the Whole Life ETL pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingestion.pipeline import load_mapping_config, run_etl_pipeline
from src.utils.db_init import init_database

MAPPING_CONFIG = Path("config/products/wl.yaml")

# ---------------------------------------------------------------------------
# Minimal WL test rows
# ---------------------------------------------------------------------------

MINIMAL_ROWS = [
    {   # Par WL, in-force, with CI rider
        "policy_id": "WL-0000001",
        "product_code": "WL",
        "plan_code": "WL_LIFE_PAY",
        "issue_date": "2014-03-15",
        "date_of_birth": "1974-03-15",
        "issue_age_anb": "40",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "face_amount": "250000",
        "premium_mode": "ANNUAL",
        "annual_premium": "3200.00",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "premium_paying_period": "LIFE_PAY",
        "guaranteed_cash_value": "18000.00",
        "dividend_option_code": "PUA",
        "dividend_on_deposit_bal": "2500.00",
        "paid_up_additions_face": "4000.00",
        "policy_loan_balance": "0.00",
        "auto_premium_loan_flag": "False",
        "non_forfeiture_status": "ACTIVE",
        "participating_flag": "True",
        "dividend_scale_rate": "0.055",
        "small_face_flag": "False",
        "ci_rider_flag": "True",
        "ci_rider_sum_assured": "125000.00",
        "ci_rider_premium": "31.25",
        "illness_code": "",
        "distribution_channel": "CAREER",
        "issue_state": "IL",
    },
    {   # Non-par WL, surrendered, no CI rider
        "policy_id": "WL-0000002",
        "product_code": "WL",
        "plan_code": "WL_20_PAY",
        "issue_date": "2010-06-01",
        "date_of_birth": "1955-06-01",
        "issue_age_anb": "55",
        "gender": "F",
        "smoker_status": "NS",
        "risk_class": "STD_NS",
        "face_amount": "100000",
        "premium_mode": "ANNUAL",
        "annual_premium": "4200.00",
        "reinsurance_flag": "False",
        "status_code": "SURRENDER",
        "termination_date": "2021-08-15",
        "termination_cause_code": "SURRENDER",
        "premium_paying_period": "20_PAY",
        "guaranteed_cash_value": "45000.00",
        "dividend_option_code": "",
        "dividend_on_deposit_bal": "0.00",
        "paid_up_additions_face": "0.00",
        "policy_loan_balance": "0.00",
        "auto_premium_loan_flag": "False",
        "non_forfeiture_status": "ACTIVE",
        "participating_flag": "False",
        "dividend_scale_rate": "",
        "small_face_flag": "False",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "INDEPENDENT",
        "issue_state": "OH",
    },
    {   # Small-face final expense, RPU, no CI rider
        "policy_id": "WL-0000003",
        "product_code": "WL",
        "plan_code": "WL_10_PAY",
        "issue_date": "2009-01-01",
        "date_of_birth": "1949-01-01",
        "issue_age_anb": "60",
        "gender": "F",
        "smoker_status": "SM",
        "risk_class": "STD_SM",
        "face_amount": "15000",
        "premium_mode": "MONTHLY",
        "annual_premium": "720.00",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "premium_paying_period": "10_PAY",
        "guaranteed_cash_value": "12000.00",
        "dividend_option_code": "",
        "dividend_on_deposit_bal": "0.00",
        "paid_up_additions_face": "0.00",
        "policy_loan_balance": "200.00",
        "auto_premium_loan_flag": "True",
        "non_forfeiture_status": "RPU",
        "participating_flag": "False",
        "dividend_scale_rate": "",
        "small_face_flag": "True",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "DIRECT",
        "issue_state": "TX",
    },
]


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    """Write MINIMAL_ROWS to a temporary CSV and return its path."""
    df = pd.DataFrame(MINIMAL_ROWS)
    csv_path = tmp_path / "wl_test.csv"
    df.to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Initialise a fresh DuckDB for each test."""
    db_path = tmp_path / "wl_etl_test.duckdb"
    init_database(db_path)
    return db_path


@pytest.fixture
def etl_result(mini_csv: Path, test_db: Path):
    """Run the WL ETL pipeline once and return (ETLResult, db_path)."""
    run_id = str(uuid.uuid4())
    result = run_etl_pipeline(
        product_code="WL",
        source_path=mini_csv,
        mapping_config_path=MAPPING_CONFIG,
        db_path=test_db,
        run_id=run_id,
    )
    return result, test_db


def _query(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Mapping config tests
# ---------------------------------------------------------------------------

class TestWLMappingConfig:
    def test_loads_successfully(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert isinstance(cfg, dict)

    def test_target_table_is_silver_wl(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert cfg["target_table"] == "silver_wl_policies"

    def test_has_wl_specific_fields(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "guaranteed_cash_value" in fields
        assert "premium_paying_period" in fields
        assert "non_forfeiture_status" in fields
        assert "participating_flag" in fields

    def test_ci_rider_fields_present(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "ci_rider_flag" in fields
        assert "ci_rider_sum_assured" in fields

    def test_no_term_specific_fields(self) -> None:
        """WL config must not contain Term-specific fields."""
        cfg = load_mapping_config(MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "level_period_years" not in fields
        assert "plt_premium_year_1" not in fields


# ---------------------------------------------------------------------------
# ETL run-level tests
# ---------------------------------------------------------------------------

class TestWLETLSuccess:
    def test_returns_success(self, etl_result) -> None:
        result, _ = etl_result
        assert result.success is True

    def test_records_ingested(self, etl_result) -> None:
        result, _ = etl_result
        assert result.records_ingested == 3

    def test_records_conformed(self, etl_result) -> None:
        result, _ = etl_result
        assert result.records_conformed == 3

    def test_no_errors(self, etl_result) -> None:
        result, _ = etl_result
        assert result.error_count == 0

    def test_duration_positive(self, etl_result) -> None:
        result, _ = etl_result
        assert result.duration_sec > 0


# ---------------------------------------------------------------------------
# Silver schema and type tests
# ---------------------------------------------------------------------------

class TestWLSilverSchema:
    def test_silver_row_count(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_wl_policies")
        assert rows[0][0] == 3

    def test_policy_id_stored_as_varchar(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT policy_id FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        assert len(row) == 1

    def test_issue_date_parsed_as_date(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT issue_date FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        from datetime import date
        assert row[0][0] == date(2014, 3, 15)

    def test_guaranteed_cash_value_is_double(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT guaranteed_cash_value FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        assert isinstance(rows[0][0], float)
        assert rows[0][0] == pytest.approx(18000.0)

    def test_guaranteed_cash_value_non_negative(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT MIN(guaranteed_cash_value) FROM silver_wl_policies")
        assert rows[0][0] >= 0.0

    def test_participating_flag_is_boolean(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT participating_flag FROM silver_wl_policies ORDER BY policy_id")
        assert rows[0][0] is True   # WL-0000001 is par
        assert rows[1][0] is False  # WL-0000002 is non-par

    def test_non_forfeiture_status_values(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT DISTINCT non_forfeiture_status FROM silver_wl_policies ORDER BY 1")
        statuses = {r[0] for r in rows}
        assert statuses.issubset({"ACTIVE", "RPU", "ETT"})

    def test_rpu_status_preserved(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT non_forfeiture_status FROM silver_wl_policies WHERE policy_id = 'WL-0000003'")
        assert rows[0][0] == "RPU"

    def test_ci_rider_flag_boolean(self, etl_result) -> None:
        _, db = etl_result
        row1 = _query(db, "SELECT ci_rider_flag FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        row2 = _query(db, "SELECT ci_rider_flag FROM silver_wl_policies WHERE policy_id = 'WL-0000002'")
        assert row1[0][0] is True
        assert row2[0][0] is False

    def test_ci_rider_sum_assured_null_when_no_rider(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT ci_rider_sum_assured FROM silver_wl_policies WHERE policy_id = 'WL-0000002'")
        assert row[0][0] is None

    def test_ci_rider_sum_assured_populated_when_rider_exists(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT ci_rider_sum_assured FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        assert row[0][0] == pytest.approx(125000.0)

    def test_small_face_flag_boolean(self, etl_result) -> None:
        _, db = etl_result
        row3 = _query(db, "SELECT small_face_flag FROM silver_wl_policies WHERE policy_id = 'WL-0000003'")
        assert row3[0][0] is True

    def test_termination_date_null_for_inforce(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT termination_date FROM silver_wl_policies WHERE policy_id = 'WL-0000001'")
        assert row[0][0] is None

    def test_surrender_status_code_preserved(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT status_code FROM silver_wl_policies WHERE policy_id = 'WL-0000002'")
        assert row[0][0] == "SURRENDER"

    def test_policy_events_populated(self, etl_result) -> None:
        """ETL must write ISSUE events to silver_policy_events."""
        _, db = etl_result
        rows = _query(
            db,
            "SELECT COUNT(*) FROM silver_policy_events WHERE product_code = 'WL'"
        )
        assert rows[0][0] >= 3  # at least one event per policy
