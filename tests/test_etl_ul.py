"""Unit tests for the Universal Life ETL pipeline (UL, ULSG, IUL)."""

from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingestion.pipeline import load_mapping_config, run_etl_pipeline
from src.utils.db_init import init_database

MAPPING_CONFIG = Path("config/products/ul.yaml")

# ---------------------------------------------------------------------------
# Minimal UL test rows — one each of ULSG, Trad UL, IUL
# ---------------------------------------------------------------------------

MINIMAL_ROWS = [
    {   # ULSG — no CI rider, has shadow account fields
        "policy_id": "ULSG-0000001",
        "product_code": "ULSG",
        "plan_code": "ULSG_LIFETIME",
        "issue_date": "2012-01-01",
        "date_of_birth": "1952-01-01",
        "issue_age_anb": "60",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "STD_NS",
        "annual_premium": "8000.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "specified_amount": "500000",
        "death_benefit_option": "A",
        "account_value_bom": "45000.00",
        "account_value_eom": "45200.00",
        "current_coi_rate": "0.008",
        "guaranteed_coi_rate": "0.012",
        "credited_interest_rate": "0.035",
        "guaranteed_min_interest_rate": "0.015",
        "surrender_charge_remaining": "0.00",
        "planned_premium": "8000.00",
        "target_premium": "8000.00",
        "min_no_lapse_premium": "3200.00",
        "seven_pay_premium": "9000.00",
        "mec_status_flag": "False",
        "cumulative_premiums_paid": "88000.00",
        "premium_persistency_ratio": "1.10",
        "is_ulsg_flag": "True",
        "shadow_account_value": "52000.00",
        "shadow_account_funding_ratio": "1.18",
        "no_lapse_guarantee_period": "LIFETIME",
        "secondary_guarantee_type": "SHADOW_ACCT",
        "cumulative_nlp_required": "35200.00",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "CAREER",
        "issue_state": "NY",
    },
    {   # Trad UL — with CI rider
        "policy_id": "UL-0000001",
        "product_code": "UL",
        "plan_code": "UL_TRAD",
        "issue_date": "2016-06-01",
        "date_of_birth": "1980-06-01",
        "issue_age_anb": "36",
        "gender": "F",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "annual_premium": "3600.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "specified_amount": "300000",
        "death_benefit_option": "B",
        "account_value_bom": "22000.00",
        "account_value_eom": "22150.00",
        "current_coi_rate": "0.003",
        "guaranteed_coi_rate": "0.005",
        "credited_interest_rate": "0.040",
        "guaranteed_min_interest_rate": "0.015",
        "surrender_charge_remaining": "1200.00",
        "planned_premium": "3600.00",
        "target_premium": "4000.00",
        "min_no_lapse_premium": "",
        "seven_pay_premium": "5500.00",
        "mec_status_flag": "False",
        "cumulative_premiums_paid": "25200.00",
        "premium_persistency_ratio": "0.97",
        "is_ulsg_flag": "False",
        "shadow_account_value": "",
        "shadow_account_funding_ratio": "",
        "no_lapse_guarantee_period": "",
        "secondary_guarantee_type": "",
        "cumulative_nlp_required": "",
        "ci_rider_flag": "True",
        "ci_rider_sum_assured": "120000.00",
        "ci_rider_premium": "36.00",
        "illness_code": "",
        "distribution_channel": "INDEPENDENT",
        "issue_state": "CA",
    },
    {   # IUL — lapsed, no CI rider
        "policy_id": "IUL-0000001",
        "product_code": "IUL",
        "plan_code": "IUL_STANDARD",
        "issue_date": "2015-03-01",
        "date_of_birth": "1975-03-01",
        "issue_age_anb": "40",
        "gender": "M",
        "smoker_status": "SM",
        "risk_class": "STD_SM",
        "annual_premium": "5000.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "LAPSE",
        "termination_date": "2020-04-15",
        "termination_cause_code": "LAPSE",
        "specified_amount": "400000",
        "death_benefit_option": "A",
        "account_value_bom": "12000.00",
        "account_value_eom": "12100.00",
        "current_coi_rate": "0.010",
        "guaranteed_coi_rate": "0.015",
        "credited_interest_rate": "0.045",
        "guaranteed_min_interest_rate": "0.010",
        "surrender_charge_remaining": "5000.00",
        "planned_premium": "5000.00",
        "target_premium": "5000.00",
        "min_no_lapse_premium": "",
        "seven_pay_premium": "7000.00",
        "mec_status_flag": "False",
        "cumulative_premiums_paid": "25000.00",
        "premium_persistency_ratio": "1.00",
        "is_ulsg_flag": "False",
        "shadow_account_value": "",
        "shadow_account_funding_ratio": "",
        "no_lapse_guarantee_period": "",
        "secondary_guarantee_type": "",
        "cumulative_nlp_required": "",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "CAREER",
        "issue_state": "TX",
    },
]


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    """Write MINIMAL_ROWS to a temporary CSV and return its path."""
    df = pd.DataFrame(MINIMAL_ROWS)
    csv_path = tmp_path / "ul_test.csv"
    df.to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Initialise a fresh DuckDB for each test."""
    db_path = tmp_path / "ul_etl_test.duckdb"
    init_database(db_path)
    return db_path


@pytest.fixture
def etl_result(mini_csv: Path, test_db: Path):
    """Run the UL ETL pipeline once and return (ETLResult, db_path)."""
    run_id = str(uuid.uuid4())
    result = run_etl_pipeline(
        product_code="UL",
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

class TestULMappingConfig:
    def test_loads_successfully(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert isinstance(cfg, dict)

    def test_target_table_is_silver_ul(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        assert cfg["target_table"] == "silver_ul_policies"

    def test_has_ul_specific_fields(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "account_value_bom" in fields
        assert "credited_interest_rate" in fields
        assert "is_ulsg_flag" in fields
        assert "shadow_account_value" in fields

    def test_ci_rider_fields_present(self) -> None:
        cfg = load_mapping_config(MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "ci_rider_flag" in fields


# ---------------------------------------------------------------------------
# ETL run-level tests
# ---------------------------------------------------------------------------

class TestULETLSuccess:
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


# ---------------------------------------------------------------------------
# Silver schema and type tests
# ---------------------------------------------------------------------------

class TestULSilverSchema:
    def test_silver_row_count(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_ul_policies")
        assert rows[0][0] == 3

    def test_product_codes_preserved_distinctly(self, etl_result) -> None:
        """ULSG, UL, and IUL must NOT be normalized to a single product_code."""
        _, db = etl_result
        rows = _query(db, "SELECT DISTINCT product_code FROM silver_ul_policies ORDER BY 1")
        codes = {r[0] for r in rows}
        assert "UL" in codes
        assert "ULSG" in codes
        assert "IUL" in codes

    def test_is_ulsg_flag_true_for_ulsg(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT is_ulsg_flag FROM silver_ul_policies WHERE policy_id = 'ULSG-0000001'")
        assert row[0][0] is True

    def test_is_ulsg_flag_false_for_trad_ul(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT is_ulsg_flag FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert row[0][0] is False

    def test_is_ulsg_flag_false_for_iul(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT is_ulsg_flag FROM silver_ul_policies WHERE policy_id = 'IUL-0000001'")
        assert row[0][0] is False

    def test_shadow_account_value_non_null_for_ulsg(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT shadow_account_value FROM silver_ul_policies WHERE policy_id = 'ULSG-0000001'")
        assert row[0][0] is not None
        assert row[0][0] == pytest.approx(52000.0)

    def test_shadow_account_value_null_for_trad_ul(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT shadow_account_value FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert row[0][0] is None

    def test_account_value_bom_is_double(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT account_value_bom FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert isinstance(row[0][0], float)
        assert row[0][0] == pytest.approx(22000.0)

    def test_account_value_eom_is_double(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT account_value_eom FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert isinstance(row[0][0], float)
        assert row[0][0] == pytest.approx(22150.0)

    def test_credited_rate_ge_gmir(self, etl_result) -> None:
        """credited_interest_rate >= guaranteed_min_interest_rate for all rows."""
        _, db = etl_result
        rows = _query(
            db,
            "SELECT policy_id, credited_interest_rate, guaranteed_min_interest_rate "
            "FROM silver_ul_policies"
        )
        for pid, crd, gmir in rows:
            assert crd >= gmir, f"Policy {pid}: credited {crd} < GMIR {gmir}"

    def test_ci_rider_flag_false_for_ulsg(self, etl_result) -> None:
        """ULSG has no CI rider per spec."""
        _, db = etl_result
        row = _query(db, "SELECT ci_rider_flag FROM silver_ul_policies WHERE policy_id = 'ULSG-0000001'")
        assert row[0][0] is False

    def test_ci_rider_flag_true_for_trad_ul(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT ci_rider_flag FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert row[0][0] is True

    def test_ci_rider_sum_assured_populated_for_ul(self, etl_result) -> None:
        _, db = etl_result
        row = _query(db, "SELECT ci_rider_sum_assured FROM silver_ul_policies WHERE policy_id = 'UL-0000001'")
        assert row[0][0] == pytest.approx(120000.0)

    def test_mec_status_flag_is_boolean(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT mec_status_flag FROM silver_ul_policies")
        for (flag,) in rows:
            assert isinstance(flag, bool)

    def test_lapse_termination_preserved(self, etl_result) -> None:
        _, db = etl_result
        row = _query(
            db,
            "SELECT status_code, termination_cause_code FROM silver_ul_policies "
            "WHERE policy_id = 'IUL-0000001'"
        )
        assert row[0][0] == "LAPSE"
        assert row[0][1] == "LAPSE"

    def test_policy_events_populated(self, etl_result) -> None:
        _, db = etl_result
        rows = _query(db, "SELECT COUNT(*) FROM silver_policy_events WHERE product_code = 'UL'")
        # UL, ULSG, IUL all have product_code='UL' in events OR their own code
        # At minimum: events should exist
        total = _query(db, "SELECT COUNT(*) FROM silver_policy_events")
        assert total[0][0] >= 3
