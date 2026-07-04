"""Governance Dashboard — the "clear what's going on" surface (Session 27).

Realises FR-4-23 (state of every assumption set + submitted study run, the live
set per lineage, pending approvals, recent activity) and FR-4-24 (exportable
compliance pack) on one read-only page. All four roles may view; exporting a
compliance pack additionally requires the ``export`` permission (FR-4-04).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR, require_auth
from src.governance.rbac import Action, is_permitted
from src.governance.reporting import (
    dashboard_data,
    export_compliance_pack,
    retention_policy,
)
from src.utils.types import ArtifactType

_CONFIG_PATH = str(CONFIG_DIR / "governance_config.yaml")

st.set_page_config(page_title="Governance Dashboard", layout="wide")
st.title("📊 Governance Dashboard")
st.caption(
    "State of every assumption set and submitted study run, the live set per "
    "lineage, the pending-approvals queue, and recent governance activity "
    "(FR-4-23) — plus the exportable compliance pack (FR-4-24)."
)

_user = require_auth()


@st.cache_data(show_spinner=False, ttl=30)
def _load_dashboard() -> dict:
    return dashboard_data(db_path=str(DB_PATH), config_path=_CONFIG_PATH)


try:
    data = _load_dashboard()
except Exception as exc:  # pragma: no cover - defensive UI guard
    st.error(f"Dashboard data unavailable: {exc}")
    st.stop()

# ============================================================
# Section A — Assumption-set / study-run states
# ============================================================
st.subheader("Artifact states")
sets_by_state = data["sets_by_state"]
state_cols = st.columns(len(sets_by_state))
for col, (state, entries) in zip(state_cols, sets_by_state.items()):
    col.metric(state, len(entries))

for state, entries in sets_by_state.items():
    if entries:
        with st.expander(f"{state} — {len(entries)} assumption set(s)"):
            st.dataframe(pd.DataFrame(entries), use_container_width=True, hide_index=True)

# ============================================================
# Section B — Live set per lineage
# ============================================================
st.subheader("Live set per lineage (as of today)")
live = data["live_set_per_lineage"]
if live:
    st.dataframe(pd.DataFrame(live), use_container_width=True, hide_index=True)
else:
    st.info("No assumption-set lineages yet.")

# ============================================================
# Section C — Pending approvals (global queue)
# ============================================================
st.subheader("Pending approvals (all roles)")
pending = data["pending_approvals"]
if pending:
    st.caption(f"{len(pending)} artifact(s) awaiting sign-off.")
    st.dataframe(pd.DataFrame(pending), use_container_width=True, hide_index=True)
else:
    st.info("Nothing is awaiting sign-off.")

# ============================================================
# Section D — Recent governance activity
# ============================================================
st.subheader("Recent activity")
recent = data["recent_activity"]
if recent:
    _cols = ["ts", "source", "actor", "role", "action", "artifact", "detail"]
    df = pd.DataFrame(recent)
    for c in _cols:
        if c not in df.columns:
            df[c] = None
    st.dataframe(df[_cols], use_container_width=True, hide_index=True)
else:
    st.info("No recent governance activity.")

# ============================================================
# Section E — Compliance pack export (FR-4-24)
# ============================================================
st.subheader("Export compliance pack")
st.caption(
    "Assembles lineage + sign-offs/attestations + audit excerpt + rationale + "
    "reproducibility + supporting-report links for an APPROVED assumption set or "
    "an approved (fit) study run into one defensible HTML document."
)

if not is_permitted(_user, Action.EXPORT, config_path=_CONFIG_PATH):
    st.info("Your role does not have the `export` permission.")
else:
    c1, c2 = st.columns([1, 2])
    with c1:
        atype = st.selectbox(
            "Artifact type", [ArtifactType.ASSUMPTION_SET.value, ArtifactType.STUDY_RUN.value]
        )
    with c2:
        aid = st.text_input("Artifact ID")

    if st.button("Export compliance pack (HTML)", type="primary"):
        if not aid.strip():
            st.warning("Enter an artifact ID.")
        else:
            try:
                # Governance ids are stored lowercase (UUIDs); normalise the free-text
                # entry so an uppercase/mixed-case paste still resolves the artifact.
                path = export_compliance_pack(
                    atype, aid.strip().lower(), fmt="html",
                    db_path=str(DB_PATH), config_path=_CONFIG_PATH,
                )
                html_bytes = Path(path).read_bytes()
                st.success(f"Compliance pack written: {Path(path).name}")
                st.download_button(
                    "Download compliance pack",
                    data=html_bytes,
                    file_name=Path(path).name,
                    mime="text/html",
                )
            except (ValueError, NotImplementedError) as exc:
                st.error(str(exc))
            except Exception as exc:  # pragma: no cover - defensive UI guard
                st.error(f"Export failed: {exc}")

# ============================================================
# Retention policy footer (FR-4-25)
# ============================================================
_ret = retention_policy(config_path=_CONFIG_PATH)
st.caption(
    f"Retention policy: hard deletes {'enabled' if _ret['hard_delete'] else 'disabled'}; "
    f"archive after {_ret['archive_after_days']} days. Governance records are never "
    "hard-deleted (FR-4-25)."
)
