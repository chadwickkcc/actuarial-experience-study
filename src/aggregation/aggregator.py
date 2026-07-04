"""Aggregation layer for A/E experience study results."""

import hashlib
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import duckdb

logger = logging.getLogger(__name__)

# All valid measure columns in gold_ae_results
_VALID_MEASURES = {
    "ae_count", "ae_amount", "ae_lapse", "ae_ci", "ae_surrender",
    "actual_deaths_count", "expected_deaths_count",
    "actual_deaths_amount", "expected_deaths_amount",
    "actual_lapses", "expected_lapses",
    "actual_ci_claims", "expected_ci_claims",
    "actual_surrenders", "expected_surrenders",
    "exposure_count", "exposure_amount",
    "lapse_exposure_count", "ci_exposure_count",
    "credibility_z", "credibility_wtd_ae", "credibility_z_lapse", "credibility_z_ci",
    "ci_lower_count", "ci_upper_count", "ci_lower_amount", "ci_upper_amount",
    "ci_lower_lapse", "ci_upper_lapse", "ci_lower_ci", "ci_upper_ci",
    "se_ae_count", "se_ae_amount", "se_ae_lapse", "se_ae_ci",
}

# A/E ratio measures must be recomputed as SUM(numerator)/SUM(denominator),
# not averaged from pre-stored ratio values (which would be exposure-unweighted).
_RATIO_COMPONENTS: dict[str, tuple[str, str]] = {
    "ae_count":     ("actual_deaths_count",  "expected_deaths_count"),
    "ae_amount":    ("actual_deaths_amount", "expected_deaths_amount"),
    "ae_lapse":     ("actual_lapses",        "expected_lapses"),
    "ae_ci":        ("actual_ci_claims",     "expected_ci_claims"),
    "ae_surrender": ("actual_surrenders",    "expected_surrenders"),
}

# Dimensions available for pivoting
_VALID_DIMS = {
    "product_code", "plan_code", "gender", "smoker_status", "risk_class",
    "issue_age_band", "attained_age_band", "duration_band",
    "policy_year", "calendar_year", "is_plt_flag",
    "premium_jump_ratio_band", "distribution_channel", "illness_code",
}


def aggregate_ae(
    db_path: Path,
    study_run_id: str,
    row_dims: list[str],
    col_dims: list[str],
    filters: dict[str, list],
    measure: str = "ae_count",
) -> pd.DataFrame:
    """
    Aggregate gold_ae_results into a pivot table for UI display.

    Args:
        db_path:        DuckDB path
        study_run_id:   Run to aggregate
        row_dims:       Dimension columns for pivot rows
        col_dims:       Dimension columns for pivot columns
        filters:        Dict of dimension -> list of allowed values
        measure:        Column to aggregate

    Returns:
        DataFrame in pivot format with totals row/column appended.
    """
    if measure not in _VALID_MEASURES:
        raise ValueError(f"Unknown measure: {measure}. Valid: {sorted(_VALID_MEASURES)}")

    for d in row_dims + col_dims:
        if d not in _VALID_DIMS:
            raise ValueError(f"Unknown dimension: {d}. Valid: {sorted(_VALID_DIMS)}")

    all_dims = list(dict.fromkeys(row_dims + col_dims))  # preserve order, dedup

    # For A/E ratio measures we fetch both components and recompute SUM/SUM.
    # This is the only correct aggregation — averaging pre-stored ratios is
    # exposure-unweighted and gives wrong results.
    use_components = measure in _RATIO_COMPONENTS
    if use_components:
        num_col, den_col = _RATIO_COMPONENTS[measure]
        select_cols = all_dims + [num_col, den_col]
    else:
        select_cols = all_dims + [measure]
    select_sql = ", ".join(select_cols)

    # Build WHERE clause
    where_parts = ["study_run_id = ?", "illness_code IS NULL"]
    params: list = [study_run_id]
    for dim, values in (filters or {}).items():
        if not values:
            continue
        placeholders = ", ".join(["?" for _ in values])
        where_parts.append(f"{dim} IN ({placeholders})")
        params.extend(values)

    sql = (
        f"SELECT {select_sql} "
        f"FROM gold_ae_results "
        f"WHERE {' AND '.join(where_parts)}"
    )
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(sql, params).df()
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame()

    group_keys = [d for d in all_dims if d in df.columns]

    # Measures that should be averaged (not summed) when not using components
    mean_measures = {
        "credibility_z", "credibility_wtd_ae", "credibility_z_lapse", "credibility_z_ci",
        "ci_lower_count", "ci_upper_count", "ci_lower_amount", "ci_upper_amount",
        "ci_lower_lapse", "ci_upper_lapse", "ci_lower_ci", "ci_upper_ci",
        "se_ae_count", "se_ae_amount", "se_ae_lapse", "se_ae_ci",
    }

    if use_components:
        # Sum both components, then divide to get the correct aggregate ratio
        agg_df = df.groupby(group_keys, dropna=False)[[num_col, den_col]].sum().reset_index()
        agg_df[measure] = agg_df[num_col] / agg_df[den_col].replace(0, np.nan)
        agg_df = agg_df.drop(columns=[num_col, den_col])
    else:
        agg_func = "mean" if measure in mean_measures else "sum"
        agg_df = df.groupby(group_keys, dropna=False)[measure].agg(agg_func).reset_index()

    if not col_dims:
        agg_df.columns = [*group_keys, measure]
        # Totals row
        total_row: dict = {d: "Total" for d in group_keys}
        if use_components:
            total_num = df[num_col].sum()
            total_den = df[den_col].sum()
            total_row[measure] = total_num / total_den if total_den else np.nan
        elif measure in mean_measures:
            total_row[measure] = agg_df[measure].mean()
        else:
            total_row[measure] = agg_df[measure].sum()
        agg_df = pd.concat([agg_df, pd.DataFrame([total_row])], ignore_index=True)
        return agg_df

    # Pivot — use sum for components (already a ratio after division), mean for mean_measures
    pivot_agg = "sum" if use_components else ("mean" if measure in mean_measures else "sum")
    pivot = agg_df.pivot_table(
        index=row_dims if len(row_dims) > 1 else row_dims[0],
        columns=col_dims if len(col_dims) > 1 else col_dims[0],
        values=measure,
        aggfunc=pivot_agg,
    )

    # Totals column and row — for ratio measures recompute from totals per group
    if use_components:
        # Row totals: re-aggregate components per row_dim
        row_totals = (
            df.groupby([r for r in row_dims if r in df.columns], dropna=False)[[num_col, den_col]]
            .sum()
        )
        pivot["Total"] = (row_totals[num_col] / row_totals[den_col].replace(0, np.nan)).values
        # Column totals
        col_totals_num = df.groupby([c for c in col_dims if c in df.columns], dropna=False)[num_col].sum()
        col_totals_den = df.groupby([c for c in col_dims if c in df.columns], dropna=False)[den_col].sum()
        col_total_vals = col_totals_num / col_totals_den.replace(0, np.nan)
        col_total_vals["Total"] = df[num_col].sum() / (df[den_col].sum() or np.nan)
        total_row_series = col_total_vals.rename("Total")
    elif measure in mean_measures:
        pivot["Total"] = pivot.mean(axis=1)
        total_row_series = pivot.mean(axis=0)
        total_row_series.name = "Total"
    else:
        pivot["Total"] = pivot.sum(axis=1)
        total_row_series = pivot.sum(axis=0)
        total_row_series.name = "Total"

    pivot = pd.concat([pivot, total_row_series.to_frame().T])
    return pivot


def get_drill_through_records(
    db_path: Path,
    study_run_id: str,
    dimension_filter: dict[str, str],
    limit: int = 200,
) -> pd.DataFrame:
    """
    Return underlying seriatim exposure records for a specific cell.
    Masks policy_id to a SHA-256 hash. Returns up to `limit` records.

    Args:
        db_path:            DuckDB path
        study_run_id:       Study run to drill into
        dimension_filter:   Exact dimension values (e.g., {"gender": "M", "duration_band": "6-10"})
        limit:              Maximum records to return

    Returns:
        DataFrame of exposure segments with policy_id replaced by policy_hash.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        where_parts = ["study_run_id = ?"]
        params: list = [study_run_id]
        for dim, val in (dimension_filter or {}).items():
            where_parts.append(f"{dim} = ?")
            params.append(val)

        sql = (
            f"SELECT * FROM gold_exposure_segments "
            f"WHERE {' AND '.join(where_parts)} "
            f"LIMIT {int(limit)}"
        )
        df = con.execute(sql, params).df()
    finally:
        con.close()

    if df.empty:
        return df

    # Mask PII: replace policy_id with deterministic SHA-256 hash
    def _hash(pid: str) -> str:
        return hashlib.sha256(str(pid).encode()).hexdigest()[:12]

    df["policy_id"] = df["policy_id"].apply(_hash)

    # Also bin face amounts to bands to reduce PII risk
    if "face_amount_wtd_avg" in df.columns:
        df["face_amount_band"] = pd.cut(
            df["face_amount_wtd_avg"].fillna(0),
            bins=[0, 50_000, 100_000, 250_000, 500_000, 1_000_000, float("inf")],
            labels=["<50K", "50-100K", "100-250K", "250-500K", "500K-1M", ">1M"],
        )
        df = df.drop(columns=["face_amount_wtd_avg", "face_amount_start", "face_amount_end",
                               "face_amount_at_decrement"], errors="ignore")

    return df
