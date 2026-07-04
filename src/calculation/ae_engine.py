"""A/E calculation engine for actuarial experience studies."""

import uuid
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import duckdb

from src.utils.types import AEResult, StudyConfig

logger = logging.getLogger(__name__)

# Map product codes to their lapse-benchmark "parent" for the lapse-rate join.
# DA sub-types share the "DA" benchmark; IUL (an indexed-UL variant, absent from
# the benchmark table) uses UL's lapse basis — without this, IUL's benchmark join
# misses, lapse_rate fills to 0, and IUL expected_lapses become 0.
_LAPSE_PARENT = {"DA_FIXED": "DA", "DA_FIA": "DA", "DA_VA": "DA", "IUL": "UL"}

# Canonical dimensions for grouping into gold_ae_results
_MAIN_GROUP_DIMS = [
    "product_code",
    "plan_code",
    "gender",
    "smoker_status",
    "risk_class",
    "issue_age_band",
    "attained_age_band",
    "duration_band",
    "policy_year",
    "calendar_year",
    "is_plt_flag",
    "premium_jump_ratio_band",
    "distribution_channel",
]

# Ordered columns matching gold_ae_results DDL
_GOLD_COLS = [
    "result_id",
    "study_run_id",
    "assumption_set_id",
    "product_code",
    "plan_code",
    "gender",
    "smoker_status",
    "risk_class",
    "issue_age_band",
    "attained_age_band",
    "duration_band",
    "policy_year",
    "calendar_year",
    "is_plt_flag",
    "premium_jump_ratio_band",
    "distribution_channel",
    "illness_code",
    "exposure_count",
    "exposure_amount",
    "actual_deaths_count",
    "actual_deaths_amount",
    "expected_deaths_count",
    "expected_deaths_amount",
    "ae_count",
    "ae_amount",
    "se_ae_count",
    "se_ae_amount",
    "ci_lower_count",
    "ci_upper_count",
    "ci_lower_amount",
    "ci_upper_amount",
    "credibility_z",
    "credibility_wtd_ae",
    "lapse_exposure_count",
    "actual_lapses",
    "expected_lapses",
    "ae_lapse",
    "se_ae_lapse",
    "ci_lower_lapse",
    "ci_upper_lapse",
    "credibility_z_lapse",
    "ci_exposure_count",
    "actual_ci_claims",
    "expected_ci_claims",
    "ae_ci",
    "se_ae_ci",
    "ci_lower_ci",
    "ci_upper_ci",
    "credibility_z_ci",
    "surrender_exposure",
    "actual_surrenders",
    "expected_surrenders",
    "ae_surrender",
    "anti_selection_flag",
    "_created_ts",
]

_Z95 = 1.96  # 95% CI z-value


def load_reference_table(table_path: str) -> pd.DataFrame:
    """
    Load a reference table (mortality, lapse, or CI incidence) from a Parquet or CSV file.
    Returns a DataFrame. Validates that required key columns are present.
    Raises ValueError if required columns are missing.

    All reference tables must be replaceable by pointing to a different file —
    this function is the single load point and must not assume any specific table name.
    """
    path = Path(table_path)
    if not path.exists():
        raise FileNotFoundError(f"Reference table not found: {path}")

    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix in (".csv", ".CSV"):
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported format: {path.suffix}. Use .parquet or .csv")

    if df.empty:
        raise ValueError(f"Reference table is empty: {path}")

    return df


def compute_credibility_z(
    actual_claims: float,
    method: str = "LF",
    threshold: float = 1082.0,
) -> float:
    """
    Compute credibility Z for a single cell or aggregate.

    LF (Limited Fluctuation):
        Z = min(1.0, sqrt(actual_claims / threshold))
    BUHLMANN (simplified fixed-K):
        Z = sqrt(actual_claims / (actual_claims + threshold))
        where ``threshold`` is reused as the Buhlmann credibility constant K.

    The method string is case-insensitive; any unrecognised value falls back to
    LF. Z is 0.0 when there are no claims (no divide-by-zero for either method).
    """
    n = float(actual_claims)
    if n <= 0:
        return 0.0
    if (method or "LF").strip().upper() == "BUHLMANN":
        return float(np.sqrt(n / (n + threshold)))
    return min(1.0, float(np.sqrt(n / threshold)))


def compute_poisson_ci(
    ae_ratio: float,
    actual_claims: float,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Compute Poisson confidence interval on A/E ratio.
    Returns (lower, upper) as A/E values.
    SE = ae_ratio / sqrt(actual_claims)
    CI = ae_ratio ± z_alpha * SE
    """
    if actual_claims <= 0 or np.isnan(ae_ratio):
        return (float("nan"), float("nan"))
    z = 1.96 if confidence >= 0.95 else 1.645
    se = ae_ratio / np.sqrt(actual_claims)
    return (ae_ratio - z * se, ae_ratio + z * se)


def _safe_divide(num: pd.Series, den: pd.Series) -> pd.Series:
    """Element-wise division returning NaN where denominator <= 0."""
    return pd.Series(
        np.where(den > 0, num / den, np.nan),
        index=num.index,
        dtype=float,
    )


def _vectorised_z(counts: pd.Series, method: str, threshold: float) -> pd.Series:
    """Vectorised credibility Z for a Series of actual counts (LF or BUHLMANN).

    LF:       Z = min(1, sqrt(n / threshold))
    BUHLMANN: Z = sqrt(n / (n + threshold))   (threshold reused as K)
    """
    arr = counts.astype(float).clip(lower=0)
    if (method or "LF").strip().upper() == "BUHLMANN":
        z = np.sqrt(arr / (arr + threshold))
    else:
        z = np.sqrt(arr / threshold).clip(upper=1.0)
    z = np.where(arr <= 0, 0.0, z)
    return pd.Series(z, index=counts.index, dtype=float)


def _add_stat_columns(
    df: pd.DataFrame,
    ae_col: str,
    n_col: str,
    suffix: str,
    threshold: float,
    method: str,
    complement: float = 1.0,
) -> pd.DataFrame:
    """
    Compute SE, 95% CI, and credibility Z for one decrement type.
    Adds: se_ae_{suffix}, ci_lower_{suffix}, ci_upper_{suffix},
          credibility_z_{suffix}, and (for mortality only) credibility_wtd_ae.
    """
    n = df[n_col].astype(float)
    ae = df[ae_col].astype(float)
    se = np.where((n > 0) & ~np.isnan(ae), ae / np.sqrt(n), np.nan)
    df[f"se_ae_{suffix}"] = se
    df[f"ci_lower_{suffix}"] = ae - _Z95 * se
    df[f"ci_upper_{suffix}"] = ae + _Z95 * se
    df[f"credibility_z_{suffix}"] = _vectorised_z(df[n_col], method, threshold)

    if suffix == "count":
        # Credibility-weighted A/E uses mortality Z
        z = df[f"credibility_z_{suffix}"]
        df["credibility_wtd_ae"] = np.where(
            ~np.isnan(ae),
            z * ae + (1 - z) * complement,
            np.nan,
        )
    return df


def _insert_ae_results(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
) -> None:
    """Insert a DataFrame of A/E results into gold_ae_results."""
    # Ensure all gold columns present; add missing as None
    for col in _GOLD_COLS:
        if col not in df.columns:
            df[col] = None

    # Only keep columns that exist in the gold_ae_results DDL (excludes anti_selection_flag)
    _DDL_COLS = [c for c in _GOLD_COLS if c != "anti_selection_flag"]
    subset = df[[c for c in _DDL_COLS if c in df.columns or c == "_created_ts"]].copy()
    for col in _DDL_COLS:
        if col not in subset.columns:
            subset[col] = None

    subset = subset[_DDL_COLS]

    # Cast integer columns explicitly
    for col in ("actual_deaths_count", "actual_lapses", "actual_ci_claims",
                "actual_surrenders"):
        if col in subset.columns:
            subset[col] = pd.array(
                subset[col].fillna(0).astype(int), dtype=pd.Int64Dtype()
            )

    col_list = ", ".join(_DDL_COLS)
    con.execute(f"INSERT INTO gold_ae_results ({col_list}) SELECT {col_list} FROM subset")
    logger.debug("Inserted %d rows into gold_ae_results", len(subset))


def _build_ci_illness_rows(
    exp_df: pd.DataFrame,
    ci_table: pd.DataFrame,
    ci_events_df: pd.DataFrame,
    study_run_id: str,
    study_config: StudyConfig,
    now_ts: datetime,
) -> pd.DataFrame:
    """
    Build gold_ae_results rows for CI A/E broken down by illness code.

    For each CI-rider exposure segment, expected CI per illness code =
    exposure_years × (incidence_rate_per_1000 / 1000).

    Actual CI claims per illness code come from silver_policy_events
    (via ci_events_df).  Records with NULL illness_code are treated as
    "unclassified" and excluded from the per-code breakdown.
    """
    ci_mask = exp_df["ci_rider_in_force_flag"] == True
    if ci_mask.sum() == 0:
        return pd.DataFrame()

    ci_segs = exp_df[ci_mask][
        ["gender", "attained_age_band", "product_code", "plan_code", "exposure_years"]
    ].copy()

    # Cross-join segments with illness code table on (gender, attained_age_band)
    expanded = ci_segs.merge(
        ci_table,
        on=["gender", "attained_age_band"],
        how="left",
    )
    expanded["expected_ci_by_code"] = (
        expanded["exposure_years"] * expanded["incidence_rate_per_1000"].fillna(0) / 1000.0
    )

    # Aggregate at (product_code, illness_code, gender, attained_age_band) so that
    # the UI can filter and chart by gender and age without re-querying exposure segments.
    # ci_events_df is pre-grouped on the same four keys so actual counts are correctly
    # matched without duplication.
    grp_dims = ["product_code", "illness_code", "gender", "attained_age_band"]
    agg = expanded.groupby(grp_dims, dropna=False).agg(
        ci_exposure_count=("exposure_years", "sum"),
        expected_ci_claims=("expected_ci_by_code", "sum"),
    ).reset_index()

    # Actual CI claims from event table, matched by (product_code, illness_code, gender, attained_age_band)
    if len(ci_events_df) > 0 and "illness_code" in ci_events_df.columns:
        valid_events = ci_events_df.dropna(subset=["illness_code"])
        actual_by_code = (
            valid_events.groupby(["product_code", "illness_code", "gender", "attained_age_band"])["cnt"]
            .sum()
            .reset_index()
            .rename(columns={"cnt": "actual_ci_claims"})
        )
        agg["illness_code"] = agg["illness_code"].astype(str)
        actual_by_code["illness_code"] = actual_by_code["illness_code"].astype(str)
        agg = agg.merge(actual_by_code, on=["product_code", "illness_code", "gender", "attained_age_band"], how="left")
    else:
        agg["actual_ci_claims"] = 0

    agg["actual_ci_claims"] = agg["actual_ci_claims"].fillna(0).astype(int)
    agg["ae_ci"] = _safe_divide(
        agg["actual_ci_claims"].astype(float),
        agg["expected_ci_claims"],
    )

    threshold = study_config.credibility_threshold
    method = study_config.credibility_method.value
    agg = _add_stat_columns(agg, "ae_ci", "actual_ci_claims", "ci", threshold, method)

    # Metadata and null-fill for non-CI columns
    agg["result_id"] = [str(uuid.uuid4()) for _ in range(len(agg))]
    agg["study_run_id"] = study_run_id
    agg["assumption_set_id"] = None
    agg["_created_ts"] = now_ts

    # Null out dimensional and mortality / lapse columns not in CI illness rows.
    # gender and attained_age_band are intentionally preserved — they are the
    # grouping keys that enable the UI heat map and gender filter to work.
    for col in [
        "plan_code", "smoker_status", "risk_class",
        "issue_age_band", "duration_band",
        "policy_year", "calendar_year",
        "is_plt_flag", "premium_jump_ratio_band", "distribution_channel",
        "exposure_count", "exposure_amount",
        "actual_deaths_count", "actual_deaths_amount",
        "expected_deaths_count", "expected_deaths_amount",
        "ae_count", "ae_amount", "se_ae_count", "se_ae_amount",
        "ci_lower_count", "ci_upper_count", "ci_lower_amount", "ci_upper_amount",
        "credibility_z", "credibility_wtd_ae",
        "lapse_exposure_count", "actual_lapses", "expected_lapses", "ae_lapse",
        "se_ae_lapse", "ci_lower_lapse", "ci_upper_lapse", "credibility_z_lapse",
        "surrender_exposure", "actual_surrenders", "expected_surrenders", "ae_surrender",
    ]:
        if col not in agg.columns:
            agg[col] = None

    # anti_selection_flag must be a non-null boolean
    if "anti_selection_flag" not in agg.columns:
        agg["anti_selection_flag"] = False
    else:
        agg["anti_selection_flag"] = agg["anti_selection_flag"].fillna(False)

    return agg


def calculate_ae(
    product_codes: list[str],
    db_path: Path,
    study_config: StudyConfig,
    study_run_id: str,
) -> AEResult:
    """
    Compute A/E ratios for all specified products.

    Steps:
        1. Load exposure segments from gold_exposure_segments
        2. Load reference tables (mortality, lapse, CI incidence) from paths in study_config
        3. Join exposure segments to reference tables on (gender, attained_age, policy_year, risk_class, product_code)
        4. Compute expected_deaths, expected_lapses, expected_ci_claims per segment
        5. Aggregate: sum actuals and expecteds by all canonical dimensions
        6. Compute A/E, SE, CI lower/upper, credibility Z, credibility-weighted A/E
        7. Write all results to gold_ae_results
        8. Return AEResult

    Reference table join keys:
        Mortality: (gender, smoker_status, risk_class, issue_age_anb, policy_year)
        Lapse:     (product_code, policy_year, premium_jump_ratio_band for PLT)
        CI:        (illness_code, gender, attained_age_band)

    Returns:
        AEResult
    """
    t0 = time.time()
    con = duckdb.connect(str(db_path))

    # Expand family codes: segments are stored with sub-type product codes
    # "UL" ETL loads IUL and ULSG from the same CSV; exposure writes their actual product_code
    # "DA" ETL loads DA_FIXED, DA_FIA, DA_VA from the same CSV; same pattern
    _UL_SUBTYPES = ["UL", "ULSG", "IUL"]
    _DA_SUBTYPES = ["DA_FIXED", "DA_FIA", "DA_VA"]
    expanded_codes: list[str] = []
    for pc in product_codes:
        if pc == "UL":
            expanded_codes.extend(_UL_SUBTYPES)
        elif pc == "DA":
            expanded_codes.extend(_DA_SUBTYPES)
        else:
            expanded_codes.append(pc)
    expanded_codes = list(dict.fromkeys(expanded_codes))  # deduplicate, preserve order

    try:
        # 1. Load exposure segments
        ph = ",".join(["?" for _ in expanded_codes])
        exp_df = con.execute(
            f"SELECT * FROM gold_exposure_segments "
            f"WHERE study_run_id = ? AND product_code IN ({ph})",
            [study_run_id] + expanded_codes,
        ).df()

        if exp_df.empty:
            raise ValueError(
                f"No exposure segments for run_id={study_run_id}, products={product_codes}"
            )

        logger.info("Loaded %d exposure segments for %s", len(exp_df), product_codes)

        # 2. Load reference tables
        mort_table = load_reference_table(study_config.mortality_table_path)
        lapse_table = load_reference_table(study_config.lapse_table_path)
        ci_table = load_reference_table(study_config.ci_table_path)

        # 3a. Mortality: join on (gender, smoker_status, risk_class, issue_age_anb, policy_year)
        _required_mort_cols = {"gender", "smoker_status", "risk_class", "issue_age_anb", "policy_year", "q_x"}
        _missing = _required_mort_cols - set(mort_table.columns)
        if _missing:
            raise ValueError(
                f"Mortality table '{study_config.mortality_table_path}' is missing required columns: {sorted(_missing)}. "
                "Select the 2015 VBT table (mortality_2015vbt.parquet) for life product studies."
            )
        exp_df = exp_df.merge(
            mort_table[["gender", "smoker_status", "risk_class", "issue_age_anb", "policy_year", "q_x"]],
            on=["gender", "smoker_status", "risk_class", "issue_age_anb", "policy_year"],
            how="left",
        )
        exp_df["q_x"] = exp_df["q_x"].fillna(0.0)

        # DA uses 2012 IAR with G2 improvement (FR-1C-12) — override q_x for DA segments
        _da_products = {"DA", "DA_FIXED", "DA_FIA", "DA_VA"}
        da_mort_mask = exp_df["product_code"].isin(_da_products)
        if da_mort_mask.any():
            # Derive IAR table path from VBT path
            _vbt_path = study_config.mortality_table_path
            _iar_path = _vbt_path.replace("2015vbt", "2012iar").replace("mortality_2015vbt", "mortality_2012iar")
            try:
                iar_table = load_reference_table(_iar_path)
                # IAR has (gender, issue_age_anb, policy_year, q_x) — no smoker/risk_class
                iar_join = iar_table[["gender", "issue_age_anb", "policy_year", "q_x"]].rename(
                    columns={"q_x": "q_x_iar"}
                )
                da_segs = exp_df[da_mort_mask].copy()
                da_segs = da_segs.drop(columns=["q_x"]).merge(
                    iar_join, on=["gender", "issue_age_anb", "policy_year"], how="left"
                )
                da_segs["q_x"] = da_segs["q_x_iar"].fillna(0.0)
                da_segs = da_segs.drop(columns=["q_x_iar"])
                exp_df = pd.concat(
                    [exp_df[~da_mort_mask], da_segs], ignore_index=True
                )
            except (FileNotFoundError, KeyError):
                logger.warning("2012 IAR table not found at %s; using VBT for DA mortality", _iar_path)

        exp_df["expected_deaths_count"] = exp_df["exposure_years"] * exp_df["q_x"]
        exp_df["expected_deaths_amount"] = exp_df["expected_deaths_count"] * exp_df["face_amount_wtd_avg"]
        exp_df["actual_deaths_count"] = (exp_df["decrement_type"] == "DEATH").astype(int)
        exp_df["actual_deaths_amount"] = np.where(
            exp_df["decrement_type"] == "DEATH",
            exp_df["face_amount_at_decrement"].fillna(exp_df["face_amount_wtd_avg"]),
            0.0,
        )

        # 3b. Lapse: split PLT vs non-PLT, then join each against appropriate reference rows.
        # lapse_table has columns: product_code, policy_year, lapse_rate, is_plt_flag, plt_jump_band
        # NOTE: is_plt_flag is repurposed for VUL (withdrawal_active) and DA (approaching_expiry).
        # Only TERM policies should use the PLT shock lapse path.
        _da_products = {"DA", "DA_FIXED", "DA_FIA", "DA_VA"}
        plt_mask = (
            exp_df["is_plt_flag"].fillna(False).astype(bool)
            & (exp_df["product_code"] == "TERM")
        )

        # Non-PLT: join on (product_code, policy_year) capped at max available year
        non_plt_tbl = lapse_table[~lapse_table["is_plt_flag"]][
            ["product_code", "policy_year", "lapse_rate"]
        ]
        # Normalize product codes to their lapse-benchmark parent (see _LAPSE_PARENT).
        max_non_plt_yr = non_plt_tbl.groupby("product_code")["policy_year"].max().to_dict()
        non_plt_df = exp_df[~plt_mask].copy()
        non_plt_df["_lapse_prod"] = non_plt_df["product_code"].map(_LAPSE_PARENT).fillna(non_plt_df["product_code"])
        non_plt_df["_lapse_yr"] = non_plt_df["policy_year"].clip(
            upper=non_plt_df["_lapse_prod"].map(max_non_plt_yr)
        )
        non_plt_df = non_plt_df.merge(
            non_plt_tbl.rename(columns={"policy_year": "_lapse_yr", "product_code": "_lapse_prod"}),
            on=["_lapse_prod", "_lapse_yr"], how="left",
        )

        # PLT year 1 shock: join on (product_code, premium_jump_ratio_band)
        plt_shock_tbl = lapse_table[
            lapse_table["is_plt_flag"] & lapse_table["plt_jump_band"].notna()
        ][["product_code", "plt_jump_band", "lapse_rate"]].rename(
            columns={"plt_jump_band": "premium_jump_ratio_band"}
        )

        # PLT continuing years (duration 2+): join on (product_code, plt_duration capped)
        plt_cont_tbl = lapse_table[
            lapse_table["is_plt_flag"] & lapse_table["plt_jump_band"].isna()
        ][["product_code", "policy_year", "lapse_rate"]]
        max_plt_cont_yr = plt_cont_tbl.groupby("product_code")["policy_year"].max().to_dict()

        plt_df = exp_df[plt_mask].copy()
        plt_yr1 = plt_df[plt_df["plt_duration"] == 1].copy()
        plt_yr1 = plt_yr1.merge(
            plt_shock_tbl, on=["product_code", "premium_jump_ratio_band"], how="left"
        )

        plt_yr2plus = plt_df[plt_df["plt_duration"] > 1].copy()
        plt_yr2plus["_plt_yr"] = plt_yr2plus["plt_duration"].clip(
            upper=plt_yr2plus["product_code"].map(max_plt_cont_yr)
        )
        plt_yr2plus = plt_yr2plus.merge(
            plt_cont_tbl.rename(columns={"policy_year": "_plt_yr"}),
            on=["product_code", "_plt_yr"], how="left",
        )

        exp_df = pd.concat([non_plt_df, plt_yr1, plt_yr2plus], ignore_index=True)
        exp_df["lapse_rate"] = exp_df["lapse_rate"].fillna(0.0)

        # Dynamic lapse multiplier for UL products (FR-1B-08)
        # min(2.5, max(0.4, 1 + k × (market_rate − credited_rate))), k=0.5
        _MACRO_MARKET_RATES = {
            2016: 0.018, 2017: 0.024, 2018: 0.029, 2019: 0.019,
            2020: 0.009, 2021: 0.015, 2022: 0.039, 2023: 0.040,
        }
        _MACRO_CREDITED_RATES = {
            2016: 0.032, 2017: 0.032, 2018: 0.032, 2019: 0.031,
            2020: 0.030, 2021: 0.029, 2022: 0.029, 2023: 0.031,
        }
        ul_mask = exp_df["product_code"].isin(["UL", "ULSG", "IUL"])
        if ul_mask.any():
            def _dyn_mult(row: pd.Series) -> float:
                yr = int(row["calendar_year"])
                mkt = _MACRO_MARKET_RATES.get(yr, 0.03)
                crd = _MACRO_CREDITED_RATES.get(yr, 0.03)
                return min(2.5, max(0.4, 1.0 + 0.5 * (mkt - crd)))
            exp_df.loc[ul_mask, "lapse_rate"] = (
                exp_df.loc[ul_mask, "lapse_rate"]
                * exp_df.loc[ul_mask].apply(_dyn_mult, axis=1)
            )

        # VUL moneyness multiplier (FR-1C-03): min(2.0, max(0.5, 1/ratio))
        vul_mask = exp_df["product_code"] == "VUL"
        if vul_mask.any() and "fund_value_to_spec_amount_ratio" in exp_df.columns:
            def _vul_mono_mult(ratio):
                if ratio is None or pd.isna(ratio) or ratio <= 0:
                    return 1.0
                return min(2.0, max(0.5, 1.0 / ratio))
            exp_df.loc[vul_mask, "lapse_rate"] = (
                exp_df.loc[vul_mask, "lapse_rate"]
                * exp_df.loc[vul_mask, "fund_value_to_spec_amount_ratio"].apply(_vul_mono_mult)
            )

        # DA: dynamic lapse with k=0.8, cap [0.3, 3.0] (FR-1C-10)
        da_mask = exp_df["product_code"].isin(_da_products)
        if da_mask.any():
            def _da_dyn_mult(row: pd.Series) -> float:
                yr = int(row["calendar_year"])
                mkt = _MACRO_MARKET_RATES.get(yr, 0.03)
                crd = _MACRO_CREDITED_RATES.get(yr, 0.03)
                return min(3.0, max(0.3, 1.0 + 0.8 * (mkt - crd)))
            exp_df.loc[da_mask, "lapse_rate"] = (
                exp_df.loc[da_mask, "lapse_rate"]
                * exp_df.loc[da_mask].apply(_da_dyn_mult, axis=1)
            )
            # GLB moneyness suppression (FR-1C-11): min(1.0, 0.4 + 0.6 × moneyness_ratio)
            # moneyness_ratio is stored in fund_value_to_spec_amount_ratio for DA
            if "fund_value_to_spec_amount_ratio" in exp_df.columns:
                def _glb_suppression(ratio):
                    if ratio is None or pd.isna(ratio):
                        return 1.0
                    return min(1.0, 0.4 + 0.6 * ratio)
                # Only suppress for GLB-elected contracts (moneyness_ratio not null)
                da_glb_mask = da_mask & exp_df["fund_value_to_spec_amount_ratio"].notna()
                if da_glb_mask.any():
                    exp_df.loc[da_glb_mask, "lapse_rate"] = (
                        exp_df.loc[da_glb_mask, "lapse_rate"]
                        * exp_df.loc[da_glb_mask, "fund_value_to_spec_amount_ratio"].apply(_glb_suppression)
                    )

        exp_df["expected_lapses"] = exp_df["lapse_exposure_years"] * exp_df["lapse_rate"]
        # WL lapse benchmark is a combined lapse+surrender rate (SOA/LIMRA WL Lapse/Surrender
        # Study). Per FR-1B-03 both lapse and surrender count as discontinuances for the lapse
        # A/E study. For all other products only LAPSE decrements count.
        _wl_mask = exp_df["product_code"] == "WL"
        exp_df["actual_lapses"] = np.where(
            _wl_mask,
            ((exp_df["decrement_type"] == "LAPSE") | (exp_df["decrement_type"] == "SURRENDER")).astype(int),
            (exp_df["decrement_type"] == "LAPSE").astype(int),
        )
        exp_df["actual_surrenders"] = (exp_df["decrement_type"] == "SURRENDER").astype(int)
        exp_df["expected_surrenders_seg"] = exp_df["lapse_exposure_years"] * exp_df["lapse_rate"]

        # 3c. CI incidence (aggregate across all illness codes)
        ci_agg = (
            ci_table.groupby(["gender", "attained_age_band"])["incidence_rate_per_1000"]
            .sum()
            .reset_index()
            .rename(columns={"incidence_rate_per_1000": "_total_ci_rate"})
        )
        exp_df = exp_df.merge(ci_agg, on=["gender", "attained_age_band"], how="left")
        exp_df["_total_ci_rate"] = exp_df["_total_ci_rate"].fillna(0.0)

        ci_in_force = exp_df["ci_rider_in_force_flag"] == True
        exp_df["ci_exposure_years"] = np.where(ci_in_force, exp_df["exposure_years"], 0.0)
        exp_df["expected_ci_claims"] = np.where(
            ci_in_force,
            exp_df["exposure_years"] * exp_df["_total_ci_rate"] / 1000.0,
            0.0,
        )
        # Actual CI from exposure segments — scoped to this run_id to avoid
        # double-counting when silver_policy_events contains multiple ETL runs.
        exp_df["actual_ci_seg"] = (exp_df["decrement_type"] == "CI_CLAIM").astype(int)

        # Build ci_events_df from segments (illness_code is present on CI_CLAIM segments).
        # Include gender and attained_age_band so per-illness rows retain those dimensions.
        ci_seg_counts = (
            exp_df[exp_df["decrement_type"] == "CI_CLAIM"]
            .groupby(["product_code", "illness_code", "gender", "attained_age_band"], dropna=False)
            .size()
            .reset_index(name="cnt")
        )
        ci_events_df = (
            ci_seg_counts.dropna(subset=["illness_code"])
            if len(ci_seg_counts) > 0
            else pd.DataFrame(columns=["product_code", "illness_code", "gender", "attained_age_band", "cnt"])
        )
        total_ci_from_events = int(ci_events_df["cnt"].sum()) if len(ci_events_df) > 0 else 0

        # 4. Aggregate to canonical dimensions
        exp_df["exposure_amount_seg"] = exp_df["exposure_years"] * exp_df["face_amount_wtd_avg"]

        agg = (
            exp_df.groupby(_MAIN_GROUP_DIMS, dropna=False)
            .agg(
                exposure_count=("exposure_years", "sum"),
                exposure_amount=("exposure_amount_seg", "sum"),
                actual_deaths_count=("actual_deaths_count", "sum"),
                actual_deaths_amount=("actual_deaths_amount", "sum"),
                expected_deaths_count=("expected_deaths_count", "sum"),
                expected_deaths_amount=("expected_deaths_amount", "sum"),
                lapse_exposure_count=("lapse_exposure_years", "sum"),
                actual_lapses=("actual_lapses", "sum"),
                expected_lapses=("expected_lapses", "sum"),
                actual_surrenders=("actual_surrenders", "sum"),
                expected_surrenders=("expected_surrenders_seg", "sum"),
                ci_exposure_count=("ci_exposure_years", "sum"),
                actual_ci_claims=("actual_ci_seg", "sum"),
                expected_ci_claims=("expected_ci_claims", "sum"),
            )
            .reset_index()
        )

        # 5. Compute A/E ratios
        agg["ae_count"] = _safe_divide(agg["actual_deaths_count"].astype(float), agg["expected_deaths_count"])
        agg["ae_amount"] = _safe_divide(agg["actual_deaths_amount"], agg["expected_deaths_amount"])
        agg["ae_lapse"] = _safe_divide(agg["actual_lapses"].astype(float), agg["expected_lapses"])
        agg["ae_ci"] = _safe_divide(agg["actual_ci_claims"].astype(float), agg["expected_ci_claims"])

        threshold = study_config.credibility_threshold
        method = study_config.credibility_method.value

        # SE, CI bands, credibility Z for mortality (count and amount share same N)
        agg = _add_stat_columns(agg, "ae_count", "actual_deaths_count", "count", threshold, method)
        # Gold schema uses 'credibility_z' (not 'credibility_z_count')
        agg = agg.rename(columns={"credibility_z_count": "credibility_z"})
        agg["se_ae_amount"] = np.where(
            (agg["actual_deaths_count"] > 0) & ~np.isnan(agg["ae_amount"].astype(float)),
            agg["ae_amount"].astype(float) / np.sqrt(agg["actual_deaths_count"].astype(float)),
            np.nan,
        )
        agg["ci_lower_amount"] = agg["ae_amount"].astype(float) - _Z95 * agg["se_ae_amount"]
        agg["ci_upper_amount"] = agg["ae_amount"].astype(float) + _Z95 * agg["se_ae_amount"]

        # Lapse stats
        agg = _add_stat_columns(agg, "ae_lapse", "actual_lapses", "lapse", threshold, method)

        # CI stats (actual_ci_claims from segments = 0 when CI_CLAIM policies are quarantined;
        # the aggregate CI A/E is computed from events table and stored in AEResult)
        agg = _add_stat_columns(agg, "ae_ci", "actual_ci_claims", "ci", threshold, method)

        # Surrender A/E (WL and UL; zero for TERM/PLT segments)
        agg["surrender_exposure"] = agg["lapse_exposure_count"]
        agg["ae_surrender"] = _safe_divide(
            agg["actual_surrenders"].astype(float),
            agg["expected_surrenders"],
        )

        # Anti-selection flag (FR-1B-10): lapse A/E > 150% in a UL cell
        agg["anti_selection_flag"] = (
            (agg["product_code"].isin(["UL", "ULSG", "IUL"]))
            & (agg["ae_lapse"] > 1.5)
        )

        # Metadata
        now_ts = datetime.utcnow()
        agg["result_id"] = [str(uuid.uuid4()) for _ in range(len(agg))]
        agg["study_run_id"] = study_run_id
        agg["assumption_set_id"] = None
        agg["illness_code"] = None
        agg["_created_ts"] = now_ts

        # 6. Write main results
        _insert_ae_results(con, agg)
        logger.info("Wrote %d A/E result rows to gold_ae_results", len(agg))

        # 6b. Write CI by illness-code rows
        ci_illness_rows = _build_ci_illness_rows(
            exp_df, ci_table, ci_events_df, study_run_id, study_config, now_ts
        )
        if len(ci_illness_rows) > 0:
            _insert_ae_results(con, ci_illness_rows)
            logger.info("Wrote %d CI-by-illness rows to gold_ae_results", len(ci_illness_rows))

        # 6c. Write study run record to gold_study_runs
        import json as _json
        duration_so_far = time.time() - t0
        con.execute(
            """
            INSERT OR REPLACE INTO gold_study_runs
                (run_id, run_ts, product_codes, study_start_date, study_end_date,
                 exposure_method, mortality_table, lapse_table, ci_table,
                 credibility_method, data_snapshot_hash, config_hash,
                 code_version, run_duration_sec, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                study_run_id,
                now_ts,
                _json.dumps(product_codes),
                study_config.study_start_date.isoformat(),
                study_config.study_end_date.isoformat(),
                study_config.exposure_method.value,
                study_config.mortality_table_path,
                study_config.lapse_table_path,
                study_config.ci_table_path,
                study_config.credibility_method.value,
                "synthetic",
                "v1",
                "1A",
                duration_so_far,
                "COMPLETE",
            ],
        )

        # 7. Aggregate statistics for return value
        total_exposure = float(agg["exposure_count"].sum())
        total_deaths = int(agg["actual_deaths_count"].sum())
        exp_deaths = float(agg["expected_deaths_count"].sum())
        total_ae_count = total_deaths / exp_deaths if exp_deaths > 0 else float("nan")

        total_deaths_amt = float(agg["actual_deaths_amount"].sum())
        exp_deaths_amt = float(agg["expected_deaths_amount"].sum())
        total_ae_amount = total_deaths_amt / exp_deaths_amt if exp_deaths_amt > 0 else float("nan")

        # Use event table for aggregate CI actual (FR-1A-23)
        total_exp_ci = float(agg["expected_ci_claims"].sum())
        total_ae_ci = total_ci_from_events / total_exp_ci if total_exp_ci > 0 else float("nan")

        return AEResult(
            run_id=study_run_id,
            products_included=product_codes,
            total_exposure=total_exposure,
            total_deaths=total_deaths,
            total_ae_count=total_ae_count,
            total_ae_amount=total_ae_amount,
            total_ci_claims=total_ci_from_events,
            total_ae_ci=total_ae_ci,
            results_df=agg,
            duration_sec=time.time() - t0,
        )

    finally:
        con.close()
