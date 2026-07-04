"""Pure, Streamlit-free helpers for the Phase-4 governance lifecycle pages.

Read-only DuckDB access only (parameterized). Kept out of the view bodies so the
selectors / lineage-overview builders can be unit-tested without a Streamlit
runtime. The governed *mutations* (submit_study_run, record_signoff, reopen,
approve_and_supersede) are called directly from the pages via the engine.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import duckdb

from src.governance.lineage import lineage_root, resolve_live_set
from ui.config import DB_PATH


def list_complete_study_runs(db_path: Path = DB_PATH) -> list[dict]:
    """COMPLETE study runs (newest first) for a governance run selector.

    Returns ``[{run_id, run_ts, products, label}]``; empty if none/DB absent.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT run_id, run_ts, product_codes FROM gold_study_runs "
            "WHERE status = 'COMPLETE' ORDER BY run_ts DESC NULLS LAST"
        ).fetchall()
    finally:
        con.close()
    out: list[dict] = []
    for run_id, run_ts, products in rows:
        out.append({
            "run_id": run_id,
            "run_ts": run_ts,
            "products": products,
            "label": f"{str(run_id)[:8]}… · {str(run_ts)[:19]} · {products}",
        })
    return out


def study_run_submitted(run_id: str, db_path: Path = DB_PATH) -> bool:
    """True if the run already has a STUDY_RUN_SUBMITTED governance event."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM gold_ae_governance_events "
            "WHERE event_type = 'STUDY_RUN_SUBMITTED' AND study_run_id = ?",
            [run_id],
        ).fetchone()
    finally:
        con.close()
    return bool(row and row[0])


def list_assumption_sets(db_path: Path = DB_PATH) -> list[dict]:
    """All assumption sets (newest first) for a lineage/compare selector.

    Returns ``[{id, version, status, parent_set_id, label}]``.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT assumption_set_id, version, status, parent_set_id, created_ts "
            "FROM gold_assumption_sets ORDER BY created_ts DESC NULLS LAST"
        ).fetchall()
    finally:
        con.close()
    out: list[dict] = []
    for sid, version, status, parent, _ts in rows:
        out.append({
            "id": sid,
            "version": version,
            "status": status,
            "parent_set_id": parent,
            "label": f"{str(sid)[:8]}… · v{version} · {status}",
        })
    return out


def _root_of(parent_of: dict, sid: str) -> Optional[str]:
    """Walk ``parent_of`` from ``sid`` to its root (cycle-guarded)."""
    seen: set[str] = set()
    cur = sid
    while cur is not None and cur not in seen:
        seen.add(cur)
        parent = parent_of.get(cur)
        if parent is None:
            return cur
        cur = parent
    return cur


def lineage_overview(
    assumption_set_id: str, as_of: date, *, db_path: Path = DB_PATH
) -> dict:
    """Full lineage view for one set: root, every version, and the live set today.

    Returns ``{root, members: [{id, version, status, effective_from,
    effective_to, superseded_by, is_selected}], live_set_id}`` — ``members``
    sorted by version ascending. Raises ``ValueError`` if the set is unknown.
    """
    root = lineage_root(assumption_set_id, db_path=str(db_path))
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT assumption_set_id, parent_set_id, version, status, "
            "       effective_from, effective_to, superseded_by "
            "FROM gold_assumption_sets"
        ).fetchall()
    finally:
        con.close()
    parent_of = {r[0]: r[1] for r in rows}
    members = []
    for sid, _parent, version, status, eff_from, eff_to, superseded_by in rows:
        if _root_of(parent_of, sid) == root:
            members.append({
                "id": sid,
                "version": version,
                "status": status,
                "effective_from": eff_from,
                "effective_to": eff_to,
                "superseded_by": superseded_by,
                "is_selected": sid == assumption_set_id,
            })
    members.sort(key=lambda m: (m["version"] is None, m["version"]))
    live_set_id = resolve_live_set(root, as_of, db_path=str(db_path))
    return {"root": root, "members": members, "live_set_id": live_set_id}
