"""Management Commentary — YoY movement, drivers, trends, AI-drafted narrative.

Deterministic analytics (``src/analysis/commentary``) render the tables and
charts; the AI drafts executive commentary — including proposed management
actions — over the pre-computed fact pack, with every number verified against
it (AI-DRAFT banner, block-not-repair).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from ui import skills_logic as skills
from src.ai.llm.base import LLMProviderError
from src.ai.llm.client import load_llm_config
from src.ai.skills.management_commentary import draft_management_commentary
from src.analysis import (
    attribute_drivers,
    classify_trends,
    compute_yoy_movement,
    justification_metrics,
    load_commentary_config,
)

from ui.theme import page_setup
page_setup("Management Commentary")

from ui.config import require_auth
require_auth()
st.title("📈 Management Commentary")
st.markdown(
    "Year-on-year A/E movement with **driver attribution**, 3-year "
    "**trend classification**, and an **AI-drafted management commentary** "
    "with proposed actions — every figure pre-computed and verified."
)

# ---------------------------------------------------------------------------
# Run + decrement selection
# ---------------------------------------------------------------------------
con = duckdb.connect(str(DB_PATH), read_only=True)
try:
    runs = con.execute(
        "SELECT run_id, run_ts, product_codes FROM gold_study_runs "
        "WHERE status = 'COMPLETE' ORDER BY run_ts DESC LIMIT 20"
    ).fetchall()
finally:
    con.close()
if not runs:
    st.warning("No completed study runs. Run a study first from **Study Setup**.")
    st.stop()

run_labels = {f"{r[0][:8]}… · {str(r[1])[:16]}": r[0] for r in runs}
c_run, c_dec = st.columns([3, 2])
run_id = run_labels[c_run.selectbox("Study run", list(run_labels.keys()))]
decrement = c_dec.selectbox(
    "Decrement", ["MORTALITY", "LAPSE", "CI_INCIDENCE", "SURRENDER"],
    format_func=lambda d: d.replace("_", " ").title(),
)

_cfg = load_commentary_config()
_dims = _cfg["attribution_dimensions"].get(decrement, [])

# ---------------------------------------------------------------------------
# YoY movement + trend badge
# ---------------------------------------------------------------------------
yoy = compute_yoy_movement(DB_PATH, run_id, decrement)
if not yoy:
    st.info("No experience for this decrement in the selected run.")
    st.stop()

trend = classify_trends(DB_PATH, run_id, decrement)
_BADGE = {"worsening": "🔴 Worsening", "improving": "🟢 Improving",
          "stable": "🟡 Stable", "insufficient_data": "⚪ Insufficient data"}

t1, t2, t3 = st.columns(3)
t1.metric("Latest A/E", f"{yoy[-1]['ae']:.4f}",
          delta=(f"{yoy[-1]['delta_vs_prior']:+.4f} vs prior year"
                 if yoy[-1]["delta_vs_prior"] is not None else None),
          delta_color="inverse")
t2.metric("3-year trend", _BADGE.get(trend["classification"], trend["classification"]))
t3.metric("Trend slope (A/E per year)",
          f"{trend['slope']:+.4f}" if trend["slope"] is not None else "—")

fig = go.Figure()
fig.add_scatter(x=[r["year"] for r in yoy], y=[r["ae"] for r in yoy],
                mode="lines+markers", name="A/E")
fig.add_hline(y=1.0, line_dash="dot", line_color="grey",
              annotation_text="A/E = 1.0")
if trend["slope"] is not None:
    ys = trend["years_used"]
    tail = [r for r in yoy if r["year"] in ys]
    fig.add_scatter(x=[r["year"] for r in tail], y=[r["ae"] for r in tail],
                    mode="lines", name=f"trend ({trend['classification']})",
                    line=dict(dash="dash", color="red" if trend["classification"] == "worsening" else "green"))
fig.update_layout(title=f"{decrement.replace('_', ' ').title()} A/E by calendar year",
                  xaxis_title="Calendar year", yaxis_title="A/E ratio", height=380)
st.plotly_chart(fig, use_container_width=True)

yoy_df = pd.DataFrame([{k: v for k, v in r.items() if k != "top_drivers"} for r in yoy])
st.dataframe(yoy_df, hide_index=True, use_container_width=True,
             column_config={
                 "ae": st.column_config.NumberColumn("A/E", format="%.4f"),
                 "delta_vs_prior": st.column_config.NumberColumn("Δ vs prior", format="%+.4f"),
             })

# ---------------------------------------------------------------------------
# Driver attribution waterfall
# ---------------------------------------------------------------------------
st.subheader("Drivers of the year-on-year movement")
years_with_delta = [r["year"] for r in yoy if r["delta_vs_prior"] is not None]
if not years_with_delta or not _dims:
    st.info("No year-on-year transition (or no configured dimensions) to attribute.")
else:
    d1, d2 = st.columns(2)
    sel_year = d1.selectbox("Movement year", years_with_delta[::-1])
    sel_dim = d2.selectbox("Attribution dimension", _dims,
                           format_func=lambda d: d.replace("_", " ").title())
    attr = attribute_drivers(DB_PATH, run_id, decrement, sel_year, sel_dim)
    if attr["delta_ae"] is None:
        st.info("Insufficient experience in one of the two years.")
    else:
        top_n = int(_cfg.get("top_n_drivers", 3))
        top = attr["contributions"][: max(top_n * 2, 6)]
        rest = attr["contributions"][len(top):]
        other = round(sum(c["contribution"] for c in rest), 6)
        labels = [c["segment"] for c in top] + (["(all other segments)"] if rest else [])
        values = [c["contribution"] for c in top] + ([other] if rest else [])
        wf = go.Figure(go.Waterfall(
            orientation="v",
            measure=["relative"] * len(values) + ["total"],
            x=labels + ["ΔA/E total"],
            y=values + [0],
            text=[f"{v:+.4f}" for v in values] + [f"{attr['delta_ae']:+.4f}"],
            textposition="outside",
        ))
        wf.update_layout(
            title=(f"Contribution to the {attr['prior_year']}→{attr['year']} "
                   f"A/E change of {attr['delta_ae']:+.4f}, by "
                   f"{sel_dim.replace('_', ' ')}"),
            height=420, showlegend=False,
        )
        st.plotly_chart(wf, use_container_width=True)
        st.caption(
            "Per-segment contributions sum exactly to the aggregate A/E change "
            "(each blends the segment's claim experience and its exposure-mix shift)."
        )

# ---------------------------------------------------------------------------
# Trend overview + assumption justification
# ---------------------------------------------------------------------------
st.subheader("Trend overview across the portfolio")
_trend_rows = []
for dec in ("MORTALITY", "LAPSE", "CI_INCIDENCE", "SURRENDER"):
    t = classify_trends(DB_PATH, run_id, dec)
    _trend_rows.append({
        "Decrement": dec.replace("_", " ").title(),
        "3-year trend": _BADGE.get(t["classification"], t["classification"]),
        "Slope": f"{t['slope']:+.4f}" if t["slope"] is not None else "—",
        "Years": ", ".join(str(y) for y in t["years_used"]),
    })
st.dataframe(pd.DataFrame(_trend_rows), hide_index=True, use_container_width=True)

with st.expander("Assumption justification (per product, where AI proposals exist)"):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        _products = [r[0] for r in con.execute(
            "SELECT DISTINCT product_code FROM gold_ae_results "
            "WHERE study_run_id = ? AND product_code IS NOT NULL ORDER BY 1",
            [run_id],
        ).fetchall()]
    finally:
        con.close()
    _just = []
    for prod in _products:
        for dec in ("MORTALITY", "LAPSE", "CI_INCIDENCE", "SURRENDER"):
            jm = justification_metrics(DB_PATH, run_id, dec, prod)
            if jm and "proposed_cells" in jm:
                _just.append(jm)
    if _just:
        st.dataframe(pd.DataFrame(_just), hide_index=True, use_container_width=True)
        st.caption(
            "cred_wtd_ae = Z·A/E + (1−Z)·1.0 — the credibility-weighted move "
            "from the current neutral multiplier toward observed experience."
        )
    else:
        st.caption("No AI-proposed factors for this run yet — fit models on the "
                   "Assumption Comparison page.")

# ---------------------------------------------------------------------------
# AI-drafted management commentary
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Draft management commentary (AI)")
st.caption(
    "Four sections: YoY movement & drivers · trends · **proposed management "
    "actions** · assumption justification. Every number is verified against the "
    "pre-computed analytics; an untraceable number blocks the draft."
)
_models = skills.available_skill_models(CONFIG_DIR)
if not _models:
    st.caption("No models configured in llm_config.yaml.")
else:
    mc1, _ = st.columns([2, 3])
    model = mc1.selectbox(
        "Model", [m["model_id"] for m in _models],
        format_func=lambda mid: next(
            (m["display_name"] + ("" if m["enabled"] else f" — {m['disabled_reason']}")
             for m in _models if m["model_id"] == mid), mid),
        key="mgmt_commentary_model",
    )
    if st.button("Draft management commentary (AI)", type="primary"):
        with st.spinner("Assembling analytics and drafting…"):
            try:
                facts = skills.assemble_commentary_facts(DB_PATH, run_id)
                st.session_state["mgmt_commentary_out"] = draft_management_commentary(
                    facts, load_llm_config(CONFIG_DIR / "llm_config.yaml"), model
                )
            except LLMProviderError as exc:
                st.session_state["mgmt_commentary_out"] = {"_provider_error": str(exc)}
    _out = st.session_state.get("mgmt_commentary_out")
    if _out is not None:
        if "_provider_error" in _out:
            st.error(_out["_provider_error"])
        elif _out.get("blocked"):
            msg = _out.get("reason") or "Draft blocked (not repaired)."
            nums = _out.get("untraceable_nums") or []
            if nums:
                msg += f" Untraceable: {', '.join(nums)}"
            st.error(msg)
        else:
            st.markdown(_out["markdown"])
            st.download_button(
                "Download commentary (.md)",
                data=_out["markdown"].encode("utf-8"),
                file_name="management_commentary.md", mime="text/markdown",
            )
