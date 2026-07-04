"""Database initialisation — creates all DuckDB tables in dependency order.

Run directly:  python -m src.utils.db_init
Idempotent:   safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
"""

import duckdb
from pathlib import Path


# Canonical location of the single DuckDB file (Tech Spec §A). Exported so other
# modules can reference the default without re-stating the path literal — the
# AI layer in particular must not carry a bare ``data/...`` write-path literal
# (FR-3A-09 write-contract guard scans for those), so it imports this instead.
DEFAULT_DB_PATH = "data/experience_study.duckdb"


# ---------------------------------------------------------------------------
# DDL statements — ordered by dependency
# ---------------------------------------------------------------------------

_BRONZE_DDL = [
    # --------------------------------------------------------
    # BRONZE: TERM LIFE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bronze_term_policies (
        raw_policy_id               VARCHAR,
        raw_product_code            VARCHAR,
        raw_plan_code               VARCHAR,
        raw_issue_date              VARCHAR,
        raw_date_of_birth           VARCHAR,
        raw_gender                  VARCHAR,
        raw_smoker_status           VARCHAR,
        raw_risk_class              VARCHAR,
        raw_face_amount             VARCHAR,
        raw_premium_mode            VARCHAR,
        raw_annual_premium          VARCHAR,
        raw_status_code             VARCHAR,
        raw_termination_date        VARCHAR,
        raw_termination_cause_code  VARCHAR,
        raw_level_period_years      VARCHAR,
        raw_plt_premium_year_1      VARCHAR,
        raw_plt_structure_code      VARCHAR,
        raw_premium_jump_ratio      VARCHAR,
        raw_distribution_channel    VARCHAR,
        raw_issue_state             VARCHAR,
        raw_conversion_flag         VARCHAR,
        raw_ci_rider_flag           VARCHAR,
        raw_ci_rider_sum_assured    VARCHAR,
        raw_ci_rider_premium        VARCHAR,
        raw_illness_code            VARCHAR,
        raw_reinsurance_flag        VARCHAR,
        _load_ts                    TIMESTAMP NOT NULL,
        _source_file                VARCHAR NOT NULL,
        _product_code               VARCHAR(20) NOT NULL,
        _row_hash                   VARCHAR(64) NOT NULL,
        _bronze_id                  VARCHAR(36) PRIMARY KEY
    )
    """,

    # --------------------------------------------------------
    # BRONZE: WHOLE LIFE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bronze_wl_policies (
        raw_policy_id               VARCHAR,
        raw_product_code            VARCHAR,
        raw_plan_code               VARCHAR,
        raw_issue_date              VARCHAR,
        raw_date_of_birth           VARCHAR,
        raw_gender                  VARCHAR,
        raw_smoker_status           VARCHAR,
        raw_risk_class              VARCHAR,
        raw_face_amount             VARCHAR,
        raw_premium_mode            VARCHAR,
        raw_annual_premium          VARCHAR,
        raw_status_code             VARCHAR,
        raw_termination_date        VARCHAR,
        raw_termination_cause_code  VARCHAR,
        raw_premium_paying_period   VARCHAR,
        raw_guaranteed_cash_value   VARCHAR,
        raw_dividend_option_code    VARCHAR,
        raw_dividend_on_deposit_bal VARCHAR,
        raw_paid_up_additions_face  VARCHAR,
        raw_policy_loan_balance     VARCHAR,
        raw_auto_premium_loan_flag  VARCHAR,
        raw_non_forfeiture_status   VARCHAR,
        raw_participating_flag      VARCHAR,
        raw_dividend_scale_rate     VARCHAR,
        raw_small_face_flag         VARCHAR,
        raw_ci_rider_flag           VARCHAR,
        raw_ci_rider_sum_assured    VARCHAR,
        raw_ci_rider_premium        VARCHAR,
        raw_reinsurance_flag        VARCHAR,
        raw_distribution_channel    VARCHAR,
        raw_issue_state             VARCHAR,
        _load_ts                    TIMESTAMP NOT NULL,
        _source_file                VARCHAR NOT NULL,
        _product_code               VARCHAR(20) NOT NULL,
        _row_hash                   VARCHAR(64) NOT NULL,
        _bronze_id                  VARCHAR(36) PRIMARY KEY
    )
    """,

    # --------------------------------------------------------
    # BRONZE: UNIVERSAL LIFE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bronze_ul_policies (
        raw_policy_id                       VARCHAR,
        raw_product_code                    VARCHAR,
        raw_plan_code                       VARCHAR,
        raw_issue_date                      VARCHAR,
        raw_date_of_birth                   VARCHAR,
        raw_gender                          VARCHAR,
        raw_smoker_status                   VARCHAR,
        raw_risk_class                      VARCHAR,
        raw_specified_amount                VARCHAR,
        raw_death_benefit_option            VARCHAR,
        raw_account_value_bom               VARCHAR,
        raw_account_value_eom               VARCHAR,
        raw_current_coi_rate                VARCHAR,
        raw_guaranteed_coi_rate             VARCHAR,
        raw_credited_interest_rate          VARCHAR,
        raw_guaranteed_min_interest_rate    VARCHAR,
        raw_surrender_charge_remaining      VARCHAR,
        raw_planned_premium                 VARCHAR,
        raw_target_premium                  VARCHAR,
        raw_min_no_lapse_premium            VARCHAR,
        raw_seven_pay_premium               VARCHAR,
        raw_mec_status_flag                 VARCHAR,
        raw_is_ulsg_flag                    VARCHAR,
        raw_shadow_account_value            VARCHAR,
        raw_shadow_account_funding_ratio    VARCHAR,
        raw_no_lapse_guarantee_period       VARCHAR,
        raw_secondary_guarantee_type        VARCHAR,
        raw_cumulative_premiums_paid        VARCHAR,
        raw_cumulative_nlp_required         VARCHAR,
        raw_premium_persistency_ratio       VARCHAR,
        raw_annual_premium                  VARCHAR,
        raw_status_code                     VARCHAR,
        raw_termination_date                VARCHAR,
        raw_termination_cause_code          VARCHAR,
        raw_ci_rider_flag                   VARCHAR,
        raw_ci_rider_sum_assured            VARCHAR,
        raw_ci_rider_premium                VARCHAR,
        raw_reinsurance_flag                VARCHAR,
        raw_distribution_channel            VARCHAR,
        raw_issue_state                     VARCHAR,
        _load_ts                            TIMESTAMP NOT NULL,
        _source_file                        VARCHAR NOT NULL,
        _product_code                       VARCHAR(20) NOT NULL,
        _row_hash                           VARCHAR(64) NOT NULL,
        _bronze_id                          VARCHAR(36) PRIMARY KEY
    )
    """,

    # --------------------------------------------------------
    # BRONZE: VARIABLE UNIVERSAL LIFE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bronze_vul_policies (
        raw_policy_id                       VARCHAR,
        raw_product_code                    VARCHAR,
        raw_plan_code                       VARCHAR,
        raw_issue_date                      VARCHAR,
        raw_date_of_birth                   VARCHAR,
        raw_gender                          VARCHAR,
        raw_smoker_status                   VARCHAR,
        raw_risk_class                      VARCHAR,
        raw_specified_amount                VARCHAR,
        raw_death_benefit_option            VARCHAR,
        raw_separate_account_total_value    VARCHAR,
        raw_fixed_account_value             VARCHAR,
        raw_sub_account_allocations         VARCHAR,
        raw_equity_allocation_pct           VARCHAR,
        raw_fund_value_to_spec_amount_ratio VARCHAR,
        raw_ma_charge_annual_rate           VARCHAR,
        raw_withdrawal_active_flag          VARCHAR,
        raw_withdrawal_rate_pct             VARCHAR,
        raw_withdrawal_regime               VARCHAR,
        raw_account_value_bom               VARCHAR,
        raw_account_value_eom               VARCHAR,
        raw_current_coi_rate                VARCHAR,
        raw_guaranteed_coi_rate             VARCHAR,
        raw_surrender_charge_remaining      VARCHAR,
        raw_planned_premium                 VARCHAR,
        raw_annual_premium                  VARCHAR,
        raw_mec_status_flag                 VARCHAR,
        raw_status_code                     VARCHAR,
        raw_termination_date                VARCHAR,
        raw_termination_cause_code          VARCHAR,
        raw_ci_rider_flag                   VARCHAR,
        raw_ci_rider_sum_assured            VARCHAR,
        raw_ci_rider_premium                VARCHAR,
        raw_reinsurance_flag                VARCHAR,
        raw_distribution_channel            VARCHAR,
        raw_issue_state                     VARCHAR,
        _load_ts                            TIMESTAMP NOT NULL,
        _source_file                        VARCHAR NOT NULL,
        _product_code                       VARCHAR(20) NOT NULL,
        _row_hash                           VARCHAR(64) NOT NULL,
        _bronze_id                          VARCHAR(36) PRIMARY KEY
    )
    """,

    # --------------------------------------------------------
    # BRONZE: DEFERRED ANNUITIES
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bronze_annuity_contracts (
        raw_contract_id                 VARCHAR,
        raw_product_code                VARCHAR,
        raw_product_type                VARCHAR,
        raw_premium_type                VARCHAR,
        raw_issue_date                  VARCHAR,
        raw_date_of_birth               VARCHAR,
        raw_gender                      VARCHAR,
        raw_market_type                 VARCHAR,
        raw_account_value               VARCHAR,
        raw_benefit_base                VARCHAR,
        raw_surrender_charge_schedule   VARCHAR,
        raw_surrender_charge_remaining  VARCHAR,
        raw_surrender_charge_year       VARCHAR,
        raw_free_withdrawal_pct         VARCHAR,
        raw_gmir                        VARCHAR,
        raw_credited_rate_current       VARCHAR,
        raw_mva_flag                    VARCHAR,
        raw_glwb_elected_flag           VARCHAR,
        raw_gmdb_type                   VARCHAR,
        raw_glwb_withdrawal_rate_pct    VARCHAR,
        raw_glwb_utilization_status     VARCHAR,
        raw_rider_fee_annual_rate       VARCHAR,
        raw_moneyness_ratio             VARCHAR,
        raw_sc_expired_flag             VARCHAR,
        raw_status_code                 VARCHAR,
        raw_termination_date            VARCHAR,
        raw_termination_cause_code      VARCHAR,
        raw_distribution_channel        VARCHAR,
        raw_issue_state                 VARCHAR,
        _load_ts                        TIMESTAMP NOT NULL,
        _source_file                    VARCHAR NOT NULL,
        _product_code                   VARCHAR(20) NOT NULL,
        _row_hash                       VARCHAR(64) NOT NULL,
        _bronze_id                      VARCHAR(36) PRIMARY KEY
    )
    """,
]

_SILVER_DDL = [
    # --------------------------------------------------------
    # SILVER: TERM LIFE POLICIES
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_term_policies (
        policy_id               VARCHAR(50) NOT NULL,
        product_code            VARCHAR(20) NOT NULL,
        plan_code               VARCHAR(20) NOT NULL,
        issue_date              DATE NOT NULL,
        date_of_birth           DATE NOT NULL,
        issue_age_anb           INTEGER NOT NULL,
        gender                  VARCHAR(1) NOT NULL,
        smoker_status           VARCHAR(2) NOT NULL,
        risk_class              VARCHAR(20) NOT NULL,
        face_amount             DOUBLE NOT NULL,
        premium_mode            VARCHAR(10) NOT NULL,
        annual_premium          DOUBLE NOT NULL,
        reinsurance_flag        BOOLEAN NOT NULL DEFAULT FALSE,
        status_code             VARCHAR(10) NOT NULL,
        termination_date        DATE,
        termination_cause_code  VARCHAR(30),
        level_period_years      INTEGER NOT NULL,
        plt_premium_year_1      DOUBLE,
        plt_structure_code      VARCHAR(20),
        premium_jump_ratio      DOUBLE,
        conversion_flag         BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_flag           BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_sum_assured    DOUBLE,
        ci_rider_premium        DOUBLE,
        distribution_channel    VARCHAR(30),
        issue_state             VARCHAR(5),
        _load_ts                TIMESTAMP NOT NULL,
        _source_bronze_id       VARCHAR(36) NOT NULL,
        _etl_run_id             VARCHAR(36) NOT NULL,
        PRIMARY KEY (policy_id, _etl_run_id)
    )
    """,

    # --------------------------------------------------------
    # SILVER: WHOLE LIFE POLICIES
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_wl_policies (
        policy_id               VARCHAR(50) NOT NULL,
        product_code            VARCHAR(20) NOT NULL,
        plan_code               VARCHAR(20) NOT NULL,
        issue_date              DATE NOT NULL,
        date_of_birth           DATE NOT NULL,
        issue_age_anb           INTEGER NOT NULL,
        gender                  VARCHAR(1) NOT NULL,
        smoker_status           VARCHAR(2) NOT NULL,
        risk_class              VARCHAR(20) NOT NULL,
        face_amount             DOUBLE NOT NULL,
        premium_mode            VARCHAR(10) NOT NULL,
        annual_premium          DOUBLE NOT NULL,
        reinsurance_flag        BOOLEAN NOT NULL DEFAULT FALSE,
        status_code             VARCHAR(10) NOT NULL,
        termination_date        DATE,
        termination_cause_code  VARCHAR(30),
        premium_paying_period   VARCHAR(20) NOT NULL,
        guaranteed_cash_value   DOUBLE NOT NULL DEFAULT 0,
        dividend_option_code    VARCHAR(10),
        dividend_on_deposit_bal DOUBLE NOT NULL DEFAULT 0,
        paid_up_additions_face  DOUBLE NOT NULL DEFAULT 0,
        policy_loan_balance     DOUBLE NOT NULL DEFAULT 0,
        auto_premium_loan_flag  BOOLEAN NOT NULL DEFAULT FALSE,
        non_forfeiture_status   VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
        participating_flag      BOOLEAN NOT NULL DEFAULT FALSE,
        dividend_scale_rate     DOUBLE,
        small_face_flag         BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_flag           BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_sum_assured    DOUBLE,
        ci_rider_premium        DOUBLE,
        distribution_channel    VARCHAR(30),
        issue_state             VARCHAR(5),
        _load_ts                TIMESTAMP NOT NULL,
        _source_bronze_id       VARCHAR(36) NOT NULL,
        _etl_run_id             VARCHAR(36) NOT NULL,
        PRIMARY KEY (policy_id, _etl_run_id)
    )
    """,

    # --------------------------------------------------------
    # SILVER: UNIVERSAL LIFE POLICIES
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_ul_policies (
        policy_id                       VARCHAR(50) NOT NULL,
        product_code                    VARCHAR(20) NOT NULL,
        plan_code                       VARCHAR(20) NOT NULL,
        issue_date                      DATE NOT NULL,
        date_of_birth                   DATE NOT NULL,
        issue_age_anb                   INTEGER NOT NULL,
        gender                          VARCHAR(1) NOT NULL,
        smoker_status                   VARCHAR(2) NOT NULL,
        risk_class                      VARCHAR(20) NOT NULL,
        annual_premium                  DOUBLE NOT NULL,
        premium_mode                    VARCHAR(10) NOT NULL,
        reinsurance_flag                BOOLEAN NOT NULL DEFAULT FALSE,
        status_code                     VARCHAR(10) NOT NULL,
        termination_date                DATE,
        termination_cause_code          VARCHAR(30),
        specified_amount                DOUBLE NOT NULL,
        death_benefit_option            VARCHAR(1) NOT NULL,
        account_value_bom               DOUBLE NOT NULL DEFAULT 0,
        account_value_eom               DOUBLE NOT NULL DEFAULT 0,
        current_coi_rate                DOUBLE NOT NULL,
        guaranteed_coi_rate             DOUBLE NOT NULL,
        credited_interest_rate          DOUBLE NOT NULL,
        guaranteed_min_interest_rate    DOUBLE NOT NULL,
        surrender_charge_remaining      DOUBLE NOT NULL DEFAULT 0,
        planned_premium                 DOUBLE,
        target_premium                  DOUBLE,
        min_no_lapse_premium            DOUBLE,
        seven_pay_premium               DOUBLE,
        mec_status_flag                 BOOLEAN NOT NULL DEFAULT FALSE,
        cumulative_premiums_paid        DOUBLE NOT NULL DEFAULT 0,
        premium_persistency_ratio       DOUBLE,
        is_ulsg_flag                    BOOLEAN NOT NULL DEFAULT FALSE,
        shadow_account_value            DOUBLE,
        shadow_account_funding_ratio    DOUBLE,
        no_lapse_guarantee_period       VARCHAR(20),
        secondary_guarantee_type        VARCHAR(20),
        cumulative_nlp_required         DOUBLE,
        ci_rider_flag                   BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_sum_assured            DOUBLE,
        ci_rider_premium                DOUBLE,
        distribution_channel            VARCHAR(30),
        issue_state                     VARCHAR(5),
        _load_ts                        TIMESTAMP NOT NULL,
        _source_bronze_id               VARCHAR(36) NOT NULL,
        _etl_run_id                     VARCHAR(36) NOT NULL,
        PRIMARY KEY (policy_id, _etl_run_id)
    )
    """,

    # --------------------------------------------------------
    # SILVER: VARIABLE UNIVERSAL LIFE POLICIES
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_vul_policies (
        policy_id                       VARCHAR(50) NOT NULL,
        product_code                    VARCHAR(20) NOT NULL,
        plan_code                       VARCHAR(20) NOT NULL,
        issue_date                      DATE NOT NULL,
        date_of_birth                   DATE NOT NULL,
        issue_age_anb                   INTEGER NOT NULL,
        gender                          VARCHAR(1) NOT NULL,
        smoker_status                   VARCHAR(2) NOT NULL,
        risk_class                      VARCHAR(20) NOT NULL,
        annual_premium                  DOUBLE NOT NULL,
        premium_mode                    VARCHAR(10) NOT NULL,
        reinsurance_flag                BOOLEAN NOT NULL DEFAULT FALSE,
        status_code                     VARCHAR(10) NOT NULL,
        termination_date                DATE,
        termination_cause_code          VARCHAR(30),
        specified_amount                DOUBLE NOT NULL,
        death_benefit_option            VARCHAR(1) NOT NULL,
        account_value_bom               DOUBLE NOT NULL DEFAULT 0,
        account_value_eom               DOUBLE NOT NULL DEFAULT 0,
        current_coi_rate                DOUBLE NOT NULL,
        guaranteed_coi_rate             DOUBLE NOT NULL,
        surrender_charge_remaining      DOUBLE NOT NULL DEFAULT 0,
        planned_premium                 DOUBLE,
        mec_status_flag                 BOOLEAN NOT NULL DEFAULT FALSE,
        separate_account_total_value    DOUBLE NOT NULL DEFAULT 0,
        fixed_account_value             DOUBLE NOT NULL DEFAULT 0,
        sub_account_allocations         VARCHAR,
        equity_allocation_pct           DOUBLE NOT NULL DEFAULT 0,
        fund_value_to_spec_amount_ratio DOUBLE,
        ma_charge_annual_rate           DOUBLE NOT NULL DEFAULT 0.014,
        withdrawal_active_flag          BOOLEAN NOT NULL DEFAULT FALSE,
        withdrawal_rate_pct             DOUBLE NOT NULL DEFAULT 0,
        withdrawal_regime               VARCHAR(10) NOT NULL DEFAULT 'NONE',
        ci_rider_flag                   BOOLEAN NOT NULL DEFAULT FALSE,
        ci_rider_sum_assured            DOUBLE,
        ci_rider_premium                DOUBLE,
        distribution_channel            VARCHAR(30),
        issue_state                     VARCHAR(5),
        _load_ts                        TIMESTAMP NOT NULL,
        _source_bronze_id               VARCHAR(36) NOT NULL,
        _etl_run_id                     VARCHAR(36) NOT NULL,
        PRIMARY KEY (policy_id, _etl_run_id)
    )
    """,

    # --------------------------------------------------------
    # SILVER: DEFERRED ANNUITY CONTRACTS
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_annuity_contracts (
        contract_id                     VARCHAR(50) NOT NULL,
        product_code                    VARCHAR(20) NOT NULL,
        product_type                    VARCHAR(20) NOT NULL,
        premium_type                    VARCHAR(10) NOT NULL,
        issue_date                      DATE NOT NULL,
        date_of_birth                   DATE NOT NULL,
        issue_age_anb                   INTEGER NOT NULL,
        gender                          VARCHAR(1) NOT NULL,
        market_type                     VARCHAR(10) NOT NULL,
        account_value                   DOUBLE NOT NULL DEFAULT 0,
        benefit_base                    DOUBLE,
        surrender_charge_schedule       VARCHAR,
        surrender_charge_remaining      DOUBLE NOT NULL DEFAULT 0,
        surrender_charge_year           INTEGER NOT NULL DEFAULT 1,
        free_withdrawal_allowance_pct   DOUBLE NOT NULL DEFAULT 0.10,
        guaranteed_min_interest_rate    DOUBLE NOT NULL DEFAULT 0,
        credited_rate_current           DOUBLE NOT NULL DEFAULT 0,
        market_value_adjustment_flag    BOOLEAN NOT NULL DEFAULT FALSE,
        glwb_elected_flag               BOOLEAN NOT NULL DEFAULT FALSE,
        gmdb_type                       VARCHAR(20),
        glwb_withdrawal_rate_pct        DOUBLE,
        glwb_utilization_status         VARCHAR(10) DEFAULT 'WAITING',
        rider_fee_annual_rate           DOUBLE NOT NULL DEFAULT 0,
        moneyness_ratio                 DOUBLE,
        is_surrender_charge_expired_flag BOOLEAN NOT NULL DEFAULT FALSE,
        status_code                     VARCHAR(10) NOT NULL,
        termination_date                DATE,
        termination_cause_code          VARCHAR(30),
        distribution_channel            VARCHAR(30),
        issue_state                     VARCHAR(5),
        _load_ts                        TIMESTAMP NOT NULL,
        _source_bronze_id               VARCHAR(36) NOT NULL,
        _etl_run_id                     VARCHAR(36) NOT NULL,
        PRIMARY KEY (contract_id, _etl_run_id)
    )
    """,

    # --------------------------------------------------------
    # SILVER: POLICY EVENTS (shared timeline)
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS silver_policy_events (
        event_id            VARCHAR(36) PRIMARY KEY,
        policy_id           VARCHAR(50) NOT NULL,
        product_code        VARCHAR(20) NOT NULL,
        event_type          VARCHAR(30) NOT NULL,
        event_date          DATE NOT NULL,
        policy_year         INTEGER NOT NULL,
        face_amount_before  DOUBLE,
        face_amount_after   DOUBLE,
        account_value       DOUBLE,
        claim_amount        DOUBLE,
        illness_code        VARCHAR(10),
        notes               VARCHAR,
        _etl_run_id         VARCHAR(36) NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_events_policy_id
        ON silver_policy_events (policy_id, product_code, event_date)
    """,
]

_GOLD_AE_DDL = [
    # --------------------------------------------------------
    # GOLD: STUDY RUNS LOG
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_study_runs (
        run_id              VARCHAR(36) PRIMARY KEY,
        run_ts              TIMESTAMP NOT NULL,
        product_codes       VARCHAR NOT NULL,
        study_start_date    DATE NOT NULL,
        study_end_date      DATE NOT NULL,
        exposure_method     VARCHAR(20) NOT NULL,
        mortality_table     VARCHAR(100) NOT NULL,
        lapse_table         VARCHAR(100),
        ci_table            VARCHAR(100),
        credibility_method  VARCHAR(20) NOT NULL,
        data_snapshot_hash  VARCHAR(64) NOT NULL,
        config_hash         VARCHAR(64) NOT NULL,
        code_version        VARCHAR(20) NOT NULL,
        run_duration_sec    DOUBLE,
        status              VARCHAR(10) NOT NULL,
        error_message       VARCHAR
    )
    """,

    # --------------------------------------------------------
    # GOLD: DQ RUN SUMMARY
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_dq_run_summary (
        dq_run_id           VARCHAR(36) PRIMARY KEY,
        study_run_id        VARCHAR(36) NOT NULL,
        product_code        VARCHAR(20) NOT NULL,
        run_ts              TIMESTAMP NOT NULL,
        total_records       INTEGER NOT NULL,
        records_passed      INTEGER NOT NULL,
        records_quarantined INTEGER NOT NULL,
        records_halted      INTEGER NOT NULL,
        dq_score_pct        DOUBLE NOT NULL,
        critical_failure    BOOLEAN NOT NULL DEFAULT FALSE,
        check_results       VARCHAR NOT NULL
    )
    """,

    # --------------------------------------------------------
    # GOLD: DQ QUARANTINE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_dq_quarantine (
        quarantine_id           VARCHAR(36) PRIMARY KEY,
        dq_run_id               VARCHAR(36) NOT NULL,
        study_run_id            VARCHAR(36) NOT NULL,
        policy_id               VARCHAR(50) NOT NULL,
        product_code            VARCHAR(20) NOT NULL,
        check_id                VARCHAR(20) NOT NULL,
        check_description       VARCHAR NOT NULL,
        failing_field           VARCHAR(50),
        failing_value           VARCHAR,
        quarantine_ts           TIMESTAMP NOT NULL,
        actuary_override_flag   BOOLEAN NOT NULL DEFAULT FALSE,
        override_ts             TIMESTAMP,
        override_justification  VARCHAR,
        override_actuary_id     VARCHAR(50)
    )
    """,

    # --------------------------------------------------------
    # GOLD: SERIATIM EXPOSURE SEGMENTS
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_exposure_segments (
        segment_id              VARCHAR(36) PRIMARY KEY,
        study_run_id            VARCHAR(36) NOT NULL,
        policy_id               VARCHAR(50) NOT NULL,
        product_code            VARCHAR(20) NOT NULL,
        segment_start_date      DATE NOT NULL,
        segment_end_date        DATE NOT NULL,
        exposure_years          DOUBLE NOT NULL,
        lapse_exposure_years    DOUBLE NOT NULL DEFAULT 0,
        face_amount_start       DOUBLE NOT NULL,
        face_amount_end         DOUBLE NOT NULL,
        face_amount_wtd_avg     DOUBLE NOT NULL,
        account_value           DOUBLE,
        ci_rider_sum_assured    DOUBLE,
        ci_rider_in_force_flag  BOOLEAN NOT NULL DEFAULT FALSE,
        attained_age_start      DOUBLE NOT NULL,
        attained_age_end        DOUBLE NOT NULL,
        attained_age_band       VARCHAR(10) NOT NULL,
        issue_age_anb           INTEGER NOT NULL,
        issue_age_band          VARCHAR(10) NOT NULL,
        policy_year             INTEGER NOT NULL,
        duration_band           VARCHAR(10) NOT NULL,
        calendar_year           INTEGER NOT NULL,
        gender                  VARCHAR(1) NOT NULL,
        smoker_status           VARCHAR(2) NOT NULL,
        risk_class              VARCHAR(20) NOT NULL,
        plan_code               VARCHAR(20) NOT NULL,
        is_plt_flag             BOOLEAN NOT NULL DEFAULT FALSE,
        plt_duration            INTEGER,
        plt_structure_code      VARCHAR(20),
        premium_jump_ratio      DOUBLE,
        premium_jump_ratio_band VARCHAR(10),
        distribution_channel    VARCHAR(30),
        decrement_flag          BOOLEAN NOT NULL DEFAULT FALSE,
        decrement_type          VARCHAR(30),
        illness_code            VARCHAR(10),
        face_amount_at_decrement DOUBLE,
        exposure_method         VARCHAR(20) NOT NULL,
        CONSTRAINT chk_exposure_positive CHECK (exposure_years > 0),
        CONSTRAINT chk_exposure_le_one   CHECK (exposure_years <= 1.0001)
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_exposure_run_product
        ON gold_exposure_segments (study_run_id, product_code)
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_exposure_policy
        ON gold_exposure_segments (policy_id, study_run_id)
    """,

    # --------------------------------------------------------
    # GOLD: IN-FORCE RECONCILIATION
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_inforce_reconciliation (
        recon_id            VARCHAR(36) PRIMARY KEY,
        study_run_id        VARCHAR(36) NOT NULL,
        product_code        VARCHAR(20) NOT NULL,
        calendar_year       INTEGER NOT NULL,
        beg_if_count        INTEGER NOT NULL,
        new_issues_count    INTEGER NOT NULL,
        deaths_count        INTEGER NOT NULL,
        lapses_count        INTEGER NOT NULL,
        surrenders_count    INTEGER NOT NULL,
        other_decrements    INTEGER NOT NULL,
        end_if_count        INTEGER NOT NULL,
        recon_diff_count    INTEGER NOT NULL,
        beg_if_amount       DOUBLE NOT NULL,
        new_issues_amount   DOUBLE NOT NULL,
        deaths_amount       DOUBLE NOT NULL,
        lapses_amount       DOUBLE NOT NULL,
        surrenders_amount   DOUBLE NOT NULL,
        other_amount        DOUBLE NOT NULL,
        end_if_amount       DOUBLE NOT NULL,
        recon_diff_amount   DOUBLE NOT NULL,
        recon_passes        BOOLEAN NOT NULL
    )
    """,

    # --------------------------------------------------------
    # GOLD: A/E RESULTS FACT TABLE
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_ae_results (
        result_id               VARCHAR(36) PRIMARY KEY,
        study_run_id            VARCHAR(36) NOT NULL,
        assumption_set_id       VARCHAR(36),
        product_code            VARCHAR(20),
        plan_code               VARCHAR(20),
        gender                  VARCHAR(1),
        smoker_status           VARCHAR(2),
        risk_class              VARCHAR(20),
        issue_age_band          VARCHAR(10),
        attained_age_band       VARCHAR(10),
        duration_band           VARCHAR(10),
        policy_year             INTEGER,
        calendar_year           INTEGER,
        is_plt_flag             BOOLEAN,
        premium_jump_ratio_band VARCHAR(10),
        distribution_channel    VARCHAR(30),
        illness_code            VARCHAR(10),
        exposure_count          DOUBLE,
        exposure_amount         DOUBLE,
        actual_deaths_count     INTEGER,
        actual_deaths_amount    DOUBLE,
        expected_deaths_count   DOUBLE,
        expected_deaths_amount  DOUBLE,
        ae_count                DOUBLE,
        ae_amount               DOUBLE,
        se_ae_count             DOUBLE,
        se_ae_amount            DOUBLE,
        ci_lower_count          DOUBLE,
        ci_upper_count          DOUBLE,
        ci_lower_amount         DOUBLE,
        ci_upper_amount         DOUBLE,
        credibility_z           DOUBLE,
        credibility_wtd_ae      DOUBLE,
        lapse_exposure_count    DOUBLE,
        actual_lapses           INTEGER,
        expected_lapses         DOUBLE,
        ae_lapse                DOUBLE,
        se_ae_lapse             DOUBLE,
        ci_lower_lapse          DOUBLE,
        ci_upper_lapse          DOUBLE,
        credibility_z_lapse     DOUBLE,
        ci_exposure_count       DOUBLE,
        actual_ci_claims        INTEGER,
        expected_ci_claims      DOUBLE,
        ae_ci                   DOUBLE,
        se_ae_ci                DOUBLE,
        ci_lower_ci             DOUBLE,
        ci_upper_ci             DOUBLE,
        credibility_z_ci        DOUBLE,
        surrender_exposure      DOUBLE,
        actual_surrenders       INTEGER,
        expected_surrenders     DOUBLE,
        ae_surrender            DOUBLE,
        anti_selection_flag     BOOLEAN NOT NULL DEFAULT FALSE,
        _created_ts             TIMESTAMP NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_ae_run_product
        ON gold_ae_results (study_run_id, product_code)
    """,
]

_GOLD_TEV_DDL = [
    # --------------------------------------------------------
    # GOLD: ASSUMPTION SETS
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_assumption_sets (
        assumption_set_id           VARCHAR(36) PRIMARY KEY,
        version                     INTEGER NOT NULL DEFAULT 1,
        status                      VARCHAR(20) NOT NULL,
        effective_date              DATE NOT NULL,
        author_id                   VARCHAR(50) NOT NULL,
        basis                       VARCHAR(20) NOT NULL DEFAULT 'best-estimate',
        source_study_run_id         VARCHAR(36) NOT NULL,
        yaml_file_path              VARCHAR NOT NULL,
        created_ts                  TIMESTAMP NOT NULL,
        approved_by                 VARCHAR(50),
        approved_ts                 TIMESTAMP,
        superseded_by               VARCHAR(36),
        description                 VARCHAR,
        rdr                         DOUBLE NOT NULL,
        earned_rate_ga              DOUBLE NOT NULL,
        earned_rate_sa              DOUBLE NOT NULL,
        tax_rate                    DOUBLE NOT NULL,
        expense_inflation           DOUBLE NOT NULL
    )
    """,

    # --------------------------------------------------------
    # GOLD: MODEL POINTS
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_model_points (
        model_point_id          VARCHAR(36) PRIMARY KEY,
        tev_run_id              VARCHAR(36) NOT NULL,
        product_code            VARCHAR(20) NOT NULL,
        plan_code               VARCHAR(20) NOT NULL,
        gender                  VARCHAR(1) NOT NULL,
        smoker_status           VARCHAR(2) NOT NULL DEFAULT 'NS',
        risk_class              VARCHAR(20) NOT NULL,
        issue_age_band          VARCHAR(10) NOT NULL,
        attained_age_band       VARCHAR(10) NOT NULL,
        wtd_avg_attained_age    DOUBLE NOT NULL,
        wtd_avg_issue_age       DOUBLE NOT NULL,
        wtd_avg_duration        DOUBLE NOT NULL,
        duration_band           VARCHAR(10) NOT NULL,
        is_plt_flag             BOOLEAN,
        premium_jump_ratio_band VARCHAR(10),
        is_ulsg_flag            BOOLEAN,
        av_band                 VARCHAR(10),
        equity_allocation_band  VARCHAR(10),
        glwb_elected_flag       BOOLEAN,
        surrender_charge_yr_band VARCHAR(10),
        participating_flag      BOOLEAN,
        policy_count            INTEGER NOT NULL,
        face_amount_total       DOUBLE NOT NULL,
        reserve_total           DOUBLE NOT NULL,
        account_value_total     DOUBLE,
        premium_total           DOUBLE NOT NULL,
        ci_rider_count          INTEGER NOT NULL DEFAULT 0,
        ci_rider_sa_total       DOUBLE NOT NULL DEFAULT 0,
        required_capital        DOUBLE NOT NULL,
        _created_ts             TIMESTAMP NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_mp_run_product
        ON gold_model_points (tev_run_id, product_code)
    """,

    # --------------------------------------------------------
    # GOLD: TEV RUN LOG
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_tev_run_log (
        tev_run_id              VARCHAR(36) PRIMARY KEY,
        assumption_set_id       VARCHAR(36) NOT NULL,
        sensitivity_id          VARCHAR(20),
        run_ts                  TIMESTAMP NOT NULL,
        model_point_hash        VARCHAR(64) NOT NULL,
        config_hash             VARCHAR(64) NOT NULL,
        code_version            VARCHAR(20) NOT NULL,
        projection_years        INTEGER NOT NULL,
        run_duration_sec        DOUBLE,
        status                  VARCHAR(10) NOT NULL,
        error_message           VARCHAR,
        total_anw               DOUBLE,
        total_pvfp              DOUBLE,
        total_pvcoc             DOUBLE,
        total_vif               DOUBLE,
        total_tev               DOUBLE,
        delta_tev_vs_prior      DOUBLE,
        prior_tev_run_id        VARCHAR(36)
    )
    """,

    # --------------------------------------------------------
    # GOLD: TEV RESULTS (per product, per run)
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_tev_results (
        result_id               VARCHAR(36) PRIMARY KEY,
        tev_run_id              VARCHAR(36) NOT NULL,
        assumption_set_id       VARCHAR(36) NOT NULL,
        sensitivity_id          VARCHAR(20),
        product_code            VARCHAR(20) NOT NULL,
        anw                     DOUBLE NOT NULL,
        anw_required_capital    DOUBLE NOT NULL,
        anw_free_surplus        DOUBLE NOT NULL,
        pvfp                    DOUBLE NOT NULL,
        pvfp_mortality_margin   DOUBLE,
        pvfp_lapse_margin       DOUBLE,
        pvfp_ci_margin          DOUBLE,
        pvfp_investment_spread  DOUBLE,
        pvfp_expense_margin     DOUBLE,
        pvfp_other              DOUBLE,
        pvfp_tax                DOUBLE,
        pvfp_reserve_release    DOUBLE,
        pvfp_change             DOUBLE,
        pvcoc                   DOUBLE NOT NULL,
        vif                     DOUBLE NOT NULL,
        tev                     DOUBLE NOT NULL,
        delta_tev               DOUBLE,
        _created_ts             TIMESTAMP NOT NULL,
        UNIQUE (tev_run_id, product_code)
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_tev_results_run
        ON gold_tev_results (tev_run_id, product_code)
    """,

    # --------------------------------------------------------
    # GOLD: WORKFLOW ITERATION LOG
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_workflow_iterations (
        iteration_id            VARCHAR(36) PRIMARY KEY,
        workflow_session_id     VARCHAR(36) NOT NULL,
        iteration_number        INTEGER NOT NULL,
        assumption_set_id       VARCHAR(36) NOT NULL,
        tev_baseline_run_id     VARCHAR(36),
        stage                   INTEGER NOT NULL,
        action                  VARCHAR(20) NOT NULL,
        -- Action values: SAVED, RAN_TEV, APPROVED_S3, RETURNED_TO_S2, ENVELOPE_RUN, SUBMITTED_S4
        actuary_id              VARCHAR(50) NOT NULL,
        actuary_comment         VARCHAR,
        total_tev               DOUBLE,
        delta_tev_vs_prior      DOUBLE,
        envelope_run_flag       BOOLEAN NOT NULL DEFAULT FALSE,
        iteration_ts            TIMESTAMP NOT NULL
    )
    """,

    # --------------------------------------------------------
    # GOLD: ASSUMPTION APPROVALS
    # --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS gold_assumption_approvals (
        approval_id             VARCHAR(36) PRIMARY KEY,
        assumption_set_id       VARCHAR(36) NOT NULL UNIQUE,
        workflow_session_id     VARCHAR(36) NOT NULL,
        source_study_run_id     VARCHAR(36) NOT NULL,
        tev_baseline_run_id     VARCHAR(36) NOT NULL,
        proposer_id             VARCHAR(50) NOT NULL,
        reviewer_id             VARCHAR(50) NOT NULL,
        reviewer_decision       VARCHAR(10) NOT NULL,
        reviewer_comment        VARCHAR NOT NULL,
        total_iterations        INTEGER NOT NULL,
        envelope_run_flag       BOOLEAN NOT NULL DEFAULT FALSE,
        envelope_tev_min        DOUBLE,
        envelope_tev_max        DOUBLE,
        proposed_envelope_percentile DOUBLE,
        baseline_tev            DOUBLE NOT NULL,
        delta_tev_vs_prior      DOUBLE,
        max_sensitivity_delta   DOUBLE,
        proposed_ts             TIMESTAMP NOT NULL,
        approved_ts             TIMESTAMP,
        iteration_history       VARCHAR NOT NULL
    )
    """,
]


# ============================================================
# GOLD: AI LAYER (Phase 3; Tech Spec v2.0.1 §D)
# ------------------------------------------------------------
# gold_ai_model_registry lands in Session 15 (GLM). The other two AI Gold
# tables (gold_ai_eval_results §D.2, gold_ai_audit_log §D.3) are appended to
# this list in Session 18 (created but not written to until Sessions 20/22).
# cv_metric_* and shap_json_path are GBM-only and stay NULL for GLM rows
# (Session 16 populates them).
# ============================================================
_GOLD_AI_DDL = [
    """
    CREATE TABLE IF NOT EXISTS gold_ai_model_registry (
        model_id            VARCHAR(36) PRIMARY KEY,
        run_id              VARCHAR(36) NOT NULL,
        model_type          VARCHAR(10) NOT NULL,   -- GLM, GBM
        decrement           VARCHAR(20) NOT NULL,   -- MORTALITY, LAPSE, CI_INCIDENCE
        product_code        VARCHAR(20) NOT NULL,
        fit_ts              TIMESTAMP NOT NULL,
        converged           BOOLEAN NOT NULL,
        n_cells             INTEGER NOT NULL,
        deviance            DOUBLE,                 -- GLM
        dispersion          DOUBLE,                 -- GLM
        aic                 DOUBLE,                 -- GLM
        cv_metric_name      VARCHAR(20),            -- GBM: deviance / logloss
        cv_metric_value     DOUBLE,                 -- GBM
        artifact_path       VARCHAR NOT NULL,       -- serialized model (§D.5)
        shap_json_path      VARCHAR,                -- GBM only (§D.6)
        data_snapshot_hash  VARCHAR(64) NOT NULL,
        config_hash         VARCHAR(64) NOT NULL,
        code_version        VARCHAR(20) NOT NULL,
        seed                INTEGER NOT NULL,
        message             VARCHAR                 -- populated when converged = FALSE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ai_registry_run
        ON gold_ai_model_registry (run_id, model_type, decrement, product_code)
    """,
    # --- gold_ai_eval_results (§D.2): one row per (harness run × model). ---
    # Written by the eval harness in Session 22 (FR-3B-52); created here.
    """
    CREATE TABLE IF NOT EXISTS gold_ai_eval_results (
        eval_run_id           VARCHAR(36) PRIMARY KEY,
        eval_ts               TIMESTAMP NOT NULL,
        model_string          VARCHAR(60) NOT NULL,
        prompt_template_hashes VARCHAR NOT NULL,    -- JSON {template_name: hash}
        tool_schema_version   VARCHAR(20) NOT NULL, -- FR-3B-16
        execution_accuracy    DOUBLE NOT NULL,
        gate_integrity        DOUBLE NOT NULL,      -- hard gate, expect 1.0
        refusal_correctness   DOUBLE NOT NULL,
        intent_routing_acc    DOUBLE NOT NULL,
        numeric_traceability  DOUBLE NOT NULL,      -- hard gate, expect 1.0
        n_golden              INTEGER NOT NULL,
        n_adversarial         INTEGER NOT NULL,
        est_cost_usd          DOUBLE,
        actual_cost_usd       DOUBLE,
        per_question          VARCHAR NOT NULL      -- JSON: [{id, intent_ok, match_ok, ...}]
    )
    """,
    # --- gold_ai_audit_log (§D.3): append-only chatbot + MCP + Skill log. ---
    # Written by the chatbot/MCP/Skill paths in Sessions 20/21 (FR-3B-14/47);
    # created here. Hashes-plus-dynamic-parts design (see §D.3 reconciliation).
    """
    CREATE TABLE IF NOT EXISTS gold_ai_audit_log (
        audit_id             VARCHAR(36) PRIMARY KEY,
        entry_ts             TIMESTAMP NOT NULL,
        source               VARCHAR(20) NOT NULL,  -- CHATBOT, MCP_SERVER, SKILL
        session_id           VARCHAR(36),           -- chatbot/Skill session; NULL for direct MCP
        turn_index           INTEGER,               -- chatbot turn ordinal
        provider             VARCHAR(20),
        model_string         VARCHAR(60),
        intent               VARCHAR(25),           -- FR-3B-27
        intent_reason        VARCHAR,
        prompt_template_hashes VARCHAR,             -- JSON {name: hash} (reconstruct full prompt)
        user_message         VARCHAR,               -- dynamic part
        retrieved_context_ref VARCHAR,              -- JSON: artifact refs / row ids (not full text)
        generated_sql        VARCHAR,
        sql_gate_outcome     VARCHAR(20),           -- SQLGateOutcome
        sql_gate_detail      VARCHAR,
        result_row_count     INTEGER,
        response_text        VARCHAR,               -- final rendered answer (dynamic)
        traceability_passed  BOOLEAN,
        untraceable_nums     VARCHAR,               -- JSON array when blocked
        faithfulness_score   INTEGER,               -- 1-5, NULL if judge disabled
        blocked              BOOLEAN NOT NULL DEFAULT FALSE,
        block_reason         VARCHAR,
        input_tokens         INTEGER,
        output_tokens        INTEGER,
        est_cost_usd         DOUBLE,
        latency_ms           DOUBLE
    )
    """,
    # --- gold_ai_proposed_factors (2026-06-27 governed-maximum amendment): ---
    # Materialised, PII-free, queryable copy of the published GLM/GBM proposed
    # adjustment factor cells. The factor values otherwise live only inside the
    # serialized model artifacts (data/ai_models/), unreachable by SQL — so the AI
    # Analyst could not answer "what are the proposed Term mortality assumptions by
    # age band?". One row per published FactorCell per registered model; written by
    # the governed AI write path (src/ai/proposals.py) at fit/registration time
    # (a fourth permitted AI Gold write target, FR-3A-09 amended). No PII.
    """
    CREATE TABLE IF NOT EXISTS gold_ai_proposed_factors (
        proposed_factor_id   VARCHAR(36) PRIMARY KEY,
        model_id             VARCHAR(36) NOT NULL,
        run_id               VARCHAR(36) NOT NULL,
        model_type           VARCHAR(10) NOT NULL,   -- GLM, GBM
        decrement            VARCHAR(20) NOT NULL,   -- MORTALITY, LAPSE, CI_INCIDENCE
        product_code         VARCHAR(20) NOT NULL,
        sex                  VARCHAR(2),             -- grain dim (NULL when not in grain)
        smoker               VARCHAR(4),             -- grain dim
        attained_age_band    VARCHAR(10),            -- grain dim
        duration_band        VARCHAR(10),            -- grain dim
        grain_key            VARCHAR,                -- full grain dict as JSON
        factor               DOUBLE NOT NULL,
        ci_low               DOUBLE,
        ci_high              DOUBLE,
        expected_events      DOUBLE,
        credibility_z        DOUBLE,
        ae_derived_factor    DOUBLE,
        fit_ts               TIMESTAMP NOT NULL
    )
    """,
]


# ============================================================
# Phase 4 — Governance tables (Tech Spec v3.0 §G)
# ------------------------------------------------------------
# Additive only; no Phase 1-3 table is altered destructively. gold_users is
# created first within the governance group (§G intro ordering); it has no FK
# dependency on later governance tables (sign-offs / events land in Sessions
# 25-26). Seeded from config/governance_config.yaml (§I.2); passwords are
# stored only as salted hashes (FR-4-02 / NFR-G-01).
# ============================================================
_GOVERNANCE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS gold_users (
        user_id          VARCHAR(36) PRIMARY KEY,
        username         VARCHAR(50) NOT NULL UNIQUE,
        display_name     VARCHAR(100) NOT NULL,
        role             VARCHAR(20) NOT NULL,
        password_hash    VARCHAR NOT NULL,
        password_salt    VARCHAR NOT NULL,
        active           BOOLEAN NOT NULL DEFAULT TRUE,
        created_ts       TIMESTAMP NOT NULL
    )
    """,
]


# ============================================================
# Phase 4 governance — sign-off log (Tech Spec v3.0 §G.2)
# ------------------------------------------------------------
# Session 25 (FR-4-12..18). One row per chain-level sign-off action on either
# artifact type (assumption set or A/E study run). Append-only and hash-chained:
# entry_hash = sha256(canonical_content || prev_hash), prev_hash = the prior
# row's entry_hash ordered by seq (empty string for the first row). Written
# exclusively through src/governance/audit.py::append_event (the standard
# parameterized write path, NOT the AI read-only sql_boundary). The Phase-2
# gold_workflow_iterations / gold_assumption_approvals logs gain their own
# hash-chain columns in Session 26 (§G.5) — not here.
# ============================================================
_GOVERNANCE_SIGNOFF_DDL = [
    """
    CREATE TABLE IF NOT EXISTS gold_governance_signoffs (
        signoff_id           VARCHAR(36) PRIMARY KEY,
        seq                  BIGINT NOT NULL UNIQUE,
        artifact_type        VARCHAR(20) NOT NULL,
        artifact_id          VARCHAR(36) NOT NULL,
        artifact_version     INTEGER,
        chain_level          INTEGER NOT NULL,
        required_role        VARCHAR(20) NOT NULL,
        actor_user_id        VARCHAR(36) NOT NULL,
        actor_role           VARCHAR(20) NOT NULL,
        decision             VARCHAR(10) NOT NULL,
        comment              VARCHAR NOT NULL,
        attestation_text     VARCHAR NOT NULL,
        delta_tev            DOUBLE,
        required_final_level INTEGER,
        signoff_ts           TIMESTAMP NOT NULL,
        prev_hash            VARCHAR(64),
        entry_hash           VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_signoff_artifact
        ON gold_governance_signoffs (artifact_type, artifact_id)
    """,
]


# ============================================================
# Phase 4 governance — A/E governance events log (Tech Spec v3.0 §G.3)
# ------------------------------------------------------------
# Session 26 (FR-4-19). A/E governance events on the existing per-module pattern:
# study-run submission for approval, sign-off/return outcomes, and DQ overrides.
# Append-only and hash-chained exactly as gold_governance_signoffs (§G.2 rule),
# written through src/governance/audit.py::append_event. The three governance
# logs stay physically separate (FR-4-19); §H.7 provides the unified read layer.
# ============================================================
_GOVERNANCE_EVENTS_DDL = [
    """
    CREATE TABLE IF NOT EXISTS gold_ae_governance_events (
        event_id       VARCHAR(36) PRIMARY KEY,
        seq            BIGINT NOT NULL UNIQUE,
        event_type     VARCHAR(30) NOT NULL,
        study_run_id   VARCHAR(36) NOT NULL,
        actor_user_id  VARCHAR(36) NOT NULL,
        detail         VARCHAR,
        event_ts       TIMESTAMP NOT NULL,
        prev_hash      VARCHAR(64),
        entry_hash     VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ae_events_run
        ON gold_ae_governance_events (study_run_id)
    """,
]


# ============================================================
# Additive column migrations (Tech Spec v2.0.1 §D.4)
# ------------------------------------------------------------
# DuckDB has no `ADD COLUMN IF NOT EXISTS`, so column additions to existing
# tables are applied idempotently via _ensure_column(). Session 17 (FR-3A-30)
# adds the AI-provenance columns to gold_assumption_sets so an adopted AI
# proposal records both the proposed value and its model_id at the set level.
# Session 24 (Tech Spec v3.0 §G.4, FR-4-07/09) adds the Phase-4 version-lineage
# and effective-dating columns: parent_set_id (NULL = lineage root) and the
# effective_from / effective_to range set at approval by the lineage engine.
# ============================================================
_COLUMN_MIGRATIONS = [
    # (table, column, type)
    ("gold_assumption_sets", "ai_proposed_value", "DOUBLE"),
    ("gold_assumption_sets", "ai_model_id", "VARCHAR(36)"),
    ("gold_assumption_sets", "parent_set_id", "VARCHAR(36)"),    # §G.4 / FR-4-07
    ("gold_assumption_sets", "effective_from", "DATE"),          # §G.4 / FR-4-09
    ("gold_assumption_sets", "effective_to", "DATE"),            # §G.4 / FR-4-09
    # Session 26 (§G.5, FR-4-20): additive hash-chain columns on the Phase-2
    # governance logs. Nullable — pre-existing rows carry NULL hashes; the §H.7
    # verifier begins each chain at the first hashed row. No UNIQUE on the migrated
    # seq (DuckDB ALTER cannot add it retroactively; the writers are not yet
    # routed through append_event, so no hashed rows exist to collide).
    ("gold_workflow_iterations", "seq", "BIGINT"),
    ("gold_workflow_iterations", "prev_hash", "VARCHAR(64)"),
    ("gold_workflow_iterations", "entry_hash", "VARCHAR(64)"),
    ("gold_assumption_approvals", "seq", "BIGINT"),
    ("gold_assumption_approvals", "prev_hash", "VARCHAR(64)"),
    ("gold_assumption_approvals", "entry_hash", "VARCHAR(64)"),
]


def _ensure_column(con: "duckdb.DuckDBPyConnection", table: str, column: str, col_type: str) -> None:
    """Idempotently add ``column`` to ``table`` (no-op if it already exists).

    DuckDB raises CatalogException both when the column already exists and when
    the table is missing; the column-set is checked first so a genuinely missing
    table still surfaces loudly via the subsequent ALTER.
    """
    existing = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ?",
            [table],
        ).fetchall()
    }
    if column in existing:
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_database(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialise the DuckDB database, creating all tables in dependency order.

    Safe to call multiple times — all statements use CREATE TABLE IF NOT EXISTS,
    and additive column migrations (§D.4) are applied idempotently.

    Args:
        db_path: Path to the DuckDB file. Parent directory must exist.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path)
    try:
        all_ddl = (
            _BRONZE_DDL + _SILVER_DDL + _GOLD_AE_DDL + _GOLD_TEV_DDL
            + _GOLD_AI_DDL + _GOVERNANCE_DDL + _GOVERNANCE_SIGNOFF_DDL
            + _GOVERNANCE_EVENTS_DDL
        )
        for stmt in all_ddl:
            con.execute(stmt.strip())

        for table, column, col_type in _COLUMN_MIGRATIONS:
            _ensure_column(con, table, column, col_type)

        tables = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        print(f"Database initialised at: {db_path}")
        print(f"Tables created ({len(tables)}):")
        for (t,) in tables:
            print(f"  {t}")
    finally:
        con.close()


if __name__ == "__main__":
    init_database()
