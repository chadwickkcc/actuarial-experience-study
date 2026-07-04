"""Lapse A/E Explorer — base lapse and PLT shock lapse by premium jump ratio band."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH
from ui.stats_helpers import credibility_z, get_run_method
from src.aggregation.aggregator import aggregate_ae

st.set_page_config(page_title="Lapse A/E Explorer", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Lapse A/E Explorer")

DIMS = [
    "product_code", "plan_code", "gender", "smoker_status", "risk_class",
    "issue_age_band", "attained_age_band", "duration_band",
    "policy_year", "calendar_year", "is_plt_flag", "premium_jump_ratio_band",
    "distribution_channel",
]

LAPSE_MEASURES = [
    "ae_lapse", "actual_lapses", "expected_lapses",
    "lapse_exposure_count", "credibility_z_lapse",
]

# Canonical sort orders for banded dimensions
DURATION_BAND_ORDER = ["1", "2-5", "6-10", "11-15", "16-20", "21-25", "26+"]
PLT_BAND_ORDER = ["<=2x", "2-3x", "3-5x", "5-8x", "8-12x", ">12x"]

# Path to lapse benchmark (for PLT tab expected calculation)
_LAPSE_BENCHMARK_PATH = Path(__file__).parent.parent.parent / "config/reference_tables/lapse_benchmarks.parquet"


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs ordered by run timestamp descending."""
    import json as _json
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (SELECT DISTINCT study_run_id FROM gold_ae_results) e
            LEFT JOIN gold_study_runs r ON r.run_id = e.study_run_id
            ORDER BY r.run_ts DESC NULLS LAST
            """
        ).fetchall()
        result = []
        for run_id, run_ts, product_codes in rows:
            if run_ts is not None:
                products = ", ".join(_json.loads(product_codes)) if product_codes else "?"
                label = f"{str(run_ts)[:16]} — {products}"
            else:
                label = run_id
            result.append((run_id, label))
        return result
    finally:
        conn.close()


def _load_filter_values(run_id: str, dim: str) -> list:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {dim} FROM gold_ae_results "
            f"WHERE study_run_id = ? AND illness_code IS NULL AND {dim} IS NOT NULL "
            f"ORDER BY {dim}",
            [run_id],
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _load_plt_ae(run_id: str) -> pd.DataFrame:
    """Load PLT shock lapse A/E by premium_jump_ratio_band for PLT year 1 only.

    Queries gold_exposure_segments directly so we can filter to plt_duration=1
    (the shock year). gold_ae_results aggregates all PLT years together because
    _MAIN_GROUP_DIMS does not include plt_duration, which would inflate A/E by
    mixing shock rates (30–88%) with lower continuing rates (8–15%).
    """
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        segs = conn.execute(
            """
            SELECT premium_jump_ratio_band,
                   COUNT(DISTINCT policy_id) AS initial_exposure,
                   SUM(CASE WHEN decrement_type = 'LAPSE' THEN 1 ELSE 0 END) AS actual_lapses
            FROM gold_exposure_segments
            WHERE study_run_id = ?
              AND is_plt_flag = TRUE
              AND plt_duration = 1
              AND premium_jump_ratio_band IS NOT NULL
            GROUP BY premium_jump_ratio_band
            """,
            [run_id],
        ).df()
        method = get_run_method(conn, run_id)
    finally:
        conn.close()

    if segs.empty:
        return segs

    # Join with benchmark to get shock lapse rate per band.
    # Expected lapses use INITIAL EXPOSURE (distinct policies at risk), not Balducci
    # fractional exposure. Balducci gives lapses fractional exposure (correct for mortality
    # as a competing decrement), but PLT benchmark rates are "X% of policies at risk
    # will lapse in year 1" — an initial-exposure concept.
    benchmark = pd.read_parquet(_LAPSE_BENCHMARK_PATH)
    shock_rates = (
        benchmark[benchmark["is_plt_flag"] & benchmark["plt_jump_band"].notna()]
        [["plt_jump_band", "lapse_rate"]]
        .rename(columns={"plt_jump_band": "premium_jump_ratio_band"})
    )
    segs = segs.merge(shock_rates, on="premium_jump_ratio_band", how="left")
    segs["expected_lapses"] = segs["initial_exposure"] * segs["lapse_rate"]
    segs["ae_lapse"] = segs["actual_lapses"] / segs["expected_lapses"].replace(0, float("nan"))
    # Poisson 95% CI on A/E: SE = A/E / sqrt(actual)
    segs["ae_ci_lower"] = (segs["ae_lapse"] - 1.96 * segs["ae_lapse"] / segs["actual_lapses"].clip(lower=1).apply(np.sqrt)).clip(lower=0)
    segs["ae_ci_upper"] = segs["ae_lapse"] + 1.96 * segs["ae_lapse"] / segs["actual_lapses"].clip(lower=1).apply(np.sqrt)
    segs["credibility_z"] = credibility_z(segs["actual_lapses"], method=method)
    # Absolute shock lapse rate observed (for context)
    segs["observed_shock_rate"] = segs["actual_lapses"] / segs["initial_exposure"].replace(0, float("nan"))

    # Apply canonical PLT band ordering
    segs["premium_jump_ratio_band"] = pd.Categorical(
        segs["premium_jump_ratio_band"], categories=PLT_BAND_ORDER, ordered=True
    )
    segs = segs.sort_values("premium_jump_ratio_band").reset_index(drop=True)
    return segs


# ── Study run selector (sidebar) ─────────────────────────────────────────────

with st.sidebar:
    st.header("Study Run")

    run_ids = _load_run_ids()
    if not run_ids:
        st.error("No A/E results found.")
        st.stop()

    run_labels  = {r: lbl for r, lbl in run_ids}
    run_id_list = [r for r, _ in run_ids]
    default_run = st.session_state.get("active_run_id", run_id_list[0])
    if default_run not in run_id_list:
        default_run = run_id_list[0]

    selected_run = st.selectbox(
        "Study run",
        options=run_id_list,
        index=run_id_list.index(default_run),
        format_func=lambda r: run_labels.get(r, r),
    )

# ── Controls expander (main area) ────────────────────────────────────────────

with st.expander("⚙️ Study Controls", expanded=True):
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

    with ctrl_col1:
        st.markdown("**Pivot Dimensions**")
        st.caption("→ Controls the pivot table and CI chart")
        row_dim = st.selectbox("Row dimension", options=DIMS, index=DIMS.index("duration_band"))
        col_dim = st.selectbox("Column dimension", options=["(none)"] + DIMS, index=0)
        col_dims = [] if col_dim == "(none)" else [col_dim]

    with ctrl_col2:
        st.markdown("**Display**")
        st.caption("→ Measure shown in pivot table")
        measure = st.selectbox(
            "Measure",
            options=LAPSE_MEASURES,
            format_func=lambda m: {
                "ae_lapse": "A/E Lapse",
                "actual_lapses": "Actual Lapses",
                "expected_lapses": "Expected Lapses",
                "lapse_exposure_count": "Lapse Exposure Years",
                "credibility_z_lapse": "Credibility Z (Lapse)",
            }[m],
        )
        show_ci = st.checkbox("Show confidence intervals", value=True)

    with ctrl_col3:
        st.markdown("**Filters**")
        st.caption("→ Restrict data included in all tabs")
        product_filter = st.multiselect(
            "Product", ["TERM", "WL", "UL", "ULSG", "IUL"],
            help="Leave empty to show all products.",
        )
        gender_filter = st.multiselect("Gender", _load_filter_values(selected_run, "gender"))
        plan_filter = st.multiselect("Plan code", _load_filter_values(selected_run, "plan_code"))
        cal_year_vals = sorted(_load_filter_values(selected_run, "calendar_year"))
        cal_year_filter = st.multiselect(
            "Calendar year",
            cal_year_vals,
            help="Filter to 2022–2023 to see the rising-rate dynamic lapse effect on UL/WL.",
        )

filters = {}
if product_filter:
    filters["product_code"] = product_filter
if gender_filter:
    filters["gender"] = gender_filter
if plan_filter:
    filters["plan_code"] = plan_filter
if cal_year_filter:
    filters["calendar_year"] = [int(y) for y in cal_year_filter]

# ── Tabs: Base Lapse, PLT Shock Lapse, Premium Persistency ───────────────────

tab_base, tab_plt, tab_persist = st.tabs(
    ["Base Lapse A/E", "PLT Shock Lapse", "Premium Persistency (UL)"]
)

# ── Base lapse tab ────────────────────────────────────────────────────────────

with tab_base:
    st.subheader("Base Lapse A/E Pivot Table")
    base_filters = dict(filters)
    base_filters["is_plt_flag"] = [False]

    try:
        pivot_df = aggregate_ae(
            db_path=DB_PATH,
            study_run_id=selected_run,
            row_dims=[row_dim],
            col_dims=col_dims,
            filters=base_filters,
            measure=measure,
        )

        if pivot_df.empty:
            st.info("No base lapse data for selected filters.")
        else:
            def _style_lapse(val):
                if not isinstance(val, (int, float)) or np.isnan(val):
                    return ""
                if measure == "ae_lapse":
                    if val < 0.85:
                        return "background-color: #a9dfbf"
                    elif val > 1.15:
                        return "background-color: #f1948a"
                if measure == "credibility_z_lapse" and val < 0.5:
                    return "color: #aaa; font-style: italic"
                return ""

            styled = pivot_df.style.map(_style_lapse)
            if measure == "ae_lapse":
                styled = styled.format(
                    lambda v: f"{v:.3f}" if isinstance(v, float) and not np.isnan(v) else str(v),
                    na_rep="—",
                )
            st.dataframe(styled, use_container_width=True)

    except Exception as exc:
        st.error(f"Could not load lapse pivot: {exc}")

    if show_ci:
        st.subheader("Lapse A/E with 95% CI")
        try:
            conn = duckdb.connect(str(DB_PATH), read_only=True)
            # Note: do NOT add "ae_lapse IS NOT NULL" here — individual cells often have
            # NULL ae_lapse (expected_lapses=0 at fine granularity) even though the
            # group-level SUM/SUM is non-NULL.  Pre-filtering would starve the aggregate.
            where_parts = [
                "study_run_id = ?", "illness_code IS NULL",
                "is_plt_flag = FALSE", f"{row_dim} IS NOT NULL",
            ]
            params: list = [selected_run]
            for dim, vals in filters.items():
                if vals:
                    placeholders = ", ".join(["?"] * len(vals))
                    where_parts.append(f"{dim} IN ({placeholders})")
                    params.extend(vals)

            ci_df = conn.execute(
                f"""
                SELECT {row_dim},
                       SUM(actual_lapses)   AS total_actual,
                       SUM(expected_lapses) AS total_expected,
                       CASE WHEN SUM(expected_lapses) > 0
                            THEN SUM(actual_lapses) / SUM(expected_lapses) END AS ae
                FROM gold_ae_results
                WHERE {' AND '.join(where_parts)}
                GROUP BY {row_dim}
                ORDER BY {row_dim}
                """,
                params,
            ).df()
            cred_method = get_run_method(conn, selected_run)
            conn.close()

            if not ci_df.empty:
                ci_df = ci_df.dropna(subset=["ae"])
                # Compute CI from aggregated totals — AVG of per-cell stored CI bounds
                # is wrong when most individual cells have NULL (zero actual lapses).
                safe_n = ci_df["total_actual"].clip(lower=1)
                ci_df["ci_lower"] = (ci_df["ae"] - 1.96 * ci_df["ae"] / np.sqrt(safe_n)).clip(lower=0)
                ci_df["ci_upper"] = ci_df["ae"] + 1.96 * ci_df["ae"] / np.sqrt(safe_n)
                ci_df["z"] = credibility_z(ci_df["total_actual"], method=cred_method)
                # Apply canonical ordering for banded dimensions
                if row_dim == "duration_band":
                    ci_df[row_dim] = pd.Categorical(
                        ci_df[row_dim], categories=DURATION_BAND_ORDER, ordered=True
                    )
                    ci_df = ci_df.sort_values(row_dim)
                elif row_dim == "premium_jump_ratio_band":
                    ci_df[row_dim] = pd.Categorical(
                        ci_df[row_dim], categories=PLT_BAND_ORDER, ordered=True
                    )
                    ci_df = ci_df.sort_values(row_dim)
                ci_df[row_dim] = ci_df[row_dim].astype(str)
                ci_df["colour"] = (ci_df["z"] < 0.5).map(
                    {True: "#aaa", False: "#27ae60"}
                )
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=ci_df[row_dim],
                    y=ci_df["ae"],
                    name="A/E Lapse",
                    marker_color=ci_df["colour"],
                    error_y=dict(
                        type="data", symmetric=False,
                        array=(ci_df["ci_upper"] - ci_df["ae"]).clip(lower=0).tolist(),
                        arrayminus=(ci_df["ae"] - ci_df["ci_lower"]).clip(lower=0).tolist(),
                        visible=True,
                    ),
                ))
                fig.add_hline(y=1.0, line_dash="dash", line_color="black")
                fig.update_layout(
                    title=f"Base Lapse A/E by {row_dim}",
                    xaxis_title=row_dim,
                    yaxis_title="Lapse A/E",
                    height=380,
                )
                st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.warning(f"CI chart skipped: {exc}")

# ── PLT shock lapse tab ───────────────────────────────────────────────────────

with tab_plt:
    st.subheader("PLT Shock Lapse A/E by Premium Jump Ratio Band")
    plt_df = _load_plt_ae(selected_run)

    if plt_df.empty:
        st.info("No PLT data found (policies may not yet have entered the PLT period).")
    else:
        col1, col2, col3 = st.columns(3)
        total_actual = int(plt_df["actual_lapses"].sum())
        total_expected = float(plt_df["expected_lapses"].sum())
        agg_ae = total_actual / total_expected if total_expected > 0 else float("nan")
        col1.metric("PLT Actual Lapses", f"{total_actual:,}")
        col2.metric("PLT Expected Lapses", f"{total_expected:,.1f}")
        col3.metric("PLT Aggregate A/E", f"{agg_ae:.3f}")

        disp = plt_df.copy()
        # Split the 95% CI into two numeric columns; CSV exports stay machine-parseable
        # and the UI can still display them compactly via column_config.
        disp["ci_lower"] = disp["ae_ci_lower"]
        disp["ci_upper"] = disp["ae_ci_upper"]
        # Store rates as percent values (e.g. 30.0 for 30%); column_config below
        # appends the % sign. CSV consumers get the numeric percent value.
        disp["lapse_rate"] = disp["lapse_rate"] * 100
        disp["observed_shock_rate"] = disp["observed_shock_rate"] * 100
        st.dataframe(
            disp.rename(columns={
                "premium_jump_ratio_band": "Jump Ratio Band",
                "initial_exposure": "Policies at Risk",
                "actual_lapses": "Actual Lapses",
                "expected_lapses": "Expected Lapses",
                "lapse_rate": "Benchmark Rate",
                "observed_shock_rate": "Observed Shock Rate",
                "ae_lapse": "A/E",
                "credibility_z": "Cred Z",
                "ci_lower": "95% CI Lower",
                "ci_upper": "95% CI Upper",
            }),
            use_container_width=True,
            hide_index=True,
            column_order=[
                "Jump Ratio Band", "Policies at Risk", "Benchmark Rate",
                "Observed Shock Rate", "Actual Lapses", "Expected Lapses", "A/E",
                "95% CI Lower", "95% CI Upper", "Cred Z",
            ],
            column_config={
                "Benchmark Rate": st.column_config.NumberColumn(format="%.1f%%"),
                "Observed Shock Rate": st.column_config.NumberColumn(format="%.1f%%"),
                "A/E": st.column_config.NumberColumn(format="%.3f"),
                "Cred Z": st.column_config.NumberColumn(format="%.2f"),
                "95% CI Lower": st.column_config.NumberColumn(format="%.2f"),
                "95% CI Upper": st.column_config.NumberColumn(format="%.2f"),
                "Expected Lapses": st.column_config.NumberColumn(format="%.1f"),
            },
        )

        # Bar chart with CI error bars
        plt_df_clean = plt_df.dropna(subset=["ae_lapse"])
        if not plt_df_clean.empty:
            bands = plt_df_clean["premium_jump_ratio_band"].astype(str).tolist()
            # Low-credibility bands (Z < 0.1) shown grey; within-spec blue; out-of-spec red
            def _plt_colour(row):
                if row["credibility_z"] < 0.1:
                    return "#aaaaaa"
                return "#e74c3c" if row["ae_lapse"] > 1.10 else ("#a9dfbf" if row["ae_lapse"] < 0.90 else "#2980b9")
            plt_df_clean = plt_df_clean.copy()
            plt_df_clean["colour"] = plt_df_clean.apply(_plt_colour, axis=1)
            fig_plt = go.Figure()
            fig_plt.add_trace(go.Bar(
                x=bands,
                y=plt_df_clean["ae_lapse"],
                marker_color=plt_df_clean["colour"],
                name="PLT A/E",
                error_y=dict(
                    type="data", symmetric=False,
                    array=(plt_df_clean["ae_ci_upper"] - plt_df_clean["ae_lapse"]).clip(lower=0).tolist(),
                    arrayminus=(plt_df_clean["ae_lapse"] - plt_df_clean["ae_ci_lower"]).clip(lower=0).tolist(),
                    visible=True,
                ),
            ))
            fig_plt.add_hline(y=1.0, line_dash="dash", line_color="black",
                               annotation_text="Expected = 1.00")
            fig_plt.add_hrect(y0=0.90, y1=1.10, fillcolor="#eafaf1",
                               opacity=0.3, line_width=0,
                               annotation_text="Spec range ±10%")
            fig_plt.update_layout(
                title="PLT Shock Lapse A/E by Premium Jump Ratio Band (Year 1 only) — grey bars = low credibility",
                xaxis_title="Premium Jump Ratio Band",
                yaxis_title="A/E Ratio",
                xaxis={"categoryorder": "array", "categoryarray": PLT_BAND_ORDER},
                height=380,
            )
            st.plotly_chart(fig_plt, use_container_width=True)

        st.caption(
            "Year 1 PLT shock lapse only. Expected basis: SOA 2021 PLT benchmark rates "
            "by premium jump ratio band. Target A/E range: 0.90–1.10. "
            "Benchmark rate (Benchmark Rate column) increases from 30% (≤2x) to 88% (>12x); "
            "this is the expected rate, not the A/E."
        )

# ── Premium Persistency tab (FR-1B-07 / FR-1B-15) ────────────────────────────

with tab_persist:
    # ── Section 1: Premium persistency ratio ─────────────────────────────────
    st.subheader("UL / IUL Premium Persistency Ratio by Duration Band")
    st.caption(
        "Average actual-to-planned premium ratio "
        "(cumulative_premiums_paid ÷ planned_premium × policy_year) for UL and IUL policies "
        "at study end (2023-12-31). Expected = 1.0 (policyholders pay their full planned "
        "premium). Declining ratios in later durations signal premium underfunding — "
        "a leading indicator of future lapse."
    )

    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        persist_df = conn.execute(
            """
            WITH latest AS (
                SELECT issue_date, premium_persistency_ratio,
                       CAST(date_diff('day', issue_date, DATE '2023-12-31') / 365.25
                            AS INTEGER) + 1 AS policy_year
                FROM silver_ul_policies
                WHERE product_code IN ('UL', 'IUL')
                  AND premium_persistency_ratio IS NOT NULL
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY policy_id ORDER BY _load_ts DESC
                ) = 1
            )
            SELECT
                CASE
                    WHEN policy_year <= 1  THEN '1'
                    WHEN policy_year <= 5  THEN '2-5'
                    WHEN policy_year <= 10 THEN '6-10'
                    WHEN policy_year <= 15 THEN '11-15'
                    WHEN policy_year <= 20 THEN '16-20'
                    WHEN policy_year <= 25 THEN '21-25'
                    ELSE '26+'
                END                                                        AS duration_band,
                COUNT(*)                                                    AS policy_count,
                AVG(premium_persistency_ratio)                              AS avg_ppr,
                PERCENTILE_CONT(0.25) WITHIN GROUP
                    (ORDER BY premium_persistency_ratio)                    AS p25,
                PERCENTILE_CONT(0.75) WITHIN GROUP
                    (ORDER BY premium_persistency_ratio)                    AS p75
            FROM latest
            GROUP BY 1
            """,
        ).df()
        conn.close()
    except Exception as exc:
        persist_df = pd.DataFrame()
        st.error(f"Premium persistency query failed: {exc}")

    if not persist_df.empty:
        persist_df["duration_band"] = pd.Categorical(
            persist_df["duration_band"], categories=DURATION_BAND_ORDER, ordered=True
        )
        persist_df = persist_df.sort_values("duration_band").reset_index(drop=True)

        col1, col2 = st.columns(2)
        with col1:
            st.dataframe(
                persist_df.rename(columns={
                    "duration_band": "Duration Band",
                    "policy_count":  "Policies",
                    "avg_ppr":       "Avg Ratio",
                    "p25":           "P25",
                    "p75":           "P75",
                }).style.format(
                    {"Avg Ratio": "{:.3f}", "P25": "{:.3f}", "P75": "{:.3f}"},
                    na_rep="—",
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("P25/P75 = interquartile range across policies in the band.")
        with col2:
            bands    = persist_df["duration_band"].astype(str).tolist()
            avg_vals = persist_df["avg_ppr"].tolist()
            p25_vals = persist_df["p25"].fillna(persist_df["avg_ppr"]).tolist()
            p75_vals = persist_df["p75"].fillna(persist_df["avg_ppr"]).tolist()
            colours  = [
                "#e74c3c" if (v or 0) < 0.70 else ("#f39c12" if (v or 0) < 0.85 else "#2980b9")
                for v in avg_vals
            ]
            fig_ppr = go.Figure()
            fig_ppr.add_trace(go.Bar(
                x=bands, y=avg_vals, name="Avg persistency ratio",
                marker_color=colours,
                error_y=dict(
                    type="data", symmetric=False,
                    array     =[max(0.0, p75 - avg) for p75, avg in zip(p75_vals, avg_vals)],
                    arrayminus=[max(0.0, avg - p25) for avg, p25 in zip(avg_vals, p25_vals)],
                    visible=True,
                ),
            ))
            fig_ppr.add_hline(y=1.0, line_dash="dash", line_color="black",
                              annotation_text="Expected = 1.0")
            fig_ppr.add_hline(y=0.85, line_dash="dot", line_color="#f39c12",
                              annotation_text="Watch level (0.85)")
            fig_ppr.update_layout(
                title="UL/IUL Premium Persistency Ratio by Duration Band",
                xaxis_title="Policy Duration Band",
                yaxis_title="Actual / Planned Premium Ratio",
                yaxis_range=[0, 1.3],
                xaxis={"categoryorder": "array", "categoryarray": DURATION_BAND_ORDER},
                height=360,
            )
            st.plotly_chart(fig_ppr, use_container_width=True)
        st.caption(
            "Blue = healthy (≥ 0.85) · Orange = watch (0.70–0.85) · Red = underfunding (< 0.70). "
            "Error bars = P25–P75 interquartile range."
        )
    else:
        st.info("No UL/IUL premium persistency data. Run the pipeline with UL products first.")

    # ── Section 2: Dynamic lapse A/E by calendar year (FR-1B-08) ─────────────
    st.divider()
    st.subheader("Dynamic Lapse A/E by Calendar Year — WL / UL / ULSG / IUL")
    _info_col, _filter_col = st.columns([3, 1])
    with _info_col:
        st.info(
            "Dynamic lapse multiplier (FR-1B-08): expected_lapses × min(2.5, max(0.4, 1 + 0.5×(mkt−crd))). "
            "A/E near 1.0 **in all years** is correct: when both actual and expected lapses are "
            "adjusted by the same multiplier, A/E stays near 1.0. The multiplier peaks at ×1.005 "
            "in 2022–2023 (0.5% uplift) — too small to be visually distinct from Poisson noise."
        )
    with _filter_col:
        _all_dyn_cal_years = sorted(_load_filter_values(selected_run, "calendar_year"))
        dyn_cal_year_filter = st.multiselect(
            "Filter calendar year",
            options=_all_dyn_cal_years,
            default=cal_year_filter or [],
            key="dyn_lapse_cal_year",
            help="Filter this chart to specific years. 2022–2023 = rising-rate regime.",
        )

    _dyn_prods = [
        p for p in (product_filter or ["WL", "UL", "ULSG", "IUL"])
        if p in ("WL", "UL", "ULSG", "IUL")
    ]
    if _dyn_prods:
        try:
            conn = duckdb.connect(str(DB_PATH), read_only=True)
            ph = ",".join(["?"] * len(_dyn_prods))
            dyn_where = [
                "study_run_id = ?", "illness_code IS NULL",
                "is_plt_flag = FALSE", "calendar_year IS NOT NULL",
                f"product_code IN ({ph})",
            ]
            dyn_params: list = [selected_run] + _dyn_prods
            if dyn_cal_year_filter:
                cyph = ",".join(["?"] * len(dyn_cal_year_filter))
                dyn_where.append(f"calendar_year IN ({cyph})")
                dyn_params.extend([int(y) for y in dyn_cal_year_filter])

            dyn_df = conn.execute(
                f"""
                SELECT calendar_year, product_code,
                       SUM(actual_lapses)   AS actual,
                       SUM(expected_lapses) AS expected,
                       CASE WHEN SUM(expected_lapses) > 0
                            THEN SUM(actual_lapses) / SUM(expected_lapses) END AS ae
                FROM gold_ae_results
                WHERE {' AND '.join(dyn_where)}
                GROUP BY calendar_year, product_code
                ORDER BY calendar_year, product_code
                """,
                dyn_params,
            ).df()
            conn.close()
        except Exception as exc:
            dyn_df = pd.DataFrame()
            st.warning(f"Dynamic lapse chart skipped: {exc}")

        if not dyn_df.empty:
            dyn_df = dyn_df.dropna(subset=["ae"])
            _PROD_COLOURS = {
                "WL": "#2980b9", "UL": "#27ae60", "ULSG": "#e67e22", "IUL": "#8e44ad",
            }
            fig_dyn = go.Figure()
            for prod in sorted(dyn_df["product_code"].unique()):
                sub = dyn_df[dyn_df["product_code"] == prod].sort_values("calendar_year")
                fig_dyn.add_trace(go.Scatter(
                    x=sub["calendar_year"].astype(str),
                    y=sub["ae"],
                    mode="lines+markers",
                    name=prod,
                    line=dict(color=_PROD_COLOURS.get(prod, "#555")),
                ))
            fig_dyn.add_hline(y=1.0, line_dash="dash", line_color="black",
                              annotation_text="A/E = 1.0")
            fig_dyn.update_layout(
                title="Base Lapse A/E by Calendar Year — Dynamic Lapse Effect (FR-1B-08)",
                xaxis_title="Calendar Year",
                yaxis_title="Lapse A/E (actual / expected)",
                height=360,
            )
            st.plotly_chart(fig_dyn, use_container_width=True)
            st.caption(
                "A/E near 1.0 across all years is the **correct outcome**: the same dynamic "
                "multiplier drives both actual lapses (in synthetic data) and expected lapses "
                "(in the engine), so A/E ≈ 1.0 by construction. The multiplier peaks at ×1.005 "
                "in 2022–2023 (0.5% uplift from a ~1% rate differential). Formula: "
                "min(2.5, max(0.4, 1 + 0.5 × (mkt_rate − crd_rate))), k=0.5 for life products."
            )
            with st.expander("Dynamic multiplier reference — macro scenario 2016–2023"):
                _mult_ref = pd.DataFrame({
                    "Year": [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023],
                    "Mkt Rate": ["1.8%", "2.4%", "2.9%", "1.9%", "0.9%", "1.5%", "3.9%", "4.0%"],
                    "Crd Rate": ["3.2%", "3.2%", "3.2%", "3.1%", "3.0%", "2.9%", "2.9%", "3.1%"],
                    "Rate Diff": ["−1.4%", "−0.8%", "−0.3%", "−1.2%", "−2.1%", "−1.4%", "+1.0%", "+0.9%"],
                    "Multiplier (k=0.5)": ["0.993", "0.996", "0.999", "0.994", "0.990", "0.993", "1.005", "1.005"],
                })
                st.dataframe(_mult_ref, hide_index=True, use_container_width=False)
        else:
            st.info("No lapse A/E data for the selected products/years.")
    else:
        st.info(
            "Select WL, UL, ULSG, or IUL in the Product filter (⚙️ Study Controls) "
            "to see the dynamic lapse chart."
        )
