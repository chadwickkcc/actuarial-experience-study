"""CI Incidence Summary — FR-1C-17.

Aggregate CI A/E across all products with CI riders; breakdown by illness code;
heat map by attained age and illness type.
"""
import json as _json
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

st.set_page_config(page_title="CI Incidence Summary", layout="wide")

from ui.config import require_auth
require_auth()
st.title("CI Incidence Summary")
st.caption(
    "Aggregate CI A/E across all life products with CI riders (Term, WL, UL, VUL). "
    "Breakdown by illness code and attained age band."
)

_CI_PRODUCTS = ["TERM", "WL", "UL", "ULSG", "IUL", "VUL"]
_ILLNESS_NAMES = {
    "CI-001": "Malignant cancer",
    "CI-002": "Myocardial infarction",
    "CI-003": "Stroke",
    "CI-004": "Coronary artery bypass",
    "CI-005": "Kidney failure",
    "CI-006": "Major organ transplant",
    "CI-007": "Multiple sclerosis",
    "CI-008": "Paralysis / paraplegia",
    "CI-009": "Blindness",
    "CI-010": "Deafness",
}
# Section 8.4 illness weights
_CI_WEIGHTS = {
    "CI-001": 0.40, "CI-002": 0.20, "CI-003": 0.12, "CI-004": 0.07,
    "CI-005": 0.05, "CI-006": 0.04, "CI-007": 0.03, "CI-008": 0.03,
    "CI-009": 0.03, "CI-010": 0.03,
}
_BUHLMANN_K = 1082  # credibility parameter per FR-1A-25


def _load_run_ids() -> list:
    """Return (run_id, label) pairs for runs with life product CI data."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (
                SELECT DISTINCT study_run_id FROM gold_ae_results
                WHERE product_code IN ('TERM','WL','UL','ULSG','IUL','VUL')
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


def _load_ci_by_illness(run_id: str) -> pd.DataFrame:
    """Load CI exposure/claims by product, illness code, age band, and gender."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code, illness_code,
                   attained_age_band, gender,
                   SUM(ci_exposure_count)  AS ci_exposure,
                   SUM(actual_ci_claims)   AS actual_ci,
                   SUM(expected_ci_claims) AS expected_ci
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IN ('TERM','WL','UL','ULSG','IUL','VUL')
              AND illness_code IS NOT NULL
            GROUP BY 1,2,3,4
            ORDER BY 1,2
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_ci_aggregate(run_id: str) -> pd.DataFrame:
    """Load product-level CI totals."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code,
                   SUM(ci_exposure_count)  AS ci_exposure,
                   SUM(actual_ci_claims)   AS actual_ci,
                   SUM(expected_ci_claims) AS expected_ci
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IN ('TERM','WL','UL','ULSG','IUL','VUL')
              AND illness_code IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()



def _add_ci_cred(df: pd.DataFrame, ae_col: str, actual_col: str, method: str = "LF") -> pd.DataFrame:
    """Add 95% Poisson CI bounds and credibility Z (FR-1A-24, FR-1A-25).

    ``method`` ("LF" or "BUHLMANN") selects the credibility form via the shared
    ``stats_helpers.credibility_z`` helper, so this page honours the run's
    configured method instead of unconditionally applying Bühlmann.
    """
    ae = df[ae_col]
    n  = df[actual_col].clip(lower=1)
    se = ae / n.pow(0.5)
    df[f"{ae_col}_ci_lo"]  = (ae - 1.96 * se).clip(lower=0)
    df[f"{ae_col}_ci_hi"]  = ae + 1.96 * se
    df[f"{ae_col}_cred_z"] = credibility_z(n, method=method, threshold=_BUHLMANN_K)
    return df


# ── Run selector ──────────────────────────────────────────────────────────────
run_ids = _load_run_ids()
if not run_ids:
    st.info("No life product A/E results found. Run the study pipeline with life products selected.")
    st.stop()

run_labels  = {r: lbl for r, lbl in run_ids}
run_id_list = [r for r, _ in run_ids]
run_id = st.selectbox("Study run", run_id_list, format_func=lambda r: run_labels.get(r, r))
ci_illness_df = _load_ci_by_illness(run_id)
ci_agg_df = _load_ci_aggregate(run_id)

_mconn = duckdb.connect(str(DB_PATH), read_only=True)
try:
    run_method = get_run_method(_mconn, run_id)
finally:
    _mconn.close()

if ci_illness_df.empty and ci_agg_df.empty:
    st.warning("No CI incidence data found for this run.")
    st.stop()

# ── Aggregate CI metrics ───────────────────────────────────────────────────────
st.subheader("Aggregate CI Incidence — All Life Products")

if not ci_agg_df.empty:
    ci_agg_df["ae_ci"] = (
        ci_agg_df["actual_ci"] / ci_agg_df["expected_ci"].replace(0, float("nan"))
    )
    total_actual   = ci_agg_df["actual_ci"].sum()
    total_expected = ci_agg_df["expected_ci"].sum()
    total_ae = total_actual / total_expected if total_expected > 0 else float("nan")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total CI claims (actual)",   f"{int(total_actual):,}")
    col2.metric("Total CI claims (expected)", f"{total_expected:,.1f}")
    col3.metric("Aggregate CI A/E",           f"{total_ae:.2%}" if not pd.isna(total_ae) else "N/A")
    col4.metric("Products with CI data",      f"{ci_agg_df[ci_agg_df['actual_ci'] > 0].shape[0]}")

    # Product bar chart with credibility error bars
    prod_plot = _add_ci_cred(ci_agg_df.copy(), "ae_ci", "actual_ci", method=run_method)
    ae_vals = prod_plot["ae_ci"].tolist()
    cred_z  = prod_plot["ae_ci_cred_z"].tolist()
    ci_hi   = prod_plot["ae_ci_ci_hi"].tolist()
    ci_lo   = prod_plot["ae_ci_ci_lo"].tolist()
    colors  = [
        "lightgrey" if z < 0.5 else ("red" if v > 1.0 else "steelblue")
        for z, v in zip(cred_z, ae_vals)
    ]
    fig_prod = go.Figure()
    fig_prod.add_bar(
        x=prod_plot["product_code"],
        y=ae_vals,
        marker_color=colors,
        text=[f"{v:.2%}" if not pd.isna(v) else "N/A" for v in ae_vals],
        textposition="outside",
        error_y=dict(
            type="data",
            array=[hi - v for hi, v in zip(ci_hi, ae_vals)],
            arrayminus=[v - lo for lo, v in zip(ci_lo, ae_vals)],
            visible=True,
            color="rgba(255,255,255,0.5)",
        ),
    )
    fig_prod.add_hline(y=1.0, line_dash="dot", line_color="grey", annotation_text="A/E = 1.0")
    max_ci = max(ci_hi + [1.2]) if ci_hi else 2.0
    fig_prod.update_traces(cliponaxis=False)
    fig_prod.update_layout(
        yaxis=dict(title="CI A/E ratio", tickformat=".0%", range=[0, max_ci * 1.1]),
        xaxis=dict(title="Product"),
        title="CI Incidence A/E by Product",
        height=400,
    )
    st.plotly_chart(fig_prod, use_container_width=True)
    st.caption(
        "Section 8.6 target (0.90–1.10) applies to the aggregate, not individual products. "
        "Grey bars: credibility Z < 0.5 — outliers driven by small claim volumes, not model error. "
        "Error bars show 95% Poisson CI (FR-1A-25)."
    )

st.divider()

# ── CI A/E by illness code ─────────────────────────────────────────────────────
st.subheader("CI A/E by Illness Code")

if not ci_illness_df.empty:
    prod_opts = ci_illness_df["product_code"].unique().tolist()
    sel_prods = st.multiselect(
        "Filter by product",
        options=prod_opts,
        default=prod_opts,
        key="ci_prod_filter",
    )
    filtered = ci_illness_df[ci_illness_df["product_code"].isin(sel_prods)] if sel_prods else ci_illness_df

    illness_agg = (
        filtered.groupby("illness_code")
        .agg(actual=("actual_ci", "sum"), expected=("expected_ci", "sum"),
             exposure=("ci_exposure", "sum"))
        .reset_index()
    )
    illness_agg["ae_ci"] = illness_agg["actual"] / illness_agg["expected"].replace(0, float("nan"))
    illness_agg["illness_name"] = illness_agg["illness_code"].map(_ILLNESS_NAMES).fillna(illness_agg["illness_code"])
    illness_agg = illness_agg.sort_values("actual", ascending=False)

    # Dual-axis chart
    fig_illness = go.Figure()
    fig_illness.add_bar(
        x=illness_agg["illness_name"],
        y=illness_agg["actual"],
        name="Actual CI claims",
        marker_color="steelblue",
        yaxis="y",
    )
    fig_illness.add_scatter(
        x=illness_agg["illness_name"],
        y=illness_agg["ae_ci"],
        name="CI A/E ratio",
        yaxis="y2",
        line=dict(color="red", width=2),
        mode="lines+markers",
    )
    fig_illness.update_layout(
        yaxis=dict(title="CI Claim Count"),
        yaxis2=dict(overlaying="y", side="right", title="CI A/E ratio", tickformat=".0%"),
        xaxis=dict(title="Illness"),
        legend=dict(x=0.01, y=0.99),
        height=380,
        title="CI Claims and A/E by Illness Code",
    )
    st.plotly_chart(fig_illness, use_container_width=True)

    # Compact 3-column illness grid
    st.markdown("**Illness breakdown**")
    grid_rows = [illness_agg.iloc[i:i+3] for i in range(0, len(illness_agg), 3)]
    for row_slice in grid_rows:
        cols = st.columns(3)
        for col, (_, row) in zip(cols, row_slice.iterrows()):
            ae_str = f"{row['ae_ci']:.1%}" if not pd.isna(row["ae_ci"]) else "N/A"
            col.markdown(f"**{row['illness_name']}** `{row['illness_code']}`")
            col.caption(f"Actual: {int(row['actual'])}  ·  A/E: {ae_str}")

st.divider()

# ── Heat map: attained age × illness code ──────────────────────────────────────
st.subheader("CI Incidence A/E Heat Map — Attained Age × Illness Code")
st.caption(
    "Expected pattern: Malignant cancer (CI-001) peaks at ages 50–65. "
    "MI (CI-002) and stroke (CI-003) intensify at ages 65+. "
    "Blindness (CI-009) and deafness (CI-010) should be sparse (3% weight each). "
    "Age bands with no observed claims are suppressed (FR-1A-29)."
)

if not ci_illness_df.empty and "attained_age_band" in ci_illness_df.columns:
    filtered2 = ci_illness_df[ci_illness_df["product_code"].isin(sel_prods)] if sel_prods else ci_illness_df

    heatmap_agg = (
        filtered2.groupby(["attained_age_band", "illness_code"])
        .agg(actual=("actual_ci", "sum"), expected=("expected_ci", "sum"))
        .reset_index()
    )
    # Suppress age bands with no actual claims (Fix 3)
    heatmap_agg = heatmap_agg[heatmap_agg["actual"] > 0]
    heatmap_agg["ae_ci"] = heatmap_agg["actual"] / heatmap_agg["expected"].replace(0, float("nan"))
    heatmap_agg["illness_name"] = heatmap_agg["illness_code"].map(_ILLNESS_NAMES).fillna(heatmap_agg["illness_code"])

    if not heatmap_agg.empty:
        pivot = heatmap_agg.pivot_table(
            index="attained_age_band",
            columns="illness_name",
            values="ae_ci",
            aggfunc="mean",
        )

        if not pivot.empty:
            age_order = sorted(
                pivot.index.tolist(),
                key=lambda x: int(x.split("-")[0]) if "-" in x else 99,
            )
            pivot = pivot.loc[[a for a in age_order if a in pivot.index]]

            fig_heat = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=pivot.columns.tolist(),
                y=pivot.index.tolist(),
                colorscale=[
                    [0.0, "green"],
                    [0.5, "white"],
                    [1.0, "red"],
                ],
                zmid=1.0,
                text=[[f"{v:.2%}" if not np.isnan(v) else "0.00%" for v in row] for row in pivot.values],
                texttemplate="%{text}",
                colorbar=dict(title="CI A/E"),
            ))
            fig_heat.update_layout(
                title="CI A/E Ratio by Attained Age Band and Illness Type",
                xaxis=dict(title="Illness Type"),
                yaxis=dict(title="Attained Age Band"),
                height=max(320, len(pivot.index) * 45 + 80),
            )
            st.plotly_chart(fig_heat, use_container_width=True)
            st.caption(f"Showing {len(pivot.index)} age bands with observed claims (out of 17 in the study).")
    else:
        st.info("No CI claims with illness codes found for the selected products.")

st.divider()

# ── Gender breakdown ──────────────────────────────────────────────────────────
st.subheader("CI A/E by Gender")

if not ci_illness_df.empty and "gender" in ci_illness_df.columns:
    gender_agg = (
        ci_illness_df[ci_illness_df["product_code"].isin(sel_prods) if sel_prods else [True] * len(ci_illness_df)]
        .groupby("gender")
        .agg(actual=("actual_ci", "sum"), expected=("expected_ci", "sum"))
        .reset_index()
    )
    gender_agg["ae_ci"] = gender_agg["actual"] / gender_agg["expected"].replace(0, float("nan"))
    gender_agg["gender_label"] = gender_agg["gender"].map({"M": "Male", "F": "Female", "U": "Unknown"})

    max_g = gender_agg["ae_ci"].max()
    fig_gender = go.Figure()
    fig_gender.add_bar(
        x=gender_agg["gender_label"],
        y=gender_agg["ae_ci"],
        marker_color=["steelblue" if g == "M" else "coral" for g in gender_agg["gender"]],
        text=[f"{v:.2%}" if not pd.isna(v) else "N/A" for v in gender_agg["ae_ci"]],
        textposition="outside",
    )
    fig_gender.add_hline(y=1.0, line_dash="dot", line_color="grey")
    fig_gender.update_traces(cliponaxis=False)
    fig_gender.update_layout(
        yaxis=dict(title="CI A/E ratio", tickformat=".0%", range=[0, (max_g or 1.5) * 1.2]),
        xaxis=dict(title="Gender"),
        height=320,
        showlegend=False,
    )
    st.plotly_chart(fig_gender, use_container_width=True)

# ── Raw data ──────────────────────────────────────────────────────────────────
with st.expander("Raw CI by illness code"):
    if not ci_illness_df.empty:
        show = ci_illness_df.copy()
        show["illness_name"] = show["illness_code"].map(_ILLNESS_NAMES).fillna(show["illness_code"])
        show["ae_ci"] = show["actual_ci"] / show["expected_ci"].replace(0, float("nan"))
        st.dataframe(show, use_container_width=True)
