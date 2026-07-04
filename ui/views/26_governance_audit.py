"""Governance & Audit — unified read layer + tamper-evidence (Session 26).

One inspection surface across the three physically-separate governance logs
(FR-4-22): a filterable unified event stream, a per-artifact history timeline,
and a hash-chain integrity check (FR-4-21 / NFR-G-04). Read-only; all four roles
may view the governance and audit surfaces (FR-4-01) — the only gate is that a
user is authenticated.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, require_auth
from src.governance.audit import (
    artifact_timeline,
    unified_audit_query,
    verify_chain,
)
from src.utils.types import AuditFilter, ArtifactType, Role

st.set_page_config(page_title="Governance & Audit", layout="wide")
st.title("🛡️ Governance & Audit")
st.caption(
    "Unified read layer over the three separate governance logs — sign-offs, "
    "A/E governance events, and the AI audit log (FR-4-19..22). Storage stays "
    "separate; this is a unified *view*."
)

_user = require_auth()

# The hash-chained logs the integrity verifier covers (matches audit._VERIFIABLE_CHAINS).
_VERIFIABLE_LOGS = [
    "gold_governance_signoffs",
    "gold_ae_governance_events",
    "gold_workflow_iterations",
    "gold_assumption_approvals",
]

_DISPLAY_COLUMNS = ["ts", "source", "actor", "role", "action", "artifact", "detail"]


def _load_users() -> dict[str, str]:
    """Return {display_name: user_id} for the actor filter (best-effort)."""
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            rows = con.execute(
                "SELECT user_id, display_name FROM gold_users ORDER BY display_name"
            ).fetchall()
        finally:
            con.close()
        return {dn: uid for uid, dn in rows}
    except Exception:
        return {}


def _as_frame(events: list[dict]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=_DISPLAY_COLUMNS)
    df = pd.DataFrame(events)
    for col in _DISPLAY_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[_DISPLAY_COLUMNS]


# ============================================================
# Section A — Unified audit stream
# ============================================================
st.subheader("Audit stream")

users = _load_users()
c1, c2, c3 = st.columns(3)
with c1:
    actor_label = st.selectbox("Actor", ["(any)"] + list(users.keys()))
    action_val = st.text_input("Action contains (exact match)", value="")
with c2:
    role_label = st.selectbox("Role", ["(any)"] + [r.value for r in Role])
    artifact_val = st.text_input("Artifact ID", value="")
with c3:
    use_dates = st.checkbox("Filter by date range")
    date_from = st.date_input("From", value=None) if use_dates else None
    date_to = st.date_input("To", value=None) if use_dates else None

_filter = AuditFilter(
    actor_user_id=users.get(actor_label) if actor_label != "(any)" else None,
    role=Role(role_label) if role_label != "(any)" else None,
    artifact_id=artifact_val.strip() or None,
    action=action_val.strip() or None,
    date_from=date_from if use_dates else None,
    date_to=date_to if use_dates else None,
)

try:
    events = unified_audit_query(_filter, db_path=DB_PATH)
    if not events:
        st.info("No governance events match the current filter.")
    else:
        st.caption(f"{len(events)} event(s), most recent first.")
        st.dataframe(_as_frame(events), use_container_width=True, hide_index=True)
except Exception as exc:  # pragma: no cover - defensive UI guard
    st.warning(f"Audit stream unavailable: {exc}")

# ============================================================
# Section B — Per-artifact timeline
# ============================================================
st.markdown("---")
st.subheader("Per-artifact timeline")
tcol1, tcol2 = st.columns([1, 2])
with tcol1:
    atype = st.radio("Artifact type", [t.value for t in ArtifactType], horizontal=False)
with tcol2:
    aid = st.text_input("Artifact ID (assumption_set_id or study run_id)", value="")

if aid.strip():
    try:
        rows = artifact_timeline(ArtifactType(atype), aid.strip(), db_path=DB_PATH)
        if not rows:
            st.info("No history recorded for that artifact.")
        else:
            st.caption(f"{len(rows)} event(s), chronological (oldest first).")
            st.dataframe(_as_frame(rows), use_container_width=True, hide_index=True)
    except Exception as exc:  # pragma: no cover - defensive UI guard
        st.warning(f"Timeline unavailable: {exc}")
else:
    st.caption("Enter an artifact ID to view its full governance history.")

# ============================================================
# Section C — Chain integrity verification
# ============================================================
st.markdown("---")
st.subheader("Tamper-evidence — chain integrity")
st.caption(
    "Recomputes each hash-chained governance log and reports the first divergence "
    "(FR-4-21). A log with no hashed rows verifies clean (chain begins at the first "
    "hashed row)."
)
if st.button("Verify integrity", type="primary"):
    for table in _VERIFIABLE_LOGS:
        try:
            result = verify_chain(table, db_path=DB_PATH)
        except Exception as exc:  # pragma: no cover - defensive UI guard
            st.warning(f"`{table}`: could not verify ({exc}).")
            continue
        if result.ok:
            st.success(
                f"`{table}` — intact ✓  ({result.rows_checked} hashed row(s) checked)"
            )
        else:
            st.error(
                f"`{table}` — TAMPER DETECTED ✗  first divergence at seq "
                f"{result.first_divergence_seq} ({result.rows_checked} row(s) checked)"
            )
