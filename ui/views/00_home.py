"""Home — landing page: what the tool does, the end-to-end workflow, and how to run it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from ui.config import require_auth

require_auth()

_VIEWS = Path(__file__).resolve().parent


def _link(filename: str, label: str, icon: str | None = None) -> None:
    """A clickable sidebar-page link (path matches the app's st.navigation entries).

    Falls back to a labelled hint if rendered outside the multipage navigation
    context (e.g. an isolated AppTest has no page registry to resolve the link).
    """
    try:
        st.page_link(str(_VIEWS / filename), label=label, icon=icon)
    except Exception:
        st.markdown(f"{icon or '→'} **{label}**")


st.title("Actuarial Experience Study Tool")
st.caption(
    "Run experience studies, quantify assumption changes with TEV, and govern the "
    "result — for Term Life, Whole Life, Universal Life / ULSG, Variable Universal "
    "Life, and Deferred Annuities."
)

# ---------------------------------------------------------------------------
# End-to-end workflow diagram
# ---------------------------------------------------------------------------
st.subheader("How it fits together")
_FLOW = """
digraph workflow {
    rankdir=LR;
    bgcolor="transparent";
    node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="#cfcfcf"];
    edge [fontname="Helvetica" fontsize=9 color="#888888"];

    setup [label="1 · Setup & Data\\nrun study · data quality" fillcolor="#e8f0fe"];
    exp   [label="2 · Experience Results\\nmortality · lapse · CI A/E" fillcolor="#e6f4ea"];
    prod  [label="Product Monitors\\nUL · ULSG · VUL · DA" fillcolor="#e6f4ea"];
    ai    [label="AI Assistance\\nproposals · analyst" fillcolor="#fef7e0" style="rounded,filled,dashed"];
    tev   [label="3 · Assumption Setting (TEV)\\nStages 1–4" fillcolor="#fce8e6"];
    gov   [label="4 · Governance\\nsign-off · audit · versioning" fillcolor="#f3e8fd"];

    setup -> exp;
    exp -> prod [label="drill in" style=dashed];
    exp -> ai [style=dashed];
    exp -> tev;
    ai -> tev [label="advisory" style=dashed];
    tev -> gov [label="approve / sign off"];
}
"""
st.graphviz_chart(_FLOW, use_container_width=True)
st.caption(
    "AI Assistance is **advisory** (dashed) — it informs assumption-setting but never "
    "changes assumptions automatically. Governance wraps the study runs and assumption "
    "sets with sign-off, audit and versioning."
)

st.divider()

# ---------------------------------------------------------------------------
# How to run the model — end to end
# ---------------------------------------------------------------------------
st.subheader("How to run the model — end to end")

st.markdown("**1. Set up & run a study** — configure dates, products and reference tables, then run the full pipeline (ETL → data quality → exposure → A/E).")
_link("01_study_setup.py", "Study Setup", "⚙️")
_link("02_data_quality.py", "Data Quality Check", "🔍")

st.markdown("**2. Review the experience** — inspect credibility-weighted A/E ratios by decrement and product; drill into product-specific mechanics as needed.")
_link("04_mortality_ae.py", "Mortality A vs E", "💀")
_link("05_lapse_ae.py", "Lapse A vs E", "📉")
_link("06_ci_explorer.py", "CI Incidence Explorer", "🏥")

st.markdown("**3. (Optional) Get AI help** — see GLM/GBM-proposed factor adjustments with a challenge model, or ask the guarded AI Analyst questions about your study data. Both are advisory.")
_link("15_assumption_comparison.py", "Assumption Comparison", "🤖")
_link("16_ai_analyst.py", "AI Analyst", "🧠")

st.markdown("**4. Set assumptions & measure impact** — create a proposed assumption set from a study run, edit within credibility guardrails, and run the TEV projection + sensitivities.")
_link("20_tev_stage1.py", "Stage 1: Select Study Basis", "1️⃣")

st.markdown("**5. Govern & sign off** — submit a study run for approval, take an assumption set through the multi-level sign-off chain, and keep a tamper-evident audit trail with versioning and compliance packs.")
_link("28_study_run_signoff.py", "Study Run Sign-Off", "✍️")
_link("27_governance_dashboard.py", "Governance Dashboard", "📊")

st.divider()

# ---------------------------------------------------------------------------
# Where the AI and Governance pages fit
# ---------------------------------------------------------------------------
col_ai, col_gov = st.columns(2, gap="large")

with col_ai:
    with st.container(border=True):
        st.markdown("#### 🤖 Where the AI pages fit")
        st.markdown(
            "The two AI pages sit **between reviewing experience and setting "
            "assumptions**, and are strictly **advisory**:\n\n"
            "- **Assumption Comparison** — read-only GLM proposals with a GBM challenge "
            "model and SHAP explanations. It surfaces suggested factor adjustments and a "
            "TEV what-if; it **never adopts** anything automatically.\n"
            "- **AI Analyst** — a guarded chatbot that answers questions over your own "
            "study data (every figure is traced back to the data; no free-form numbers).\n\n"
            "You stay in control: any change is made by you in the TEV assumption editor."
        )

with col_gov:
    with st.container(border=True):
        st.markdown("#### 🛡️ Where Governance fits")
        st.markdown(
            "Governance is the **controls layer around assumption-setting**:\n\n"
            "- **Sign-off chains** — a study run must be approved *fit for "
            "assumption-setting*, and an assumption set is taken through junior → senior "
            "→ chief sign-off (proposer ≠ approver enforced).\n"
            "- **Audit & Integrity** — every governance action is recorded in a "
            "tamper-evident (hash-chained) log you can verify.\n"
            "- **Versioning & Lineage** — re-open an approved set into a new version, set "
            "effective dates, supersede the prior, and compare versions.\n"
            "- **Compliance packs** — export a defensible HTML dossier for an approved "
            "artifact."
        )

st.divider()

# ---------------------------------------------------------------------------
# Full page reference (collapsed to keep the landing page clean)
# ---------------------------------------------------------------------------
with st.expander("All pages — quick reference", expanded=False):
    ref_cols = st.columns(3, gap="large")

    with ref_cols[0]:
        st.markdown("**Getting Started**")
        _link("00_home.py", "Home", "🏠")
        _link("01_study_setup.py", "Study Setup", "⚙️")
        _link("02_data_quality.py", "Data Quality Check", "🔍")
        _link("07_run_log.py", "Study Run Log", "📋")

        st.markdown("**Experience Results (A/E)**")
        _link("03_exposure_summary.py", "Exposure Summary", "📐")
        _link("04_mortality_ae.py", "Mortality A vs E", "💀")
        _link("05_lapse_ae.py", "Lapse A vs E", "📉")
        _link("06_ci_explorer.py", "CI Incidence Explorer", "🏥")
        _link("14_ci_incidence_summary.py", "CI Incidence Summary", "🩺")
        _link("13_product_comparison.py", "Product Comparison", "⚖️")

    with ref_cols[1]:
        st.markdown("**Product Monitors**")
        _link("08_ul_account_value.py", "UL Account Value Monitor", "💰")
        _link("09_ulsg_shadow_account.py", "ULSG Shadow Account Monitor", "🔐")
        _link("12_vul_fund_value.py", "VUL Fund Value Monitor", "📈")
        _link("10_annuity_surrender.py", "Annuity Surrender Explorer", "🔄")
        _link("11_glb_utilisation.py", "GLB Utilisation Monitor", "📊")

        st.markdown("**AI Assistance**")
        _link("15_assumption_comparison.py", "Assumption Comparison", "🤖")
        _link("16_ai_analyst.py", "AI Analyst", "🧠")

    with ref_cols[2]:
        st.markdown("**Assumption Setting (TEV)**")
        _link("20_tev_stage1.py", "Stage 1: Select Study Basis", "1️⃣")
        _link("21_tev_stage2.py", "Stage 2: Edit Assumptions", "2️⃣")
        _link("22_tev_stage3.py", "Stage 3: TEV Analysis", "3️⃣")
        _link("23_tev_stage4.py", "Stage 4: Approve & Lock", "4️⃣")

        st.markdown("**Governance**")
        _link("26_governance_audit.py", "Audit & Integrity", "🛡️")
        _link("27_governance_dashboard.py", "Governance Dashboard", "📊")
        _link("28_study_run_signoff.py", "Study Run Sign-Off", "✍️")
        _link("29_assumption_lineage.py", "Versioning & Lineage", "🌿")
