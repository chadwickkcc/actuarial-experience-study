"""Mortality A/E Explorer — pivot table, heat map, confidence intervals."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH
from ui.stats_helpers import credibility_z, get_run_method
from src.aggregation.aggregator import aggregate_ae, get_drill_through_records

st.set_page_config(page_title="Mortality A/E Explorer", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Mortality A/E Explorer")

DIMS = [
    "product_code", "plan_code", "gender", "smoker_status", "risk_class",
    "issue_age_band", "attained_age_band", "duration_band",
    "policy_year", "calendar_year", "is_plt_flag", "premium_jump_ratio_band",
    "distribution_channel",
]

MORTALITY_MEASURES = [
    "ae_count", "ae_amount", "actual_deaths_count", "expected_deaths_count",
    "actual_deaths_amount", "expected_deaths_amount",
    "exposure_count", "exposure_amount",
    "credibility_z", "credibility_wtd_ae",
]

MEASURE_LABELS = {
    "ae_count": "A/E (count basis)",
    "ae_amount": "A/E (amount basis)",
    "actual_deaths_count": "Actual Deaths (count)",
    "expected_deaths_count": "Expected Deaths (count)",
    "actual_deaths_amount": "Actual Deaths ($)",
    "expected_deaths_amount": "Expected Deaths ($)",
    "exposure_count": "Exposure (years)",
    "exposure_amount": "Exposure ($ × years)",
    "credibility_z": "Credibility Z",
    "credibility_wtd_ae": "Credibility-Weighted A/E",
}


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



# ── Study run selector (sidebar) ─────────────────────────────────────────────

with st.sidebar:
    st.header("Study Run")
    run_ids = _load_run_ids()
    if not run_ids:
        st.error("No A/E results found. Run a study on the Study Setup page first.")
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

# ── Controls expander (main area) ─────────────────────────────────────────────

with st.expander("⚙️ Study Controls", expanded=True):
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

    with ctrl_col1:
        st.markdown("**Pivot Dimensions**")
        st.caption("→ Controls the pivot table")
        row_dim = st.selectbox("Row dimension", options=DIMS, index=DIMS.index("attained_age_band"))
        col_dim = st.selectbox("Column dimension", options=["(none)"] + DIMS, index=0)
        col_dims = [] if col_dim == "(none)" else [col_dim]
        measure = st.selectbox(
            "Measure",
            options=MORTALITY_MEASURES,
            format_func=lambda m: MEASURE_LABELS[m],
        )

    with ctrl_col2:
        st.markdown("**Display Options**")
        st.caption("→ Basis and CI toggle control the heat map and CI chart")
        basis_toggle = st.radio("Basis", options=["Count", "Amount"], index=0, horizontal=True)
        show_ci = st.checkbox("Show confidence intervals", value=True)
        plt_filter_opt = st.radio(
            "PLT / Level filter",
            options=["All", "Level only", "PLT only"],
            index=0,
            horizontal=True,
        )

    with ctrl_col3:
        st.markdown("**Filters** _(leave blank = all)_")
        product_filter = st.multiselect(
            "Product", options=["TERM", "WL", "UL", "ULSG", "IUL"],
        )
        gender_filter = st.multiselect(
            "Gender", options=_load_filter_values(selected_run, "gender"),
        )
        smoker_filter = st.multiselect(
            "Smoker status", options=_load_filter_values(selected_run, "smoker_status"),
        )
        risk_class_filter = st.multiselect(
            "Risk class", options=_load_filter_values(selected_run, "risk_class"),
        )
        plan_filter = st.multiselect(
            "Plan code", options=_load_filter_values(selected_run, "plan_code"),
        )

# ── Build filters dict ────────────────────────────────────────────────────────

filters: dict[str, list] = {}
if product_filter:
    filters["product_code"] = product_filter
if gender_filter:
    filters["gender"] = gender_filter
if smoker_filter:
    filters["smoker_status"] = smoker_filter
if risk_class_filter:
    filters["risk_class"] = risk_class_filter
if plan_filter:
    filters["plan_code"] = plan_filter
if plt_filter_opt == "Level only":
    filters["is_plt_flag"] = [False]
elif plt_filter_opt == "PLT only":
    filters["is_plt_flag"] = [True]

# ── Pivot table ───────────────────────────────────────────────────────────────

st.subheader("A/E Pivot Table")
try:
    pivot_df = aggregate_ae(
        db_path=DB_PATH,
        study_run_id=selected_run,
        row_dims=[row_dim],
        col_dims=col_dims,
        filters=filters,
        measure=measure,
    )

    if pivot_df.empty:
        st.info("No data for the selected filters.")
    else:
        # Annotate the pivot's index and column axes with their dimension names
        # so the CSV download header is self-describing (e.g. "policy_year=1"
        # rather than just "1"). Streamlit's dataframe download preserves
        # column names but drops index/column .name attributes — apply via
        # rename so the labels survive the export.
        if col_dims:
            _col_dim = col_dims[0]
            pivot_df = pivot_df.rename(
                columns=lambda c: f"{_col_dim}={c}" if str(c) != "Total" else c
            )
        pivot_df.index.name = row_dim

        def _style_ae(val):
            if not isinstance(val, (int, float)) or np.isnan(val):
                return ""
            if measure in ("ae_count", "ae_amount", "credibility_wtd_ae"):
                if val < 0.85:
                    return "background-color: #aed6f1"
                elif val > 1.15:
                    return "background-color: #f1948a"
                elif 0.95 <= val <= 1.05:
                    return "background-color: #a9dfbf"
            if measure == "credibility_z" and val < 0.5:
                return "color: #aaa; font-style: italic"
            return ""

        styled = pivot_df.style.map(_style_ae)
        if measure in ("ae_count", "ae_amount", "credibility_wtd_ae"):
            styled = styled.format(
                lambda v: f"{v:.3f}" if isinstance(v, float) and not np.isnan(v) else str(v),
                na_rep="—",
            )
        st.dataframe(styled, use_container_width=True)
        st.caption(
            "Blue = A/E < 0.85 (better than expected).  "
            "Red = A/E > 1.15 (worse than expected).  "
            "Green = within ±5% of expected.  "
            "Grey italic = credibility Z < 0.5."
        )

except Exception as exc:
    st.error(f"Could not load pivot: {exc}")

# ── Heat map: A/E by selected row × col dimensions ───────────────────────────

hm_row = row_dim
hm_col = col_dims[0] if col_dims else "duration_band"
st.subheader(f"A/E Heat Map — {hm_row} × {hm_col}")
try:
    heatmap_measure = "ae_count" if basis_toggle == "Count" else "ae_amount"
    hm_df = aggregate_ae(
        db_path=DB_PATH,
        study_run_id=selected_run,
        row_dims=[hm_row],
        col_dims=[hm_col],
        filters=filters,
        measure=heatmap_measure,
    )

    if not hm_df.empty:
        # Remove totals row/column; works for both flat and MultiIndex columns
        hm_df = hm_df.loc[hm_df.index != "Total", :]
        hm_df = hm_df.drop(
            columns=[c for c in hm_df.columns if "Total" in str(c)], errors="ignore"
        )
        z = hm_df.values.astype(float)
        fig_hm = go.Figure(
            go.Heatmap(
                z=z,
                x=[str(c) for c in hm_df.columns],
                y=[str(r) for r in hm_df.index],
                colorscale=[
                    [0.0, "#2980b9"], [0.4, "#85c1e9"],
                    [0.5, "#ffffff"],
                    [0.6, "#f1948a"], [1.0, "#c0392b"],
                ],
                zmin=0.0,
                zmid=1.0,
                zmax=2.0,
                colorbar={"title": "A/E"},
                text=np.where(np.isnan(z), "", np.round(z, 3).astype(str)),
                texttemplate="%{text}",
            )
        )
        fig_hm.update_layout(
            title=f"A/E ({basis_toggle} basis) by {hm_row} × {hm_col}",
            xaxis_title=hm_col,
            yaxis_title=hm_row,
            height=420,
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.caption(
            "Colour scale clamped to A/E 0–2 (centred at 1.0) so on-target cells read clearly; "
            "sparse cells with very few claims can exceed 2.0 (deepest red) — these are "
            "low-credibility and shown in the cell labels."
        )
    else:
        st.info("No data available for the heat map with the current filters.")

except Exception as exc:
    st.warning(f"Heat map skipped: {exc}")

# ── Bar chart with confidence intervals ───────────────────────────────────────

if show_ci:
    st.subheader("A/E with 95% Confidence Intervals")
    try:
        ae_col        = "actual_deaths_count" if basis_toggle == "Count" else "actual_deaths_amount"
        exp_col       = "expected_deaths_count" if basis_toggle == "Count" else "expected_deaths_amount"

        where_parts = [
            "study_run_id = ?",
            "illness_code IS NULL",
            f"{row_dim} IS NOT NULL",
            f"{ae_col} IS NOT NULL",
            f"{exp_col} IS NOT NULL",
            f"{exp_col} > 0",
        ]
        params: list = [selected_run]
        for dim, vals in filters.items():
            if vals:
                placeholders = ", ".join(["?" for _ in vals])
                where_parts.append(f"{dim} IN ({placeholders})")
                params.extend(vals)

        conn = duckdb.connect(str(DB_PATH), read_only=True)
        ci_df = conn.execute(
            f"""
            SELECT {row_dim},
                   SUM(CAST({ae_col} AS DOUBLE)) / NULLIF(SUM({exp_col}), 0) AS ae,
                   SUM({ae_col}) AS total_actuals,
                   SUM(actual_deaths_count) AS total_claims
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
            # Compute Poisson CI inline: SE = A/E / sqrt(actual_claims)
            ci_df["se"] = ci_df["ae"] / ci_df["total_actuals"].clip(lower=1).pow(0.5)
            ci_df["ci_lower"] = (ci_df["ae"] - 1.96 * ci_df["se"]).clip(lower=0)
            ci_df["ci_upper"] = ci_df["ae"] + 1.96 * ci_df["se"]
            # Credibility Z recomputed from the aggregate claim count (FR-1A-24),
            # not averaged from per-cell values.
            ci_df["z"] = credibility_z(ci_df["total_claims"], method=cred_method)
            ci_df["low_credibility"] = ci_df["z"] < 0.5
            ci_df["colour"] = ci_df["low_credibility"].map({True: "#aaa", False: "#2980b9"})

            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                x=ci_df[row_dim].astype(str),
                y=ci_df["ae"],
                name="A/E",
                marker_color=ci_df["colour"],
                error_y=dict(
                    type="data",
                    symmetric=False,
                    array=(ci_df["ci_upper"] - ci_df["ae"]).clip(lower=0),
                    arrayminus=(ci_df["ae"] - ci_df["ci_lower"]).clip(lower=0),
                    visible=True,
                ),
            ))
            fig_bar.add_hline(y=1.0, line_dash="dash", line_color="black", annotation_text="1.00")
            fig_bar.update_layout(
                title=f"A/E ({basis_toggle}) by {row_dim} with 95% CI",
                xaxis=dict(title=row_dim, automargin=True, tickangle=-30),
                yaxis_title="A/E Ratio",
                height=380,
                margin=dict(b=120),
            )
            fig_bar.update_traces(cliponaxis=False)
            st.plotly_chart(fig_bar, use_container_width=True)
            st.caption(
                "Error bars = 95% Poisson confidence intervals.  "
                "Grey bars = credibility Z < 0.5 (low data volume — treat with caution)."
            )
        else:
            st.info("No data available for the CI chart with the current filters.")

    except Exception as exc:
        st.warning(f"CI chart skipped: {exc}")

# ── Drill-through ─────────────────────────────────────────────────────────────

with st.expander("Drill-Through: Underlying Exposure Records"):
    st.markdown(
        """
        **How to use:** Select a dimension and a specific value to view the underlying
        seriatim exposure records that make up that cell's A/E result.

        - **policy_id** is masked to a SHA-256 hash (PII protection)
        - **face_amount** is shown as a band (e.g. \$100K–\$250K)
        - Up to 200 records are returned; apply filters above to narrow the slice
        - **Tip:** to investigate an outlier cell (e.g. A/E > 1.15 at age 30–34),
          select `attained_age_band` → `30-34` to see which policies are driving it
        """
    )
    drill_col, drill_val_col = st.columns(2)
    drill_dim = drill_col.selectbox("Dimension", options=DIMS, key="drill_dim")
    drill_vals = _load_filter_values(selected_run, drill_dim)
    drill_val = drill_val_col.selectbox("Value", options=drill_vals or ["(none)"], key="drill_val")

    if drill_vals and drill_val != "(none)":
        drilled = get_drill_through_records(
            db_path=DB_PATH,
            study_run_id=selected_run,
            dimension_filter={drill_dim: str(drill_val)},
            limit=200,
        )
        if not drilled.empty:
            st.dataframe(drilled, use_container_width=True, hide_index=True)
        else:
            st.info("No records found for this selection.")
