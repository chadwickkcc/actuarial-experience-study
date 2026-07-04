"""UL Account Value Monitor — FR-1B-13.

Time-series of average account value grouped by a user-selected dimension
(attained age band, policy year, or product code), overlaid with the credited
interest rate series from the macro scenario.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from ui.config import DB_PATH

st.set_page_config(page_title="UL Account Value Monitor", layout="wide")

from ui.config import require_auth
require_auth()
st.title("UL Account Value Monitor")
st.caption("Average account value grouped by a selected dimension, overlaid with macro credited rate series.")

# Macro scenario credited rates (from requirements spec Section 9.4)
_MACRO_CREDITED = {
    2016: 0.032, 2017: 0.032, 2018: 0.032, 2019: 0.031,
    2020: 0.030, 2021: 0.029, 2022: 0.029, 2023: 0.031,
}
_MACRO_MARKET = {
    2016: 0.018, 2017: 0.024, 2018: 0.029, 2019: 0.019,
    2020: 0.009, 2021: 0.015, 2022: 0.039, 2023: 0.040,
}

_GROUP_LABEL = {
    "attained_age_band": "Attained Age Band",
    "policy_year": "Policy Year",
    "product_code": "Product Code",
}


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for runs containing UL exposure data."""
    import json as _json
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (
                SELECT DISTINCT study_run_id FROM gold_exposure_segments
                WHERE product_code IN ('UL', 'ULSG', 'IUL')
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
    finally:
        conn.close()


def _load_av_grouped(run_id: str, products: list[str], group_col: str) -> pd.DataFrame:
    """Average account_value per calendar_year, grouped by group_col."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        prod_ph = ", ".join(["?"] * len(products))
        return conn.execute(
            f"""
            SELECT calendar_year,
                   {group_col} AS group_label,
                   AVG(account_value)        AS avg_account_value,
                   SUM(exposure_years)       AS exposure_years,
                   COUNT(DISTINCT policy_id) AS policy_count
            FROM gold_exposure_segments
            WHERE study_run_id = ?
              AND product_code IN ({prod_ph})
              AND account_value IS NOT NULL
              AND account_value > 0
            GROUP BY calendar_year, {group_col}
            ORDER BY calendar_year, {group_col}
            """,
            [run_id] + products,
        ).df()
    finally:
        conn.close()


def _load_av_summary(run_id: str, products: list[str]) -> pd.DataFrame:
    """Overall average account_value per calendar_year."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        prod_ph = ", ".join(["?"] * len(products))
        return conn.execute(
            f"""
            SELECT calendar_year,
                   AVG(account_value)        AS avg_account_value,
                   SUM(account_value)        AS total_account_value,
                   COUNT(DISTINCT policy_id) AS policy_count
            FROM gold_exposure_segments
            WHERE study_run_id = ?
              AND product_code IN ({prod_ph})
              AND account_value IS NOT NULL
              AND account_value > 0
            GROUP BY calendar_year
            ORDER BY calendar_year
            """,
            [run_id] + products,
        ).df()
    finally:
        conn.close()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Controls")
    run_ids = _load_run_ids()
    if not run_ids:
        st.error("No UL exposure segments found. Run the pipeline with UL product first.")
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

# ── Chart controls ────────────────────────────────────────────────────────────

ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1, 1, 2])

with ctrl_col1:
    product_filter = st.multiselect(
        "UL product variant",
        options=["UL", "ULSG", "IUL"],
        default=["UL", "ULSG", "IUL"],
    )
    if not product_filter:
        product_filter = ["UL", "ULSG", "IUL"]

with ctrl_col2:
    group_by = st.radio(
        "View by",
        options=["attained_age_band", "policy_year", "product_code"],
        format_func=_GROUP_LABEL.get,
        horizontal=True,
    )

sub_filter: list[str] = []
with ctrl_col3:
    if group_by == "attained_age_band":
        sub_filter = st.multiselect(
            "Filter age bands (leave empty = all)",
            options=["30-34", "35-39", "40-44", "45-49", "50-54",
                     "55-59", "60-64", "65-69", "70-74", "75-79", "80+"],
        )
    elif group_by == "policy_year":
        sub_filter = [
            str(v) for v in st.multiselect(
                "Filter policy years (leave empty = all)",
                options=list(range(1, 21)),
            )
        ]

# ── Summary metrics ───────────────────────────────────────────────────────────

summary_df = _load_av_summary(selected_run, product_filter)

if summary_df.empty:
    st.warning("No account value data found for this run.")
    st.stop()

latest = summary_df.iloc[-1]
col1, col2, col3 = st.columns(3)
col1.metric("In-Force Policies (latest year)", f"{int(latest['policy_count']):,}")
col2.metric("Avg Account Value (latest)", f"${latest['avg_account_value']:,.0f}")
col3.metric("Total Account Value (latest)", f"${latest['total_account_value']:,.0f}")

# ── Main chart ────────────────────────────────────────────────────────────────

prod_str = " / ".join(product_filter)
chart_title = f"UL Avg Account Value by {_GROUP_LABEL[group_by]} — {prod_str}"
st.subheader(chart_title)

av_df = _load_av_grouped(selected_run, product_filter, group_by)

# Apply sub-filter when the user selected specific values
if sub_filter:
    av_df = av_df[av_df["group_label"].astype(str).isin(sub_filter)]

if av_df.empty:
    st.info("No data for selected filters.")
else:
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    groups = sorted(av_df["group_label"].dropna().unique(), key=lambda v: (str(v).zfill(10)))
    colours = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a",
    ]
    for i, grp in enumerate(groups):
        grp_df = av_df[av_df["group_label"] == grp].sort_values("calendar_year")
        fig.add_trace(
            go.Scatter(
                x=grp_df["calendar_year"],
                y=grp_df["avg_account_value"],
                name=str(grp),
                mode="lines+markers",
                line=dict(color=colours[i % len(colours)]),
            ),
            secondary_y=False,
        )

    # Overlay credited rate (red dashed, clearly visible)
    macro_years = sorted(_MACRO_CREDITED.keys())
    fig.add_trace(
        go.Scatter(
            x=macro_years,
            y=[_MACRO_CREDITED[y] * 100 for y in macro_years],
            name="Credited rate (%)",
            mode="lines+markers",
            line=dict(color="#e74c3c", dash="dash", width=3),
            marker=dict(symbol="diamond"),
        ),
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=macro_years,
            y=[_MACRO_MARKET[y] * 100 for y in macro_years],
            name="Market rate (%)",
            mode="lines",
            line=dict(color="grey", dash="dot", width=1),
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=chart_title,
        xaxis_title="Calendar Year",
        height=500,
        margin=dict(b=140),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Average Account Value ($)", secondary_y=False)
    fig.update_yaxes(title_text="Rate (%)", secondary_y=True)

    st.plotly_chart(fig, use_container_width=True)

# ── Aggregate time series table ───────────────────────────────────────────────

st.subheader("Aggregate by Calendar Year")
disp = summary_df.copy()
disp["credited_rate_pct"] = disp["calendar_year"].map(
    lambda y: _MACRO_CREDITED.get(y, 0) * 100
)
disp["market_rate_pct"] = disp["calendar_year"].map(
    lambda y: _MACRO_MARKET.get(y, 0) * 100
)
disp_renamed = disp.rename(columns={
    "calendar_year": "Year",
    "avg_account_value": "Avg AV",
    "total_account_value": "Total AV",
    "policy_count": "Policies",
    "credited_rate_pct": "Credited Rate",
    "market_rate_pct": "Market Rate",
})
disp_renamed["Avg AV"] = disp_renamed["Avg AV"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
disp_renamed["Total AV"] = disp_renamed["Total AV"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
st.dataframe(
    disp_renamed,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Credited Rate": st.column_config.NumberColumn(format="%.1f%%"),
        "Market Rate": st.column_config.NumberColumn(format="%.1f%%"),
    },
)

st.caption(
    f"Products shown: {prod_str}. "
    "Account value sourced from gold_exposure_segments.account_value "
    "(= account_value_eom from silver_ul_policies). "
    "Credited and market rates from macro scenario (requirements spec Section 9.4)."
)
