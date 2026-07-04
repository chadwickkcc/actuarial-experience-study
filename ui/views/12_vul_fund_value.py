"""VUL Fund Value Monitor — FR-1C-15.

Fund-value distribution by equity allocation band; fund_value_to_spec_amount_ratio time series.
"""
import json as _json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH

st.set_page_config(page_title="VUL Fund Value Monitor", layout="wide")

from ui.config import require_auth
require_auth()
st.title("VUL Fund Value Monitor")
st.caption("Separate account distribution by equity allocation band; fund value-to-spec amount ratio analysis.")


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for runs with VUL A/E data."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (
                SELECT DISTINCT study_run_id FROM gold_ae_results
                WHERE product_code = 'VUL'
            ) e
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
    except Exception:
        return []
    finally:
        conn.close()


def _load_ae_data(run_id: str) -> pd.DataFrame:
    """Load lapse and mortality A/E results for VUL from the gold layer."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT attained_age_band, duration_band, policy_year, calendar_year,
                   is_plt_flag,
                   SUM(actual_lapses)        AS actual_lapses,
                   SUM(expected_lapses)      AS expected_lapses,
                   SUM(lapse_exposure_count) AS lapse_exposure,
                   SUM(actual_deaths_count)  AS actual_deaths,
                   SUM(expected_deaths_count) AS expected_deaths,
                   SUM(exposure_count)       AS exposure_count
            FROM gold_ae_results
            WHERE study_run_id = ? AND product_code = 'VUL'
            GROUP BY 1,2,3,4,5
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_silver_vul(run_id: str) -> pd.DataFrame:
    """Load VUL policy snapshot from silver table, including sub-account allocation JSON."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT policy_id, issue_age_anb, gender, risk_class,
                   specified_amount, separate_account_total_value, fixed_account_value,
                   equity_allocation_pct, fund_value_to_spec_amount_ratio,
                   ma_charge_annual_rate, withdrawal_active_flag, withdrawal_rate_pct,
                   withdrawal_regime, surrender_charge_remaining,
                   account_value_bom, account_value_eom,
                   sub_account_allocations,
                   status_code, termination_date
            FROM silver_vul_policies
            WHERE _etl_run_id = ?
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


run_ids = _load_run_ids()
if not run_ids:
    st.info("No VUL A/E results found. Run the study pipeline with VUL product selected.")
    st.stop()

run_labels  = {r: lbl for r, lbl in run_ids}
run_id_list = [r for r, _ in run_ids]
run_id = st.selectbox("Study run", run_id_list, format_func=lambda r: run_labels.get(r, r))
ae_df = _load_ae_data(run_id)
silver_df = _load_silver_vul(run_id)

# --- Summary metrics ---
if not silver_df.empty:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total VUL policies", f"{len(silver_df):,}")
    with col2:
        avg_ratio = silver_df["fund_value_to_spec_amount_ratio"].dropna().mean()
        st.metric("Avg fund/spec ratio", f"{avg_ratio:.3f}" if not pd.isna(avg_ratio) else "N/A")
    with col3:
        withdrawal_pct = silver_df["withdrawal_active_flag"].mean()
        st.metric("Withdrawal-active policies", f"{withdrawal_pct:.1%}")
    with col4:
        total_sa = silver_df["separate_account_total_value"].sum()
        st.metric("Total SA value", f"${total_sa:,.0f}")

st.divider()

# --- Fund value distribution by equity allocation band ---
st.subheader("Separate Account Value by Equity Allocation Band")
st.info(
    "fund_value_to_spec_amount_ratio is computed as a point-in-time snapshot at the study run date. "
    "A per-calendar-year time series of this ratio is not available from the silver layer. "
    "See the 'VUL Lapse A/E by Calendar Year' chart below for the equity-return impact over time."
)

if not silver_df.empty:
    silver_df2 = silver_df.copy()
    silver_df2["equity_band"] = pd.cut(
        silver_df2["equity_allocation_pct"],
        bins=[-0.01, 0.25, 0.50, 0.75, 1.01],
        labels=["0-25% (Conservative)", "25-50% (Moderate)", "50-75% (Balanced)", "75-100% (Aggressive)"],
    )

    band_agg = (
        silver_df2.groupby("equity_band", observed=True)
        .agg(
            policy_count=("policy_id", "count"),
            total_sa=("separate_account_total_value", "sum"),
            avg_ratio=("fund_value_to_spec_amount_ratio", "mean"),
        )
        .reset_index()
    )

    col_bar, col_pie = st.columns(2)
    with col_bar:
        fig_sa = px.bar(
            band_agg,
            x="equity_band",
            y="total_sa",
            title="Total SA Value by Equity Band",
            labels={"equity_band": "Equity Allocation Band", "total_sa": "Total SA Value ($)"},
            color="equity_band",
            color_discrete_sequence=px.colors.sequential.Blues_r,
        )
        fig_sa.update_layout(showlegend=False, height=360)
        st.plotly_chart(fig_sa, use_container_width=True)

    with col_pie:
        fig_count = px.pie(
            band_agg,
            names="equity_band",
            values="policy_count",
            title="Policy Count by Equity Band",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_count.update_layout(height=360)
        st.plotly_chart(fig_count, use_container_width=True)

    fig_ratio = px.box(
        silver_df2.dropna(subset=["fund_value_to_spec_amount_ratio"]),
        x="equity_band",
        y="fund_value_to_spec_amount_ratio",
        title="Fund Value / Specified Amount Ratio Distribution by Equity Band",
        labels={
            "equity_band": "Equity Allocation Band",
            "fund_value_to_spec_amount_ratio": "Fund Value / Spec Amount Ratio",
        },
        color="equity_band",
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    fig_ratio.add_hline(y=1.0, line_dash="dash", line_color="red",
                        annotation_text="Ratio = 1.0 (fund = spec amount)")
    fig_ratio.update_layout(showlegend=False, height=380)
    st.plotly_chart(fig_ratio, use_container_width=True)

st.divider()

# --- Lapse A/E by calendar year (equity return time-series proxy) ---
st.subheader("VUL Lapse A/E by Calendar Year (Equity Return Impact)")
st.caption(
    "Elevated lapse A/E in 2022 indicates the −18% equity return is feeding through: "
    "policies with depleted fund values lapsed at higher-than-expected rates. "
    "Recovery in 2023 (+26% return) should be visible as a moderation in A/E."
)

if not ae_df.empty:
    yr_agg = (
        ae_df.groupby("calendar_year")
        .agg(actual=("actual_lapses", "sum"), expected=("expected_lapses", "sum"))
        .reset_index()
    )
    yr_agg["ae"] = yr_agg["actual"] / yr_agg["expected"].replace(0, float("nan"))

    fig_yr = go.Figure()
    fig_yr.add_bar(
        x=yr_agg["calendar_year"],
        y=yr_agg["ae"],
        name="Lapse A/E",
        marker_color=["red" if v > 1.0 else "steelblue" for v in yr_agg["ae"].fillna(0)],
        text=[f"{v:.1%}" if not pd.isna(v) else "" for v in yr_agg["ae"]],
        textposition="outside",
    )
    fig_yr.add_hline(y=1.0, line_dash="dot", line_color="grey", annotation_text="A/E = 1.0")
    fig_yr.update_traces(cliponaxis=False)
    fig_yr.update_layout(
        yaxis=dict(title="Lapse A/E ratio", tickformat=".0%",
                   range=[0, yr_agg["ae"].max() * 1.2 if not yr_agg.empty else 3.0]),
        xaxis=dict(title="Calendar Year"),
        height=360,
    )
    st.plotly_chart(fig_yr, use_container_width=True)

st.divider()

# --- FR-1C-03: Lapse moneyness multiplier ---
st.subheader("Lapse Moneyness Multiplier (FR-1C-03)")
st.caption(
    "Multiplier = min(2.0, max(0.5, 1 / fund_value_to_spec_amount_ratio)). "
    "Policies with ratio < 0.5 (fund well below spec amount) receive a 2× lapse load — highest lapse risk."
)

if not silver_df.empty and "fund_value_to_spec_amount_ratio" in silver_df.columns:
    mono_df = silver_df[["policy_id", "fund_value_to_spec_amount_ratio"]].dropna().copy()

    if not mono_df.empty:
        mono_df["mono_mult"] = mono_df["fund_value_to_spec_amount_ratio"].apply(
            lambda r: min(2.0, max(0.5, 1.0 / r)) if r > 0 else 1.0
        )
        mono_df["risk_tier"] = pd.cut(
            mono_df["fund_value_to_spec_amount_ratio"],
            bins=[0, 0.5, 2.0, float("inf")],
            labels=["High-risk (ratio < 0.5, mult = 2.0)", "Mid-range (0.5–2.0)", "Low-risk (ratio > 2.0, mult = 0.5)"],
        )

        high_risk = (mono_df["fund_value_to_spec_amount_ratio"] < 0.5).sum()
        low_risk  = (mono_df["fund_value_to_spec_amount_ratio"] > 2.0).sum()
        mid_range = len(mono_df) - high_risk - low_risk

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("High-lapse-risk (ratio < 0.5)", f"{high_risk:,}",
                   f"{high_risk / len(mono_df):.1%} of GLWB policies" if len(mono_df) else "")
        mc2.metric("Mid-range (0.5 ≤ ratio ≤ 2.0)", f"{mid_range:,}",
                   f"{mid_range / len(mono_df):.1%}")
        mc3.metric("Low-lapse-risk (ratio > 2.0)", f"{low_risk:,}",
                   f"{low_risk / len(mono_df):.1%}")

        col_hist, col_scatter = st.columns(2)
        with col_hist:
            fig_mult_hist = px.histogram(
                mono_df, x="mono_mult", nbins=20,
                title="Moneyness Multiplier Distribution",
                labels={"mono_mult": "Lapse Moneyness Multiplier"},
                color_discrete_sequence=["steelblue"],
            )
            fig_mult_hist.update_layout(height=340)
            st.plotly_chart(fig_mult_hist, use_container_width=True)

        with col_scatter:
            scatter_sample = mono_df.sample(min(500, len(mono_df)), random_state=42)
            fig_scatter = px.scatter(
                scatter_sample,
                x="fund_value_to_spec_amount_ratio",
                y="mono_mult",
                title="Ratio vs Multiplier (FR-1C-03 formula shape)",
                labels={
                    "fund_value_to_spec_amount_ratio": "Fund Value / Spec Amount Ratio",
                    "mono_mult": "Lapse Moneyness Multiplier",
                },
                color_discrete_sequence=["steelblue"],
                opacity=0.5,
            )
            fig_scatter.add_hline(y=1.0, line_dash="dash", line_color="grey")
            fig_scatter.update_layout(height=340)
            st.plotly_chart(fig_scatter, use_container_width=True)

st.divider()

# --- DQ-VUL-02: Sub-account reconciliation ---
st.subheader("DQ-VUL-02: Sub-Account Reconciliation")
st.caption(
    "separate_account_total_value must equal the sum of fund_value across sub_account_allocations "
    "within $1 rounding for every policy."
)

if not silver_df.empty and "sub_account_allocations" in silver_df.columns:
    dq_df = silver_df[["policy_id", "separate_account_total_value", "sub_account_allocations"]].copy()
    dq_df = dq_df.dropna(subset=["sub_account_allocations"])

    if not dq_df.empty:
        def _sum_fund_values(alloc_json: str) -> float:
            """Sum fund_value fields from sub_account_allocations JSON."""
            try:
                funds = _json.loads(alloc_json)
                return sum(f.get("fund_value", 0.0) for f in funds)
            except Exception:
                return float("nan")

        dq_df["sum_fund_values"] = dq_df["sub_account_allocations"].apply(_sum_fund_values)
        dq_df["discrepancy"] = dq_df["separate_account_total_value"] - dq_df["sum_fund_values"]
        dq_df["fail"] = dq_df["discrepancy"].abs() > 1.0

        fail_count = dq_df["fail"].sum()
        max_disc   = dq_df["discrepancy"].abs().max()
        pass_rate  = 1 - dq_df["fail"].mean()

        dq1, dq2, dq3 = st.columns(3)
        dq1.metric("Policies failing DQ-VUL-02", f"{int(fail_count):,}")
        dq2.metric("Max absolute discrepancy", f"${max_disc:,.2f}")
        dq3.metric("Pass rate", f"{pass_rate:.1%}")

        if fail_count == 0:
            st.success("DQ-VUL-02 PASS — all policies reconcile within $1.")
        else:
            st.error(f"DQ-VUL-02 FAIL — {int(fail_count)} policies have discrepancy > $1.")

        fig_dq = px.histogram(
            dq_df, x="discrepancy", nbins=30,
            title="Sub-Account Reconciliation Discrepancy Distribution",
            labels={"discrepancy": "Discrepancy (SA total − sum of fund values, $)"},
            color_discrete_sequence=["steelblue"],
        )
        fig_dq.add_vline(x=0, line_dash="dash", line_color="grey")
        fig_dq.update_layout(height=320)
        st.plotly_chart(fig_dq, use_container_width=True)
    else:
        st.info("No sub_account_allocations data available for this run.")
else:
    st.info("sub_account_allocations column not found in silver VUL data.")

st.divider()

# --- Withdrawal persistence analysis ---
st.subheader("Withdrawal Persistence Analysis")
st.caption("Policies with withdrawal_active_flag=True have entered a persistent high-withdrawal regime (FR-1C-04).")

if not silver_df.empty:
    wd_summary = (
        silver_df.groupby("withdrawal_active_flag")
        .agg(
            count=("policy_id", "count"),
            avg_ratio=("fund_value_to_spec_amount_ratio", "mean"),
            avg_equity=("equity_allocation_pct", "mean"),
            avg_wd_rate=("withdrawal_rate_pct", "mean"),
        )
        .reset_index()
    )
    wd_summary["label"] = wd_summary["withdrawal_active_flag"].map(
        {True: "Withdrawal Active", False: "No Withdrawal"}
    )

    st.dataframe(
        wd_summary[["label", "count", "avg_ratio", "avg_equity", "avg_wd_rate"]].rename(columns={
            "label": "Status",
            "count": "Policy Count",
            "avg_ratio": "Avg Fund/Spec Ratio",
            "avg_equity": "Avg Equity %",
            "avg_wd_rate": "Avg Withdrawal Rate",
        }),
        use_container_width=True,
    )

st.divider()

# --- Lapse A/E by withdrawal flag ---
st.subheader("Lapse A/E — Withdrawal Active vs Non-Active")
st.warning(
    "**Data limitation:** `is_plt_flag` in `gold_ae_results` is a portfolio-level flag designed "
    "for post-level-term (PLT) term products. For VUL policies it may not correctly capture "
    "`withdrawal_active_flag`, which is why the two A/E values appear nearly identical. "
    "A proper withdrawal-active A/E split requires `withdrawal_active_flag` to be added as "
    "a dimension in the gold aggregation layer. The metrics below are indicative only."
)
st.caption(
    "Expected behaviour: withdrawal-active policies should lapse at materially lower rates "
    "(they are receiving income withdrawals rather than surrendering), so their A/E should "
    "be well below 1.0 relative to the non-active cohort."
)

if not ae_df.empty:
    lapse_grp = (
        ae_df.groupby("is_plt_flag")
        .agg(actual=("actual_lapses", "sum"), expected=("expected_lapses", "sum"),
             exposure=("lapse_exposure", "sum"))
        .reset_index()
    )
    lapse_grp["ae"] = lapse_grp["actual"] / lapse_grp["expected"].replace(0, float("nan"))
    lapse_grp["label"] = lapse_grp["is_plt_flag"].map(
        {True: "Withdrawal Active (is_plt_flag=True)", False: "No Withdrawal (is_plt_flag=False)"}
    )

    col_a, col_b = st.columns(2)
    for _, row in lapse_grp.iterrows():
        col = col_b if row["is_plt_flag"] else col_a
        col.metric(
            row["label"],
            f"Lapse A/E = {row['ae']:.2%}" if not pd.isna(row["ae"]) else "Lapse A/E = N/A",
            f"{int(row['actual'])} lapses",
        )

# --- Raw data ---
with st.expander("Raw A/E data"):
    st.dataframe(ae_df, use_container_width=True)

if not silver_df.empty:
    with st.expander("Silver VUL policy data"):
        st.dataframe(silver_df.drop(columns=["sub_account_allocations"], errors="ignore"),
                     use_container_width=True)
