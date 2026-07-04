"""Exposure Summary — in-force reconciliation and exposure totals by year."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from ui.config import DB_PATH

st.set_page_config(page_title="Exposure Summary", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Exposure Summary")


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs ordered by run timestamp descending."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM gold_exposure_segments e
            LEFT JOIN gold_study_runs r ON r.run_id = e.study_run_id
            ORDER BY r.run_ts DESC NULLS LAST
            """
        ).fetchall()
        result = []
        for run_id, run_ts, product_codes in rows:
            if run_ts is not None:
                products = ", ".join(json.loads(product_codes)) if product_codes else "?"
                label = f"{str(run_ts)[:16]} — {products}"
            else:
                label = run_id
            result.append((run_id, label))
        return result
    finally:
        conn.close()


def _load_recon(run_id: str) -> pd.DataFrame:
    """Load in-force reconciliation rows for a study run."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT calendar_year, beg_if_count, new_issues_count,
                   deaths_count, lapses_count, surrenders_count,
                   other_decrements, end_if_count, recon_diff_count,
                   beg_if_amount, end_if_amount, recon_diff_amount, recon_passes
            FROM gold_inforce_reconciliation
            WHERE study_run_id = ?
            ORDER BY calendar_year
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


def _load_exposure_by_year(run_id: str) -> pd.DataFrame:
    """Load exposure aggregated by calendar year and product."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT calendar_year,
                   product_code,
                   SUM(exposure_years)                          AS exposure_years,
                   SUM(lapse_exposure_years)                    AS lapse_exposure_years,
                   COUNT(*)                                     AS segment_count,
                   SUM(face_amount_wtd_avg * exposure_years)    AS exposure_amount
            FROM gold_exposure_segments
            WHERE study_run_id = ?
            GROUP BY calendar_year, product_code
            ORDER BY calendar_year, product_code
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


def _load_exposure_by_policy_year(run_id: str) -> pd.DataFrame:
    """Load exposure aggregated by policy year and product, with Level/PLT split."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT policy_year,
                   product_code,
                   SUM(exposure_years) AS exposure_years,
                   SUM(CASE WHEN is_plt_flag THEN exposure_years ELSE 0 END)      AS plt_exposure_years,
                   SUM(CASE WHEN NOT is_plt_flag THEN exposure_years ELSE 0 END)  AS level_exposure_years,
                   COUNT(*) AS segment_count
            FROM gold_exposure_segments
            WHERE study_run_id = ? AND policy_year <= 35
            GROUP BY policy_year, product_code
            ORDER BY policy_year, product_code
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


def _load_exposure_totals(run_id: str) -> dict:
    """Return aggregate exposure summary metrics for the run."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        total_row = conn.execute(
            """
            SELECT COUNT(DISTINCT policy_id), SUM(exposure_years)
            FROM gold_exposure_segments
            WHERE study_run_id = ?
            """,
            [run_id],
        ).fetchone()
        dec_row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN decrement_type = 'DEATH'                    THEN 1 ELSE 0 END),
                SUM(CASE WHEN decrement_type IN ('LAPSE', 'PLT_LAPSE')    THEN 1 ELSE 0 END),
                SUM(CASE WHEN decrement_type = 'CI_CLAIM'                 THEN 1 ELSE 0 END)
            FROM gold_exposure_segments
            WHERE study_run_id = ? AND decrement_flag = TRUE
            """,
            [run_id],
        ).fetchone()
        return {
            "total_policies":       total_row[0] or 0,
            "total_exposure_years": total_row[1] or 0.0,
            "total_deaths":         dec_row[0] or 0,
            "total_lapses":         dec_row[1] or 0,
            "total_ci_claims":      dec_row[2] or 0,
        }
    finally:
        conn.close()


# ── Run selector ──────────────────────────────────────────────────────────────

run_ids = _load_run_ids()
if not run_ids:
    st.info("No exposure data found. Run a study from the Study Setup page.")
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

# ── In-force reconciliation ───────────────────────────────────────────────────

st.subheader("In-Force Reconciliation")
recon_df = _load_recon(selected_run)

if recon_df.empty:
    st.info("No reconciliation data found for this run.")
else:
    all_pass = recon_df["recon_passes"].all()
    if all_pass:
        st.success("In-force reconciliation PASSED for all years (diff = 0).")
    else:
        fail_yrs = recon_df[~recon_df["recon_passes"]]["calendar_year"].tolist()
        st.error(f"Reconciliation FAILED for years: {fail_yrs}")

    disp = recon_df.copy()
    disp["recon_passes"] = disp["recon_passes"].map({True: "✓", False: "✗"})
    disp = disp[
        ["calendar_year", "beg_if_count", "new_issues_count", "deaths_count",
         "lapses_count", "surrenders_count", "other_decrements", "end_if_count",
         "recon_diff_count", "beg_if_amount", "end_if_amount", "recon_passes"]
    ].rename(columns={
        "calendar_year":    "Year",
        "beg_if_count":     "Beg IF",
        "new_issues_count": "New",
        "deaths_count":     "Deaths",
        "lapses_count":     "Lapses",
        "surrenders_count": "Surrenders",
        "other_decrements": "Other",
        "end_if_count":     "End IF",
        "recon_diff_count": "Diff",
        "beg_if_amount":    "Beg Amt",
        "end_if_amount":    "End Amt",
        "recon_passes":     "Pass",
    })
    disp_fmt = disp.copy()
    disp_fmt["Beg Amt"] = disp_fmt["Beg Amt"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
    disp_fmt["End Amt"] = disp_fmt["End Amt"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
    st.dataframe(disp_fmt, use_container_width=True, hide_index=True)

# ── Product filter (shared by both exposure charts) ───────────────────────────

exp_yr_df   = _load_exposure_by_year(selected_run)
all_products = sorted(exp_yr_df["product_code"].unique().tolist()) if not exp_yr_df.empty else []

if len(all_products) > 1:
    selected_products = st.multiselect(
        "Filter by product",
        options=all_products,
        default=all_products,
    )
    if not selected_products:
        selected_products = all_products
else:
    selected_products = all_products

# ── Exposure by calendar year ─────────────────────────────────────────────────

st.subheader("Exposure Years by Calendar Year")
if not exp_yr_df.empty:
    filtered_yr = exp_yr_df[exp_yr_df["product_code"].isin(selected_products)]

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Exposure-Years",       f"{filtered_yr['exposure_years'].sum():,.1f}")
    col2.metric("Total Lapse Exposure-Years", f"{filtered_yr['lapse_exposure_years'].sum():,.1f}")
    col3.metric("Total Segments",             f"{filtered_yr['segment_count'].sum():,}")

    fig = px.bar(
        filtered_yr,
        x="calendar_year",
        y="exposure_years",
        color="product_code",
        barmode="stack",
        labels={
            "calendar_year":  "Calendar Year",
            "exposure_years": "Exposure Years",
            "product_code":   "Product",
        },
        title="Total Exposure Years by Calendar Year",
    )
    fig.update_layout(height=350)
    st.plotly_chart(fig, use_container_width=True)

# ── Exposure by policy year ───────────────────────────────────────────────────

st.subheader("Exposure Years by Policy Year (Level vs PLT)")
exp_py_df = _load_exposure_by_policy_year(selected_run)
if not exp_py_df.empty:
    filtered_py = exp_py_df[exp_py_df["product_code"].isin(selected_products)]

    # Single Term product selected → show Level vs PLT stacked split
    term_only = set(selected_products) == {"TERM"}
    if term_only:
        plot_df = filtered_py.melt(
            id_vars=["policy_year"],
            value_vars=["level_exposure_years", "plt_exposure_years"],
            var_name="Period",
            value_name="Exposure Years",
        ).replace({"level_exposure_years": "Level", "plt_exposure_years": "PLT"})
        fig2 = px.bar(
            plot_df,
            x="policy_year",
            y="Exposure Years",
            color="Period",
            barmode="stack",
            labels={"policy_year": "Policy Year"},
            title="Exposure Years by Policy Year (Level vs PLT — Term Life)",
            color_discrete_map={"Level": "#2980b9", "PLT": "#e74c3c"},
        )
    else:
        # Multi-product: stack by product_code
        agg = (
            filtered_py
            .groupby(["policy_year", "product_code"])["exposure_years"]
            .sum()
            .reset_index()
        )
        fig2 = px.bar(
            agg,
            x="policy_year",
            y="exposure_years",
            color="product_code",
            barmode="stack",
            labels={
                "policy_year":    "Policy Year",
                "exposure_years": "Exposure Years",
                "product_code":   "Product",
            },
            title="Exposure Years by Policy Year",
        )

    fig2.update_layout(height=350)
    st.plotly_chart(fig2, use_container_width=True)

# ── Total Exposure Summary ────────────────────────────────────────────────────

st.subheader("Total Exposure Summary")
totals = _load_exposure_totals(selected_run)
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Policies in Study",    f"{totals['total_policies']:,}")
col2.metric("Total Exposure-Years", f"{totals['total_exposure_years']:,.1f}")
col3.metric("Total Deaths",         f"{totals['total_deaths']:,}")
col4.metric("Total Lapses",         f"{totals['total_lapses']:,}")
col5.metric("Total CI Claims",      f"{totals['total_ci_claims']:,}")
