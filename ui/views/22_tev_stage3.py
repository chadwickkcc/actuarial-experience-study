"""Stage 3 — TEV Impact Analysis.

Runs the full TEV engine on the current PROPOSED assumption set and displays
the baseline waterfall, ΔTEV vs prior, sensitivity tornado, TEV-impact matrix,
and the credibility envelope analysis panel (read-only governance artefact).

Implements FR-2-38 to FR-2-41.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR, REPORTS_DIR
from src.reporting.generator import generate_tev_working_actuary_report
from src.tev.assumption_set import load_assumption_set, save_assumption_set
from src.tev.tev_core import run_tev
from src.tev.sensitivities import run_sensitivity_grid
from src.tev.envelope import run_envelope_analysis
from src.tev.model_points import build_model_points, ModelPointReconciliationError
from src.tev.workflow import (
    log_workflow_iteration,
    transition_assumption_set_status,
    get_workflow_iterations,
    get_next_iteration_number,
)

# ---------------------------------------------------------------------------
# Helper — extract current multiplier for a decrement key
# ---------------------------------------------------------------------------
def _get_current_multiplier(aset, key: str) -> float:
    """Return the mean multiplier for a named decrement key from the assumption set."""
    key_lower = key.lower()
    if "lapse" in key_lower:
        mults = aset.lapse_multipliers
    elif "mortality" in key_lower or "mort" in key_lower:
        mults = aset.mortality_multipliers
    elif "ci" in key_lower:
        mults = aset.ci_incidence_multipliers
    elif "expense" in key_lower or "exp" in key_lower:
        return aset.maintenance_per_policy / 72.0
    elif "rdr" in key_lower:
        return aset.rdr
    else:
        mults = []
    if not mults:
        return 1.0
    vals = [m.multiplier for m in mults]
    return float(np.mean(vals)) if vals else 1.0


st.set_page_config(page_title="TEV Stage 3 — Impact Analysis", layout="wide")

from ui.config import require_auth, user_can
from src.governance.rbac import Action, PermissionDenied, require
_user = require_auth()
_can_propose = user_can(_user, Action.PROPOSE)
st.title("Stage 3 — TEV Impact Analysis")
st.markdown(
    "Run the full TEV engine on the current proposed assumption set. "
    "Review the baseline waterfall, sensitivity grid, and TEV-impact matrix, "
    "then optionally compute the credibility envelope, "
    "then either **Submit for sign-off** or **Refine** to return to Stage 2."
)

# ---------------------------------------------------------------------------
# Workflow progress indicator
# ---------------------------------------------------------------------------
cols_prog = st.columns(4)
cols_prog[0].success("Stage 1 — Experience Study ✓")
cols_prog[1].success("Stage 2 — Assumptions ✓")  # shortened to prevent wrapping
if st.session_state.get("stage3_approved"):
    cols_prog[2].success("Stage 3 — TEV Impact Analysis ✓")
    cols_prog[3].info("Stage 4 — Governance Sign-Off")
else:
    cols_prog[2].info("Stage 3 — TEV Impact Analysis ◀")
    cols_prog[3].info("Stage 4 — Governance Sign-Off (locked)")

st.divider()

# ---------------------------------------------------------------------------
# Session state guard
# ---------------------------------------------------------------------------
aset_id = st.session_state.get("active_assumption_set_id")
if not aset_id:
    st.warning(
        "No active assumption set found in this session. "
        "Start from **Stage 1** to create or resume a workflow."
    )
    st.stop()

workflow_session_id = st.session_state.get("workflow_session_id", str(uuid.uuid4()))
# Fall back to the authenticated username (never the "ACTUARY_1" placeholder) so the
# workflow audit trail always carries a real, referenceable identity (FR-4-03).
author_id = st.session_state.get("workflow_author_id") or _user.username

# ---------------------------------------------------------------------------
# Load assumption set
# ---------------------------------------------------------------------------
@st.cache_data(ttl=0)
def _load_aset(aset_id: str):
    return load_assumption_set(aset_id, DB_PATH)


aset = _load_aset(aset_id)

with st.sidebar:
    st.header("Assumption Set")
    st.caption(f"ID: `{aset_id[:8]}…`")
    st.caption(f"Status: **{aset.status}**")
    st.caption(f"Author: {aset.author_id}")
    st.markdown("---")
    prior_run_id = st.text_input(
        "Prior TEV run ID for ΔTEV (optional)",
        value=st.session_state.get("prior_tev_run_id", ""),
        key="s3_prior_run_id",
    )

run_tev_btn = st.button(
    "▶ Run Full TEV", type="primary", use_container_width=True, disabled=not _can_propose
)
if not _can_propose:
    st.caption(
        f"Your role ({_user.role.value}) cannot propose — "
        "sign in as an analyst to run the TEV and submit for sign-off."
    )

# ---------------------------------------------------------------------------
# Run TEV baseline + sensitivity grid
# ---------------------------------------------------------------------------
if run_tev_btn:
    try:
        require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
    except PermissionDenied as exc:
        st.error(str(exc))
        st.stop()
    study_run_id = st.session_state.get("source_study_run_id")
    if not study_run_id:
        st.error(
            "No source study run found in this session. "
            "Return to **Stage 1** and create an assumption set from a study run first."
        )
        st.stop()

    with st.spinner("Building model points…"):
        try:
            con = duckdb.connect(str(DB_PATH))
            row = con.execute(
                "SELECT product_codes FROM gold_study_runs WHERE run_id = ?",
                [study_run_id],
            ).fetchone()
            con.close()
            if row is None:
                st.error(f"Study run `{study_run_id}` not found in gold_study_runs.")
                st.stop()
            product_codes: list[str] = json.loads(row[0])
        except Exception as exc:
            st.error(f"Failed to load study run metadata: {exc}")
            st.stop()

        mp_build_run_id = str(uuid.uuid4())
        mp_errors = []
        mp_zero = []
        for pc in product_codes:
            try:
                result = build_model_points(pc, DB_PATH, study_run_id, mp_build_run_id, aset)
                if result.model_point_count == 0:
                    mp_zero.append(pc)
            except ModelPointReconciliationError as exc:
                mp_errors.append(f"{pc}: reconciliation failed — {exc}")
            except Exception as exc:
                mp_errors.append(f"{pc}: {exc}")
        if mp_errors:
            for msg in mp_errors:
                st.error(f"Model point build error — {msg}")
            st.stop()
        if mp_zero:
            for pc in mp_zero:
                st.error(
                    f"Model point build for **{pc}** produced zero records. "
                    f"Check that the silver policies table for {pc} contains in-force records "
                    f"(status_code = 'IF') and re-run the study if needed."
                )
            st.stop()

    with st.spinner("Running baseline TEV…"):
        try:
            baseline_result = run_tev(
                db_path=DB_PATH,
                assumption_set_id=aset_id,
                prior_tev_run_id=prior_run_id.strip() or None,
            )
            st.session_state["s3_baseline_result"] = baseline_result
            st.session_state["prior_tev_run_id"] = baseline_result.tev_run_id
        except Exception as exc:
            st.error(f"TEV baseline failed: {exc}")
            st.stop()

    with st.spinner("Running 11-sensitivity grid…"):
        try:
            grid_result = run_sensitivity_grid(
                db_path=DB_PATH,
                assumption_set_id=aset_id,
                baseline_tev_run_id=baseline_result.tev_run_id,
            )
            st.session_state["s3_grid_result"] = grid_result
        except Exception as exc:
            st.error(f"Sensitivity grid failed: {exc}")
            st.stop()

    iteration_number = get_next_iteration_number(DB_PATH, workflow_session_id)
    log_workflow_iteration(
        db_path=DB_PATH,
        workflow_session_id=workflow_session_id,
        iteration_number=iteration_number,
        assumption_set_id=aset_id,
        stage=3,
        action="RAN_TEV",
        actuary_id=author_id,
        tev_baseline_run_id=baseline_result.tev_run_id,
        total_tev=baseline_result.total_tev,
        delta_tev_vs_prior=baseline_result.delta_tev,
    )
    st.session_state["workflow_iteration"] = iteration_number
    st.success("TEV + sensitivity grid complete.")

# ---------------------------------------------------------------------------
# Display results (from session state)
# ---------------------------------------------------------------------------
baseline_result = st.session_state.get("s3_baseline_result")
grid_result = st.session_state.get("s3_grid_result")

if baseline_result is None:
    st.stop()

# ---------------------------------------------------------------------------
# TEV Waterfall Chart
# ---------------------------------------------------------------------------
st.subheader("Baseline TEV Waterfall")

prod_rows = []
for pr in baseline_result.product_results:
    prod_rows.append({
        "Product": pr.product_code,
        "ANW": pr.anw,
        "PVFP": pr.pvfp,
        "PVCoC": pr.pvcoc,
        "VIF": pr.vif,
        "TEV": pr.tev,
    })
prod_df = pd.DataFrame(prod_rows) if prod_rows else pd.DataFrame()

if not prod_df.empty:
    anw_total = baseline_result.total_anw
    pvfp_total = baseline_result.total_pvfp
    pvcoc_total = baseline_result.total_pvcoc
    tev_total = baseline_result.total_tev

    fig_wf = go.Figure(go.Waterfall(
        name="TEV",
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["ANW", "PVFP", "−PVCoC", "TEV"],
        y=[anw_total, pvfp_total, -pvcoc_total, tev_total],
        text=[f"${v:,.0f}" for v in [anw_total, pvfp_total, -pvcoc_total, tev_total]],
        textposition="outside",
        connector={"line": {"color": "rgb(63,63,63)"}},
        increasing={"marker": {"color": "#2ca02c"}},
        decreasing={"marker": {"color": "#d62728"}},
        totals={"marker": {"color": "#1f77b4"}},
    ))
    fig_wf.update_layout(
        title="TEV = ANW + PVFP − PVCoC",
        height=380,
        margin=dict(t=40, b=20),
        yaxis_title="USD",
    )
    st.plotly_chart(fig_wf, use_container_width=True)

    if baseline_result.delta_tev is not None:
        delta_fmt = f"${baseline_result.delta_tev:+,.0f}"
        st.metric("ΔTEV vs prior assumption set", delta_fmt)

    with st.expander("TEV by product", expanded=True):
        fmt_cols = ["ANW", "PVFP", "PVCoC", "VIF", "TEV"]
        disp = prod_df.copy()
        totals = {
            "Product": "TOTAL",
            "ANW": anw_total,
            "PVFP": pvfp_total,
            "PVCoC": pvcoc_total,
            "VIF": baseline_result.total_vif,
            "TEV": tev_total,
        }
        disp = pd.concat([disp, pd.DataFrame([totals])], ignore_index=True)
        disp_fmt = disp.copy()
        for col in fmt_cols:
            disp_fmt[col] = disp_fmt[col].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
        st.dataframe(disp_fmt, hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Sensitivity Tornado Chart
# ---------------------------------------------------------------------------
if grid_result is not None:
    st.subheader("Single-axis Sensitivity Tornado (ΔTEV vs Baseline)")

    impact_df = grid_result.impact_matrix_df

    if "TOTAL" in impact_df.index:
        total_row = impact_df.loc["TOTAL"]
    else:
        total_row = impact_df.sum()

    sens_names = [
        "Lapse −10%", "Lapse +10%",
        "Mortality −5%", "Mortality +5%",
        "Annuity Longevity +5%",
        "CI Incidence −10%", "CI Incidence +10%",
        "Expense −10%", "Expense +10%",
        "RDR +100bp", "RDR −100bp",
    ]
    sens_ids = [f"SENS-{i:02d}" for i in range(1, 12)]
    delta_values = [float(total_row.get(sid, 0)) for sid in sens_ids]

    paired = sorted(zip(delta_values, sens_names), key=lambda x: abs(x[0]))
    delta_sorted, names_sorted = zip(*paired) if paired else ([], [])

    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in delta_sorted]

    fig_tornado = go.Figure(go.Bar(
        x=list(delta_sorted),
        y=list(names_sorted),
        orientation="h",
        marker_color=colors,
        text=[f"${v:+,.0f}" for v in delta_sorted],
        textposition="outside",
        cliponaxis=False,
    ))
    fig_tornado.update_layout(
        title="ΔTEV by single-axis sensitivity shock (total across all products)",
        height=420,
        margin=dict(t=40, b=20, l=180),
        xaxis_title="ΔTEV (USD)",
    )
    fig_tornado.add_vline(x=0, line_width=1, line_color="black")
    st.plotly_chart(fig_tornado, use_container_width=True)

    st.divider()

    # ---------------------------------------------------------------------------
    # TEV-Impact Matrix
    # ---------------------------------------------------------------------------
    st.subheader("TEV-Impact Matrix")

    # ASCII minus only — these labels feed both the heatmap X-axis and the CSV
    # download header. Using ASCII keeps tev_impact_matrix.csv parseable by
    # downstream tools that don't tolerate Unicode minus (U+2212).
    col_labels = {
        "SENS-01": "Lapse -10%",
        "SENS-02": "Lapse +10%",
        "SENS-03": "Mort -5%",
        "SENS-04": "Mort +5%",
        "SENS-05": "Longevity +5%",
        "SENS-06": "CI Inc -10%",
        "SENS-07": "CI Inc +10%",
        "SENS-08": "Exp -10%",
        "SENS-09": "Exp +10%",
        "SENS-10": "RDR +100bp",
        "SENS-11": "RDR -100bp",
    }

    matrix = impact_df.copy()
    matrix.columns = [col_labels.get(c, c) for c in matrix.columns]

    z_vals = matrix.values.astype(float)
    row_max = np.abs(z_vals).max(axis=1, keepdims=True)
    row_max = np.where(row_max == 0, 1, row_max)
    z_norm = z_vals / row_max

    hover_text = [[f"${v:,.0f}" for v in row] for row in z_vals]

    fig_heat = go.Figure(go.Heatmap(
        z=z_norm,
        x=list(matrix.columns),
        y=list(matrix.index),
        text=hover_text,
        texttemplate="%{text}",
        textfont={"size": 10},
        colorscale="RdYlGn",
        zmid=0,
        zmin=-1,
        zmax=1,
        showscale=False,
    ))
    fig_heat.update_layout(
        height=max(300, 55 * len(matrix.index) + 80),
        margin=dict(t=30, b=60, l=100),
        xaxis={"tickangle": -30},
    )
    st.plotly_chart(fig_heat, use_container_width=True)
    st.caption(
        "Colour scale normalised within each product row (green = higher ΔTEV, red = lower; "
        "shading is relative within a row, not comparable across rows). Cell labels show ΔTEV in USD."
    )

    csv_bytes = matrix.to_csv().encode()
    st.download_button(
        "Download TEV-impact matrix (CSV)",
        data=csv_bytes,
        file_name="tev_impact_matrix.csv",
        mime="text/csv",
    )

    st.divider()

    # ---------------------------------------------------------------------------
    # Credibility Envelope Analysis Panel
    # ---------------------------------------------------------------------------
    st.subheader("Credibility Envelope Analysis")
    st.markdown(
        "Computes the maximum and minimum aggregate TEV reachable within the "
        "credibility bounds for the top-5 most TEV-sensitive decrements. "
        "This is a **read-only governance artefact** — it does not alter the "
        "current assumption set."
    )

    env_col1, env_col2 = st.columns([1, 2])
    with env_col1:
        max_evals = st.number_input(
            "Max evaluations per run",
            min_value=20,
            max_value=500,
            value=200,
            step=10,
            help="Applied independently to each of the two L-BFGS-B runs (TEV_max and TEV_min).",
        )
        run_env_btn = st.button(
            "Compute Credibility Envelope",
            use_container_width=True,
            type="secondary",
            disabled=not _can_propose,
        )

    if run_env_btn:
        try:
            require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
        except PermissionDenied as exc:
            st.error(str(exc))
            st.stop()
        with st.spinner("Running credibility envelope analysis (two L-BFGS-B runs)…"):
            try:
                env_result = run_envelope_analysis(
                    db_path=DB_PATH,
                    assumption_set_id=aset_id,
                    baseline_tev_run_id=baseline_result.tev_run_id,
                    impact_matrix_df=grid_result.impact_matrix_df,
                    max_evaluations=int(max_evals),
                )
                st.session_state["s3_env_result"] = env_result
                st.session_state["envelope_run"] = True

                iter_num = get_next_iteration_number(DB_PATH, workflow_session_id)
                log_workflow_iteration(
                    db_path=DB_PATH,
                    workflow_session_id=workflow_session_id,
                    iteration_number=iter_num,
                    assumption_set_id=aset_id,
                    stage=3,
                    action="ENVELOPE_RUN",
                    actuary_id=author_id,
                    tev_baseline_run_id=baseline_result.tev_run_id,
                    total_tev=env_result.proposed_tev,
                    envelope_run_flag=True,
                )
                st.session_state["workflow_iteration"] = iter_num
            except Exception as exc:
                st.error(f"Envelope analysis failed: {exc}")

    env_result = st.session_state.get("s3_env_result")
    if env_result is not None:
        with env_col2:
            if env_result.success:
                st.success(
                    f"Envelope computed. "
                    f"TEV_min = ${env_result.tev_min:,.0f} | "
                    f"TEV_max = ${env_result.tev_max:,.0f}"
                )
            else:
                st.warning(
                    f"Analysis completed with warnings. "
                    f"Min convergence: {env_result.convergence_message_min}"
                )

        # --- TEV_min / TEV_max headline metrics ---
        m1, m2, m3 = st.columns(3)
        m1.metric("TEV_min", f"${env_result.tev_min:,.0f}")
        m2.metric("TEV_proposed", f"${env_result.proposed_tev:,.0f}")
        m3.metric("TEV_max", f"${env_result.tev_max:,.0f}")

        w1, w2 = st.columns(2)
        w1.metric(
            "Envelope width (abs)",
            f"${env_result.envelope_width_abs:,.0f}",
        )
        w2.metric(
            "Envelope width (% of proposed)",
            f"{env_result.envelope_width_pct * 100:.2f}%",
        )

        if env_result.proposed_envelope_percentile is not None:
            pct_pct = env_result.proposed_envelope_percentile * 100
            st.info(
                f"**Proposed assumption set is at the "
                f"{pct_pct:.1f}th percentile** of the credibility envelope "
                f"({pct_pct:.1f}% of envelope width from TEV_min)."
            )
        else:
            st.info(
                f"**Percentile: Not meaningful** — "
                f"{env_result.percentile_undefined_reason or 'envelope width below materiality threshold'}."
            )

        # --- Per-decrement table ---
        st.markdown("**Credibility envelope per decrement (top 5 by TEV sensitivity)**")
        dec_rows = []
        for dk in env_result.top5_decrements:
            lb, ub = env_result.credibility_bounds.get(dk, (None, None))
            t_prop = env_result.theta_proposed.get(dk)
            t_min  = env_result.theta_min.get(dk)
            t_max  = env_result.theta_max.get(dk)

            # Directional label
            if t_prop is not None and t_min is not None and t_max is not None:
                span = t_max - t_min
                if abs(span) < 1e-9:
                    direction = "No room to vary"
                else:
                    lo_label = "high" if t_min > t_prop else "low"
                    hi_label = "high" if t_max > t_prop else "low"
                    direction = f"theta_min: {lo_label} {dk};  theta_max: {hi_label} {dk}"
            else:
                direction = "—"

            dec_rows.append({
                "Decrement": dk,
                "Cred Lower": f"{lb:.4f}" if lb is not None else "—",
                "Cred Upper": f"{ub:.4f}" if ub is not None else "—",
                "theta_proposed": f"{t_prop:.4f}" if t_prop is not None else "—",
                "theta_min": f"{t_min:.4f}" if t_min is not None else "—",
                "theta_max": f"{t_max:.4f}" if t_max is not None else "—",
                "Direction": direction,
            })
        st.dataframe(pd.DataFrame(dec_rows), hide_index=True, use_container_width=True)

        # --- Convergence metadata ---
        with st.expander("Convergence metadata", expanded=False):
            c1, c2 = st.columns(2)
            c1.caption(
                f"**TEV_max run** — {env_result.n_evaluations_max} evaluations\n\n"
                f"`{env_result.convergence_message_max}`"
            )
            c2.caption(
                f"**TEV_min run** — {env_result.n_evaluations_min} evaluations\n\n"
                f"`{env_result.convergence_message_min}`"
            )

        # --- Read-only YAML download ---
        yaml_path = env_result.envelope_yaml_path
        if yaml_path and Path(yaml_path).exists():
            with open(yaml_path, "rb") as _f:
                yaml_bytes = _f.read()
            st.download_button(
                "Export envelope as YAML (audit artefact — read-only)",
                data=yaml_bytes,
                file_name=Path(yaml_path).name,
                mime="application/x-yaml",
            )

    st.divider()

# ---------------------------------------------------------------------------
# Working Actuary Report (FR-2-47)
# ---------------------------------------------------------------------------
st.subheader("Working Actuary Report (FR-2-47)")
if baseline_result is not None:
    if st.button("Generate Working Actuary Report", use_container_width=False, disabled=not _can_propose):
        try:
            require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
        except PermissionDenied as exc:
            st.error(str(exc))
            st.stop()
        env_res = st.session_state.get("s3_env_result")
        with st.spinner("Generating report…"):
            try:
                rpt_path = generate_tev_working_actuary_report(
                    db_path=DB_PATH,
                    assumption_set_id=aset_id,
                    tev_run_id=baseline_result.tev_run_id,
                    workflow_session_id=workflow_session_id,
                    output_dir=REPORTS_DIR,
                    envelope_run=st.session_state.get("envelope_run", False),
                    envelope_tev_min=env_res.tev_min if env_res else None,
                    envelope_tev_max=env_res.tev_max if env_res else None,
                    envelope_percentile=env_res.proposed_envelope_percentile if env_res else None,
                    envelope_width_abs=env_res.envelope_width_abs if env_res else None,
                    envelope_width_pct=env_res.envelope_width_pct if env_res else None,
                    top5_decrements=env_res.top5_decrements if env_res else None,
                    theta_proposed=env_res.theta_proposed if env_res else None,
                    theta_min=env_res.theta_min if env_res else None,
                    theta_max=env_res.theta_max if env_res else None,
                    credibility_bounds=env_res.credibility_bounds if env_res else None,
                )
                with open(rpt_path, "rb") as _f:
                    st.download_button(
                        "Download Working Actuary Report (HTML)",
                        data=_f.read(),
                        file_name=Path(rpt_path).name,
                        mime="text/html",
                    )
                st.success(f"Report generated: `{Path(rpt_path).name}`")
            except Exception as exc:
                st.error(f"Report generation failed: {exc}")
else:
    st.caption("Run baseline TEV first to enable report generation.")

st.divider()

# ---------------------------------------------------------------------------
# Workflow iteration history
# ---------------------------------------------------------------------------
with st.expander("Iteration history for this workflow session", expanded=False):
    history = get_workflow_iterations(DB_PATH, workflow_session_id)
    if history:
        hist_df = pd.DataFrame(history)
        display_cols = [
            "iteration_number", "stage", "action", "actuary_id",
            "total_tev", "delta_tev_vs_prior", "envelope_run_flag",
            "iteration_ts",
        ]
        hist_df = hist_df[[c for c in display_cols if c in hist_df.columns]]
        hist_disp = hist_df.copy()
        if "total_tev" in hist_disp.columns:
            hist_disp["total_tev"] = hist_disp["total_tev"].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "")
        if "delta_tev_vs_prior" in hist_disp.columns:
            hist_disp["delta_tev_vs_prior"] = hist_disp["delta_tev_vs_prior"].apply(lambda v: f"${v:+,.0f}" if pd.notna(v) else "")
        st.dataframe(hist_disp, hide_index=True, use_container_width=True)
    else:
        st.caption("No iterations recorded yet for this session.")

st.divider()

# ---------------------------------------------------------------------------
# Stage 3 decision buttons
# ---------------------------------------------------------------------------
st.subheader("Stage 3 Decision")

if baseline_result is None:
    st.info("Run the TEV first before making a Stage 3 decision.")
else:
    dec_col1, dec_col2 = st.columns(2)

    with dec_col1:
        st.markdown("**Option A — Submit for governance sign-off**")
        s3_comment = st.text_area(
            "Mandatory comment for submission to sign-off",
            key="s3_approval_comment",
            height=100,
            placeholder="Describe the basis for submission…",
        )
        # Once submitted (STAGE3_APPROVED) or locked (APPROVED), re-submitting is a
        # no-op that only clutters the audit trail — and re-submitting an APPROVED set
        # would try to unlock it (blocked server-side). Disable until the set is
        # refined back to PROPOSED via Stage 2.
        _already_submitted = aset.status in ("STAGE3_APPROVED", "APPROVED")
        if _already_submitted:
            st.info(
                f"This assumption set is already **{aset.status}**. To re-submit, first "
                "refine it on **Stage 2** (which returns it to PROPOSED)."
            )
        approve_btn = st.button(
            "Submit for sign-off →",
            type="primary",
            use_container_width=True,
            disabled=not _can_propose or _already_submitted,
        )

        if approve_btn:
            if not _can_propose:
                st.error(
                    f"Your role ({_user.role.value}) cannot propose — "
                    "sign in as an analyst to submit for sign-off."
                )
                st.stop()
            if _already_submitted:
                st.warning(
                    f"Assumption set is already {aset.status}; nothing to submit."
                )
                st.stop()
            try:
                require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
            except PermissionDenied as exc:
                st.error(str(exc))
                st.stop()
            if not s3_comment.strip():
                st.error("A comment is mandatory before submitting.")
            else:
                try:
                    transition_assumption_set_status(DB_PATH, aset_id, "STAGE3_APPROVED")
                except Exception as exc:
                    st.error(f"Status transition failed: {exc}")
                    st.stop()

                env_used = st.session_state.get("envelope_run", False)
                env_res = st.session_state.get("s3_env_result")

                iter_num = get_next_iteration_number(DB_PATH, workflow_session_id)
                log_workflow_iteration(
                    db_path=DB_PATH,
                    workflow_session_id=workflow_session_id,
                    iteration_number=iter_num,
                    assumption_set_id=aset_id,
                    stage=3,
                    action="SUBMITTED_S4",
                    actuary_id=author_id,
                    actuary_comment=s3_comment.strip(),
                    tev_baseline_run_id=baseline_result.tev_run_id,
                    total_tev=baseline_result.total_tev,
                    delta_tev_vs_prior=baseline_result.delta_tev,
                    envelope_run_flag=env_used,
                )
                st.session_state["stage3_approved"] = True
                st.session_state["s3_tev_run_id"] = baseline_result.tev_run_id
                st.session_state["s3_total_tev"] = baseline_result.total_tev
                st.session_state["s3_delta_tev"] = baseline_result.delta_tev
                # Pass envelope data through to Stage 4
                st.session_state["s3_envelope_run"] = env_used
                if env_res is not None:
                    st.session_state["s3_envelope_tev_min"] = env_res.tev_min
                    st.session_state["s3_envelope_tev_max"] = env_res.tev_max
                    st.session_state["s3_envelope_percentile"] = env_res.proposed_envelope_percentile
                else:
                    st.session_state["s3_envelope_tev_min"] = None
                    st.session_state["s3_envelope_tev_max"] = None
                    st.session_state["s3_envelope_percentile"] = None
                if grid_result is not None:
                    impact_df = grid_result.impact_matrix_df
                    total_row_s = impact_df.loc["TOTAL"] if "TOTAL" in impact_df.index else impact_df.sum()
                    st.session_state["s3_max_sensitivity_delta"] = float(total_row_s.abs().max())
                st.cache_data.clear()
                st.success(
                    "✅ Stage 3 approved. Assumption set is now STAGE3_APPROVED. "
                    "Navigate to **Stage 4** to complete governance sign-off."
                )

    with dec_col2:
        st.markdown("**Option B — Refine assumptions (return to Stage 2)**")
        refine_comment = st.text_area(
            "Reason for continuing iteration (optional)",
            key="s3_refine_comment",
            height=100,
            placeholder="Describe what you want to adjust…",
        )
        refine_btn = st.button(
            "← Refine assumptions (return to Stage 2)",
            use_container_width=True,
            disabled=not _can_propose,
        )

        if refine_btn:
            try:
                require(_user, Action.PROPOSE)  # server-side re-check (defense-in-depth)
            except PermissionDenied as exc:
                st.error(str(exc))
                st.stop()
            iter_num = get_next_iteration_number(DB_PATH, workflow_session_id)
            log_workflow_iteration(
                db_path=DB_PATH,
                workflow_session_id=workflow_session_id,
                iteration_number=iter_num,
                assumption_set_id=aset_id,
                stage=3,
                action="RETURNED_TO_S2",
                actuary_id=author_id,
                actuary_comment=refine_comment.strip(),
                tev_baseline_run_id=baseline_result.tev_run_id,
                total_tev=baseline_result.total_tev,
            )
            st.session_state["stage3_approved"] = False
            st.info("Iteration logged. Navigate to **Stage 2** to refine assumptions.")
