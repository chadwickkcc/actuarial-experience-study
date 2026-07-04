"""CI Incidence Explorer — A/E by illness code, age band, and gender."""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH
from ui.stats_helpers import credibility_z, poisson_ci, get_run_method

st.set_page_config(page_title="CI Incidence Explorer", layout="wide")

from ui.config import require_auth
require_auth()
st.title("CI Incidence Explorer")

ILLNESS_NAMES = {
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


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for runs with CI incidence data."""
    import json as _json
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (
                SELECT DISTINCT study_run_id FROM gold_ae_results
                WHERE illness_code IS NOT NULL
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


def _query_ci_by_illness(
    run_id: str, gender: Optional[list] = None, products: Optional[list] = None
) -> pd.DataFrame:
    """CI A/E aggregated by illness_code."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        where = "study_run_id = ? AND illness_code IS NOT NULL"
        params: list = [run_id]
        if gender:
            placeholders = ", ".join(["?"] * len(gender))
            where += f" AND gender IN ({placeholders})"
            params.extend(gender)
        if products:
            placeholders = ", ".join(["?"] * len(products))
            where += f" AND product_code IN ({placeholders})"
            params.extend(products)
        df = conn.execute(
            f"""
            SELECT illness_code,
                   SUM(actual_ci_claims)  AS actual_ci_claims,
                   SUM(expected_ci_claims) AS expected_ci_claims,
                   SUM(ci_exposure_count) AS ci_exposure_years,
                   CASE WHEN SUM(expected_ci_claims) > 0
                        THEN SUM(actual_ci_claims) / SUM(expected_ci_claims)
                        ELSE NULL END AS ae_ci
            FROM gold_ae_results
            WHERE {where}
            GROUP BY illness_code
            ORDER BY illness_code
            """,
            params,
        ).df()
        cred_method = get_run_method(conn, run_id)
        conn.close()
        # Credibility Z is recomputed from the aggregate claim count per illness,
        # not averaged from per-cell values (FR-1A-24).
        df["credibility_z"] = credibility_z(df["actual_ci_claims"], method=cred_method)
        df["illness_name"] = df["illness_code"].map(ILLNESS_NAMES).fillna(df["illness_code"])
        return df
    except Exception:
        conn.close()
        raise


def _query_ci_by_age_band(
    run_id: str, gender: Optional[list] = None, products: Optional[list] = None
) -> pd.DataFrame:
    """CI A/E aggregated by attained_age_band — shows incidence trend by age."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        where = "study_run_id = ? AND illness_code IS NOT NULL AND attained_age_band IS NOT NULL"
        params: list = [run_id]
        if gender:
            placeholders = ", ".join(["?"] * len(gender))
            where += f" AND gender IN ({placeholders})"
            params.extend(gender)
        if products:
            placeholders = ", ".join(["?"] * len(products))
            where += f" AND product_code IN ({placeholders})"
            params.extend(products)
        df = conn.execute(
            f"""
            SELECT attained_age_band,
                   SUM(actual_ci_claims)   AS actual_ci_claims,
                   SUM(expected_ci_claims) AS expected_ci_claims,
                   SUM(ci_exposure_count)  AS ci_exposure_years,
                   CASE WHEN SUM(expected_ci_claims) > 0
                        THEN SUM(actual_ci_claims) / SUM(expected_ci_claims)
                        ELSE NULL END AS ae_ci
            FROM gold_ae_results
            WHERE {where}
            GROUP BY attained_age_band
            ORDER BY attained_age_band
            """,
            params,
        ).df()
        return df
    finally:
        conn.close()


def _query_ci_drill(run_id: str, age_band: str, illness_code: str) -> pd.DataFrame:
    """Underlying CI_CLAIM exposure segments for a specific age band × illness code."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT
                md5(policy_id)[:12]  AS policy_hash,
                product_code,
                attained_age_band,
                CASE
                    WHEN face_amount_wtd_avg < 100000  THEN '<100K'
                    WHEN face_amount_wtd_avg < 250000  THEN '100K-250K'
                    WHEN face_amount_wtd_avg < 500000  THEN '250K-500K'
                    WHEN face_amount_wtd_avg < 1000000 THEN '500K-1M'
                    ELSE '>1M'
                END AS face_band,
                ROUND(exposure_years, 4) AS exposure_years,
                illness_code,
                calendar_year
            FROM gold_exposure_segments
            WHERE study_run_id = ?
              AND decrement_type = 'CI_CLAIM'
              AND attained_age_band = ?
              AND illness_code = ?
            ORDER BY product_code, calendar_year
            LIMIT 200
            """,
            [run_id, age_band, illness_code],
        ).df()
    finally:
        conn.close()


def _query_ci_by_age_illness(run_id: str) -> pd.DataFrame:
    """CI A/E by attained_age_band × illness_code for heat map."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT attained_age_band, illness_code,
                   SUM(actual_ci_claims) AS actual_ci_claims,
                   SUM(expected_ci_claims) AS expected_ci_claims,
                   CASE WHEN SUM(expected_ci_claims) > 0
                        THEN SUM(actual_ci_claims) / SUM(expected_ci_claims)
                        ELSE NULL END AS ae_ci
            FROM gold_ae_results
            WHERE study_run_id = ? AND illness_code IS NOT NULL
              AND expected_ci_claims > 0
            GROUP BY attained_age_band, illness_code
            ORDER BY attained_age_band, illness_code
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


def _query_ci_by_gender(run_id: str) -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT gender,
                   SUM(actual_ci_claims)   AS actual_ci_claims,
                   SUM(expected_ci_claims)  AS expected_ci_claims,
                   CASE WHEN SUM(expected_ci_claims)>0
                        THEN SUM(actual_ci_claims)/SUM(expected_ci_claims)
                        ELSE NULL END AS ae_ci
            FROM gold_ae_results
            WHERE study_run_id = ? AND illness_code IS NOT NULL
            GROUP BY gender ORDER BY gender
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


# ── Run selector ─────────────────────────────────────────────────────────────

run_ids = _load_run_ids()
if not run_ids:
    st.info(
        "No CI incidence results found. This may indicate no CI rider policies "
        "were present in the study, or CI A/E has not been calculated yet."
    )
    st.stop()

run_labels  = {r: lbl for r, lbl in run_ids}
run_id_list = [r for r, _ in run_ids]
default_run = st.session_state.get("active_run_id", run_id_list[0])
if default_run not in run_id_list:
    default_run = run_id_list[0]

col_run, col_product, col_gender = st.columns([3, 1, 1])
selected_run = col_run.selectbox(
    "Study run",
    options=run_id_list,
    index=run_id_list.index(default_run),
    format_func=lambda r: run_labels.get(r, r),
)
product_filter = col_product.multiselect("Product", options=["TERM", "WL", "UL", "ULSG", "IUL"])
gender_filter = col_gender.multiselect("Gender", options=["M", "F"])

# ── Summary metrics ───────────────────────────────────────────────────────────

ci_by_illness = _query_ci_by_illness(
    selected_run, gender_filter or None, product_filter or None
)
total_actual = int(ci_by_illness["actual_ci_claims"].sum())
total_expected = float(ci_by_illness["expected_ci_claims"].sum())
agg_ae = total_actual / total_expected if total_expected > 0 else float("nan")
ci_exposure = float(ci_by_illness["ci_exposure_years"].sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Actual CI Claims", f"{total_actual:,}")
c2.metric("Expected CI Claims", f"{total_expected:,.1f}")
c3.metric("Aggregate CI A/E", f"{agg_ae:.3f}" if not np.isnan(agg_ae) else "—")
c4.metric("CI Exposure Years", f"{ci_exposure:,.0f}")

spec_low, spec_high = 0.90, 1.10
if not np.isnan(agg_ae):
    if spec_low <= agg_ae <= spec_high:
        st.success(f"CI A/E {agg_ae:.3f} is within specification range {spec_low}–{spec_high}.")
    else:
        st.warning(f"CI A/E {agg_ae:.3f} is outside specification range {spec_low}–{spec_high}.")

# Note: CI claims from products not in this study run (e.g. IUL when run covers TERM/WL/UL only)
# are counted in exposure segments but excluded from A/E results. Counts here reflect study scope.
st.caption(
    f"CI claims shown ({total_actual:,}) reflect the products included in this study run. "
    "IUL or VUL policies processed under the UL/VUL product code appear in exposure but may "
    "be excluded if their sub-product was not selected."
)

# ── A/E by illness code (horizontal bar chart) ────────────────────────────────

st.subheader("CI A/E by Illness Code")

if not ci_by_illness.empty and ci_by_illness["ae_ci"].notna().any():
    ci_plot = ci_by_illness.dropna(subset=["ae_ci"]).sort_values("ae_ci")
    ci_plot["label"] = ci_plot["illness_code"] + " — " + ci_plot["illness_name"]
    ci_plot["colour"] = ci_plot["ae_ci"].apply(
        lambda v: "#e74c3c" if v > 1.10 else ("#a9dfbf" if v < 0.90 else "#2980b9")
    )

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        y=ci_plot["label"],
        x=ci_plot["ae_ci"],
        orientation="h",
        marker_color=ci_plot["colour"],
        text=ci_plot["ae_ci"].map(lambda v: f"{v:.3f}"),
        textposition="outside",
        customdata=ci_plot[["actual_ci_claims", "expected_ci_claims"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "A/E: %{x:.3f}<br>"
            "Actual: %{customdata[0]}<br>"
            "Expected: %{customdata[1]:.1f}<extra></extra>"
        ),
    ))
    fig_bar.add_vline(x=1.0, line_dash="dash", line_color="black", annotation_text="1.00")
    fig_bar.add_vrect(
        x0=spec_low, x1=spec_high, fillcolor="#eafaf1", opacity=0.3,
        line_width=0, annotation_text="Spec range", annotation_position="top left",
    )
    fig_bar.update_layout(
        title="CI Incidence A/E by Illness Code",
        xaxis_title="A/E Ratio",
        yaxis_title="Illness Code",
        height=420,
        margin=dict(l=250),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

# ── Detail table ──────────────────────────────────────────────────────────────

disp = ci_by_illness.copy()
disp["ae_ci"] = disp["ae_ci"].map(lambda v: f"{v:.3f}" if pd.notna(v) else "—")
disp["credibility_z"] = disp["credibility_z"].map(lambda v: f"{v:.3f}" if pd.notna(v) else "—")
st.dataframe(
    disp[["illness_code", "illness_name", "actual_ci_claims", "expected_ci_claims",
          "ci_exposure_years", "ae_ci", "credibility_z"]].rename(columns={
        "illness_code": "Code", "illness_name": "Illness",
        "actual_ci_claims": "Actual", "expected_ci_claims": "Expected",
        "ci_exposure_years": "Exposure Years", "ae_ci": "A/E", "credibility_z": "Cred Z",
    }),
    use_container_width=True,
    hide_index=True,
)

# ── A/E by attained age band ──────────────────────────────────────────────────

st.subheader("CI A/E by Attained Age Band")
st.caption("CI incidence should increase with age — bars shift right for older bands if the age pattern is correctly calibrated.")

age_band_df = _query_ci_by_age_band(
    selected_run, gender_filter or None, product_filter or None
)
if not age_band_df.empty and age_band_df["ae_ci"].notna().any():
    ab_plot = age_band_df.dropna(subset=["ae_ci"])
    ab_plot["colour"] = ab_plot["ae_ci"].apply(
        lambda v: "#e74c3c" if v > spec_high else ("#a9dfbf" if v < spec_low else "#2980b9")
    )
    fig_age = go.Figure()
    fig_age.add_trace(go.Bar(
        y=ab_plot["attained_age_band"],
        x=ab_plot["ae_ci"],
        orientation="h",
        marker_color=ab_plot["colour"],
        text=ab_plot["ae_ci"].map(lambda v: f"{v:.3f}"),
        textposition="outside",
        customdata=ab_plot[["actual_ci_claims", "expected_ci_claims", "ci_exposure_years"]].values,
        hovertemplate=(
            "<b>Age band: %{y}</b><br>"
            "A/E: %{x:.3f}<br>"
            "Actual: %{customdata[0]}<br>"
            "Expected: %{customdata[1]:.1f}<br>"
            "Exposure years: %{customdata[2]:,.0f}<extra></extra>"
        ),
    ))
    fig_age.add_vline(x=1.0, line_dash="dash", line_color="black", annotation_text="1.00")
    fig_age.add_vrect(
        x0=spec_low, x1=spec_high, fillcolor="#eafaf1", opacity=0.3, line_width=0,
    )
    fig_age.update_layout(
        title="CI Incidence A/E by Attained Age Band",
        xaxis_title="A/E Ratio",
        yaxis_title="Age Band",
        height=max(300, len(ab_plot) * 28 + 80),
        margin=dict(l=80),
        yaxis=dict(categoryorder="category ascending"),
    )
    st.plotly_chart(fig_age, use_container_width=True)
else:
    st.info("No age band data available. Re-run the study after updating the A/E engine.")

# ── Heat map: age band × illness code ────────────────────────────────────────

st.subheader("CI A/E Heat Map — Attained Age × Illness Code")
age_ill_df = _query_ci_by_age_illness(selected_run)
if not age_ill_df.empty:
    pivot_hm = age_ill_df.pivot_table(
        index="attained_age_band", columns="illness_code",
        values="ae_ci", aggfunc="mean",
    )
    if not pivot_hm.empty:
        fig_hm = go.Figure(
            go.Heatmap(
                z=pivot_hm.values,
                x=pivot_hm.columns.tolist(),
                y=pivot_hm.index.tolist(),
                colorscale=[
                    [0.0, "#2980b9"], [0.45, "#85c1e9"],
                    [0.5, "#ffffff"],
                    [0.55, "#f1948a"], [1.0, "#c0392b"],
                ],
                zmin=0.0,
                zmid=1.0,
                zmax=2.0,
                colorbar={"title": "A/E"},
                text=np.round(pivot_hm.values, 2),
                texttemplate="%{text}",
            )
        )
        fig_hm.update_layout(
            title="CI A/E by Attained Age Band × Illness Code",
            xaxis_title="Illness Code",
            yaxis_title="Attained Age Band",
            height=420,
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.caption(
            "Colour scale is clamped to A/E 0–2 so the on-target range is readable. "
            "Cells with very few expected claims can show extreme A/E (deep red) — these are "
            "low-credibility (Z < 0.5) and reflect small claim volumes, not model error."
        )
else:
    st.info("Insufficient CI data for age × illness heat map.")

# ── By gender ─────────────────────────────────────────────────────────────────

st.subheader("CI A/E by Gender")
gender_df = _query_ci_by_gender(selected_run)
if not gender_df.empty and gender_df["ae_ci"].notna().any():
    gdf = gender_df.dropna(subset=["ae_ci"]).copy()
    _gconn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        _gender_method = get_run_method(_gconn, selected_run)
    finally:
        _gconn.close()
    gdf["cred_z"] = credibility_z(gdf["actual_ci_claims"], method=_gender_method)
    g_lo, g_hi = poisson_ci(gdf["ae_ci"], gdf["actual_ci_claims"])
    gdf["ci_lo"], gdf["ci_hi"] = g_lo, g_hi
    g_colours = [
        "lightgrey" if z < 0.5 else ("#e74c3c" if g == "F" else "#2980b9")
        for z, g in zip(gdf["cred_z"], gdf["gender"])
    ]
    fig_g = go.Figure()
    fig_g.add_bar(
        x=gdf["gender"],
        y=gdf["ae_ci"],
        marker_color=g_colours,
        text=gdf["ae_ci"].map(lambda v: f"{v:.2%}"),
        textposition="outside",
        error_y=dict(
            type="data",
            array=(gdf["ci_hi"] - gdf["ae_ci"]).clip(lower=0).tolist(),
            arrayminus=(gdf["ae_ci"] - gdf["ci_lo"]).clip(lower=0).tolist(),
            visible=True,
        ),
    )
    fig_g.add_hline(y=1.0, line_dash="dash", line_color="black")
    fig_g.update_traces(cliponaxis=False)
    fig_g.update_layout(
        title="Aggregate CI Incidence A/E by Gender",
        xaxis_title="Gender",
        yaxis=dict(title="CI A/E", tickformat=".0%"),
        height=320,
        showlegend=False,
    )
    st.plotly_chart(fig_g, use_container_width=True)
    st.caption("Error bars show the 95% Poisson CI (FR-1A-25). Grey bars: credibility Z < 0.5.")

# ── Drill-through: underlying exposure records ────────────────────────────────

st.subheader("Drill-Through: Underlying CI Claim Records")
st.caption(
    "Select an age band and illness code to view the underlying exposure segments "
    "for CI_CLAIM decrements. Policy IDs are masked to a 12-character hash."
)

_age_bands = sorted(age_ill_df["attained_age_band"].dropna().unique().tolist()) if not age_ill_df.empty else []
_ill_codes  = sorted(age_ill_df["illness_code"].dropna().unique().tolist()) if not age_ill_df.empty else []

if _age_bands and _ill_codes:
    col_dt_age, col_dt_ill = st.columns(2)
    drill_age = col_dt_age.selectbox(
        "Age band", options=_age_bands, key="drill_age",
    )
    drill_ill = col_dt_ill.selectbox(
        "Illness code",
        options=_ill_codes,
        format_func=lambda c: f"{c} — {ILLNESS_NAMES.get(c, c)}",
        key="drill_ill",
    )
    drill_df = _query_ci_drill(selected_run, drill_age, drill_ill)
    if drill_df.empty:
        st.info(f"No CI_CLAIM records for age band {drill_age} × {drill_ill}.")
    else:
        st.dataframe(
            drill_df.rename(columns={
                "policy_hash": "Policy (hashed)",
                "product_code": "Product",
                "attained_age_band": "Age Band",
                "face_band": "Face Amount Band",
                "exposure_years": "Exposure Yrs",
                "illness_code": "Illness Code",
                "calendar_year": "Cal Year",
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"{len(drill_df)} record(s) shown (max 200).")
else:
    st.info("Drill-through available after re-running the study with updated A/E engine.")
