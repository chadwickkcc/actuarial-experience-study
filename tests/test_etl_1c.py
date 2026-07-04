"""Unit tests for the Phase 1C ETL pipelines — VUL and Deferred Annuity."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingestion.pipeline import load_mapping_config, run_etl_pipeline
from src.utils.db_init import init_database

VUL_MAPPING_CONFIG = Path("config/products/vul.yaml")
DA_MAPPING_CONFIG  = Path("config/products/annuity.yaml")


# ===========================================================================
# VUL ETL tests
# ===========================================================================

VUL_ROWS = [
    {   # Withdrawal-inactive, with CI rider, moderate equity
        "policy_id": "VUL-0000001",
        "product_code": "VUL",
        "plan_code": "VUL_STANDARD",
        "issue_date": "2015-04-01",
        "date_of_birth": "1975-04-01",
        "issue_age_anb": "40",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "annual_premium": "5000.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "specified_amount": "500000",
        "death_benefit_option": "B",
        "account_value_bom": "85000.00",
        "account_value_eom": "86200.00",
        "current_coi_rate": "0.005",
        "guaranteed_coi_rate": "0.008",
        "surrender_charge_remaining": "0.00",
        "planned_premium": "5000.00",
        "mec_status_flag": "False",
        "separate_account_total_value": "78000.00",
        "fixed_account_value": "8000.00",
        "sub_account_allocations": json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.50, "fund_value": 39000.0},
            {"fund_id": "BALANCED",     "alloc_pct": 0.30, "fund_value": 23400.0},
            {"fund_id": "BOND_INTMED",  "alloc_pct": 0.20, "fund_value": 15600.0},
        ]),
        "equity_allocation_pct": "0.50",
        "fund_value_to_spec_amount_ratio": "0.156",
        "ma_charge_annual_rate": "0.014",
        "withdrawal_active_flag": "False",
        "withdrawal_rate_pct": "0.00",
        "withdrawal_regime": "NONE",
        "ci_rider_flag": "True",
        "ci_rider_sum_assured": "150000.00",
        "ci_rider_premium": "52.50",
        "illness_code": "",
        "distribution_channel": "INDEPENDENT",
        "issue_state": "CA",
    },
    {   # Withdrawal-active, no CI rider, high equity
        "policy_id": "VUL-0000002",
        "product_code": "VUL",
        "plan_code": "VUL_STANDARD",
        "issue_date": "2010-07-01",
        "date_of_birth": "1960-07-01",
        "issue_age_anb": "50",
        "gender": "F",
        "smoker_status": "NS",
        "risk_class": "STD_NS",
        "annual_premium": "8000.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "specified_amount": "300000",
        "death_benefit_option": "A",
        "account_value_bom": "142000.00",
        "account_value_eom": "143500.00",
        "current_coi_rate": "0.009",
        "guaranteed_coi_rate": "0.014",
        "surrender_charge_remaining": "0.00",
        "planned_premium": "8000.00",
        "mec_status_flag": "False",
        "separate_account_total_value": "130000.00",
        "fixed_account_value": "13000.00",
        "sub_account_allocations": json.dumps([
            {"fund_id": "EQ_LARGE_CAP", "alloc_pct": 0.80, "fund_value": 104000.0},
            {"fund_id": "EQ_INTL",      "alloc_pct": 0.20, "fund_value": 26000.0},
        ]),
        "equity_allocation_pct": "0.80",
        "fund_value_to_spec_amount_ratio": "0.433",
        "ma_charge_annual_rate": "0.014",
        "withdrawal_active_flag": "True",
        "withdrawal_rate_pct": "0.05",
        "withdrawal_regime": "MAX",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "CAREER",
        "issue_state": "TX",
    },
    {   # Lapsed, no CI rider
        "policy_id": "VUL-0000003",
        "product_code": "VUL",
        "plan_code": "VUL_STANDARD",
        "issue_date": "2016-01-01",
        "date_of_birth": "1985-01-01",
        "issue_age_anb": "31",
        "gender": "M",
        "smoker_status": "NS",
        "risk_class": "PREF_NS",
        "annual_premium": "3600.00",
        "premium_mode": "ANNUAL",
        "reinsurance_flag": "False",
        "status_code": "LAPSE",
        "termination_date": "2020-01-01",
        "termination_cause_code": "LAPSE",
        "specified_amount": "200000",
        "death_benefit_option": "A",
        "account_value_bom": "8000.00",
        "account_value_eom": "7900.00",
        "current_coi_rate": "0.002",
        "guaranteed_coi_rate": "0.004",
        "surrender_charge_remaining": "3000.00",
        "planned_premium": "3600.00",
        "mec_status_flag": "False",
        "separate_account_total_value": "7500.00",
        "fixed_account_value": "400.00",
        "sub_account_allocations": json.dumps([
            {"fund_id": "BALANCED", "alloc_pct": 1.0, "fund_value": 7500.0},
        ]),
        "equity_allocation_pct": "0.40",
        "fund_value_to_spec_amount_ratio": "0.038",
        "ma_charge_annual_rate": "0.014",
        "withdrawal_active_flag": "False",
        "withdrawal_rate_pct": "0.00",
        "withdrawal_regime": "NONE",
        "ci_rider_flag": "False",
        "ci_rider_sum_assured": "",
        "ci_rider_premium": "",
        "illness_code": "",
        "distribution_channel": "BANK",
        "issue_state": "FL",
    },
]


@pytest.fixture
def vul_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame(VUL_ROWS)
    p = tmp_path / "vul_test.csv"
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def vul_db(tmp_path: Path) -> Path:
    db = tmp_path / "vul_etl.duckdb"
    init_database(db)
    return db


@pytest.fixture
def vul_etl(vul_csv: Path, vul_db: Path):
    result = run_etl_pipeline(
        product_code="VUL",
        source_path=vul_csv,
        mapping_config_path=VUL_MAPPING_CONFIG,
        db_path=vul_db,
        run_id=str(uuid.uuid4()),
    )
    return result, vul_db


def _q(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class TestVULMappingConfig:
    def test_loads_successfully(self) -> None:
        cfg = load_mapping_config(VUL_MAPPING_CONFIG)
        assert isinstance(cfg, dict)

    def test_target_table_is_silver_vul(self) -> None:
        cfg = load_mapping_config(VUL_MAPPING_CONFIG)
        assert cfg["target_table"] == "silver_vul_policies"

    def test_has_vul_specific_fields(self) -> None:
        cfg = load_mapping_config(VUL_MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "withdrawal_active_flag" in fields
        assert "separate_account_total_value" in fields
        assert "sub_account_allocations" in fields
        assert "fund_value_to_spec_amount_ratio" in fields


class TestVULETLSuccess:
    def test_returns_success(self, vul_etl) -> None:
        result, _ = vul_etl
        assert result.success is True

    def test_records_ingested(self, vul_etl) -> None:
        result, _ = vul_etl
        assert result.records_ingested == 3

    def test_records_conformed(self, vul_etl) -> None:
        result, _ = vul_etl
        assert result.records_conformed == 3


class TestVULSilverSchema:
    def test_row_count(self, vul_etl) -> None:
        _, db = vul_etl
        rows = _q(db, "SELECT COUNT(*) FROM silver_vul_policies")
        assert rows[0][0] == 3

    def test_withdrawal_active_flag_boolean_false(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT withdrawal_active_flag FROM silver_vul_policies WHERE policy_id = 'VUL-0000001'")
        assert row[0][0] is False

    def test_withdrawal_active_flag_boolean_true(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT withdrawal_active_flag FROM silver_vul_policies WHERE policy_id = 'VUL-0000002'")
        assert row[0][0] is True

    def test_sub_account_allocations_stored_as_varchar(self, vul_etl) -> None:
        """sub_account_allocations must be stored as a JSON string, not parsed."""
        _, db = vul_etl
        row = _q(db, "SELECT sub_account_allocations FROM silver_vul_policies WHERE policy_id = 'VUL-0000001'")
        alloc = row[0][0]
        assert alloc is not None
        # Must be parseable as JSON
        parsed = json.loads(alloc)
        assert isinstance(parsed, list)
        assert len(parsed) == 3

    def test_equity_allocation_pct_in_bounds(self, vul_etl) -> None:
        _, db = vul_etl
        rows = _q(db, "SELECT equity_allocation_pct FROM silver_vul_policies")
        for (pct,) in rows:
            assert 0.0 <= pct <= 1.0, f"equity_allocation_pct {pct} out of [0,1]"

    def test_fund_value_to_spec_amount_ratio_present(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT fund_value_to_spec_amount_ratio FROM silver_vul_policies WHERE policy_id = 'VUL-0000001'")
        assert row[0][0] is not None
        assert isinstance(row[0][0], float)

    def test_separate_account_total_value_non_negative(self, vul_etl) -> None:
        _, db = vul_etl
        rows = _q(db, "SELECT MIN(separate_account_total_value) FROM silver_vul_policies")
        assert rows[0][0] >= 0.0

    def test_ci_rider_flag_boolean(self, vul_etl) -> None:
        _, db = vul_etl
        row1 = _q(db, "SELECT ci_rider_flag FROM silver_vul_policies WHERE policy_id = 'VUL-0000001'")
        row2 = _q(db, "SELECT ci_rider_flag FROM silver_vul_policies WHERE policy_id = 'VUL-0000002'")
        assert row1[0][0] is True
        assert row2[0][0] is False

    def test_ci_rider_sum_assured_populated_when_rider(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT ci_rider_sum_assured FROM silver_vul_policies WHERE policy_id = 'VUL-0000001'")
        assert row[0][0] == pytest.approx(150000.0)

    def test_ci_rider_sum_assured_null_without_rider(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT ci_rider_sum_assured FROM silver_vul_policies WHERE policy_id = 'VUL-0000002'")
        assert row[0][0] is None

    def test_withdrawal_regime_preserved(self, vul_etl) -> None:
        _, db = vul_etl
        row = _q(db, "SELECT withdrawal_regime FROM silver_vul_policies WHERE policy_id = 'VUL-0000002'")
        assert row[0][0] == "MAX"


# ===========================================================================
# DA ETL tests
# ===========================================================================

DA_ROWS = [
    {   # DA_FIXED, not expired, no GLWB
        "contract_id": "DAF-0000001",
        "product_code": "DA_FIXED",
        "product_type": "DA_FIXED",
        "premium_type": "SINGLE",
        "issue_date": "2016-01-01",
        "date_of_birth": "1961-01-01",
        "issue_age_anb": "55",
        "gender": "M",
        "market_type": "NQ",
        "account_value": "85000.00",
        "benefit_base": "",
        "surrender_charge_schedule": json.dumps([
            {"year": 1, "rate": 0.08}, {"year": 2, "rate": 0.07},
            {"year": 3, "rate": 0.06}, {"year": 4, "rate": 0.05},
            {"year": 5, "rate": 0.04}, {"year": 6, "rate": 0.03},
            {"year": 7, "rate": 0.02},
        ]),
        "surrender_charge_remaining": "3400.00",
        "surrender_charge_year": "5",
        "free_withdrawal_allowance_pct": "0.10",
        "guaranteed_min_interest_rate": "0.030",
        "credited_rate_current": "0.035",
        "market_value_adjustment_flag": "False",
        "glwb_elected_flag": "False",
        "gmdb_type": "",
        "glwb_withdrawal_rate_pct": "",
        "glwb_utilization_status": "WAITING",
        "rider_fee_annual_rate": "0.00",
        "moneyness_ratio": "",
        "is_surrender_charge_expired_flag": "False",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "distribution_channel": "BANK",
        "issue_state": "FL",
    },
    {   # DA_VA, GLWB-elected (moneyness > 1), SC expired
        "contract_id": "DAV-0000001",
        "product_code": "DA_VA",
        "product_type": "DA_VA",
        "premium_type": "FLEXIBLE",
        "issue_date": "2010-06-01",
        "date_of_birth": "1958-06-01",
        "issue_age_anb": "52",
        "gender": "F",
        "market_type": "TRAD_IRA",
        "account_value": "145000.00",
        "benefit_base": "165000.00",
        "surrender_charge_schedule": json.dumps([
            {"year": i, "rate": max(0, 0.09 - 0.01*i)} for i in range(1, 8)
        ]),
        "surrender_charge_remaining": "0.00",
        "surrender_charge_year": "7",
        "free_withdrawal_allowance_pct": "0.10",
        "guaranteed_min_interest_rate": "0.010",
        "credited_rate_current": "0.040",
        "market_value_adjustment_flag": "False",
        "glwb_elected_flag": "True",
        "gmdb_type": "RATCHET",
        "glwb_withdrawal_rate_pct": "0.05",
        "glwb_utilization_status": "ACTIVE",
        "rider_fee_annual_rate": "0.010",
        "moneyness_ratio": "1.138",
        "is_surrender_charge_expired_flag": "True",
        "status_code": "IF",
        "termination_date": "",
        "termination_cause_code": "",
        "distribution_channel": "IBD",
        "issue_state": "NY",
    },
    {   # DA_FIXED, surrendered
        "contract_id": "DAF-0000002",
        "product_code": "DA_FIXED",
        "product_type": "DA_FIXED",
        "premium_type": "SINGLE",
        "issue_date": "2014-01-01",
        "date_of_birth": "1954-01-01",
        "issue_age_anb": "60",
        "gender": "M",
        "market_type": "QUAL",
        "account_value": "52000.00",
        "benefit_base": "",
        "surrender_charge_schedule": json.dumps([
            {"year": i, "rate": max(0, 0.07 - 0.01*i)} for i in range(1, 8)
        ]),
        "surrender_charge_remaining": "0.00",
        "surrender_charge_year": "7",
        "free_withdrawal_allowance_pct": "0.10",
        "guaranteed_min_interest_rate": "0.030",
        "credited_rate_current": "0.042",
        "market_value_adjustment_flag": "False",
        "glwb_elected_flag": "False",
        "gmdb_type": "",
        "glwb_withdrawal_rate_pct": "",
        "glwb_utilization_status": "WAITING",
        "rider_fee_annual_rate": "0.00",
        "moneyness_ratio": "",
        "is_surrender_charge_expired_flag": "True",
        "status_code": "SURRENDER",
        "termination_date": "2021-06-15",
        "termination_cause_code": "FULL_SURRENDER",
        "distribution_channel": "RIA",
        "issue_state": "TX",
    },
]


@pytest.fixture
def da_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame(DA_ROWS)
    p = tmp_path / "da_test.csv"
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def da_db(tmp_path: Path) -> Path:
    db = tmp_path / "da_etl.duckdb"
    init_database(db)
    return db


@pytest.fixture
def da_etl(da_csv: Path, da_db: Path):
    result = run_etl_pipeline(
        product_code="DA",
        source_path=da_csv,
        mapping_config_path=DA_MAPPING_CONFIG,
        db_path=da_db,
        run_id=str(uuid.uuid4()),
    )
    return result, da_db


class TestDAMappingConfig:
    def test_loads_successfully(self) -> None:
        cfg = load_mapping_config(DA_MAPPING_CONFIG)
        assert isinstance(cfg, dict)

    def test_target_table_is_silver_annuity(self) -> None:
        cfg = load_mapping_config(DA_MAPPING_CONFIG)
        assert cfg["target_table"] == "silver_annuity_contracts"

    def test_primary_key_is_contract_id(self) -> None:
        cfg = load_mapping_config(DA_MAPPING_CONFIG)
        assert cfg.get("primary_key") == "contract_id"

    def test_has_da_specific_fields(self) -> None:
        cfg = load_mapping_config(DA_MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "contract_id" in fields
        assert "glwb_elected_flag" in fields
        assert "is_surrender_charge_expired_flag" in fields
        assert "moneyness_ratio" in fields

    def test_no_ci_rider_fields(self) -> None:
        """DA does not carry CI riders — these fields must not appear in the DA config."""
        cfg = load_mapping_config(DA_MAPPING_CONFIG)
        fields = {m["source_field"] for m in cfg["field_mappings"]}
        assert "ci_rider_flag" not in fields
        assert "ci_rider_sum_assured" not in fields
        assert "ci_rider_premium" not in fields


class TestDAETLSuccess:
    def test_returns_success(self, da_etl) -> None:
        result, _ = da_etl
        assert result.success is True

    def test_records_ingested(self, da_etl) -> None:
        result, _ = da_etl
        assert result.records_ingested == 3

    def test_records_conformed(self, da_etl) -> None:
        result, _ = da_etl
        assert result.records_conformed == 3


class TestDASilverSchema:
    def test_row_count(self, da_etl) -> None:
        _, db = da_etl
        rows = _q(db, "SELECT COUNT(*) FROM silver_annuity_contracts")
        assert rows[0][0] == 3

    def test_contract_id_is_primary_key(self, da_etl) -> None:
        _, db = da_etl
        row = _q(db, "SELECT contract_id FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000001'")
        assert len(row) == 1

    def test_daf_prefix_preserved(self, da_etl) -> None:
        _, db = da_etl
        rows = _q(db, "SELECT contract_id FROM silver_annuity_contracts WHERE contract_id LIKE 'DAF-%'")
        assert len(rows) == 2

    def test_dav_prefix_preserved(self, da_etl) -> None:
        _, db = da_etl
        rows = _q(db, "SELECT contract_id FROM silver_annuity_contracts WHERE contract_id LIKE 'DAV-%'")
        assert len(rows) == 1

    def test_no_ci_rider_columns_in_silver(self, da_etl) -> None:
        """silver_annuity_contracts must not have ci_rider columns."""
        _, db = da_etl
        con = duckdb.connect(str(db), read_only=True)
        try:
            cols = [row[0] for row in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'silver_annuity_contracts'"
            ).fetchall()]
        finally:
            con.close()
        assert "ci_rider_flag" not in cols
        assert "ci_rider_sum_assured" not in cols

    def test_glwb_elected_flag_boolean(self, da_etl) -> None:
        _, db = da_etl
        row1 = _q(db, "SELECT glwb_elected_flag FROM silver_annuity_contracts WHERE contract_id = 'DAV-0000001'")
        row2 = _q(db, "SELECT glwb_elected_flag FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000001'")
        assert row1[0][0] is True
        assert row2[0][0] is False

    def test_surrender_charge_schedule_stored_as_varchar_json(self, da_etl) -> None:
        _, db = da_etl
        row = _q(db, "SELECT surrender_charge_schedule FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000001'")
        sched = row[0][0]
        assert sched is not None
        parsed = json.loads(sched)
        assert isinstance(parsed, list)
        assert len(parsed) == 7

    def test_moneyness_ratio_non_null_for_glb_contract(self, da_etl) -> None:
        _, db = da_etl
        row = _q(db, "SELECT moneyness_ratio FROM silver_annuity_contracts WHERE contract_id = 'DAV-0000001'")
        assert row[0][0] is not None
        assert row[0][0] == pytest.approx(1.138, rel=1e-3)

    def test_moneyness_ratio_null_for_non_glb(self, da_etl) -> None:
        _, db = da_etl
        row = _q(db, "SELECT moneyness_ratio FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000001'")
        assert row[0][0] is None

    def test_is_sc_expired_flag_boolean(self, da_etl) -> None:
        _, db = da_etl
        row1 = _q(db, "SELECT is_surrender_charge_expired_flag FROM silver_annuity_contracts WHERE contract_id = 'DAV-0000001'")
        row2 = _q(db, "SELECT is_surrender_charge_expired_flag FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000001'")
        assert row1[0][0] is True
        assert row2[0][0] is False

    def test_account_value_non_negative(self, da_etl) -> None:
        _, db = da_etl
        rows = _q(db, "SELECT MIN(account_value) FROM silver_annuity_contracts")
        assert rows[0][0] >= 0.0

    def test_surrender_status_preserved(self, da_etl) -> None:
        _, db = da_etl
        row = _q(db, "SELECT status_code, termination_cause_code FROM silver_annuity_contracts WHERE contract_id = 'DAF-0000002'")
        assert row[0][0] == "SURRENDER"
        assert row[0][1] == "FULL_SURRENDER"
