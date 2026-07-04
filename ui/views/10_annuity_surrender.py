"""Annuity Surrender Explorer — FR-1C-13.

A/E by contract year, product type, market type; shock-lapse panel;
dynamic-lapse diagnostic; market-type cut; surrender vs withdrawal split;
GLB suppression analysis.

Sections 1–3 query gold_ae_results (pre-aggregated A/E).
Sections 4–6 query gold_exposure_segments + silver_annuity_contracts directly
because market_type, glwb_elected_flag, and decrement_type are not dimensions
in gold_ae_results.  Those sections show observed rates (actual / exposure),
not A/E ratios, and are captioned accordingly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH

st.set_page_config(page_title="Annuity Surrender Explorer", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Annuity Surrender Explorer")
st.caption(
    "Surrender A/E by contract year and product type (Sections 1–3). "
    "Observed surrender rates by market type, decrement type, and GLB election (Sections 4–6)."
)

_DA_PRODUCTS = ["DA", "DA_FIXED", "DA_FIA", "DA_VA"]

_MACRO_MARKET = {
    2016: 0.018, 2017: 0.024, 2018: 0.029, 2019: 0.019,
    2020: 0.009, 2021: 0.015, 2022: 0.039, 2023: 0.040,
}
_MACRO_CREDITED = {
    2016: 0.032, 2017: 0.032, 2018: 0.032, 2019: 0.031,
    2020: 0.030, 2021: 0.029, 2022: 0.029, 2023: 0.031,
}


# ── Data loaders ──────────────────────────────────────────────────────────────


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for runs with annuity A/E data."""
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
            result.append((run_id, label))
        return result
    except Exception:
        return []
    finally:
        conn.close()


def _load_ae_data(run_id: str) -> pd.DataFrame:
    """Pre-aggregated A/E from gold_ae_results."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT product_code, duration_band, policy_year, calendar_year,
                   is_plt_flag,
                   SUM(actual_surrenders)      AS actual_surrenders,
                   SUM(expected_surrenders)    AS expected_surrenders,
                   SUM(surrender_exposure)     AS surrender_exposure,
                   SUM(ae_surrender * surrender_exposure)
                       / NULLIF(SUM(surrender_exposure), 0) AS ae_surrender
            FROM gold_ae_results
            WHERE study_run_id = ?
              AND product_code IN ('DA','DA_FIXED','DA_FIA','DA_VA')
            GROUP BY 1,2,3,4,5
            ORDER BY policy_year
            """,
            [run_id],
        ).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_market_type(run_id: str, products: list[str]) -> pd.DataFrame:
    """Observed surrender rate by market_type × policy_year × attained_age_band."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        prod_ph = ", ".join(["?"] * len(products))
        return conn.execute(
            f"""
            SELECT s.market_type,
                   e.policy_year,
                   e.attained_age_band,
                   SUM(CASE WHEN e.decrement_type = 'SURRENDER' THEN 1 ELSE 0 END)
                       AS actual_surrenders,
                   SUM(e.exposure_years) AS surrender_exposure
            FROM gold_exposure_segments e
            JOIN (SELECT DISTINCT contract_id, market_type
                  FROM silver_annuity_contracts) s
              ON s.contract_id = e.policy_id
            WHERE e.study_run_id = ?
              AND e.product_code IN ({prod_ph})
            GROUP BY s.market_type, e.policy_year, e.attained_age_band
            ORDER BY s.market_type, e.policy_year, e.attained_age_band
            """,
            [run_id] + products,
        ).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_decrement_type(run_id: str, products: list[str]) -> pd.DataFrame:
    """Observed rate by decrement_type × policy_year × glwb_elected_flag.

    The observed rate denominator is the *at-risk* exposure across all segments in
    each (policy_year, glwb) cell — not just the decrement segments. Dividing event
    counts by the exposure of only the decrement segments (each a partial year)
    inflates the rate far above 100%; using full at-risk exposure yields a true
    annual decrement rate.
    """
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        prod_ph = ", ".join(["?"] * len(products))
        return conn.execute(
            f"""
            WITH seg AS (
                SELECT e.policy_year,
                       e.decrement_type,
                       e.decrement_flag,
                       e.exposure_years,
                       COALESCE(s.glwb_elected_flag, FALSE) AS glwb_elected_flag
                FROM gold_exposure_segments e
                JOIN (SELECT DISTINCT contract_id, glwb_elected_flag
                      FROM silver_annuity_contracts) s
                  ON s.contract_id = e.policy_id
                WHERE e.study_run_id = ?
                  AND e.product_code IN ({prod_ph})
            ),
            atrisk AS (
                SELECT policy_year, glwb_elected_flag,
                       SUM(exposure_years) AS exposure
                FROM seg
                GROUP BY policy_year, glwb_elected_flag
            ),
            events AS (
                SELECT decrement_type, policy_year, glwb_elected_flag,
                       SUM(CASE WHEN decrement_flag THEN 1 ELSE 0 END) AS actual_events
                FROM seg
                WHERE decrement_type IN ('SURRENDER', 'WITHDRAWAL')
                GROUP BY decrement_type, policy_year, glwb_elected_flag
            )
            SELECT ev.decrement_type,
                   ev.policy_year,
                   ev.glwb_elected_flag,
                   ev.actual_events,
                   ar.exposure,
                   ev.actual_events::DOUBLE / NULLIF(ar.exposure, 0) AS observed_rate
            FROM events ev
            JOIN atrisk ar USING (policy_year, glwb_elected_flag)
            ORDER BY ev.decrement_type, ev.policy_year
            """,
            [run_id] + products,
        ).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_glb_suppression(run_id: str, products: list[str]) -> pd.DataFrame:
    """Observed surrender rate by glwb_elected_flag × policy_year, with avg moneyness."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        prod_ph = ", ".join(["?"] * len(products))
        return conn.execute(
            f"""
            SELECT s.glwb_elected_flag,
                   e.policy_year,
                   s.moneyness_ratio,
                   SUM(CASE WHEN e.decrement_type = 'SURRENDER' THEN 1 ELSE 0 END)
                       AS actual_surrenders,
                   SUM(e.exposure_years) AS surrender_exposure,
                   SUM(CASE WHEN e.decrement_type = 'SURRENDER' THEN 1 ELSE 0 END)::DOUBLE
                       / NULLIF(SUM(e.exposure_years), 0) AS observed_rate
            FROM gold_exposure_segments e
            JOIN (SELECT DISTINCT contract_id, glwb_elected_flag, moneyness_ratio
                  FROM silver_annuity_contracts) s
              ON s.contract_id = e.policy_id
            WHERE e.study_run_id = ?
              AND e.product_code IN ({prod_ph})
            GROUP BY s.glwb_elected_flag, e.policy_year, s.moneyness_ratio
            ORDER BY s.glwb_elected_flag, e.policy_year
            """,
            [run_id] + products,
        ).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


# ── Run selector ──────────────────────────────────────────────────────────────

run_ids = _load_run_ids()
if not run_ids:
    st.info("No annuity A/E results found. Run the study pipeline with DA product selected.")
    st.stop()

run_labels  = {r: lbl for r, lbl in run_ids}
run_id_list = [r for r, _ in run_ids]
run_id = st.selectbox("Study run", run_id_list, format_func=lambda r: run_labels.get(r, r))
ae_df = _load_ae_data(run_id)

if ae_df.empty:
    st.warning("No DA A/E data for this run.")
    st.stop()

# ── Page controls ─────────────────────────────────────────────────────────────

ctrl1, ctrl2, ctrl3 = st.columns(3)
with ctrl1:
    prod_filter = st.multiselect("Product type", _DA_PRODUCTS, default=_DA_PRODUCTS)
    if not prod_filter:
        prod_filter = _DA_PRODUCTS
with ctrl2:
    view = st.radio("View by", ["Policy year", "Calendar year"], horizontal=True)
with ctrl3:
    yr_opts = sorted(ae_df["policy_year"].dropna().unique().astype(int).tolist())
    yr_filter = st.multiselect("Filter contract year(s) (leave empty = all)", yr_opts)

# Apply filters
ae_df = ae_df[ae_df["product_code"].isin(prod_filter)]
if yr_filter:
    ae_df = ae_df[ae_df["policy_year"].isin(yr_filter)]

if ae_df.empty:
    st.warning("No data for the selected filters.")
    st.stop()

# ── Section 1: Main A/E chart ─────────────────────────────────────────────────

_s1_dim_label = "Contract Year" if view == "Policy year" else "Calendar Year"
st.subheader(f"1 · Surrender A/E by {_s1_dim_label}")

grp_col = "policy_year" if view == "Policy year" else "calendar_year"
main_agg = (
    ae_df.groupby(grp_col)
    .agg(actual=("actual_surrenders", "sum"), expected=("expected_surrenders", "sum"))
    .reset_index()
)
main_agg["ae"] = main_agg["actual"] / main_agg["expected"].replace(0, float("nan"))

fig1 = go.Figure()
fig1.add_bar(x=main_agg[grp_col], y=main_agg["actual"],
             name="Actual surrenders", marker_color="#1f77b4")
fig1.add_scatter(
    x=main_agg[grp_col], y=main_agg["ae"],
    name="A/E ratio", yaxis="y2",
    line=dict(color="red", width=2),
)
fig1.update_layout(
    yaxis2=dict(overlaying="y", side="right", title="A/E ratio", tickformat=".0%"),
    yaxis=dict(title="Surrender count"),
    xaxis=dict(title=_s1_dim_label),
    legend=dict(orientation="h", y=-0.2),
    height=400,
)
st.plotly_chart(fig1, use_container_width=True)

# ── Section 2: Shock lapse panel ─────────────────────────────────────────────

st.subheader("2 · Surrender-Charge Expiry Shock Lapse Panel")
st.caption(
    "is_plt_flag=TRUE marks contracts in the SC shock year (final year before expiry). "
    "Shock bars are red; A/E in the range 0.85–1.15 confirms the expected basis captures "
    "the 60% shock rate."
)

shock_df = ae_df[ae_df["is_plt_flag"] == True].copy() if "is_plt_flag" in ae_df.columns else pd.DataFrame()
base_df  = ae_df[ae_df["is_plt_flag"] != True].copy() if "is_plt_flag" in ae_df.columns else ae_df

# Metric tiles
col_s, col_b = st.columns(2)
with col_s:
    st.metric("Shock year surrenders", int(shock_df["actual_surrenders"].sum()) if not shock_df.empty else 0)
    if not shock_df.empty and shock_df["expected_surrenders"].sum() > 0:
        st.metric("Shock A/E", f"{shock_df['actual_surrenders'].sum() / shock_df['expected_surrenders'].sum():.2%}")
    else:
        st.metric("Shock A/E", "N/A")
with col_b:
    st.metric("Base year surrenders", int(base_df["actual_surrenders"].sum()) if not base_df.empty else 0)
    if not base_df.empty and base_df["expected_surrenders"].sum() > 0:
        st.metric("Base A/E", f"{base_df['actual_surrenders'].sum() / base_df['expected_surrenders'].sum():.2%}")
    else:
        st.metric("Base A/E", "N/A")

# Spike bar chart
spike_agg = (
    ae_df.groupby(["policy_year", "is_plt_flag"])
    .agg(
        actual=("actual_surrenders", "sum"),
        expected=("expected_surrenders", "sum"),
        exposure=("surrender_exposure", "sum"),
    )
    .reset_index()
)
spike_agg["ae"] = spike_agg["actual"] / spike_agg["expected"].replace(0, float("nan"))

if not spike_agg.empty:
    bar_colors = ["#e74c3c" if v else "#1f77b4" for v in spike_agg["is_plt_flag"].fillna(False)]
    fig_spike = go.Figure()
    fig_spike.add_bar(
        x=spike_agg["policy_year"], y=spike_agg["actual"],
        name="Actual surrenders",
        marker_color=bar_colors,
    )
    fig_spike.add_scatter(
        x=spike_agg["policy_year"], y=spike_agg["ae"],
        name="A/E ratio", yaxis="y2",
        mode="lines+markers",
        line=dict(color="orange", width=2),
    )
    fig_spike.add_hline(y=1.0, line_dash="dot", line_color="grey", annotation_text="A/E = 1.0")
    fig_spike.update_layout(
        title="Surrender Count & A/E by Contract Year  (shock year = red)",
        yaxis=dict(title="Surrender count"),
        yaxis2=dict(overlaying="y", side="right", title="A/E ratio", tickformat=".0%"),
        legend=dict(orientation="h", y=-0.2),
        height=380,
    )
    st.plotly_chart(fig_spike, use_container_width=True)

# ── Section 3: Dynamic Lapse Diagnostic ──────────────────────────────────────

st.subheader("3 · Dynamic Lapse Diagnostic")

diag_view = st.radio(
    "Diagnostic view",
    ["By Calendar Year", "By Policy Year"],
    horizontal=True,
)

if diag_view == "By Calendar Year":
    macro_df = pd.DataFrame({
        "year": list(_MACRO_MARKET.keys()),
        "market_rate": list(_MACRO_MARKET.values()),
        "credited_rate": list(_MACRO_CREDITED.values()),
    })
    macro_df["dyn_mult"] = (macro_df["market_rate"] - macro_df["credited_rate"]).apply(
        lambda d: min(3.0, max(0.3, 1.0 + 0.8 * d))
    )

    yr_agg = (
        ae_df.groupby("calendar_year")
        .agg(actual=("actual_surrenders", "sum"), expected=("expected_surrenders", "sum"))
        .reset_index()
    )
    yr_agg["ae"] = yr_agg["actual"] / yr_agg["expected"].replace(0, float("nan"))

    fig_diag = go.Figure()
    fig_diag.add_bar(
        x=yr_agg["calendar_year"], y=yr_agg["ae"], name="Observed A/E",
        marker_color=["red" if ae > 1.0 else "steelblue" for ae in yr_agg["ae"].fillna(0)],
    )
    fig_diag.add_scatter(
        x=macro_df["year"], y=macro_df["dyn_mult"],
        name="Dynamic lapse multiplier (k=0.8)",
        line=dict(color="orange", dash="dash"),
    )
    fig_diag.add_hline(y=1.0, line_dash="dot", line_color="grey")
    fig_diag.update_layout(
        yaxis=dict(title="A/E / Multiplier"),
        xaxis=dict(title="Calendar year"),
        legend=dict(orientation="h", y=-0.2),
        height=350,
    )
    st.caption("Observed A/E vs theoretical dynamic lapse multiplier (FR-1C-10, k=0.8). "
               "A/E should track the multiplier in rising-rate years (2021–2023).")
else:
    py_agg = (
        ae_df.groupby("policy_year")
        .agg(actual=("actual_surrenders", "sum"), expected=("expected_surrenders", "sum"))
        .reset_index()
    )
    py_agg["ae"] = py_agg["actual"] / py_agg["expected"].replace(0, float("nan"))

    fig_diag = go.Figure()
    fig_diag.add_bar(
        x=py_agg["policy_year"], y=py_agg["ae"], name="Observed A/E",
        marker_color=["red" if ae > 1.0 else "steelblue" for ae in py_agg["ae"].fillna(0)],
    )
    fig_diag.add_hline(y=1.0, line_dash="dot", line_color="grey")
    fig_diag.update_layout(
        yaxis=dict(title="A/E ratio"),
        xaxis=dict(title="Policy year (contract duration)"),
        legend=dict(orientation="h", y=-0.2),
        height=350,
    )
    st.caption("Observed A/E by policy year. Dynamic multiplier not shown — it is a "
               "calendar-year effect, not a duration effect.")

st.plotly_chart(fig_diag, use_container_width=True)

st.divider()

# ── Section 4: Market type cut ────────────────────────────────────────────────

st.subheader("4 · Market Type Surrender Rate")
st.caption(
    "Observed surrender rate (actual surrenders / exposure years) from gold_exposure_segments "
    "joined to silver_annuity_contracts. A/E not shown — market_type is not a dimension in "
    "gold_ae_results. TRAD_IRA surrenders before age 59½ incur a 10% IRS early withdrawal "
    "penalty; rates should be lower than NQ in early contract years."
)

mt_df = _load_market_type(run_id, prod_filter)

if mt_df.empty:
    st.info("No exposure segment data for market type analysis.")
else:
    _all_market_types = sorted(mt_df["market_type"].dropna().unique().tolist())
    s4c1, s4c2 = st.columns(2)
    with s4c1:
        s4_market_filter = st.multiselect(
            "Market types shown",
            options=_all_market_types,
            default=_all_market_types,
            key="s4_market_filter",
        )
        if not s4_market_filter:
            s4_market_filter = _all_market_types
    with s4c2:
        s4_dim = st.radio(
            "X-axis",
            ["By contract year", "By attained age band"],
            horizontal=True,
            key="s4_dim",
        )

    mt_filtered = mt_df[mt_df["market_type"].isin(s4_market_filter)]
    x_col = "policy_year" if s4_dim == "By contract year" else "attained_age_band"
    mt_agg = (
        mt_filtered.groupby(["market_type", x_col])
        .agg(actual_surrenders=("actual_surrenders", "sum"),
             surrender_exposure=("surrender_exposure", "sum"))
        .reset_index()
    )
    mt_agg["observed_rate"] = (
        mt_agg["actual_surrenders"] / mt_agg["surrender_exposure"].replace(0, float("nan"))
    )

    mt_colours = {
        "NQ": "#1f77b4", "TRAD_IRA": "#e74c3c",
        "ROTH_IRA": "#2ca02c", "QUAL": "#ff7f0e",
    }
    _age_band_order = [
        "30-34", "35-39", "40-44", "45-49", "50-54",
        "55-59", "60-64", "65-69", "70-74", "75-79", "80+",
    ]
    fig_mt = go.Figure()
    for mtype, grp in mt_agg.groupby("market_type"):
        if x_col == "attained_age_band":
            grp = grp.copy()
            grp["_sort"] = grp[x_col].apply(
                lambda b: _age_band_order.index(b) if b in _age_band_order else 99
            )
            grp = grp.sort_values("_sort")
        else:
            grp = grp.sort_values(x_col)
        fig_mt.add_scatter(
            x=grp[x_col], y=grp["observed_rate"],
            mode="lines+markers",
            name=str(mtype),
            line=dict(color=mt_colours.get(str(mtype), "#999")),
        )
    x_title = "Contract year (policy year)" if x_col == "policy_year" else "Attained age band"
    fig_mt.update_layout(
        yaxis=dict(title="Observed surrender rate", tickformat=".1%"),
        xaxis=dict(title=x_title),
        legend=dict(orientation="h", y=-0.2),
        height=380,
    )
    st.plotly_chart(fig_mt, use_container_width=True)

# ── Section 5: Full surrender vs partial withdrawal ───────────────────────────

st.subheader("5 · Full Surrender vs Partial Withdrawal (FR-1C-07)")
st.caption(
    "FR-1C-07 requires full surrender and partial withdrawal as separate decrements. "
    "Both are shown by default. Observed rates from gold_exposure_segments "
    "(decrement_type IN ('SURRENDER','WITHDRAWAL')). "
    "Full surrender A/E is in Section 1; partial withdrawal A/E requires a separate withdrawal "
    "expected basis not yet stored in gold_ae_results."
)

dt_df = _load_decrement_type(run_id, prod_filter)

if dt_df.empty:
    st.info("No decrement-type data found. Verify that decrement_type is populated in gold_exposure_segments.")
else:
    s5c1, s5c2, s5c3 = st.columns(3)
    with s5c1:
        s5_decrement_filter = st.multiselect(
            "Decrement type(s) shown",
            options=["SURRENDER", "WITHDRAWAL"],
            default=["SURRENDER", "WITHDRAWAL"],
            key="s5_decrement_filter",
        )
        if not s5_decrement_filter:
            s5_decrement_filter = ["SURRENDER", "WITHDRAWAL"]
    with s5c2:
        s5_metric = st.radio(
            "Y-axis metric",
            ["Observed rate", "Event count"],
            horizontal=True,
            key="s5_metric",
        )
    with s5c3:
        s5_glb = st.radio(
            "GLB filter",
            ["All contracts", "GLWB elected", "No GLB rider"],
            horizontal=True,
            key="s5_glb",
        )

    # Apply GLB filter
    dt_filtered = dt_df.copy()
    if s5_glb == "GLWB elected":
        dt_filtered = dt_filtered[dt_filtered["glwb_elected_flag"] == True]
    elif s5_glb == "No GLB rider":
        dt_filtered = dt_filtered[dt_filtered["glwb_elected_flag"] == False]

    # Aggregate (collapse glwb_elected_flag dimension after filtering)
    dt_agg = (
        dt_filtered.groupby(["decrement_type", "policy_year"])
        .agg(actual_events=("actual_events", "sum"),
             exposure=("exposure", "sum"))
        .reset_index()
    )
    dt_agg["observed_rate"] = (
        dt_agg["actual_events"] / dt_agg["exposure"].replace(0, float("nan"))
    )

    # Apply decrement type filter
    dt_agg = dt_agg[dt_agg["decrement_type"].isin(s5_decrement_filter)]

    y_col = "observed_rate" if s5_metric == "Observed rate" else "actual_events"
    y_fmt  = ".1%" if s5_metric == "Observed rate" else None
    y_title = "Observed rate" if s5_metric == "Observed rate" else "Event count"

    dt_colours = {"SURRENDER": "#e74c3c", "WITHDRAWAL": "#1f77b4"}
    fig_dt = go.Figure()
    for dtype, grp in dt_agg.groupby("decrement_type"):
        grp = grp.sort_values("policy_year")
        fig_dt.add_scatter(
            x=grp["policy_year"], y=grp[y_col],
            mode="lines+markers",
            name=str(dtype),
            line=dict(color=dt_colours.get(str(dtype), "#999")),
        )
    y_axis_cfg = dict(title=y_title)
    if y_fmt:
        y_axis_cfg["tickformat"] = y_fmt
    fig_dt.update_layout(
        yaxis=y_axis_cfg,
        xaxis=dict(title="Contract year (policy year)"),
        legend=dict(orientation="h", y=-0.2),
        height=360,
    )
    st.plotly_chart(fig_dt, use_container_width=True)

# ── Section 6: GLB suppression ────────────────────────────────────────────────

st.subheader("6 · GLB Suppression Analysis (FR-1C-11)")
st.caption(
    "FR-1C-11: expected surrender suppression multiplier = min(1.0, 0.4 + 0.6 × moneyness_ratio) "
    "when glwb_elected_flag = TRUE. In-the-money contracts (moneyness < 1.0) suppress surrenders "
    "toward 40% of the base rate. GLB-elected lines should sit materially below non-GLB, "
    "especially in early-to-mid contract years when benefit bases tend to exceed account values. "
    "Observed rates shown (actual / exposure years) from gold_exposure_segments."
)

glb_df = _load_glb_suppression(run_id, prod_filter)

if glb_df.empty:
    st.info("No GLB suppression data found. Verify glwb_elected_flag in silver_annuity_contracts.")
else:
    s6c1, s6c2 = st.columns(2)
    with s6c1:
        s6_view = st.radio(
            "View",
            ["By GLB election", "By moneyness tier"],
            horizontal=True,
            key="s6_view",
        )
    with s6c2:
        s6_overlay = st.checkbox(
            "Overlay FR-1C-11 formula",
            value=False,
            disabled=(s6_view != "By GLB election"),
            key="s6_overlay",
        )

    fig_glb = go.Figure()

    if s6_view == "By GLB election":
        # Aggregate to (glwb_elected_flag, policy_year)
        glb_agg = (
            glb_df.groupby(["glwb_elected_flag", "policy_year"])
            .agg(actual_surrenders=("actual_surrenders", "sum"),
                 surrender_exposure=("surrender_exposure", "sum"),
                 avg_moneyness=("moneyness_ratio", "mean"))
            .reset_index()
        )
        glb_agg["observed_rate"] = (
            glb_agg["actual_surrenders"]
            / glb_agg["surrender_exposure"].replace(0, float("nan"))
        )

        for elected, grp in glb_agg.groupby("glwb_elected_flag"):
            grp = grp.sort_values("policy_year")
            label = "GLWB elected" if elected else "No GLB rider"
            colour = "#e74c3c" if elected else "#1f77b4"
            fig_glb.add_scatter(
                x=grp["policy_year"], y=grp["observed_rate"],
                mode="lines+markers", name=label,
                line=dict(color=colour),
            )

        if s6_overlay:
            # Theoretical GLWB rate = base_rate × min(1.0, 0.4 + 0.6 × avg_moneyness)
            base = glb_agg[glb_agg["glwb_elected_flag"] == False].set_index("policy_year")["observed_rate"]
            glwb = glb_agg[glb_agg["glwb_elected_flag"] == True].sort_values("policy_year")
            theoretical = []
            for _, row in glwb.iterrows():
                yr = row["policy_year"]
                base_rate = base.get(yr, float("nan"))
                m = row["avg_moneyness"]
                mult = min(1.0, 0.4 + 0.6 * m) if pd.notna(m) else 1.0
                theoretical.append((yr, base_rate * mult))
            if theoretical:
                th_years, th_rates = zip(*theoretical)
                fig_glb.add_scatter(
                    x=list(th_years), y=list(th_rates),
                    mode="lines", name="FR-1C-11 formula (theoretical)",
                    line=dict(color="#9467bd", dash="dash", width=2),
                )

    else:
        # Moneyness tier view (GLWB-elected only — non-GLB has moneyness_ratio = NULL)
        def _moneyness_tier(v: float) -> str:
            """Bin moneyness ratio into ITM / ATM / OTM tiers."""
            if pd.isna(v):
                return "Unknown"
            if v < 0.9:
                return "ITM (<0.9)"
            if v <= 1.1:
                return "ATM (0.9–1.1)"
            return "OTM (>1.1)"

        glb_df = glb_df[glb_df["glwb_elected_flag"] == True].copy()
        glb_df["moneyness_tier"] = glb_df["moneyness_ratio"].apply(_moneyness_tier)
        tier_agg = (
            glb_df.groupby(["moneyness_tier", "policy_year"])
            .agg(actual_surrenders=("actual_surrenders", "sum"),
                 surrender_exposure=("surrender_exposure", "sum"))
            .reset_index()
        )
        tier_agg["observed_rate"] = (
            tier_agg["actual_surrenders"]
            / tier_agg["surrender_exposure"].replace(0, float("nan"))
        )
        tier_colours = {
            "ITM (<0.9)": "#e74c3c",
            "ATM (0.9–1.1)": "#ff7f0e",
            "OTM (>1.1)": "#2ca02c",
            "Unknown": "#aaa",
        }
        for tier, grp in tier_agg.groupby("moneyness_tier"):
            grp = grp.sort_values("policy_year")
            fig_glb.add_scatter(
                x=grp["policy_year"], y=grp["observed_rate"],
                mode="lines+markers", name=str(tier),
                line=dict(color=tier_colours.get(str(tier), "#999")),
            )
        st.caption(
            "Only GLWB-elected contracts shown (non-GLB contracts have no moneyness ratio). "
            "ITM = in-the-money (moneyness < 0.9, strong suppression expected); "
            "OTM = out-of-the-money (no suppression). If ITM is not the lowest line, "
            "the suppression multiplier is not wiring through correctly."
        )

    fig_glb.update_layout(
        yaxis=dict(title="Observed surrender rate", tickformat=".1%"),
        xaxis=dict(title="Contract year (policy year)"),
        legend=dict(orientation="h", y=-0.2),
        height=380,
    )
    st.plotly_chart(fig_glb, use_container_width=True)

st.divider()

# ── Raw data expander ─────────────────────────────────────────────────────────

with st.expander("Raw A/E data (gold_ae_results)"):
    st.dataframe(ae_df, use_container_width=True)
