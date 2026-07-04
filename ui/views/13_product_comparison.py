"""Product Comparison — FR-1C-16.

Aggregate A/E by product across all five products on a single chart.
"""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH
from ui.stats_helpers import credibility_z, get_run_method

st.set_page_config(page_title="Product Comparison", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Product Comparison — All Five Products")
st.caption("Aggregate A/E ratios by product: mortality, lapse/surrender, and CI incidence.")

_ALL_PRODUCTS = ["TERM", "WL", "UL", "ULSG", "IUL", "VUL", "DA", "DA_FIXED", "DA_FIA", "DA_VA"]
_PRODUCT_DISPLAY = {
    "TERM": "Term Life",
    "WL": "Whole Life",
    "UL": "Universal Life",
    "ULSG": "UL Sec. Guarantee",
    "IUL": "Indexed UL",
    "VUL": "Variable UL",
    "DA": "Deferred Annuity",
    "DA_FIXED": "DA Fixed",
    "DA_FIA": "DA FIA",
    "DA_VA": "DA Variable",
}

_BUHLMANN_K = 1082  # credibility parameter per FR-1A-25


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for all runs with A/E data."""
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
    except Exception:
        return []
    finally:
        conn.close()


def _load_ae_summary(run_id: str) -> pd.DataFrame:
    """Load product-level A/E totals across all decrement types."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code,
                   SUM(exposure_count)          AS exposure_count,
                   SUM(exposure_amount)         AS exposure_amount,
                   SUM(actual_deaths_count)     AS actual_deaths,
                   SUM(expected_deaths_count)   AS expected_deaths,
                   SUM(actual_lapses)           AS actual_lapses,
                   SUM(expected_lapses)         AS expected_lapses,
                   SUM(actual_surrenders)       AS actual_surrenders,
                   SUM(expected_surrenders)     AS expected_surrenders,
                   SUM(actual_ci_claims)        AS actual_ci_claims,
                   SUM(expected_ci_claims)      AS expected_ci_claims
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IS NOT NULL
            GROUP BY product_code
            ORDER BY product_code
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_ae_by_year(run_id: str) -> pd.DataFrame:
    """Load product-level A/E by calendar year for trend charts."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute("""
            SELECT product_code, calendar_year,
                   SUM(actual_deaths_count)   AS actual_deaths,
                   SUM(expected_deaths_count) AS expected_deaths,
                   SUM(actual_lapses)         AS actual_lapses,
                   SUM(expected_lapses)       AS expected_lapses,
                   SUM(actual_surrenders)     AS actual_surrenders,
                   SUM(expected_surrenders)   AS expected_surrenders,
                   SUM(actual_ci_claims)      AS actual_ci_claims,
                   SUM(expected_ci_claims)    AS expected_ci_claims
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IS NOT NULL
              AND calendar_year IS NOT NULL
            GROUP BY 1, 2
            ORDER BY 1, 2
        """, [run_id]).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _add_ci(df: pd.DataFrame, ae_col: str, actual_col: str, method: str = "LF") -> pd.DataFrame:
    """Add 95% Poisson CI and credibility Z to df in-place (FR-1A-24, FR-1A-25).

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


def _bar_with_ci(
    data: pd.DataFrame,
    x_col: str,
    ae_col: str,
    actual_col: str,
    title: str,
    high_color: str = "orange",
    base_color: str = "steelblue",
    threshold: float = 1.1,
    method: str = "LF",
) -> go.Figure:
    """Return a Plotly Figure with error bars and credibility-grey colouring."""
    df = _add_ci(data.copy(), ae_col, actual_col, method=method)
    ae_vals  = df[ae_col].tolist()
    cred_z   = df[f"{ae_col}_cred_z"].tolist()
    ci_hi    = df[f"{ae_col}_ci_hi"].tolist()
    ci_lo    = df[f"{ae_col}_ci_lo"].tolist()

    colors = [
        "lightgrey" if z < 0.5 else (high_color if v > threshold else base_color)
        for z, v in zip(cred_z, ae_vals)
    ]
    fig = go.Figure()
    fig.add_bar(
        x=df[x_col],
        y=ae_vals,
        marker_color=colors,
        text=[f"{v:.2%}" for v in ae_vals],
        textposition="outside",
        error_y=dict(
            type="data",
            array=[hi - v for hi, v in zip(ci_hi, ae_vals)],
            arrayminus=[v - lo for lo, v in zip(ci_lo, ae_vals)],
            visible=True,
            color="rgba(255,255,255,0.5)",
        ),
        name=title,
    )
    fig.add_hline(y=1.0, line_dash="dot", line_color="grey", annotation_text="A/E = 1.0")
    max_val = max((ci_hi or [1.5]) + [1.1])
    fig.update_traces(cliponaxis=False)
    fig.update_layout(
        title=title,
        yaxis=dict(title="A/E ratio", tickformat=".0%", range=[0, max_val * 1.15]),
        xaxis=dict(title="Product"),
        height=400,
        showlegend=False,
    )
    return fig


def _trend_chart(
    yr_df: pd.DataFrame,
    selected: list[str],
    actual_col: str,
    expected_col: str,
    title: str,
) -> Optional[go.Figure]:
    """Return a line chart of A/E trend by calendar year for selected products."""
    df = yr_df[yr_df["product_code"].isin(selected)].copy()
    df["ae"] = df[actual_col] / df[expected_col].replace(0, float("nan"))
    df = df[df["ae"].notna() & (df[actual_col] > 0)]
    df["product_label"] = df["product_code"].map(_PRODUCT_DISPLAY).fillna(df["product_code"])
    if df.empty:
        return None
    fig = px.line(
        df, x="calendar_year", y="ae", color="product_label", markers=True,
        title=title,
        labels={"calendar_year": "Calendar Year", "ae": "A/E Ratio", "product_label": "Product"},
    )
    fig.add_hline(y=1.0, line_dash="dot", line_color="grey")
    fig.update_layout(yaxis=dict(tickformat=".0%"), height=380)
    return fig


# ── Run selector ──────────────────────────────────────────────────────────────
run_ids = _load_run_ids()
if not run_ids:
    st.info("No A/E results found. Run the study pipeline first.")
    st.stop()

run_labels  = {r: lbl for r, lbl in run_ids}
run_id_list = [r for r, _ in run_ids]
run_id = st.selectbox("Study run", run_id_list, format_func=lambda r: run_labels.get(r, r))
summary_df = _load_ae_summary(run_id)
yr_df = _load_ae_by_year(run_id)

_mconn = duckdb.connect(str(DB_PATH), read_only=True)
try:
    run_method = get_run_method(_mconn, run_id)
finally:
    _mconn.close()

if summary_df.empty:
    st.warning("No A/E data for this run.")
    st.stop()

# ── Compute aggregate A/E ratios ──────────────────────────────────────────────
summary_df["ae_mortality"] = (
    summary_df["actual_deaths"] / summary_df["expected_deaths"].replace(0, float("nan"))
)
summary_df["ae_lapse"] = (
    summary_df["actual_lapses"] / summary_df["expected_lapses"].replace(0, float("nan"))
)
summary_df["ae_surrender"] = (
    summary_df["actual_surrenders"] / summary_df["expected_surrenders"].replace(0, float("nan"))
)
summary_df["ae_ci"] = (
    summary_df["actual_ci_claims"] / summary_df["expected_ci_claims"].replace(0, float("nan"))
)
summary_df["product_label"] = summary_df["product_code"].map(_PRODUCT_DISPLAY).fillna(summary_df["product_code"])

# ── Filter controls ───────────────────────────────────────────────────────────
available_products = summary_df["product_code"].unique().tolist()
selected_products = st.multiselect(
    "Products to display",
    options=available_products,
    default=available_products,
)
view = st.radio(
    "Decrement view",
    ["Mortality", "Lapse", "Surrender", "CI Incidence"],
    horizontal=True,
)
st.caption("Grey bars indicate credibility Z < 0.5 — treat with caution. Error bars show 95% Poisson CI (FR-1A-25).")

plot_df = summary_df[summary_df["product_code"].isin(selected_products)].copy()

# ── Mortality view ────────────────────────────────────────────────────────────
if view == "Mortality":
    st.subheader("Mortality A/E by Product")
    st.caption("DA (annuity) mortality uses 2012 IAR. Life products use 2015 VBT.")

    life_mort = plot_df[plot_df["ae_mortality"].notna() & (plot_df["actual_deaths"] > 0)].copy()
    if not life_mort.empty:
        fig_mort = _bar_with_ci(
            life_mort, "product_label", "ae_mortality", "actual_deaths",
            "Mortality A/E by Product", high_color="red", base_color="steelblue", threshold=1.0,
            method=run_method,
        )
        st.plotly_chart(fig_mort, use_container_width=True)
    else:
        st.info("No mortality A/E data available for selected products.")

    fig_ts = _trend_chart(yr_df, selected_products, "actual_deaths", "expected_deaths",
                          "Mortality A/E Trend by Product")
    if fig_ts:
        st.plotly_chart(fig_ts, use_container_width=True)

# ── Lapse view ────────────────────────────────────────────────────────────────
elif view == "Lapse":
    st.subheader("Lapse A/E by Product")

    lapse_data = plot_df[plot_df["ae_lapse"].notna() & (plot_df["actual_lapses"] > 0)].copy()
    if not lapse_data.empty:
        fig_lapse = _bar_with_ci(
            lapse_data, "product_label", "ae_lapse", "actual_lapses",
            "Lapse A/E by Product",
            method=run_method,
        )
        st.plotly_chart(fig_lapse, use_container_width=True)
    else:
        st.info("No lapse A/E data available for selected products.")

    # NFR-C-07 directionality check
    st.subheader("NFR-C-07 Directionality Check — ULSG vs UL Lapse A/E")
    ul_row   = plot_df[plot_df["product_code"] == "UL"]
    ulsg_row = plot_df[plot_df["product_code"] == "ULSG"]

    if not ul_row.empty and not ulsg_row.empty:
        ul_ae   = ul_row["ae_lapse"].iloc[0]
        ulsg_ae = ulsg_row["ae_lapse"].iloc[0]
        nfr_col1, nfr_col2 = st.columns(2)
        nfr_col1.metric("UL Lapse A/E",   f"{ul_ae:.2%}"   if not pd.isna(ul_ae)   else "N/A")
        nfr_col2.metric("ULSG Lapse A/E", f"{ulsg_ae:.2%}" if not pd.isna(ulsg_ae) else "N/A")

        if not pd.isna(ul_ae) and not pd.isna(ulsg_ae):
            if ulsg_ae < ul_ae:
                st.success("NFR-C-07 PASS: ULSG lapse A/E is below UL lapse A/E (as expected for a lapse-supported product).")
            else:
                st.warning(
                    f"**NFR-C-07 FAIL:** ULSG lapse A/E ({ulsg_ae:.2%}) ≥ UL lapse A/E ({ul_ae:.2%}). "
                    "Expected ULSG to show lower lapse A/E — ULSG base lapse is 50% of Trad UL (Section 8.3). "
                    "Root cause: the dynamic lapse multiplier (interest-rate spread) is applied equally to UL "
                    "and ULSG without rescaling for the already-reduced ULSG base, causing ULSG actuals to "
                    "slightly exceed the 50% reference-table expectation."
                )
    else:
        st.info("Both UL and ULSG must be selected to run the NFR-C-07 check.")

    fig_ts = _trend_chart(yr_df, selected_products, "actual_lapses", "expected_lapses",
                          "Lapse A/E Trend by Product")
    if fig_ts:
        st.plotly_chart(fig_ts, use_container_width=True)

# ── Surrender view ────────────────────────────────────────────────────────────
elif view == "Surrender":
    st.subheader("Surrender A/E by Product")
    st.caption("No confidence intervals available for surrender — CI columns are not in the gold schema.")

    surr_data = plot_df[plot_df["ae_surrender"].notna() & (plot_df["actual_surrenders"] > 0)].copy()
    if not surr_data.empty:
        colors = ["orange" if v > 1.1 else "teal" for v in surr_data["ae_surrender"]]
        max_val = surr_data["ae_surrender"].max()
        fig_surr = go.Figure()
        fig_surr.add_bar(
            x=surr_data["product_label"],
            y=surr_data["ae_surrender"],
            marker_color=colors,
            text=[f"{v:.2%}" for v in surr_data["ae_surrender"]],
            textposition="outside",
        )
        fig_surr.add_hline(y=1.0, line_dash="dot", line_color="grey", annotation_text="A/E = 1.0")
        fig_surr.update_traces(cliponaxis=False)
        fig_surr.update_layout(
            title="Surrender A/E by Product",
            yaxis=dict(title="A/E ratio", tickformat=".0%", range=[0, max_val * 1.2]),
            xaxis=dict(title="Product"),
            height=400,
            showlegend=False,
        )
        st.plotly_chart(fig_surr, use_container_width=True)
    else:
        st.info("No surrender A/E data available for selected products.")

    fig_ts = _trend_chart(yr_df, selected_products, "actual_surrenders", "expected_surrenders",
                          "Surrender A/E Trend by Product")
    if fig_ts:
        st.plotly_chart(fig_ts, use_container_width=True)

# ── CI Incidence view ─────────────────────────────────────────────────────────
elif view == "CI Incidence":
    st.subheader("CI Incidence A/E by Product")

    ci_data = plot_df[plot_df["ae_ci"].notna() & (plot_df["actual_ci_claims"] > 0)].copy()
    if not ci_data.empty:
        fig_ci = _bar_with_ci(
            ci_data, "product_label", "ae_ci", "actual_ci_claims",
            "CI Incidence A/E by Product", high_color="red", base_color="steelblue", threshold=1.0,
            method=run_method,
        )
        st.plotly_chart(fig_ci, use_container_width=True)
    else:
        st.info("No CI incidence A/E data available for selected products.")

st.divider()

# ── Summary table (always visible) ───────────────────────────────────────────
st.subheader("A/E Summary Table — All Products")
display_cols = ["product_label", "exposure_count", "actual_deaths", "ae_mortality",
                "actual_lapses", "ae_lapse", "actual_surrenders", "ae_surrender",
                "actual_ci_claims", "ae_ci"]
show_df = plot_df[[c for c in display_cols if c in plot_df.columns]].copy()
show_df = show_df.rename(columns={
    "product_label": "Product",
    "exposure_count": "Exposure (yrs)",
    "actual_deaths": "Actual Deaths",
    "ae_mortality": "Mortality A/E",
    "actual_lapses": "Actual Lapses",
    "ae_lapse": "Lapse A/E",
    "actual_surrenders": "Actual Surrenders",
    "ae_surrender": "Surrender A/E",
    "actual_ci_claims": "Actual CI Claims",
    "ae_ci": "CI A/E",
})

pct_cols = [c for c in ["Mortality A/E", "Lapse A/E", "Surrender A/E", "CI A/E"] if c in show_df.columns]
st.dataframe(
    show_df.style.format({c: "{:.2%}" for c in pct_cols}, na_rep="N/A"),
    use_container_width=True,
)
