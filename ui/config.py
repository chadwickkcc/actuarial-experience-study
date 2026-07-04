"""Shared configuration for all Streamlit pages."""
import sys
from pathlib import Path

# Ensure project root is importable from any page location
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "experience_study.duckdb"
REPORTS_DIR = PROJECT_ROOT / "reports"
CONFIG_DIR = PROJECT_ROOT / "config"
SYNTHETIC_DATA_DIR = PROJECT_ROOT / "synthetic_data" / "output"
REFERENCE_TABLES_DIR = PROJECT_ROOT / "config" / "reference_tables"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Default study configuration matching study_config.yaml
DEFAULT_STUDY_START = "2016-01-01"
DEFAULT_STUDY_END = "2023-12-31"
DEFAULT_MORTALITY_TABLE = str(REFERENCE_TABLES_DIR / "mortality_2015vbt.parquet")
DEFAULT_LAPSE_TABLE = str(REFERENCE_TABLES_DIR / "lapse_benchmarks.parquet")
DEFAULT_CI_TABLE = str(REFERENCE_TABLES_DIR / "ci_incidence.parquet")
DEFAULT_ANNUITY_MORTALITY_TABLE = str(REFERENCE_TABLES_DIR / "mortality_2012iar.parquet")

def require_auth():
    """Block a page unless a user is authenticated; return the current ``User``.

    Defense-in-depth companion to ``login_gate()`` in ``ui/app.py`` (FR-4-02 /
    NFR-G-01). Under normal navigation the entrypoint's ``login_gate`` runs first
    on every rerun, so this never fires for a signed-in user; it guarantees no
    page can render its content if reached by any direct-execution path. Streamlit
    is imported lazily so importing this module outside a Streamlit runtime (e.g.
    tests) does not require the guard.
    """
    import streamlit as st

    from src.governance.auth import current_user

    user = current_user()
    if user is None:
        st.warning("You must be signed in to view this page.")
        st.stop()
    return user


def user_can(user, action) -> bool:
    """Whether the authenticated ``user``'s role permits a governance ``action``.

    A thin UI-layer wrapper over ``rbac.is_permitted`` so view bodies can gate
    write affordances (e.g. disable the "propose" buttons on Stages 1-3 for a
    non-proposer role) without importing the governance package themselves. The
    authoritative check remains server-side ``rbac.require`` at the write site.
    """
    from src.governance.rbac import is_permitted

    return is_permitted(user, action)


TERM_SOURCE_CSV = str(SYNTHETIC_DATA_DIR / "term_policies.csv")
TERM_MAPPING_YAML = str(CONFIG_DIR / "products" / "term.yaml")
WL_SOURCE_CSV = str(SYNTHETIC_DATA_DIR / "wl_policies.csv")
WL_MAPPING_YAML = str(CONFIG_DIR / "products" / "wl.yaml")
UL_SOURCE_CSV = str(SYNTHETIC_DATA_DIR / "ul_policies.csv")
UL_MAPPING_YAML = str(CONFIG_DIR / "products" / "ul.yaml")
VUL_SOURCE_CSV = str(SYNTHETIC_DATA_DIR / "vul_policies.csv")
VUL_MAPPING_YAML = str(CONFIG_DIR / "products" / "vul.yaml")
DA_SOURCE_CSV = str(SYNTHETIC_DATA_DIR / "annuity_contracts.csv")
DA_MAPPING_YAML = str(CONFIG_DIR / "products" / "annuity.yaml")
