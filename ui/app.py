"""Main Streamlit application entry point for the Actuarial Experience Study Tool."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Experience Study Tool",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Phase 4 governance: login gate ahead of every page (FR-4-02 / NFR-G-01) ---
from src.governance.auth import login_gate, logout  # noqa: E402


@st.cache_resource
def _ensure_governance_users() -> bool:
    """Create gold_users if needed and seed it from config (idempotent; §I.2).

    Runs once per server process so the login gate always has accounts. Real
    credentials come from the git-ignored config/governance_config.local.yaml
    override; committed placeholders seed accounts that cannot log in until set.
    """
    from src.governance.users import seed_users_from_config
    from src.utils.db_init import DEFAULT_DB_PATH, init_database

    try:
        seed_users_from_config()
    except Exception:
        init_database(DEFAULT_DB_PATH)  # idempotent; creates gold_users if missing
        seed_users_from_config()
    return True


_ensure_governance_users()

_user = login_gate()  # blocks (st.stop) until authenticated; returns the User
with st.sidebar:
    st.caption(f"Signed in as **{_user.display_name}** · {_user.role.value}")
    if st.button("Sign out", use_container_width=True):
        logout()
        st.rerun()

_PAGES_DIR = Path(__file__).resolve().parent / "views"


def _page(filename: str, title: str, icon: str) -> st.Page:
    return st.Page(str(_PAGES_DIR / filename), title=title, icon=icon)


# Sidebar navigation — grouped and ordered to follow the end-to-end workflow:
# set up & run → review experience → drill into product mechanics → get AI help →
# set assumptions & measure TEV → govern. (Page files/titles/icons unchanged.)
pg = st.navigation(
    {
        "1 · Getting Started": [
            _page("00_home.py",             "Home",               "🏠"),
            _page("01_study_setup.py",      "Study Setup",        "⚙️"),
            _page("02_data_quality.py",     "Data Quality Check", "🔍"),
            _page("07_run_log.py",          "Study Run Log",      "📋"),
        ],
        "2 · Experience Results (A/E)": [
            _page("03_exposure_summary.py",     "Exposure Summary",       "📐"),
            _page("04_mortality_ae.py",         "Mortality A vs E",       "💀"),
            _page("05_lapse_ae.py",             "Lapse A vs E",           "📉"),
            _page("06_ci_explorer.py",          "CI Incidence Explorer",  "🏥"),
            _page("14_ci_incidence_summary.py", "CI Incidence Summary",   "🩺"),
            _page("13_product_comparison.py",   "Product Comparison",     "⚖️"),
        ],
        "3 · Product Monitors": [
            _page("08_ul_account_value.py",    "UL Account Value Monitor",    "💰"),
            _page("09_ulsg_shadow_account.py", "ULSG Shadow Account Monitor", "🔐"),
            _page("12_vul_fund_value.py",      "VUL Fund Value Monitor",      "📈"),
            _page("10_annuity_surrender.py",   "Annuity Surrender Explorer",  "🔄"),
            _page("11_glb_utilisation.py",     "GLB Utilisation Monitor",     "📊"),
        ],
        "4 · AI Assistance": [
            _page("15_assumption_comparison.py", "Assumption Comparison", "🤖"),
            _page("16_ai_analyst.py",            "AI Analyst",            "🧠"),
        ],
        "5 · Assumption Setting (TEV)": [
            _page("20_tev_stage1.py", "Stage 1: Select Study Basis", "1️⃣"),
            _page("21_tev_stage2.py", "Stage 2: Edit Assumptions",   "2️⃣"),
            _page("22_tev_stage3.py", "Stage 3: TEV Analysis",       "3️⃣"),
            _page("23_tev_stage4.py", "Stage 4: Approve & Lock",     "4️⃣"),
        ],
        "6 · Governance": [
            _page("26_governance_audit.py",    "Audit & Integrity",    "🛡️"),
            _page("27_governance_dashboard.py","Governance Dashboard", "📊"),
            _page("28_study_run_signoff.py",   "Study Run Sign-Off",   "✍️"),
            _page("29_assumption_lineage.py",  "Versioning & Lineage", "🌿"),
        ],
    }
)

pg.run()
