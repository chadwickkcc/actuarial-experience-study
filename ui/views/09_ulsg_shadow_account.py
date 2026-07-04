"""ULSG Shadow Account Monitor — FR-1B-14.

Displays NLG funding risk split by product design type:

  SHADOW_ACCT: primary risk indicator = shadow_account_value ≤ 0
               (notional fund exhausted — NLG may lapse)

  SPEC_PREM:   primary risk indicator = cumulative_premiums_paid /
               cumulative_nlp_required < 1.0
               (policy has not met the cumulative NLP schedule)

The stored shadow_account_funding_ratio (= shadow_account_value /
cumulative_nlp_required) is shown as a supplemental monitoring metric only;
it is NOT the contractual pass/fail test for either design type.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH

st.set_page_config(page_title="ULSG Shadow Account Monitor", layout="wide")

from ui.config import require_auth
require_auth()
st.title("ULSG Shadow Account Monitor")
st.caption(
    "NLG risk interpretation differs by product design. "
    "SHADOW_ACCT: primary risk = shadow account value ≤ 0 (notional fund exhausted). "
    "SPEC_PREM: primary risk = cumulative premiums paid < cumulative no-lapse premium required "
    "(funding ratio = cumul_premiums_paid / cumul_nlp_required < 1.0). "
    "The ratio shadow_account_value / cumulative_nlp_required is a supplemental monitoring "
    "metric only — not the contractual pass/fail test for either design."
)


def _load_run_ids() -> list[tuple[str, str]]:
    """Return (etl_run_id, label) pairs for ULSG ETL runs."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT _etl_run_id, MIN(_load_ts) AS load_ts
            FROM silver_ul_policies
            WHERE is_ulsg_flag = TRUE
            GROUP BY _etl_run_id
            ORDER BY load_ts DESC
            """
        ).fetchall()
        result = []
        for run_id, load_ts in rows:
            label = f"{str(load_ts)[:16]} — ULSG" if load_ts else run_id
            result.append((run_id, label))
        return result
    finally:
        conn.close()


def _load_funding_ratios(run_id: str) -> pd.DataFrame:
    """Load ULSG policies with type-correct risk metrics."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(
            """
            SELECT
                policy_id,
                status_code,
                secondary_guarantee_type,
                shadow_account_value,
                shadow_account_funding_ratio,
                account_value_eom,
                cumulative_premiums_paid,
                cumulative_nlp_required,
                issue_age_anb,
                gender,
                no_lapse_guarantee_period,
                -- Spec-prem contractual ratio: cumul premiums paid / cumul NLP required
                CASE
                    WHEN cumulative_nlp_required > 0
                    THEN cumulative_premiums_paid / cumulative_nlp_required
                    ELSE NULL
                END AS nlg_funding_ratio,
                -- SHADOW_ACCT tier: by shadow account value
                CASE
                    WHEN secondary_guarantee_type != 'SHADOW_ACCT' THEN NULL
                    WHEN shadow_account_value IS NULL               THEN 'Unknown'
                    WHEN shadow_account_value <= 0                  THEN 'At risk (AV ≤ 0)'
                    WHEN shadow_account_value < 5000                THEN 'Low (<$5k)'
                    ELSE 'OK'
                END AS shadow_acct_tier,
                -- SPEC_PREM tier: by cumul_premiums_paid / cumul_nlp_required
                CASE
                    WHEN secondary_guarantee_type != 'SPEC_PREM'                    THEN NULL
                    WHEN cumulative_nlp_required IS NULL
                      OR cumulative_nlp_required = 0                                THEN 'Unknown'
                    WHEN cumulative_premiums_paid / cumulative_nlp_required >= 1.5  THEN '≥1.50 (Well-funded)'
                    WHEN cumulative_premiums_paid / cumulative_nlp_required >= 1.0  THEN '1.00–1.49 (Funded)'
                    WHEN cumulative_premiums_paid / cumulative_nlp_required >= 0.75 THEN '0.75–0.99 (At risk)'
                    WHEN cumulative_premiums_paid / cumulative_nlp_required >= 0.50 THEN '0.50–0.74 (At risk)'
                    ELSE '<0.50 (Critical)'
                END AS spec_prem_tier
            FROM silver_ul_policies
            WHERE _etl_run_id = ?
              AND is_ulsg_flag = TRUE
            ORDER BY policy_id
            """,
            [run_id],
        ).df()
    finally:
        conn.close()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Controls")
    run_ids = _load_run_ids()
    if not run_ids:
        st.error("No ULSG policies found. Run the pipeline with UL product first.")
        st.stop()

    run_labels  = {r: lbl for r, lbl in run_ids}
    run_id_list = [r for r, _ in run_ids]
    default_run = st.session_state.get("active_run_id", run_id_list[0])
    if default_run not in run_id_list:
        default_run = run_id_list[0]

    selected_run = st.selectbox(
        "ETL run",
        options=run_id_list,
        index=run_id_list.index(default_run),
        format_func=lambda r: run_labels.get(r, r),
    )

# ── Page controls ─────────────────────────────────────────────────────────────

flt_col1, flt_col2 = st.columns([1, 2])
with flt_col1:
    show_if_only = st.checkbox("In-force policies only", value=True)
with flt_col2:
    nlg_type_filter = st.multiselect(
        "NLG type",
        options=["SHADOW_ACCT", "SPEC_PREM"],
        help="Filter by secondary guarantee type.",
    )

# ── Load data ─────────────────────────────────────────────────────────────────

df = _load_funding_ratios(selected_run)

if show_if_only:
    df = df[df["status_code"] == "IF"]
if nlg_type_filter:
    df = df[df["secondary_guarantee_type"].isin(nlg_type_filter)]

if df.empty:
    st.warning("No ULSG policies found for the selected filters.")
    st.stop()

# ── Compute type-correct at-risk flags ────────────────────────────────────────

df["is_at_risk"] = (
    ((df["secondary_guarantee_type"] == "SHADOW_ACCT") &
     (df["shadow_account_value"].fillna(1.0) <= 0)) |
    ((df["secondary_guarantee_type"] == "SPEC_PREM") &
     (df["nlg_funding_ratio"].fillna(1.0) < 1.0))
)

shadow_df    = df[df["secondary_guarantee_type"] == "SHADOW_ACCT"]
spec_prem_df = df[df["secondary_guarantee_type"] == "SPEC_PREM"]

shadow_at_risk    = int((shadow_df["shadow_account_value"].fillna(1.0) <= 0).sum())
spec_prem_at_risk = int((spec_prem_df["nlg_funding_ratio"].fillna(1.0) < 1.0).sum())
total_at_risk     = shadow_at_risk + spec_prem_at_risk
total             = len(df)

# ── Summary metrics ───────────────────────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total ULSG Policies", f"{total:,}")
col2.metric("SHADOW_ACCT Policies", f"{len(shadow_df):,}")
col3.metric(
    "SHADOW_ACCT At-risk (AV≤0)",
    f"{shadow_at_risk:,}",
    f"{shadow_at_risk / max(len(shadow_df), 1) * 100:.1f}%",
    delta_color="inverse",
)
col4.metric("SPEC_PREM Policies", f"{len(spec_prem_df):,}")
col5.metric(
    "SPEC_PREM At-risk (prem<NLP)",
    f"{spec_prem_at_risk:,}",
    f"{spec_prem_at_risk / max(len(spec_prem_df), 1) * 100:.1f}%",
    delta_color="inverse",
)

if total_at_risk > 0:
    parts = []
    if shadow_at_risk > 0:
        parts.append(f"{shadow_at_risk} SHADOW_ACCT (shadow account value ≤ 0)")
    if spec_prem_at_risk > 0:
        parts.append(f"{spec_prem_at_risk} SPEC_PREM (cumulative premiums below NLP schedule)")
    st.warning(
        f"**{total_at_risk} ULSG {'policy' if total_at_risk == 1 else 'policies'} "
        f"may be at risk of losing their NLG**: "
        + "; ".join(parts)
        + ". Subject to contractual grace, catch-up, loan, and withdrawal provisions."
    )

# ── Distribution charts split by NLG type ────────────────────────────────────

st.subheader("NLG Risk Distribution by Design Type")

tab_shadow, tab_spec = st.tabs(["SHADOW_ACCT NLG", "SPEC_PREM NLG"])

with tab_shadow:
    st.markdown(
        "**Primary risk test:** `shadow_account_value ≤ 0` — the notional shadow fund is exhausted."
    )
    col_hist, col_pie = st.columns([2, 1])

    with col_hist:
        sav_data = shadow_df["shadow_account_value"].dropna()
        if len(sav_data) > 0:
            fig_h = go.Figure()
            fig_h.add_trace(go.Histogram(
                x=sav_data,
                nbinsx=40,
                marker=dict(
                    color=["#e74c3c" if v <= 0 else "#27ae60" for v in sav_data],
                    line=dict(width=0.5, color="white"),
                ),
                name="Policies",
            ))
            fig_h.add_vline(
                x=0, line_dash="dash", line_color="black", line_width=2,
                annotation_text="Risk threshold (AV = 0)", annotation_position="top right",
            )
            fig_h.update_layout(
                title="Shadow Account Value Distribution",
                xaxis_title="Shadow Account Value ($)",
                yaxis_title="Number of Policies",
                height=380,
                showlegend=False,
            )
            st.plotly_chart(fig_h, use_container_width=True)
        else:
            st.info("No SHADOW_ACCT data.")

    with col_pie:
        tier_colours = {
            "At risk (AV ≤ 0)": "#e74c3c",
            "Low (<$5k)": "#f39c12",
            "OK": "#27ae60",
            "Unknown": "#bdc3c7",
        }
        tier_order = ["At risk (AV ≤ 0)", "Low (<$5k)", "OK", "Unknown"]
        tc = shadow_df["shadow_acct_tier"].value_counts().reset_index()
        tc.columns = ["tier", "count"]
        tc["order"] = tc["tier"].apply(lambda t: tier_order.index(t) if t in tier_order else 99)
        tc = tc.sort_values("order")
        fig_p = go.Figure(go.Pie(
            labels=tc["tier"],
            values=tc["count"],
            marker=dict(colors=[tier_colours.get(t, "#bdc3c7") for t in tc["tier"]]),
            hole=0.3,
        ))
        fig_p.update_layout(title="SHADOW_ACCT Tier Breakdown", height=380)
        st.plotly_chart(fig_p, use_container_width=True)

with tab_spec:
    st.markdown(
        "**Primary risk test:** `cumulative_premiums_paid / cumulative_nlp_required < 1.0` — "
        "policy has not met the cumulative no-lapse premium schedule."
    )
    col_hist, col_pie = st.columns([2, 1])

    with col_hist:
        ratio_data = spec_prem_df["nlg_funding_ratio"].dropna()
        if len(ratio_data) > 0:
            fig_h2 = go.Figure()
            fig_h2.add_trace(go.Histogram(
                x=ratio_data,
                nbinsx=40,
                marker=dict(
                    color=["#e74c3c" if v < 1.0 else "#27ae60" for v in ratio_data],
                    line=dict(width=0.5, color="white"),
                ),
                name="Policies",
            ))
            fig_h2.add_vline(
                x=1.0, line_dash="dash", line_color="black", line_width=2,
                annotation_text="NLG threshold (1.0)", annotation_position="top right",
            )
            fig_h2.update_layout(
                title="Spec Prem NLG Funding Ratio Distribution",
                xaxis_title="Cumul Premiums Paid / Cumul NLP Required",
                yaxis_title="Number of Policies",
                height=380,
                showlegend=False,
            )
            st.plotly_chart(fig_h2, use_container_width=True)
        else:
            st.info("No SPEC_PREM data.")

    with col_pie:
        sp_tier_order = [
            "≥1.50 (Well-funded)", "1.00–1.49 (Funded)",
            "0.75–0.99 (At risk)", "0.50–0.74 (At risk)", "<0.50 (Critical)", "Unknown",
        ]
        sp_tier_colours = ["#27ae60", "#a9dfbf", "#f39c12", "#e67e22", "#e74c3c", "#bdc3c7"]
        tc2 = spec_prem_df["spec_prem_tier"].value_counts().reset_index()
        tc2.columns = ["tier", "count"]
        tc2["order"] = tc2["tier"].apply(
            lambda t: sp_tier_order.index(t) if t in sp_tier_order else 99
        )
        tc2 = tc2.sort_values("order")
        fig_p2 = go.Figure(go.Pie(
            labels=tc2["tier"],
            values=tc2["count"],
            marker=dict(colors=sp_tier_colours[:len(tc2)]),
            hole=0.3,
        ))
        fig_p2.update_layout(title="SPEC_PREM Tier Breakdown", height=380)
        st.plotly_chart(fig_p2, use_container_width=True)

# ── By NLG type and issue age breakdown ──────────────────────────────────────

st.subheader("By NLG Type and Issue Age Band")

col_nlg, col_age = st.columns(2)

with col_nlg:
    rows = []
    for nlg_type, grp in df.groupby("secondary_guarantee_type"):
        if nlg_type == "SHADOW_ACCT":
            at_risk_n = int((grp["shadow_account_value"].fillna(1.0) <= 0).sum())
            avg_metric = grp["shadow_account_value"].mean()
            metric_label = "Avg Shadow AV ($)"
            avg_fmt = f"${avg_metric:,.0f}" if pd.notna(avg_metric) else "—"
        else:
            at_risk_n = int((grp["nlg_funding_ratio"].fillna(1.0) < 1.0).sum())
            avg_metric = grp["nlg_funding_ratio"].mean()
            metric_label = "Avg Prem/NLP Ratio"
            avg_fmt = f"{avg_metric:.3f}" if pd.notna(avg_metric) else "—"
        rows.append({
            "NLG Type": nlg_type,
            "Count": len(grp),
            "Primary Metric": "Shadow AV" if nlg_type == "SHADOW_ACCT" else "Prem/NLP Ratio",
            "Avg Primary Metric": avg_fmt,
            "At-risk Count": at_risk_n,
            "At-risk %": f"{at_risk_n / max(len(grp), 1) * 100:.1f}%",
        })
    nlg_summary = pd.DataFrame(rows)
    st.dataframe(nlg_summary, use_container_width=True, hide_index=True)

with col_age:
    df["issue_age_band"] = pd.cut(
        df["issue_age_anb"],
        bins=[0, 49, 54, 59, 64, 69, 74, 120],
        labels=["<50", "50-54", "55-59", "60-64", "65-69", "70-74", "75+"],
    ).astype(str)

    # Shadow ACCT: bar showing avg shadow AV by age band
    shadow_age = (
        shadow_df.assign(
            issue_age_band=pd.cut(
                shadow_df["issue_age_anb"],
                bins=[0, 49, 54, 59, 64, 69, 74, 120],
                labels=["<50", "50-54", "55-59", "60-64", "65-69", "70-74", "75+"],
            ).astype(str)
        )
        .groupby("issue_age_band")["shadow_account_value"]
        .mean()
        .reset_index()
    )
    spec_age = (
        spec_prem_df.assign(
            issue_age_band=pd.cut(
                spec_prem_df["issue_age_anb"],
                bins=[0, 49, 54, 59, 64, 69, 74, 120],
                labels=["<50", "50-54", "55-59", "60-64", "65-69", "70-74", "75+"],
            ).astype(str)
        )
        .groupby("issue_age_band")["nlg_funding_ratio"]
        .mean()
        .reset_index()
    )

    fig_age = go.Figure()
    if not shadow_age.empty:
        fig_age.add_trace(go.Bar(
            name="SHADOW_ACCT avg shadow AV ($)",
            x=shadow_age["issue_age_band"],
            y=shadow_age["shadow_account_value"],
            marker_color="#1f77b4",
            yaxis="y",
        ))
    if not spec_age.empty:
        fig_age.add_trace(go.Bar(
            name="SPEC_PREM avg prem/NLP ratio",
            x=spec_age["issue_age_band"],
            y=spec_age["nlg_funding_ratio"],
            marker_color="#ff7f0e",
            yaxis="y2",
        ))
        fig_age.add_hline(
            y=1.0, line_dash="dash", line_color="#ff7f0e",
            annotation_text="SPEC_PREM threshold (1.0)",
            annotation_position="top right",
        )
    fig_age.update_layout(
        title="Primary Risk Metric by Issue Age Band",
        xaxis_title="Issue Age Band",
        yaxis=dict(title="Avg Shadow AV ($)", side="left"),
        yaxis2=dict(title="Avg Prem/NLP Ratio", side="right", overlaying="y"),
        barmode="group",
        height=320,
        legend=dict(orientation="h", y=-0.3),
    )
    st.plotly_chart(fig_age, use_container_width=True)

# ── At-risk policy detail ─────────────────────────────────────────────────────

at_risk_count = int(df["is_at_risk"].sum())
with st.expander(f"At-risk policies (correct test per NLG type) — {at_risk_count} policies"):
    at_risk_df = df[df["is_at_risk"]][[
        "policy_id", "status_code", "secondary_guarantee_type",
        "shadow_account_value", "nlg_funding_ratio",
        "shadow_account_funding_ratio", "account_value_eom",
        "no_lapse_guarantee_period",
    ]].sort_values(["secondary_guarantee_type", "shadow_account_value"]).head(200)

    if at_risk_df.empty:
        st.info("No at-risk policies.")
    else:
        st.dataframe(
            at_risk_df.rename(columns={
                "policy_id": "Policy ID",
                "status_code": "Status",
                "secondary_guarantee_type": "NLG Type",
                "shadow_account_value": "Shadow AV ($)",
                "nlg_funding_ratio": "Prem/NLP Ratio",
                "shadow_account_funding_ratio": "Shadow AV / Cumul NLP",
                "account_value_eom": "Account Value ($)",
                "no_lapse_guarantee_period": "NLG Period",
            }).style.format({
                "Shadow AV ($)": "${:,.0f}",
                "Prem/NLP Ratio": "{:.4f}",
                "Shadow AV / Cumul NLP": "{:.4f}",
                "Account Value ($)": "${:,.0f}",
            }, na_rep="—"),
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "Data sourced from silver_ul_policies where is_ulsg_flag = TRUE. "
    "SHADOW_ACCT at-risk = shadow_account_value ≤ 0 (primary risk: notional fund exhausted). "
    "SPEC_PREM at-risk = cumulative_premiums_paid / cumulative_nlp_required < 1.0 "
    "(primary risk: cumulative NLP schedule underfunded). "
    "shadow_account_funding_ratio = shadow_account_value / cumulative_nlp_required is a "
    "supplemental monitoring metric only. "
    "DQ-UL-02 (ERROR): shadow_account_funding_ratio < 0. "
    "DQ-UL-03 (WARN): in-force shadow_account_funding_ratio < 1.0."
)
