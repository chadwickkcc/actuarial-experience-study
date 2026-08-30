"""Home — demo landing page: the end-to-end experience-study workflow."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from ui.config import require_auth

require_auth()

def _link(fname: str, label: str, icon: str) -> None:
    """Page link that degrades to plain text when rendered outside st.navigation
    (e.g. under AppTest, where page URLs cannot resolve)."""
    try:
        st.page_link(f"views/{fname}", label=label, icon=icon)
    except Exception:  # noqa: BLE001 - standalone render (tests) has no nav registry
        st.markdown(f"{icon} {label}")

st.title("📊 Experience Study Tool")
st.markdown(
    "Run life-insurance experience studies end-to-end — **data quality → "
    "actual-vs-expected results → management commentary → fraud screening → "
    "AI-proposed assumptions → governed sign-off** — with AI assistance that is "
    "verified, audited, and always subject to actuarial review."
)

# ---------------------------------------------------------------------------
# How it fits together
# ---------------------------------------------------------------------------
st.subheader("How it fits together")
_FLOW = """
digraph workflow {
    rankdir=LR;
    bgcolor="transparent";
    node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="#cfcfcf"];
    edge [fontname="Helvetica" fontsize=9 color="#888888"];

    setup [label="1 · Run the study\\nETL · data quality · exposure · A/E" fillcolor="#e8f0fe"];
    exp   [label="2 · Experience results\\nmortality · lapse · CI\\nYoY movement · trends" fillcolor="#e6f4ea"];
    fraud [label="3 · Fraud screening\\n6 indicators · composite score\\nAI narrative" fillcolor="#fdeaea"];
    ai    [label="4 · AI assistance\\nproposed factors · analyst chat\\ndrafted commentary" fillcolor="#fef7e0" style="rounded,filled,dashed"];
    aset  [label="5 · Assumption setting\\nSteps 1–3: propose · edit · sign off" fillcolor="#fce8e6"];
    gov   [label="6 · Governance\\nsign-off chains · tamper-evident audit\\ncompliance pack" fillcolor="#f3e8fd"];

    setup -> exp;
    exp -> fraud [style=dashed label="claims"];
    exp -> ai [style=dashed];
    ai -> aset [label="advisory"];
    exp -> aset;
    aset -> gov [label="approve & lock"];
    setup -> gov [style=dashed label="run sign-off"];
}
"""
st.graphviz_chart(_FLOW, use_container_width=True)

# ---------------------------------------------------------------------------
# The demo storyline
# ---------------------------------------------------------------------------
st.subheader("A guided tour")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown(
        "**1 · Run & review**\n\n"
        "Run the 25,000-policy study in seconds, check data quality, then read "
        "credibility-weighted A/E by product, age, duration and calendar year."
    )
    _link("01_study_setup.py", "Run Study", "⚙️")
    _link("04_mortality_ae.py", "Mortality A/E", "💀")
    _link("18_management_commentary.py", "Management Commentary", "📈")
with c2:
    st.markdown(
        "**2 · Let the AI assist**\n\n"
        "Screen every claim against six fraud indicators, ask the AI Analyst "
        "questions in plain language, and review AI-proposed assumption factors "
        "— every number verified against the data, nothing adopted automatically."
    )
    _link("17_fraud_monitor.py", "Fraud Monitor", "🕵️")
    _link("16_ai_analyst.py", "AI Analyst", "🧠")
    _link("15_assumption_comparison.py", "AI Assumption Proposals", "🤖")
with c3:
    st.markdown(
        "**3 · Decide & govern**\n\n"
        "Propose an assumption set, edit within credibility guardrails, submit "
        "through the multi-level sign-off chain, and export the tamper-evident "
        "compliance pack."
    )
    _link("20_assumption_step1.py", "Step 1 · Select Study Basis", "1️⃣")
    _link("22_assumption_step3.py", "Step 3 · Sign Off & Lock", "3️⃣")
    _link("26_governance_audit.py", "Audit & Integrity", "🛡️")

st.divider()

# ---------------------------------------------------------------------------
# Where the AI fits / where governance fits
# ---------------------------------------------------------------------------
g1, g2 = st.columns(2)
with g1:
    st.markdown("##### 🤖 Where the AI fits")
    st.markdown(
        "- **Proposes, never decides** — GLM factors with confidence intervals, "
        "a challenger model, and explainability; adoption is a human edit with "
        "recorded provenance.\n"
        "- **Every number verified** — drafted memos, commentary and fraud "
        "narratives are checked figure-by-figure against the data; an invented "
        "number blocks the draft.\n"
        "- **Data access is governed** — the analyst chat reaches the database "
        "only through read-only, allow-listed tools; no policyholder identity "
        "ever reaches a model.\n"
        "- **Everything is audited** — every AI turn is logged and reviewable."
    )
with g2:
    st.markdown("##### 🛡️ Where governance fits")
    st.markdown(
        "- **Segregation of duties** — the proposer can never approve their own "
        "assumption set.\n"
        "- **Materiality-driven sign-off** — larger assumption changes require "
        "the chief actuary; smaller ones complete earlier in the chain.\n"
        "- **Tamper-evident audit** — governance logs are hash-chained; one "
        "click re-verifies their integrity.\n"
        "- **Version lineage** — approved sets are locked; changes create new "
        "versions with effective dating and a full compliance pack."
    )

st.divider()

with st.expander("All pages", expanded=False):
    ref_cols = st.columns(3)

    with ref_cols[0]:
        st.markdown("**Overview**")
        _link("01_study_setup.py", "Run Study", "⚙️")
        _link("02_data_quality.py", "Data Quality", "🔍")
        _link("07_run_log.py", "Study Run Log", "📋")
        st.markdown("**Experience Results**")
        _link("03_exposure_summary.py", "Exposure Summary", "📐")
        _link("04_mortality_ae.py", "Mortality A/E", "💀")
        _link("05_lapse_ae.py", "Lapse A/E", "📉")
        _link("06_ci_explorer.py", "Critical Illness A/E", "🏥")
        _link("13_product_comparison.py", "Product Comparison", "⚖️")
        _link("18_management_commentary.py", "Management Commentary", "📈")

    with ref_cols[1]:
        st.markdown("**Risk & Fraud**")
        _link("17_fraud_monitor.py", "Fraud Monitor", "🕵️")
        st.markdown("**Assumptions & AI**")
        _link("15_assumption_comparison.py", "AI Assumption Proposals", "🤖")
        _link("16_ai_analyst.py", "AI Analyst", "🧠")
        _link("20_assumption_step1.py", "Step 1 · Select Study Basis", "1️⃣")
        _link("21_assumption_step2.py", "Step 2 · Edit & Submit", "2️⃣")
        _link("22_assumption_step3.py", "Step 3 · Sign Off & Lock", "3️⃣")
        _link("29_assumption_lineage.py", "Versioning & Lineage", "🌿")

    with ref_cols[2]:
        st.markdown("**Governance**")
        _link("28_study_run_signoff.py", "Study Run Sign-Off", "✍️")
        _link("27_governance_dashboard.py", "Governance Dashboard", "📊")
        _link("26_governance_audit.py", "Audit & Integrity", "🛡️")
