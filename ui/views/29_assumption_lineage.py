"""Assumption Versioning & Lineage — the version lifecycle UI (FR-4-07..11, 4-18).

Surfaces the lineage engine that was previously engine-only:
  • browse a lineage (every version, status, effective range, the live set today);
  • **Re-open** an APPROVED set → a new DRAFT child version (proposer);
  • **Publish** an APPROVED set → set its effective range + supersede the prior live
    version (approver);
  • **Compare** two versions (changed cells + ΔTEV + rationale).

Reconciliation of the two approval paths: the Stage-4 sign-off chain remains the
approval authority (it sets a set to APPROVED). "Publish" here only adds
effective-dating + supersession on top, and is restricted to sets that are ALREADY
APPROVED via the chain — so the chain is never bypassed. The lineage engine
(src/governance/lineage.py) is unchanged; this page is UI wiring with RBAC gates.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import duckdb
import math
import pandas as pd
import streamlit as st

from ui.config import DB_PATH, require_auth, user_can
from ui import governance_logic as gov
from src.governance.auth import current_user
from src.governance.lineage import (
    OverlappingEffectiveRange,
    approve_and_supersede,
    compare_versions,
)
from src.governance.rbac import Action
from src.governance.workflow import reopen

st.set_page_config(page_title="Versioning & Lineage", layout="wide")

_user = require_auth()
me = current_user()
st.title("Assumption Versioning & Lineage")
st.markdown(
    "Manage the assumption-set version lifecycle: browse a lineage, **re-open** an "
    "approved set into a new draft version, **publish** an approved version with an "
    "effective date range (superseding the prior live version), and **compare** "
    "versions. Approval itself happens in **Stage 4**; this page manages versions "
    "around it."
)

DB = str(DB_PATH)
_can_propose = user_can(_user, Action.PROPOSE)
_can_signoff = user_can(_user, Action.SIGN_OFF)

# ---------------------------------------------------------------------------
# Set selector + lineage overview
# ---------------------------------------------------------------------------
sets = gov.list_assumption_sets()
if not sets:
    st.warning("No assumption sets found. Create one in **TEV Stage 1** first.")
    st.stop()
labels = {s["label"]: s["id"] for s in sets}
selected_label = st.selectbox("Assumption set", list(labels.keys()))
set_id = labels[selected_label]
selected = next(s for s in sets if s["id"] == set_id)

overview = gov.lineage_overview(set_id, date.today(), db_path=DB_PATH)
st.subheader("Lineage")
st.caption(f"Lineage root: `{overview['root'][:8]}…`")
members_df = pd.DataFrame([
    {
        "Version": m["version"],
        "Set ID": m["id"],
        "Status": m["status"],
        "Effective from": m["effective_from"],
        "Effective to": m["effective_to"],
        "Superseded by": (m["superseded_by"] or "")[:8],
        "◀": "◀ selected" if m["is_selected"] else "",
    }
    for m in overview["members"]
])
st.dataframe(members_df, hide_index=True, use_container_width=True)
if overview["live_set_id"]:
    st.success(f"Live set today: `{overview['live_set_id'][:8]}…`")
else:
    st.info(
        "No live set for today — an APPROVED version needs an effective range "
        "(use **Publish** below) before it resolves as live."
    )

st.divider()

# ---------------------------------------------------------------------------
# Re-open (proposer): APPROVED set → new DRAFT child
# ---------------------------------------------------------------------------
st.subheader("Re-open into a new version")
if selected["status"] != "APPROVED":
    st.caption(
        f"Selected set is **{selected['status']}** — only an APPROVED set can be "
        "re-opened."
    )
else:
    justification = st.text_area(
        "Justification (mandatory)", height=90,
        placeholder="Why is a new version needed (e.g. new experience, basis change)?",
        key="lin_reopen_just",
    )
    reopen_btn = st.button(
        "Re-open → create DRAFT child", type="primary", disabled=not _can_propose
    )
    if not _can_propose:
        st.caption(
            f"Your role ({_user.role.value}) cannot re-open — re-opening creates a draft "
            "for a proposer (analyst) to edit."
        )
    if reopen_btn:
        if not _can_propose:  # UI is the only gate (reopen has no engine RBAC)
            st.error("You do not have permission to re-open a set.")
            st.stop()
        if not justification.strip():
            st.error("A justification is mandatory to re-open an approved set.")
        else:
            try:
                new_id = reopen(set_id, me, justification.strip(), db_path=DB)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            except Exception as exc:  # noqa: BLE001 - defensive UI guard
                st.error(f"Re-open failed: {exc}")
                st.stop()
            # Hand the new DRAFT child to the Stage-2 editor via session state.
            _con = duckdb.connect(DB, read_only=True)
            try:
                _src = _con.execute(
                    "SELECT source_study_run_id FROM gold_assumption_sets "
                    "WHERE assumption_set_id = ?",
                    [new_id],
                ).fetchone()
            finally:
                _con.close()
            st.session_state["active_assumption_set_id"] = new_id
            if _src and _src[0]:
                st.session_state["source_study_run_id"] = _src[0]
            st.success(
                f"Created DRAFT version `{new_id[:8]}…` (child of `{set_id[:8]}…`). "
                "Open **TEV Stage 2** to edit it, then run Stages 3–4 to approve."
            )
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Publish (approver): APPROVED set → effective range + supersede prior live
# ---------------------------------------------------------------------------
st.subheader("Publish (set effective range + supersede prior)")
st.caption(
    "Makes an APPROVED version the live set for a date range and supersedes the "
    "prior live version in the lineage. Approval must already be complete (Stage 4)."
)
if selected["status"] != "APPROVED":
    st.caption(f"Selected set is **{selected['status']}** — only an APPROVED set can be published.")
else:
    c1, c2 = st.columns(2)
    with c1:
        eff_from = st.date_input("Effective from", value=date.today(), key="lin_eff_from")
    with c2:
        eff_to = st.date_input(
            "Effective to", value=date.today() + timedelta(days=365), key="lin_eff_to"
        )
    publish_btn = st.button(
        "Publish version", type="primary", disabled=not _can_signoff
    )
    if not _can_signoff:
        st.caption(
            f"Your role ({_user.role.value}) cannot publish — publishing is an approver "
            "(sign-off) action."
        )
    if publish_btn:
        if not _can_signoff:  # UI is the only gate (approve_and_supersede has no engine RBAC)
            st.error("You do not have permission to publish a version.")
            st.stop()
        try:
            approve_and_supersede(set_id, eff_from, eff_to, db_path=DB)
        except OverlappingEffectiveRange as exc:
            st.error(f"Effective range overlaps another version in the lineage: {exc}")
            st.stop()
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        except Exception as exc:  # noqa: BLE001 - defensive UI guard
            st.error(f"Publish failed: {exc}")
            st.stop()
        st.success(
            f"Published `{set_id[:8]}…` effective {eff_from} → {eff_to}; any prior live "
            "version in this lineage is now SUPERSEDED."
        )
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Compare versions (any viewer)
# ---------------------------------------------------------------------------
st.subheader("Compare versions")
members = overview["members"]
if len(members) < 2:
    st.caption("At least two versions are needed to compare.")
else:
    mlabels = {f"v{m['version']} · {m['id'][:8]}… · {m['status']}": m["id"] for m in members}
    cc1, cc2 = st.columns(2)
    with cc1:
        a_label = st.selectbox("Version A", list(mlabels.keys()), index=0, key="lin_cmp_a")
    with cc2:
        b_label = st.selectbox("Version B", list(mlabels.keys()), index=len(mlabels) - 1, key="lin_cmp_b")
    if st.button("Compare", key="lin_cmp_btn"):
        set_a, set_b = mlabels[a_label], mlabels[b_label]
        if set_a == set_b:
            st.info("Choose two different versions to compare.")
        else:
            diff = compare_versions(set_a, set_b, db_path=DB)
            dtev = diff.delta_tev
            st.metric(
                "ΔTEV (B − A)",
                "n/a" if dtev is None or (isinstance(dtev, float) and math.isnan(dtev))
                else f"{dtev:,.2f}",
            )
            if diff.changed_cells:
                rows = []
                for c in diff.changed_cells:
                    dim = c["dimension"]
                    rows.append({
                        "Decrement": c["decrement"],
                        "Product": dim.get("product"),
                        "Gender": dim.get("gender"),
                        "Risk class": dim.get("risk_class"),
                        "Duration band": dim.get("duration_band"),
                        "Old": c["old"],
                        "New": c["new"],
                        "Rationale": c["rationale"],
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            else:
                st.info("No cell-level differences between these two versions.")
