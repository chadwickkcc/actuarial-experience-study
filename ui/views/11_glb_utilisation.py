"""GLB Utilisation Monitor — FR-1C-14.

Moneyness ratio distribution; GLWB utilisation rate by attained age and duration.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH

st.set_page_config(page_title="GLB Utilisation Monitor", layout="wide")

from ui.config import require_auth
require_auth()
st.title("GLB Utilisation Monitor")
st.caption("GLWB moneyness distribution and utilisation rates for deferred annuity contracts.")

_DA_PRODUCTS = ["DA", "DA_FIXED", "DA_FIA", "DA_VA"]


def _load_run_ids() -> list[tuple[str, str, object]]:
    """Return (run_id, label, run_ts) triples for runs with annuity A/E data."""
    import json as _json
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (
                SELECT DISTINCT study_run_id FROM gold_ae_results
                WHERE product_code IN ('DA','DA_FIXED','DA_FIA','DA_VA')
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
            result.append((run_id, label, run_ts))
        return result
    except Exception:
        return []
    finally:
        conn.close()


def _load_ae_data(run_id: str) -> pd.DataFrame:
    """Load A/E results for DA products from the gold layer."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code, attained_age_band, duration_band, policy_year, calendar_year,
                   is_plt_flag,
                   SUM(actual_surrenders)   AS actual_surrenders,
                   SUM(expected_surrenders) AS expected_surrenders,
                   SUM(surrender_exposure)  AS surrender_exposure
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IN ('DA','DA_FIXED','DA_FIA','DA_VA')
            GROUP BY 1,2,3,4,5,6
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_silver_glb(run_id: str) -> pd.DataFrame:
    """Load GLB contract data from silver table, including issue_date for age/duration derivation."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code, issue_age_anb, issue_date, gender,
                   glwb_elected_flag, glwb_utilization_status,
                   moneyness_ratio, rider_fee_annual_rate,
                   surrender_charge_year,
                   account_value, benefit_base
            FROM silver_annuity_contracts
            WHERE _etl_run_id = ?
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


run_ids = _load_run_ids()
if not run_ids:
    st.info("No annuity A/E results found. Run the study pipeline with DA product selected.")
    st.stop()

run_labels  = {r: lbl for r, lbl, _ in run_ids}
run_ts_map  = {r: ts  for r, _, ts  in run_ids}
run_id_list = [r for r, _, _ in run_ids]
run_id = st.selectbox("Study run", run_id_list, format_func=lambda r: run_labels.get(r, r))
run_ts = run_ts_map.get(run_id)

ae_df = _load_ae_data(run_id)
silver_df = _load_silver_glb(run_id)

if ae_df.empty and silver_df.empty:
    st.warning("No DA data found for this run.")
    st.stop()

# --- Summary metrics ---
col1, col2, col3, col4 = st.columns(4)
if not silver_df.empty:
    glb_contracts = silver_df[silver_df["glwb_elected_flag"] == True]
    total_contracts = len(silver_df)
    glb_count = len(glb_contracts)
    active_util = (glb_contracts["glwb_utilization_status"] == "ACTIVE").sum()
    avg_moneyness = glb_contracts["moneyness_ratio"].dropna().mean()

    with col1:
        st.metric("Total DA contracts", f"{total_contracts:,}")
    with col2:
        st.metric("GLWB-elected", f"{glb_count:,}", f"{glb_count/total_contracts:.1%} of total" if total_contracts else "N/A")
    with col3:
        st.metric("Active GLWB withdrawals", f"{active_util:,}")
    with col4:
        st.metric("Avg moneyness ratio", f"{avg_moneyness:.3f}" if not pd.isna(avg_moneyness) else "N/A")

st.divider()

# --- Moneyness ratio distribution ---
st.subheader("Moneyness Ratio Distribution (GLWB Contracts)")
st.caption(
    "Moneyness = account value / benefit base. Ratio < 1 means benefit base > account value (ITM). "
    "Data is a point-in-time snapshot as of the study run date — no calendar-year filter is available on this chart."
)

if not silver_df.empty and "moneyness_ratio" in silver_df.columns:
    glb_df = silver_df[silver_df["glwb_elected_flag"] == True].copy()
    glb_df = glb_df.dropna(subset=["moneyness_ratio"])

    if not glb_df.empty:
        prod_filter = st.multiselect(
            "Filter by product",
            options=glb_df["product_code"].unique().tolist(),
            default=glb_df["product_code"].unique().tolist(),
            key="mon_prod_filter",
        )
        plot_df = glb_df[glb_df["product_code"].isin(prod_filter)] if prod_filter else glb_df

        fig_mon = px.histogram(
            plot_df,
            x="moneyness_ratio",
            color="product_code",
            nbins=30,
            title="Moneyness Ratio Distribution",
            labels={"moneyness_ratio": "Moneyness Ratio (AV / Benefit Base)", "count": "Contract Count"},
            barmode="overlay",
            opacity=0.75,
        )
        fig_mon.add_vline(x=1.0, line_dash="dash", line_color="red",
                          annotation_text="ITM / OTM boundary", annotation_position="top right")
        fig_mon.update_layout(height=380)
        st.plotly_chart(fig_mon, use_container_width=True)

        itm_pct = (plot_df["moneyness_ratio"] < 1.0).mean()
        st.caption(f"In-the-money (moneyness < 1.0): **{itm_pct:.1%}** of GLWB contracts")
        st.info(
            "A high ITM% is expected when benefit bases have grown faster than account values — "
            "e.g., a 5%/yr rollup compounding over policy duration, combined with negative equity return years "
            "in the study period. Use the raw data expander below to inspect individual contracts."
        )
    else:
        st.info("No GLWB contracts with moneyness data found.")
else:
    st.info("Silver annuity table not available for this run ID.")

st.divider()

# --- Utilisation rate by attained age and duration ---
st.subheader("GLWB Utilisation Rate by Attained Age Band and Duration Band")

if not silver_df.empty:
    glb_df2 = silver_df[silver_df["glwb_elected_flag"] == True].copy()

    if not glb_df2.empty:
        glb_df2["is_active"] = (glb_df2["glwb_utilization_status"] == "ACTIVE").astype(int)

        # Derive attained age and duration from issue_date and study run timestamp
        if "issue_date" in glb_df2.columns and run_ts is not None:
            ref_year = pd.Timestamp(run_ts).year
            glb_df2["issue_year"] = pd.to_datetime(glb_df2["issue_date"]).dt.year
            glb_df2["attained_age"] = glb_df2["issue_age_anb"] + (ref_year - glb_df2["issue_year"])
            glb_df2["duration_years"] = ref_year - glb_df2["issue_year"]
        else:
            glb_df2["attained_age"] = glb_df2["issue_age_anb"]
            glb_df2["duration_years"] = 0

        glb_df2["attained_age_band"] = pd.cut(
            glb_df2["attained_age"],
            bins=[39, 49, 54, 59, 64, 69, 74, 79, 84, 120],
            labels=["40-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80-84", "85+"],
        )

        glb_df2["duration_band"] = pd.cut(
            glb_df2["duration_years"],
            bins=[-1, 2, 5, 10, 15, 200],
            labels=["0-2", "3-5", "6-10", "11-15", "16+"],
        )

        # --- Attained age chart ---
        util_age = (
            glb_df2.groupby("attained_age_band", observed=True)
            .agg(total=("is_active", "count"), active=("is_active", "sum"))
            .reset_index()
        )
        util_age["util_rate"] = util_age["active"] / util_age["total"]

        max_age_rate = util_age["util_rate"].max() if not util_age.empty else 0.5
        fig_util_age = go.Figure()
        fig_util_age.add_bar(
            x=util_age["attained_age_band"],
            y=util_age["util_rate"],
            name="GLWB utilisation rate",
            marker_color="steelblue",
            text=[f"{v:.1%}" for v in util_age["util_rate"]],
            textposition="outside",
        )
        fig_util_age.update_traces(cliponaxis=False)
        fig_util_age.update_layout(
            title="GLWB Utilisation Rate by Attained Age Band",
            yaxis=dict(title="Utilisation Rate", tickformat=".0%", range=[0, max_age_rate * 1.2]),
            xaxis=dict(title="Attained Age Band"),
            height=360,
            showlegend=False,
        )
        st.plotly_chart(fig_util_age, use_container_width=True)

        # --- Duration band chart ---
        util_dur = (
            glb_df2.groupby("duration_band", observed=True)
            .agg(total=("is_active", "count"), active=("is_active", "sum"))
            .reset_index()
        )
        util_dur["util_rate"] = util_dur["active"] / util_dur["total"]

        max_dur_rate = util_dur["util_rate"].max() if not util_dur.empty else 0.5
        fig_util_dur = go.Figure()
        fig_util_dur.add_bar(
            x=util_dur["duration_band"],
            y=util_dur["util_rate"],
            name="GLWB utilisation rate",
            marker_color="steelblue",
            text=[f"{v:.1%}" for v in util_dur["util_rate"]],
            textposition="outside",
        )
        fig_util_dur.update_traces(cliponaxis=False)
        fig_util_dur.update_layout(
            title="GLWB Utilisation Rate by Duration Band",
            yaxis=dict(title="Utilisation Rate", tickformat=".0%", range=[0, max_dur_rate * 1.2]),
            xaxis=dict(title="Policy Duration (Years Since Issue)"),
            height=360,
            showlegend=False,
        )
        st.plotly_chart(fig_util_dur, use_container_width=True)

        # --- Status breakdown ---
        st.subheader("GLWB Status Breakdown")
        status_counts = glb_df2["glwb_utilization_status"].value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        fig_status = px.pie(status_counts, names="Status", values="Count",
                            title="GLWB Utilisation Status Distribution",
                            color_discrete_sequence=px.colors.qualitative.Set2)
        fig_status.update_layout(height=320)
        st.plotly_chart(fig_status, use_container_width=True)
    else:
        st.info("No GLWB-elected contracts found.")

st.divider()

# --- GLB suppression: ITM vs OTM comparison (FR-1C-11) ---
st.subheader("Surrender Behaviour by Moneyness Status (FR-1C-11 Suppression Check)")
st.caption(
    "ITM contracts (moneyness < 1) should surrender at lower rates when GLB suppression is active. "
    "A higher utilisation rate in the ITM cohort confirms policyholders are holding contracts to preserve the in-the-money guarantee."
)

if not silver_df.empty and "moneyness_ratio" in silver_df.columns:
    glb_sup = silver_df[silver_df["glwb_elected_flag"] == True].copy()
    glb_sup = glb_sup.dropna(subset=["moneyness_ratio"])

    if not glb_sup.empty:
        glb_sup["moneyness_status"] = glb_sup["moneyness_ratio"].apply(
            lambda x: "ITM (moneyness < 1)" if x < 1.0 else "OTM (moneyness ≥ 1)"
        )
        glb_sup["is_active"] = (glb_sup["glwb_utilization_status"] == "ACTIVE").astype(int)

        sup_agg = (
            glb_sup.groupby("moneyness_status")
            .agg(total=("is_active", "count"), active=("is_active", "sum"))
            .reset_index()
        )
        sup_agg["util_rate"] = sup_agg["active"] / sup_agg["total"]
        sup_agg["is_itm"] = sup_agg["moneyness_status"].str.startswith("ITM")

        c1, c2 = st.columns(2)
        for _, row in sup_agg.iterrows():
            col = c1 if row["is_itm"] else c2
            col.metric(
                row["moneyness_status"],
                f"Utilisation: {row['util_rate']:.1%}",
                f"{int(row['active'])} active / {int(row['total'])} contracts",
            )

        if st.checkbox("Show FR-1C-11 interpretation guide"):
            st.info(
                "**FR-1C-11 GLB Suppression Interpretation**\n\n"
                "If GLB suppression is correctly reflected in expected surrenders, the ITM cohort "
                "(moneyness < 1) should show materially lower A/E than the OTM cohort. "
                "A higher utilisation rate in the ITM group confirms policyholders are keeping contracts "
                "active to preserve their in-the-money guarantee.\n\n"
                "A full A/E comparison by moneyness tier requires adding `moneyness_band` as a dimension "
                "to the gold aggregation layer (`gold_ae_results`)."
            )
    else:
        st.info("No GLWB contracts with moneyness data available.")

st.divider()

# --- Surrender A/E by SC expiry status ---
st.subheader("Surrender A/E by GLB Status (from A/E results)")
st.caption("is_plt_flag=TRUE in A/E results represents contracts approaching SC expiry (shock-lapse cohort).")

if not ae_df.empty:
    glb_ae = (
        ae_df.groupby("is_plt_flag")
        .agg(actual=("actual_surrenders", "sum"), expected=("expected_surrenders", "sum"),
             exposure=("surrender_exposure", "sum"))
        .reset_index()
    )
    glb_ae["ae"] = glb_ae["actual"] / glb_ae["expected"].replace(0, float("nan"))
    glb_ae["label"] = glb_ae["is_plt_flag"].map({True: "Approaching SC Expiry", False: "Base (not expiring)"})

    c1, c2 = st.columns(2)
    for _, row in glb_ae.iterrows():
        col = c1 if not row["is_plt_flag"] else c2
        col.metric(
            row["label"],
            f"A/E = {row['ae']:.2%}" if not pd.isna(row["ae"]) else "A/E = N/A",
            f"{int(row['actual'])} surrenders / {row['exposure']:.0f} exposure years",
        )

# --- Raw data ---
with st.expander("Raw A/E data"):
    st.dataframe(ae_df, use_container_width=True)

if not silver_df.empty:
    with st.expander("Silver contract data (GLWB only)"):
        glb_show = silver_df[silver_df["glwb_elected_flag"] == True] if "glwb_elected_flag" in silver_df.columns else silver_df
        st.dataframe(glb_show, use_container_width=True)
