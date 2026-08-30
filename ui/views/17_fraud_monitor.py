"""Fraud Monitor — rule-based claims fraud detection (AI-assisted).

Runs the six configurable fraud indicators over every claim, shows the
composite-score distribution, entity concentrations and the flagged-claims
drill-down, and drafts an AI narrative over the scan AGGREGATES (no
policyholder or claimant identity ever reaches the model).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from ui import fraud_logic
from ui import skills_logic as skills
from src.ai.llm.base import LLMProviderError
from src.ai.llm.client import load_llm_config
from src.ai.skills.fraud_narrative import draft_fraud_narrative
from src.fraud import run_fraud_scan

from ui.theme import page_setup
page_setup("Fraud Monitor")

from ui.config import require_auth, user_can
from src.governance.rbac import Action
_user = require_auth()
st.title("🕵️ Fraud Monitor — AI-assisted claims screening")
st.markdown(
    "Six configurable **rule indicators** score every death and CI claim; a "
    "weighted composite flags claims for investigation. The AI drafts a "
    "narrative over the **aggregates only** — no policyholder or claimant "
    "identity reaches the model. Indicators prioritise investigation; they "
    "are not determinations of fraud."
)

# ---------------------------------------------------------------------------
# Run a scan
# ---------------------------------------------------------------------------
_can_run = user_can(_user, Action.PROPOSE)

col_run, col_info = st.columns([2, 3])
with col_run:
    run_clicked = st.button(
        "🔍 Run fraud scan", type="primary", use_container_width=True,
        disabled=not _can_run,
    )
    if not _can_run:
        st.caption(f"Your role ({_user.role.value}) cannot run scans — sign in as an analyst.")

if run_clicked:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        st.error("No COMPLETE study run — run a study first.")
        st.stop()
    with st.spinner("Scoring claims against the six indicators…"):
        res = run_fraud_scan(DB_PATH, row[0], run_by=_user.username)
    st.success(
        f"Scan complete: {res.n_claims_scored:,} claims scored, "
        f"{res.n_claims_flagged} flagged (threshold {res.composite_threshold})."
    )

scan = fraud_logic.latest_scan(DB_PATH)
if scan is None:
    st.info("No fraud scan yet — run one above.")
    st.stop()

scores = fraud_logic.scan_scores(DB_PATH, scan["fraud_run_id"])

# ---------------------------------------------------------------------------
# Headline metrics
# ---------------------------------------------------------------------------
st.divider()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Claims scored", f"{int(scan['n_claims_scored']):,}")
m2.metric("Flagged for investigation", int(scan["n_claims_flagged"]))
m3.metric("Flag threshold", f"{scan['composite_threshold']:.2f}")
m4.metric("Max composite score", f"{scan['score_max']:.2f}")
st.caption(
    f"Scan `{str(scan['fraud_run_id'])[:8]}…` · {str(scan['run_ts'])[:16]} · "
    f"by {scan['run_by']} · config `{str(scan['config_hash'])[:12]}…`"
)

# ---------------------------------------------------------------------------
# Rule hits + score distribution
# ---------------------------------------------------------------------------
c1, c2 = st.columns(2)
with c1:
    hits = pd.DataFrame(
        sorted(json.loads(scan["rule_hit_counts"]).items()),
        columns=["rule", "claims hit"],
    )
    _RULE_LABELS = {
        "FR-RULE-01": "First policy-year claim",
        "FR-RULE-02": "Claim ≫ premiums paid",
        "FR-RULE-03": "Above materiality",
        "FR-RULE-04": "Office early-claim cluster",
        "FR-RULE-05": "Similar claims, same claimant",
        "FR-RULE-06": "High-risk region / hospital",
    }
    hits["indicator"] = hits["rule"].map(_RULE_LABELS).fillna(hits["rule"])
    fig = px.bar(hits, x="claims hit", y="indicator", orientation="h",
                 title="Claims hit per indicator")
    fig.update_layout(height=320, yaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    fig2 = px.histogram(scores, x="composite_score", nbins=24,
                        title="Composite-score distribution")
    fig2.add_vline(x=float(scan["composite_threshold"]), line_dash="dash",
                   line_color="red",
                   annotation_text="flag threshold")
    fig2.update_layout(height=320, xaxis_title="composite score",
                       yaxis_title="claims")
    st.plotly_chart(fig2, use_container_width=True)

# ---------------------------------------------------------------------------
# Entity concentrations (flagged claims)
# ---------------------------------------------------------------------------
flagged = scores[scores["flagged"]]
if not flagged.empty:
    st.subheader("Where the flagged claims concentrate")
    e1, e2, e3 = st.columns(3)
    for col, dim, label in (
        (e1, "agency_office_id", "Agency office"),
        (e2, "hospital_id", "Hospital"),
        (e3, "claim_region", "Region"),
    ):
        agg = (
            flagged.groupby(dim, dropna=True)["claim_event_id"].count()
            .sort_values(ascending=False).head(5).reset_index()
        )
        agg.columns = [label, "flagged claims"]
        with col:
            st.dataframe(agg, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# Flagged-claims drill-down
# ---------------------------------------------------------------------------
st.subheader("Flagged claims")
show_cols = [
    "claim_event_id", "product_code", "event_type", "event_date",
    "claim_amount", "agency_office_id", "hospital_id", "claim_region",
    "composite_score", "n_rules_hit",
]
st.dataframe(
    flagged[show_cols].reset_index(drop=True),
    use_container_width=True, hide_index=True,
    column_config={
        "claim_amount": st.column_config.NumberColumn("Claim amount", format="$%,.0f"),
        "composite_score": st.column_config.NumberColumn("Score", format="%.2f"),
    },
)
st.download_button(
    "Download flagged claims (CSV)",
    data=flagged[show_cols].to_csv(index=False).encode("utf-8"),
    file_name="fraud_flagged_claims.csv", mime="text/csv",
)

sel = st.selectbox(
    "Inspect a flagged claim",
    ["(choose)"] + flagged["claim_event_id"].tolist(),
)
if sel != "(choose)":
    detail = fraud_logic.claim_flags(DB_PATH, scan["fraud_run_id"], sel)
    row = flagged[flagged["claim_event_id"] == sel].iloc[0]
    st.markdown(
        f"**{sel}** — {row['product_code']} {row['event_type']} on "
        f"{str(row['event_date'])[:10]}, claim ${row['claim_amount']:,.0f}, "
        f"composite score **{row['composite_score']:.2f}**"
    )
    for r in detail.itertuples():
        ev = json.loads(r.evidence) if r.evidence else {}
        ev_str = ", ".join(f"{k}={v}" for k, v in ev.items() if v is not None)
        st.markdown(f"- `{r.rule_id}` (weight {r.weight:.2f}) — {ev_str}")

# ---------------------------------------------------------------------------
# AI narrative (aggregates only)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("AI narrative")
st.caption(
    "Drafted from scan **aggregates** only (rule counts, score distribution, "
    "office/hospital/region concentrations). Every number is verified against "
    "the scan; an untraceable number blocks the draft. No policy or claimant "
    "identity is sent to the model."
)
_models = skills.available_skill_models(CONFIG_DIR)
if not _models:
    st.caption("No models configured in llm_config.yaml.")
else:
    nc1, nc2 = st.columns([2, 3])
    narrative_model = nc1.selectbox(
        "Model", [m["model_id"] for m in _models],
        format_func=lambda mid: next(
            (m["display_name"] + ("" if m["enabled"] else f" — {m['disabled_reason']}")
             for m in _models if m["model_id"] == mid), mid),
        key="fraud_model",
    )
    if st.button("Draft fraud narrative (AI)"):
        with st.spinner("Drafting narrative…"):
            try:
                facts = fraud_logic.assemble_fraud_facts(DB_PATH, scan["fraud_run_id"])
                st.session_state["fraud_narrative_out"] = draft_fraud_narrative(
                    facts, load_llm_config(CONFIG_DIR / "llm_config.yaml"),
                    narrative_model,
                )
            except LLMProviderError as exc:
                st.session_state["fraud_narrative_out"] = {"_provider_error": str(exc)}
    _out = st.session_state.get("fraud_narrative_out")
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
                "Download narrative (.md)",
                data=_out["markdown"].encode("utf-8"),
                file_name="fraud_narrative.md", mime="text/markdown",
            )
