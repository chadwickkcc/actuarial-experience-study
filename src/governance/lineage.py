"""Phase-4 assumption-set versioning & lineage (Session 24).

Implements the Technical Spec v3.0 §H.5 contract, realising FR-4-07 … FR-4-11 and
NFR-G-05: parent→child version lineage, supersession (≤1 APPROVED-current per
lineage), effective-dating with a live-set resolver, cross-version comparison
(changed cells + ΔTEV + rationale), and a reproducibility stamp.

Governance is ordinary application code outside ``src/ai/``: all DB access here
uses the standard parameterized DuckDB write path (NOT ``src/utils/sql_boundary``,
which is the AI layer's read-only boundary). Lineage functions are pure data
operations — RBAC enforcement is the Session-25 workflow engine's responsibility.
"""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import duckdb

from src.tev.assumption_set import (
    AssumptionSet,
    create_assumption_set_from_ae_run,
    load_assumption_set,
    save_assumption_set,
)
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import AssumptionSetStatus, User, VersionDiff


class OverlappingEffectiveRange(Exception):
    """Raised when an effective range would overlap another in the same lineage."""


# Decrement attribute on AssumptionSet → label used in a VersionDiff cell.
_DECREMENT_ATTRS = {
    "mortality": "mortality_multipliers",
    "lapse": "lapse_multipliers",
    "surrender": "surrender_multipliers",
    "ci_incidence": "ci_incidence_multipliers",
    "premium_persistency": "premium_persistency",
}


# ---------------------------------------------------------------------------
# create_version + lineage_root (FR-4-07)
# ---------------------------------------------------------------------------

def create_version(
    parent_set_id: Optional[str],
    source_study_run_id: str,
    author: User,
    *,
    db_path: str = DEFAULT_DB_PATH,
    tev_config_path: Optional[str] = None,
    output_yaml_dir: Optional[str] = None,
) -> str:
    """Create a new assumption-set version in DRAFT; return its id (FR-4-07).

    When ``parent_set_id`` is given the parent's full content is cloned into a new
    DRAFT version (``version = parent.version + 1``); when ``None`` a lineage root
    is seeded from the A/E study run via ``create_assumption_set_from_ae_run`` and
    then marked DRAFT with ``parent_set_id = NULL``.
    """
    out_dir = Path(output_yaml_dir) if output_yaml_dir else Path(db_path).parent / "assumption_sets"

    if parent_set_id is None:
        cfg_path = (
            Path(tev_config_path) if tev_config_path
            else Path(db_path).parent.parent / "config" / "tev_config.yaml"
        )
        aset = create_assumption_set_from_ae_run(
            source_study_run_id, author.username, Path(db_path), cfg_path, out_dir,
        )
        new_id = aset.id
        _set_columns(db_path, new_id, {"status": AssumptionSetStatus.DRAFT.value, "parent_set_id": None})
        return new_id

    parent = load_assumption_set(parent_set_id, Path(db_path))
    new_id = str(uuid.uuid4())
    child = copy.deepcopy(parent)
    child.id = new_id
    child.version = parent.version + 1
    child.status = AssumptionSetStatus.DRAFT
    child.source_study_run_id = source_study_run_id
    child.author_id = author.username
    child.yaml_file_path = str(out_dir / f"{new_id}.yaml")
    save_assumption_set(child, Path(db_path))
    # The metadata INSERT omits parent_set_id; record the lineage link explicitly.
    _set_columns(db_path, new_id, {"parent_set_id": parent_set_id})
    return new_id


def lineage_root(assumption_set_id: str, *, db_path: str = DEFAULT_DB_PATH) -> str:
    """Walk ``parent_set_id`` to the lineage root and return its id (FR-4-07)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        seen: set[str] = set()
        cur = assumption_set_id
        while cur not in seen:
            seen.add(cur)
            row = con.execute(
                "SELECT parent_set_id FROM gold_assumption_sets "
                "WHERE assumption_set_id = ?",
                [cur],
            ).fetchone()
            if row is None:
                raise ValueError(f"Assumption set {cur} not found in DB.")
            if row[0] is None:
                return cur
            cur = row[0]
        return cur  # cycle guard (should not occur with consistent data)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# approve_and_supersede (FR-4-08; NFR-G-05)
# ---------------------------------------------------------------------------

def approve_and_supersede(
    assumption_set_id: str,
    effective_from: date,
    effective_to: date,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Approve a set, set its effective range, and supersede the prior approved
    set in the same lineage (FR-4-08/09).

    Enforces non-overlapping effective ranges within the lineage and ≤1
    APPROVED-current per lineage. Raises ``OverlappingEffectiveRange`` (before any
    write) if the requested range overlaps an existing range in the lineage.
    """
    if effective_from > effective_to:
        raise ValueError("effective_from must be on or before effective_to")

    root = lineage_root(assumption_set_id, db_path=db_path)

    con = duckdb.connect(str(db_path))
    try:
        members = _lineage_members(con, root)
        # Overlap check against every OTHER member that already carries a range.
        for mid, _status, m_from, m_to in members:
            if mid == assumption_set_id or m_from is None or m_to is None:
                continue
            if effective_from <= m_to and m_from <= effective_to:
                raise OverlappingEffectiveRange(
                    f"Effective range [{effective_from}, {effective_to}] overlaps "
                    f"set {mid} [{m_from}, {m_to}] in lineage {root}."
                )

        # Approve the target.
        con.execute(
            "UPDATE gold_assumption_sets "
            "SET status = ?, effective_from = ?, effective_to = ?, approved_ts = ? "
            "WHERE assumption_set_id = ?",
            [
                AssumptionSetStatus.APPROVED.value,
                effective_from, effective_to, datetime.utcnow(),
                assumption_set_id,
            ],
        )
        # Supersede any other currently-APPROVED set(s) in the lineage.
        for mid, status, _m_from, _m_to in members:
            if mid == assumption_set_id:
                continue
            if status == AssumptionSetStatus.APPROVED.value:
                con.execute(
                    "UPDATE gold_assumption_sets "
                    "SET status = ?, superseded_by = ? WHERE assumption_set_id = ?",
                    [AssumptionSetStatus.SUPERSEDED.value, assumption_set_id, mid],
                )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# resolve_live_set (FR-4-09)
# ---------------------------------------------------------------------------

def resolve_live_set(
    lineage_id: str, as_of: date, *, db_path: str = DEFAULT_DB_PATH
) -> Optional[str]:
    """Return the APPROVED set in the lineage whose effective range contains
    ``as_of`` (FR-4-09), or ``None`` if there is no live set for that date.

    ``lineage_id`` is normally the root assumption-set id (see ``lineage_root``),
    but any member of the lineage is accepted — it is normalised to the root —
    and an unknown id returns ``None``.
    """
    try:
        root = lineage_root(lineage_id, db_path=db_path)
    except ValueError:
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for mid, status, m_from, m_to in _lineage_members(con, root):
            if status != AssumptionSetStatus.APPROVED.value:
                continue
            if m_from is not None and m_to is not None and m_from <= as_of <= m_to:
                return mid
        return None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# compare_versions (FR-4-10)
# ---------------------------------------------------------------------------

def compare_versions(
    set_id_a: str, set_id_b: str, *, db_path: str = DEFAULT_DB_PATH
) -> VersionDiff:
    """Cell-level diff between two assumption-set versions (FR-4-10).

    Reports each changed multiplier cell (old, new, rationale), ΔTEV from each
    set's latest baseline TEV run (``tev_b - tev_a``; NaN if either is missing),
    and a per-cell rationale map keyed by the cell identifier.
    """
    aset_a = load_assumption_set(set_id_a, Path(db_path))
    aset_b = load_assumption_set(set_id_b, Path(db_path))

    changed_cells: list[dict] = []
    rationale_by_cell: dict[str, str] = {}

    for decrement, attr in _DECREMENT_ATTRS.items():
        cells_a = {_cell_key(m): m for m in getattr(aset_a, attr)}
        cells_b = {_cell_key(m): m for m in getattr(aset_b, attr)}
        for key in sorted(set(cells_a) | set(cells_b)):
            ma = cells_a.get(key)
            mb = cells_b.get(key)
            old = ma.multiplier if ma is not None else None
            new = mb.multiplier if mb is not None else None
            if old == new:
                continue
            rationale = mb.override_rationale if mb is not None else ""
            cell_id = f"{decrement}|{key}"
            changed_cells.append({
                "decrement": decrement,
                "dimension": _cell_dimension(ma or mb),
                "old": old,
                "new": new,
                "rationale": rationale,
            })
            if rationale:
                rationale_by_cell[cell_id] = rationale

    delta_tev = _baseline_tev(db_path, set_id_b) - _baseline_tev(db_path, set_id_a)
    return VersionDiff(
        changed_cells=changed_cells,
        delta_tev=delta_tev,
        rationale_by_cell=rationale_by_cell,
    )


# ---------------------------------------------------------------------------
# reproducibility_stamp (FR-4-11)
# ---------------------------------------------------------------------------

def reproducibility_stamp(
    assumption_set_id: str, *, db_path: str = DEFAULT_DB_PATH
) -> dict:
    """Trace an assumption set to the exact study run + AI model + data snapshot
    that produced it (FR-4-11). Returns a flat dict of provenance fields."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT gas.assumption_set_id, gas.version, gas.source_study_run_id, "
            "       gas.ai_model_id, "
            "       gsr.data_snapshot_hash, gsr.config_hash, gsr.code_version, "
            "       gsr.credibility_method, "
            "       gam.model_type, gam.fit_ts "
            "FROM gold_assumption_sets gas "
            "LEFT JOIN gold_study_runs gsr ON gas.source_study_run_id = gsr.run_id "
            "LEFT JOIN gold_ai_model_registry gam ON gas.ai_model_id = gam.model_id "
            "WHERE gas.assumption_set_id = ?",
            [assumption_set_id],
        ).fetchone()
    finally:
        con.close()

    if row is None:
        raise ValueError(f"Assumption set {assumption_set_id} not found in DB.")

    return {
        "assumption_set_id": row[0],
        "version": row[1],
        "source_study_run_id": row[2],
        "ai_model_id": row[3],
        "data_snapshot_hash": row[4],
        "config_hash": row[5],
        "code_version": row[6],
        "credibility_method": row[7],
        "ai_model_type": row[8],
        "ai_model_fit_ts": row[9],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _set_columns(db_path: str, assumption_set_id: str, values: dict) -> None:
    """Parameterized UPDATE of named columns on one assumption-set row.

    Column names come from internal constants only (never user input)."""
    if not values:
        return
    set_clause = ", ".join(f"{col} = ?" for col in values)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            f"UPDATE gold_assumption_sets SET {set_clause} "
            "WHERE assumption_set_id = ?",
            [*values.values(), assumption_set_id],
        )
    finally:
        con.close()


def _lineage_members(con: "duckdb.DuckDBPyConnection", root_id: str) -> list[tuple]:
    """Return [(id, status, effective_from, effective_to), …] for every set whose
    lineage root is ``root_id`` (walks parent links in memory)."""
    rows = con.execute(
        "SELECT assumption_set_id, parent_set_id, status, effective_from, effective_to "
        "FROM gold_assumption_sets"
    ).fetchall()
    parent_of = {r[0]: r[1] for r in rows}
    members = []
    for sid, _parent, status, eff_from, eff_to in rows:
        if _root_of(parent_of, sid) == root_id:
            members.append((sid, status, eff_from, eff_to))
    return members


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


def _cell_key(mult) -> str:
    """Stable identity for a multiplier cell within a decrement type."""
    lo, hi = mult.duration_band[0], mult.duration_band[1]
    return f"{mult.product}|{mult.gender}|{mult.risk_class}|{lo}-{hi}"


def _cell_dimension(mult) -> dict:
    return {
        "product": mult.product,
        "gender": mult.gender,
        "risk_class": mult.risk_class,
        "duration_band": list(mult.duration_band),
    }


def _baseline_tev(db_path: str, assumption_set_id: str) -> float:
    """Latest baseline (sensitivity_id NULL) total_tev for a set; NaN if none."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT total_tev FROM gold_tev_run_log "
            "WHERE assumption_set_id = ? AND sensitivity_id IS NULL "
            "AND total_tev IS NOT NULL "
            "ORDER BY run_ts DESC LIMIT 1",
            [assumption_set_id],
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        return float("nan")
    return float(row[0])
