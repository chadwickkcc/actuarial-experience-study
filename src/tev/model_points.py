"""Model Point Compression module for TEV Phase 2.

Implements stratified grouping of seriatim in-force population into model
points, exactly matching the interface contract in Technical Specification
Section B.9.

Reconciliation tolerances: <0.1% on policy count, face amount, and reserve.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import math

import duckdb
import pandas as pd
import numpy as np

from src.utils.types import ModelPointResult
from src.exposure.engine import compute_age_band, compute_duration_band

if TYPE_CHECKING:
    from src.tev.assumption_set import AssumptionSet


# ---------------------------------------------------------------------------
# Product grouping dimensions — exactly as specified in Section B.9
# ---------------------------------------------------------------------------

PRODUCT_GROUPING_DIMS: dict[str, list[str]] = {
    "TERM": [
        "plan_code", "gender", "smoker_status", "risk_class",
        "issue_age_band", "duration_band", "level_period_years", "is_plt_flag",
    ],
    "WL": [
        "plan_code", "gender", "smoker_status", "risk_class",
        "issue_age_band", "duration_band", "premium_paying_period", "participating_flag",
    ],
    "UL": [
        "plan_code", "gender", "risk_class",
        "issue_age_band", "duration_band", "is_ulsg_flag", "av_band",
    ],
    "ULSG": [
        "plan_code", "gender", "risk_class",
        "issue_age_band", "duration_band", "is_ulsg_flag", "av_band",
    ],
    "VUL": [
        "plan_code", "gender", "risk_class",
        "issue_age_band", "duration_band", "equity_allocation_band",
    ],
    "DA": [
        "product_type", "gender", "market_type",
        "issue_age_band", "surrender_charge_yr_band", "glwb_elected_flag",
    ],
}

# Study end date used for attained-age / duration calculations
_STUDY_END_DATE = date(2023, 12, 31)

# RC % by product (used when assumption_set is not provided)
_DEFAULT_RC_PCT = {
    "TERM": 0.030,
    "WL": 0.045,
    "UL": 0.060,
    "ULSG": 0.080,
    "VUL": 0.035,
    "DA": 0.045,
    "DA_FIXED": 0.045,
    "DA_FIA": 0.045,
    "DA_VA": 0.045,
}

# CARVM loading for annuities
_CARVM_LOADING = {
    "DA_FIXED": 1.00,
    "FA_FIXED": 1.00,
    "FA_FIA": 1.01,
    "DA_FIA": 1.01,
    "DA_VA": 1.02,
    "FA_VA": 1.02,
    "VA": 1.02,
}


# ---------------------------------------------------------------------------
# Reserve proxy (FR-2-14)
# ---------------------------------------------------------------------------

# Gompertz-Makeham parameters matching tev_core.py (2015 VBT calibration)
_NLP_MORT_A = 0.00005
_NLP_MORT_B = 0.000004
_NLP_MORT_C = 0.112
_NLP_VALUATION_RATE = 0.035   # statutory term reserve valuation interest rate


def _term_nlp_reserve(
    attained_age: float,
    remaining_years: int,
    face_amount: float,
    annual_premium: float,
) -> float:
    """Prospective NLP reserve for a level-premium term policy.

    Uses Gompertz-Makeham mortality calibrated to 2015 VBT and a 3.5%
    valuation interest rate (consistent with CRVM methodology).

    V_t = face × A_{x+t : n-t|} - P × a_{x+t : n-t|}

    where A is the discrete term insurance value and a is the annuity-due.
    Returns 0.0 for PLT policies (remaining_years <= 0).
    """
    if remaining_years <= 0 or face_amount <= 0:
        return 0.0

    v = 1.0 / (1.0 + _NLP_VALUATION_RATE)
    survival = 1.0
    A = 0.0   # PV of death benefits
    a = 0.0   # PV of annuity-due (premium timing)

    for t in range(remaining_years):
        age_t = attained_age + t
        q_t = min(0.999, _NLP_MORT_A + _NLP_MORT_B * math.exp(_NLP_MORT_C * age_t))
        a += (v ** t) * survival
        A += (v ** (t + 1)) * survival * q_t
        survival *= (1.0 - q_t)

    if a <= 0.0:
        return 0.0

    reserve = face_amount * A - annual_premium * a
    return max(0.0, reserve)


def compute_statutory_reserve(
    row: pd.Series,
    product_code: str,
    reserve_config: dict,
) -> float:
    """Compute approximate statutory reserve for a single policy row.

    Uses simplified proxy formulas from requirements spec FR-2-14.

    Products:
        TERM:   CRVM proxy = max(0, (policy_year / level_period_years) * 0.02
                                  * face_amount)
        WL:     NLP proxy = max(0, duration_ratio * 0.40 * face_amount)
        UL/ULSG: max(account_value_eom, AG38_formula_proxy)
        VUL:    max(0.035 * specified_amount, account_value_eom)
        DA/*:   account_value * carvm_loading

    Args:
        row:            Single policy row as pd.Series.
        product_code:   Canonical product code (TERM, WL, UL, ULSG, VUL, DA*).
        reserve_config: Dict from tev_config.yaml (may be empty; defaults used).

    Returns:
        Float statutory reserve value >= 0.
    """
    pc = product_code.upper()

    if pc == "TERM":
        face = float(row.get("face_amount", 0) or 0)
        policy_year = float(row.get("_policy_year", 1) or 1)
        level_period = int(row.get("level_period_years", 20) or 20)
        if level_period <= 0:
            level_period = 20
        remaining = max(0, level_period - int(policy_year))
        if remaining == 0:
            return 0.0   # PLT: annually renewable, no advance reserve
        attained_age = float(row.get("_attained_age", 50) or 50)
        annual_premium = float(row.get("annual_premium", 0) or 0)
        return _term_nlp_reserve(attained_age, remaining, face, annual_premium)

    elif pc == "WL":
        face = float(row.get("face_amount", 0) or 0)
        policy_year = int(row.get("_policy_year", 1) or 1)
        attained_age = float(row.get("_attained_age", 50) or 50)
        issue_age = int(row.get("issue_age_anb", 35) or 35)
        # Duration ratio: (attained_age - issue_age) / (90 - issue_age)
        remaining = max(1, 90 - issue_age)
        ratio = min(1.0, max(0.0, (attained_age - issue_age) / remaining))
        wl_pct = float(reserve_config.get("wl_reserve_pct", 0.40))
        return max(0.0, ratio * wl_pct * face)

    elif pc in ("UL", "ULSG", "IUL"):
        av = float(row.get("account_value_eom", 0) or 0)
        min_nlp = float(row.get("min_no_lapse_premium", 0) or 0)
        attained_age = float(row.get("_attained_age", 60) or 60)
        nlp_end_age = 90.0
        remaining_years = max(0.0, nlp_end_age - attained_age)
        ag38_mult = float(reserve_config.get("ag38_multiplier", 0.85))
        ag38_proxy = min_nlp * remaining_years * ag38_mult
        return max(av, ag38_proxy)

    elif pc == "VUL":
        spec_amt = float(row.get("specified_amount", 0) or 0)
        av = float(row.get("account_value_eom", 0) or 0)
        return max(0.035 * spec_amt, av)

    else:
        # Deferred Annuities (DA_FIXED, DA_FIA, DA_VA, DA, etc.)
        av = float(row.get("account_value", 0) or 0)
        prod_type = str(row.get("product_type", pc) or pc).upper()
        loading = _CARVM_LOADING.get(prod_type, 1.00)
        return av * loading


# ---------------------------------------------------------------------------
# Helper: compute derived columns on the silver DataFrame
# ---------------------------------------------------------------------------

def _add_derived_columns(df: pd.DataFrame, product_code: str) -> pd.DataFrame:
    """Add attained_age, policy_year, age_band, duration_band and product-
    specific band columns to the loaded silver DataFrame.
    """
    study_end = _STUDY_END_DATE

    # Normalise issue_date
    df["issue_date"] = pd.to_datetime(df["issue_date"])

    # Years since issue (fractional)
    df["_years_since_issue"] = (
        pd.Timestamp(study_end) - df["issue_date"]
    ).dt.days / 365.25

    df["_attained_age"] = df["issue_age_anb"] + df["_years_since_issue"]
    df["_policy_year"] = np.floor(df["_years_since_issue"]).astype(int) + 1
    df["_policy_year"] = df["_policy_year"].clip(lower=1)

    df["attained_age_band"] = df["_attained_age"].apply(
        lambda x: compute_age_band(float(x))
    )
    df["issue_age_band"] = df["issue_age_anb"].apply(
        lambda x: compute_age_band(float(x))
    )
    df["duration_band"] = df["_policy_year"].apply(compute_duration_band)

    # Product-specific bands
    pc = product_code.upper()

    if pc == "TERM":
        # PLT flag: policy entered PLT if policy_year > level_period_years
        df["is_plt_flag"] = df["_policy_year"] > df["level_period_years"]

    elif pc in ("UL", "ULSG"):
        av_col = "account_value_eom"
        if av_col not in df.columns:
            df[av_col] = 0.0
        df[av_col] = df[av_col].fillna(0.0)
        df["av_band"] = _quintile_band(df[av_col])
        df["is_ulsg_flag"] = df["is_ulsg_flag"].fillna(False).astype(bool)

    elif pc == "VUL":
        av_col = "account_value_eom"
        if av_col not in df.columns:
            df[av_col] = 0.0
        df[av_col] = df[av_col].fillna(0.0)
        df["av_band"] = _quintile_band(df[av_col])

        eq_col = "equity_allocation_pct"
        if eq_col not in df.columns:
            df[eq_col] = 0.0
        df[eq_col] = df[eq_col].fillna(0.0)
        df["equity_allocation_band"] = df[eq_col].apply(_equity_band)

    elif pc == "DA":
        sc_yr_col = "surrender_charge_year"
        if sc_yr_col not in df.columns:
            df[sc_yr_col] = 1
        df[sc_yr_col] = df[sc_yr_col].fillna(1).astype(int)
        df["surrender_charge_yr_band"] = df[sc_yr_col].apply(_sc_year_band)

        av_col = "account_value"
        if av_col not in df.columns:
            df[av_col] = 0.0
        df[av_col] = df[av_col].fillna(0.0)
        df["av_band"] = _quintile_band(df[av_col])

    return df


def _quintile_band(series: pd.Series) -> pd.Series:
    """Assign 'Q1'–'Q5' labels based on quintile breakpoints."""
    if series.nunique() <= 1:
        return pd.Series(["Q3"] * len(series), index=series.index)
    try:
        labels = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        return pd.qcut(series, q=5, labels=labels, duplicates="drop").astype(str)
    except Exception:
        return pd.Series(["Q3"] * len(series), index=series.index)


def _equity_band(pct: float) -> str:
    """Return equity allocation band label for a given allocation pct."""
    if pct < 0.25:
        return "0-25"
    elif pct < 0.50:
        return "25-50"
    elif pct < 0.75:
        return "50-75"
    return "75-100"


def _sc_year_band(yr: int) -> str:
    """Return surrender-charge year band."""
    if yr <= 2:
        return "1-2"
    elif yr <= 5:
        return "3-5"
    elif yr <= 7:
        return "6-7"
    return "8+"


# ---------------------------------------------------------------------------
# Silver table loading helpers
# ---------------------------------------------------------------------------

_SILVER_TABLE: dict[str, str] = {
    "TERM":  "silver_term_policies",
    "WL":    "silver_wl_policies",
    "UL":    "silver_ul_policies",
    "ULSG":  "silver_ul_policies",   # filtered by is_ulsg_flag = TRUE
    "VUL":   "silver_vul_policies",
    "DA":    "silver_annuity_contracts",
}

# Columns to load per product — union of all needed for reserve + grouping
_TERM_COLS = (
    "policy_id, product_code, plan_code, issue_date, issue_age_anb, gender, "
    "smoker_status, risk_class, face_amount, annual_premium, status_code, "
    "level_period_years, ci_rider_flag, ci_rider_sum_assured"
)
_WL_COLS = (
    "policy_id, product_code, plan_code, issue_date, issue_age_anb, gender, "
    "smoker_status, risk_class, face_amount, annual_premium, status_code, "
    "premium_paying_period, participating_flag, guaranteed_cash_value, "
    "ci_rider_flag, ci_rider_sum_assured"
)
_UL_COLS = (
    "policy_id, product_code, plan_code, issue_date, issue_age_anb, gender, "
    "smoker_status, risk_class, annual_premium, status_code, specified_amount, "
    "account_value_bom, account_value_eom, min_no_lapse_premium, "
    "is_ulsg_flag, ci_rider_flag, ci_rider_sum_assured"
)
_VUL_COLS = (
    "policy_id, product_code, plan_code, issue_date, issue_age_anb, gender, "
    "smoker_status, risk_class, annual_premium, status_code, specified_amount, "
    "account_value_bom, account_value_eom, equity_allocation_pct, "
    "ci_rider_flag, ci_rider_sum_assured"
)
_DA_COLS = (
    "contract_id, product_code, product_type, issue_date, issue_age_anb, gender, "
    "market_type, account_value, surrender_charge_year, glwb_elected_flag, status_code"
)

_SILVER_COLS: dict[str, str] = {
    "TERM": _TERM_COLS,
    "WL":   _WL_COLS,
    "UL":   _UL_COLS,
    "ULSG": _UL_COLS,
    "VUL":  _VUL_COLS,
    "DA":   _DA_COLS,
}


def _load_silver_inforce(con: duckdb.DuckDBPyConnection,
                          product_code: str) -> pd.DataFrame:
    """Load in-force policies for a product using the latest ETL run."""
    table = _SILVER_TABLE[product_code]
    cols = _SILVER_COLS[product_code]

    # Use the ETL run with the most rows (latest full load)
    etl_row = con.execute(f"""
        SELECT _etl_run_id
        FROM {table}
        GROUP BY _etl_run_id
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """).fetchone()

    if etl_row is None:
        return pd.DataFrame()

    latest_etl = etl_row[0]

    # Filter to IF records from the latest ETL run
    if product_code == "ULSG":
        where = f"_etl_run_id = '{latest_etl}' AND status_code = 'IF' AND is_ulsg_flag = TRUE"
    elif product_code == "UL":
        where = f"_etl_run_id = '{latest_etl}' AND status_code = 'IF' AND is_ulsg_flag = FALSE"
    else:
        where = f"_etl_run_id = '{latest_etl}' AND status_code = 'IF'"

    df = con.execute(
        f"SELECT {cols} FROM {table} WHERE {where}"
    ).fetchdf()

    # Rename contract_id to policy_id for annuities to unify downstream logic
    if product_code == "DA" and "contract_id" in df.columns:
        df = df.rename(columns={"contract_id": "policy_id"})

    return df


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def build_model_points(
    product_code: str,
    db_path: Path,
    study_run_id: str,
    tev_run_id: str,
    assumption_set: "AssumptionSet",
) -> ModelPointResult:
    """Compress the seriatim Silver table into model points.

    Steps:
        1. Load silver_{product}_policies filtered to IF policies (latest ETL run).
        2. Compute derived columns: attained_age, policy_year, age bands,
           duration bands, and product-specific band variables.
        3. Apply compute_statutory_reserve() to each row.
        4. Group by PRODUCT_GROUPING_DIMS[product_code].
        5. Aggregate within each group.
        6. Write model points to gold_model_points.
        7. Run reconciliation check (< 0.1% on count, face, reserve).

    Args:
        product_code:   Product code — key in PRODUCT_GROUPING_DIMS.
        db_path:        Path to the DuckDB file.
        study_run_id:   UUID of the study run providing the in-force snapshot.
        tev_run_id:     UUID for this TEV run (written to every model point row).
        assumption_set: AssumptionSet supplying rc_pct_reserve percentages.

    Returns:
        ModelPointResult with compression metrics and the model_points DataFrame.

    Raises:
        ValueError:                     if product_code is not recognised.
        ModelPointReconciliationError:  if reconciliation exceeds 0.1%.
    """
    if product_code not in PRODUCT_GROUPING_DIMS:
        raise ValueError(
            f"Unknown product_code '{product_code}'. "
            f"Valid codes: {list(PRODUCT_GROUPING_DIMS)}"
        )

    group_dims = PRODUCT_GROUPING_DIMS[product_code]
    rc_pct = assumption_set.rc_pct_reserve.get(
        product_code, _DEFAULT_RC_PCT.get(product_code, 0.04)
    )
    reserve_config: dict = {}   # use formula defaults from tev_config

    con = duckdb.connect(str(db_path))
    try:
        df = _load_silver_inforce(con, product_code)
    finally:
        con.close()

    if df.empty:
        empty_mp = pd.DataFrame()
        return ModelPointResult(
            tev_run_id=tev_run_id,
            product_code=product_code,
            seriatim_count=0,
            model_point_count=0,
            compression_ratio=0.0,
            recon_count_diff_pct=0.0,
            recon_face_diff_pct=0.0,
            recon_reserve_diff_pct=0.0,
            model_points_df=empty_mp,
        )

    df = _add_derived_columns(df.copy(), product_code)

    # Compute statutory reserve for each seriatim row
    df["_reserve"] = df.apply(
        lambda row: compute_statutory_reserve(row, product_code, reserve_config),
        axis=1,
    )

    # Determine face amount column (face_amount / specified_amount / account_value)
    if product_code == "DA":
        face_col = "account_value"
    elif product_code in ("VUL", "UL", "ULSG"):
        face_col = "specified_amount"
    else:
        face_col = "face_amount"

    if face_col not in df.columns:
        df[face_col] = 0.0
    df[face_col] = df[face_col].fillna(0.0)

    # Pre-compression totals
    pre_count = len(df)
    pre_face = df[face_col].sum()
    pre_reserve = df["_reserve"].sum()

    # Build available grouping dims (only those that exist in the DataFrame)
    available_dims = [d for d in group_dims if d in df.columns]

    # Fill NA in group dims to avoid lost groups
    for dim in available_dims:
        if df[dim].dtype == object:
            df[dim] = df[dim].fillna("UNKNOWN")
        else:
            df[dim] = df[dim].fillna(0)

    # Group and aggregate
    mp_df = _aggregate_model_points(df, available_dims, face_col, product_code)

    # Add metadata columns
    mp_df["tev_run_id"] = tev_run_id
    mp_df["product_code"] = product_code
    mp_df["required_capital"] = mp_df["reserve_total"] * rc_pct
    mp_df["model_point_id"] = [str(uuid.uuid4()) for _ in range(len(mp_df))]
    mp_df["_created_ts"] = datetime.utcnow()

    # Post-compression totals
    post_count = mp_df["policy_count"].sum()
    post_face = mp_df["face_amount_total"].sum()
    post_reserve = mp_df["reserve_total"].sum()

    # Reconciliation checks
    def _pct_diff(pre: float, post: float) -> float:
        if pre == 0:
            return 0.0
        return abs(pre - post) / pre * 100.0

    recon_count_pct = _pct_diff(pre_count, post_count)
    recon_face_pct = _pct_diff(pre_face, post_face)
    recon_reserve_pct = _pct_diff(pre_reserve, post_reserve)

    if any(x > 0.1 for x in [recon_count_pct, recon_face_pct, recon_reserve_pct]):
        raise ModelPointReconciliationError(
            product_code=product_code,
            count_diff_pct=recon_count_pct,
            face_diff_pct=recon_face_pct,
            reserve_diff_pct=recon_reserve_pct,
        )

    # Write to DB
    _write_model_points(db_path, mp_df, product_code)

    return ModelPointResult(
        tev_run_id=tev_run_id,
        product_code=product_code,
        seriatim_count=pre_count,
        model_point_count=len(mp_df),
        compression_ratio=pre_count / max(len(mp_df), 1),
        recon_count_diff_pct=recon_count_pct,
        recon_face_diff_pct=recon_face_pct,
        recon_reserve_diff_pct=recon_reserve_pct,
        model_points_df=mp_df,
    )


def _aggregate_model_points(df: pd.DataFrame, group_dims: list[str],
                             face_col: str, product_code: str) -> pd.DataFrame:
    """Group seriatim policies and compute representative model-point values."""
    pc = product_code.upper()

    # Determine AV column
    if pc == "DA":
        av_col = "account_value"
    elif pc in ("UL", "ULSG", "VUL"):
        av_col = "account_value_eom"
    else:
        av_col = None

    # Premium column
    prem_col = "annual_premium" if "annual_premium" in df.columns else None

    # CI rider columns
    has_ci_flag = "ci_rider_flag" in df.columns
    has_ci_sa = "ci_rider_sum_assured" in df.columns

    agg_funcs: dict = {
        face_col: "sum",
        "_reserve": "sum",
        "_attained_age": "mean",
        "_years_since_issue": "mean",
        "_policy_year": "mean",
        "issue_age_anb": "mean",
    }
    if av_col and av_col in df.columns:
        agg_funcs[av_col] = "sum"
    if prem_col and prem_col in df.columns:
        agg_funcs[prem_col] = "sum"
    if has_ci_flag:
        agg_funcs["ci_rider_flag"] = "sum"
    if has_ci_sa:
        agg_funcs["ci_rider_sum_assured"] = "sum"

    grouped = df.groupby(group_dims, dropna=False, observed=True)
    agg = grouped.agg(agg_funcs)
    agg["policy_count"] = grouped.size()
    agg = agg.reset_index()

    # Rename to model-point schema columns.
    # Build rename_map carefully: when face_col == av_col (DA case),
    # rename once then copy to create the second alias.
    rename_map = {
        "_reserve": "reserve_total",
        "_attained_age": "wtd_avg_attained_age",
        "_years_since_issue": "wtd_avg_duration",
        "issue_age_anb": "wtd_avg_issue_age",
    }
    face_av_same = (av_col is not None and av_col == face_col)
    if not face_av_same:
        rename_map[face_col] = "face_amount_total"
        if av_col and av_col in agg.columns:
            rename_map[av_col] = "account_value_total"
    else:
        # DA: account_value is both face and AV — rename once, copy after
        rename_map[face_col] = "face_amount_total"

    if prem_col and prem_col in agg.columns:
        rename_map[prem_col] = "premium_total"
    if has_ci_flag:
        rename_map["ci_rider_flag"] = "ci_rider_count"
    if has_ci_sa:
        rename_map["ci_rider_sum_assured"] = "ci_rider_sa_total"

    agg = agg.rename(columns=rename_map)

    # For DA, account_value_total mirrors face_amount_total
    if face_av_same and "face_amount_total" in agg.columns:
        agg["account_value_total"] = agg["face_amount_total"]

    # Ensure required output columns exist with defaults
    if "account_value_total" not in agg.columns:
        agg["account_value_total"] = 0.0
    if "premium_total" not in agg.columns:
        agg["premium_total"] = 0.0
    if "ci_rider_count" not in agg.columns:
        agg["ci_rider_count"] = 0
    if "ci_rider_sa_total" not in agg.columns:
        agg["ci_rider_sa_total"] = 0.0

    # Fill integer count
    agg["ci_rider_count"] = agg["ci_rider_count"].fillna(0).astype(int)

    # Computed age/duration bands on aggregated weighted averages
    agg["attained_age_band"] = agg["wtd_avg_attained_age"].apply(
        lambda x: compute_age_band(float(x))
    )
    if "issue_age_band" not in agg.columns:
        agg["issue_age_band"] = agg["wtd_avg_issue_age"].apply(
            lambda x: compute_age_band(float(x))
        )

    # product_code column for DB schema
    agg["product_code"] = product_code

    return agg


def _write_model_points(db_path: Path, mp_df: pd.DataFrame,
                         product_code: str) -> None:
    """Insert model points into gold_model_points table."""
    pc = product_code.upper()
    con = duckdb.connect(str(db_path))
    try:
        for _, row in mp_df.iterrows():
            # Map grouping dim values to explicit DB columns
            plan_code = str(row.get("plan_code", row.get("product_type", "UNKNOWN")))
            gender = str(row.get("gender", "U"))
            smoker_status = str(row.get("smoker_status", "NS"))
            risk_class = str(row.get("risk_class", "STD_NS"))
            issue_age_band = str(row.get("issue_age_band", "35-39"))
            attained_age_band = str(row.get("attained_age_band", "40-44"))
            duration_band = str(row.get("duration_band", "1"))

            # Optional product-specific columns (NULL for non-applicable)
            is_plt_flag = bool(row["is_plt_flag"]) if "is_plt_flag" in row and pd.notna(row.get("is_plt_flag")) else None
            premium_jump_band = str(row["premium_jump_ratio_band"]) if "premium_jump_ratio_band" in row and pd.notna(row.get("premium_jump_ratio_band")) else None
            is_ulsg_flag = bool(row["is_ulsg_flag"]) if "is_ulsg_flag" in row and pd.notna(row.get("is_ulsg_flag")) else None
            av_band = str(row["av_band"]) if "av_band" in row and pd.notna(row.get("av_band")) else None
            equity_band = str(row["equity_allocation_band"]) if "equity_allocation_band" in row and pd.notna(row.get("equity_allocation_band")) else None
            glwb_flag = bool(row["glwb_elected_flag"]) if "glwb_elected_flag" in row and pd.notna(row.get("glwb_elected_flag")) else None
            sc_yr_band = str(row["surrender_charge_yr_band"]) if "surrender_charge_yr_band" in row and pd.notna(row.get("surrender_charge_yr_band")) else None
            participating = bool(row["participating_flag"]) if "participating_flag" in row and pd.notna(row.get("participating_flag")) else None

            con.execute("""
                INSERT INTO gold_model_points (
                    model_point_id, tev_run_id, product_code,
                    plan_code, gender, smoker_status, risk_class,
                    issue_age_band, attained_age_band,
                    wtd_avg_attained_age, wtd_avg_issue_age, wtd_avg_duration,
                    duration_band,
                    is_plt_flag, premium_jump_ratio_band, is_ulsg_flag,
                    av_band, equity_allocation_band,
                    glwb_elected_flag, surrender_charge_yr_band, participating_flag,
                    policy_count, face_amount_total, reserve_total,
                    account_value_total, premium_total,
                    ci_rider_count, ci_rider_sa_total, required_capital,
                    _created_ts
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?
                )
            """, [
                str(row["model_point_id"]),
                str(row["tev_run_id"]),
                str(row["product_code"]),
                plan_code, gender, smoker_status, risk_class,
                issue_age_band, attained_age_band,
                float(row.get("wtd_avg_attained_age", 0) or 0),
                float(row.get("wtd_avg_issue_age", 0) or 0),
                float(row.get("wtd_avg_duration", 0) or 0),
                duration_band,
                is_plt_flag, premium_jump_band, is_ulsg_flag,
                av_band, equity_band,
                glwb_flag, sc_yr_band, participating,
                int(row.get("policy_count", 1) or 1),
                float(row.get("face_amount_total", 0) or 0),
                float(row.get("reserve_total", 0) or 0),
                float(row.get("account_value_total", 0) or 0),
                float(row.get("premium_total", 0) or 0),
                int(row.get("ci_rider_count", 0) or 0),
                float(row.get("ci_rider_sa_total", 0) or 0),
                float(row.get("required_capital", 0) or 0),
                row["_created_ts"],
            ])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ModelPointReconciliationError(Exception):
    """Raised when model point reconciliation exceeds 0.1% tolerance."""

    def __init__(self, product_code: str, count_diff_pct: float,
                 face_diff_pct: float, reserve_diff_pct: float):
        self.product_code = product_code
        self.count_diff_pct = count_diff_pct
        self.face_diff_pct = face_diff_pct
        self.reserve_diff_pct = reserve_diff_pct
        super().__init__(
            f"Model point reconciliation failed for {product_code}: "
            f"count={count_diff_pct:.4f}%, face={face_diff_pct:.4f}%, "
            f"reserve={reserve_diff_pct:.4f}%"
        )
