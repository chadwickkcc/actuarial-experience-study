"""Governance reporting & compliance export (Session 27, §H.8).

Realises FR-4-23 (governance dashboard data), FR-4-24 (exportable compliance
pack), FR-4-25 (retention policy — no hard deletes), and the NFR-G-07 timing
target. Governance is ordinary application code **outside** ``src/ai/``: all
reads use read-only, parameterized DuckDB (never the AI read-only
``sql_boundary``), and the compliance pack reuses the existing Jinja2 machinery
(``src.reporting.generator._get_jinja_env`` with ``autoescape=True``, FR-3A-03).

This module only *reads* the governance/results tables and *renders* HTML — it
writes no database rows and changes no Phase 1–3 behaviour.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.governance import lineage as _lineage
from src.governance import workflow as _workflow
from src.governance.audit import artifact_timeline, unified_audit_query
from src.governance.users import DEFAULT_CONFIG_PATH
from src.reporting.generator import _get_jinja_env
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import ArtifactType, AuditFilter

# Report output root without importing the UI layer (src must not depend on ui/).
# reporting.py lives at src/governance/reporting.py -> parents[2] is the project root.
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports"

_ASSUMPTION_STATES = [
    "DRAFT",
    "PROPOSED",
    "STAGE3_APPROVED",
    "APPROVED",
    "SUPERSEDED",
]

_COMPLIANCE_TEMPLATE = "compliance_pack.html.j2"


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #

def _load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Parse the governance config YAML (roles/chain/materiality/retention)."""
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _as_artifact_type(artifact_type) -> ArtifactType:
    """Accept an ``ArtifactType`` or its string value; normalise to the enum."""
    if isinstance(artifact_type, ArtifactType):
        return artifact_type
    return ArtifactType(str(artifact_type))


def _fmt_ts(value) -> str:
    """Render a timestamp/date as an ISO string; empty string for NULL."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _fmt_num(value) -> str:
    """Render a float for display; '—' for NULL / NaN."""
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f != f:  # NaN
        return "—"
    return f"{f:,.6g}"


def _fmt_dimension(dim) -> str:
    """Render a changed-cell dimension as a readable ``k=v, k=v`` string.

    ``lineage.compare_versions`` reports ``dimension`` as a dict
    (e.g. ``{"product":"TERM","gender":"M","duration_band":[1,5]}``); rendering it
    verbatim would drop a raw Python dict literal into the compliance document.
    """
    if isinstance(dim, dict):
        parts = []
        for k, v in dim.items():
            if isinstance(v, (list, tuple)):
                v = "-".join(str(x) for x in v)
            parts.append(f"{k}={v}")
        return ", ".join(parts)
    return "" if dim is None else str(dim)


def _user_display_map(db_path: str) -> dict:
    """{user_id: (display_name, role)} for resolving sign-off actors."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT user_id, display_name, role FROM gold_users"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _table_columns(con: duckdb.DuckDBPyConnection, table: str) -> set:
    """Column names present on ``table`` (empty set if the table is absent)."""
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return {r[0] for r in rows}


def _read_assumption_set_rows(db_path: str) -> list:
    """Read (id, version, status, parent_set_id, effective_from, effective_to).

    Tolerant of a DB that predates the Session-24 lineage columns (or has no
    assumption-set table at all): missing columns come back as None so the
    dashboard and compliance pack degrade gracefully rather than hard-crashing.
    Column names are drawn from a fixed internal allowlist intersected with the
    live schema — never from caller input.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        have = _table_columns(con, "gold_assumption_sets")
        if not have:
            return []
        base = ["assumption_set_id", "version", "status"]
        optional = ["parent_set_id", "effective_from", "effective_to"]
        select_cols = base + [c for c in optional if c in have]
        rows = con.execute(
            f"SELECT {', '.join(select_cols)} FROM gold_assumption_sets"  # noqa: S608 (fixed allowlist)
        ).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        rec = dict(zip(select_cols, r))
        out.append((
            rec["assumption_set_id"], rec["version"], rec["status"],
            rec.get("parent_set_id"), rec.get("effective_from"), rec.get("effective_to"),
        ))
    return out


def _study_run_artifact_ids(db_path: str) -> list:
    """Distinct study-run ids that have entered the approval chain.

    Union of runs that already carry a sign-off (`gold_governance_signoffs`) and
    runs **submitted** for approval but not yet signed (a `STUDY_RUN_SUBMITTED`
    event in `gold_ae_governance_events`) — so a freshly-submitted, unsigned run
    still surfaces on the global pending queue. Both tables are absence-tolerant.
    """
    ids: set = set()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        if _table_columns(con, "gold_governance_signoffs"):
            ids.update(
                r[0] for r in con.execute(
                    "SELECT DISTINCT artifact_id FROM gold_governance_signoffs "
                    "WHERE artifact_type = 'STUDY_RUN'"
                ).fetchall()
            )
        if _table_columns(con, "gold_ae_governance_events"):
            ids.update(
                r[0] for r in con.execute(
                    "SELECT DISTINCT study_run_id FROM gold_ae_governance_events "
                    "WHERE event_type = 'STUDY_RUN_SUBMITTED'"
                ).fetchall()
            )
    finally:
        con.close()
    return sorted(ids)


def _root_from_parent_map(set_id: str, parent_map: dict) -> str:
    """Walk parent links to the lineage root using an in-memory map.

    Cycle-guarded. An **orphan** whose ``parent_set_id`` points at an id that is
    not itself a known set is treated as its own root (we stop at the deepest
    *known* ancestor rather than returning a phantom id), matching
    ``lineage._root_of``'s orphan handling.
    """
    seen: set = set()
    current = set_id
    while current in parent_map and current not in seen:
        parent = parent_map[current]
        if not parent or parent not in parent_map:
            break
        seen.add(current)
        current = parent
    return current


# --------------------------------------------------------------------------- #
# FR-4-25 — retention policy                                                  #
# --------------------------------------------------------------------------- #

def retention_policy(cfg: Optional[dict] = None, *, config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return the configured retention policy (FR-4-25).

    The system performs **no hard deletes**; superseded/archived artifacts are
    retained and marked as such. ``cfg`` is the parsed governance config; when
    omitted it is loaded from ``config_path``. Returns a normalised dict with
    ``hard_delete`` (always coerced to bool) and ``archive_after_days``.
    """
    if cfg is None:
        cfg = _load_config(config_path)
    ret = (cfg or {}).get("retention", {}) or {}
    return {
        "hard_delete": bool(ret.get("hard_delete", False)),
        "archive_after_days": int(ret.get("archive_after_days", 3650)),
    }


# --------------------------------------------------------------------------- #
# FR-4-23 — governance dashboard data                                         #
# --------------------------------------------------------------------------- #

def dashboard_data(
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    recent_limit: int = 50,
) -> dict:
    """Assemble the governance dashboard's data (FR-4-23; NFR-G-07 < 3 s).

    Returns::

        {
          "sets_by_state":        {status: [ {assumption_set_id, version,
                                              lineage_root, effective_from,
                                              effective_to} ]},
          "live_set_per_lineage": [ {lineage_root, live_set_id} ],
          "pending_approvals":    [ {artifact_type, artifact_id,
                                     artifact_version, next_level,
                                     required_role} ],   # global (all roles)
          "recent_activity":      [ unified-audit event dicts, newest first ],
        }

    All reads are read-only; ``pending_approvals`` here is the *global* queue
    (every in-chain artifact awaiting any level — assumption sets by status,
    study runs submitted or partially signed), distinct from
    ``workflow.pending_approvals(user)`` which filters to one user's role.

    Note (upstream seam): ``live_set_id`` is resolved from the effective-date
    range, which is set by ``lineage.approve_and_supersede`` — the Stage-4 chain
    approval (``workflow.record_signoff``) locks a set to APPROVED but does not
    set effective dates. So a set approved purely through the chain shows
    ``live_set_id = None`` until an effective range is recorded. The dashboard
    faithfully reflects the stored data.
    """
    sets_by_state: dict = {state: [] for state in _ASSUMPTION_STATES}
    parent_map: dict = {}

    rows = _read_assumption_set_rows(db_path)
    run_ids = _study_run_artifact_ids(db_path)

    for set_id, _version, _status, parent_id, _ef, _et in rows:
        parent_map[set_id] = parent_id

    for set_id, version, status, _parent, ef, et in rows:
        entry = {
            "assumption_set_id": set_id,
            "version": version,
            "lineage_root": _root_from_parent_map(set_id, parent_map),
            "effective_from": _fmt_ts(ef),
            "effective_to": _fmt_ts(et),
        }
        sets_by_state.setdefault(status, []).append(entry)

    # Live set per lineage root, as of today (FR-4-09).
    roots = sorted({_root_from_parent_map(sid, parent_map) for sid in parent_map})
    today = date.today()
    live_set_per_lineage = []
    for root in roots:
        live_id = _lineage.resolve_live_set(root, today, db_path=db_path)
        live_set_per_lineage.append({"lineage_root": root, "live_set_id": live_id})

    # Global pending-approvals queue (mirror workflow.pending_approvals, no role filter).
    pending: list = []
    for set_id, version, status, _parent, _ef, _et in rows:
        if status not in ("PROPOSED", "STAGE3_APPROVED"):
            continue
        nxt = _workflow.next_required_level(
            ArtifactType.ASSUMPTION_SET, set_id, db_path=db_path, config_path=config_path
        )
        if nxt is not None:
            pending.append({
                "artifact_type": ArtifactType.ASSUMPTION_SET.value,
                "artifact_id": set_id,
                "artifact_version": version,
                "next_level": nxt.level,
                "required_role": nxt.required_role.value,
            })
    for run_id in run_ids:
        nxt = _workflow.next_required_level(
            ArtifactType.STUDY_RUN, run_id, db_path=db_path, config_path=config_path
        )
        if nxt is not None:
            pending.append({
                "artifact_type": ArtifactType.STUDY_RUN.value,
                "artifact_id": run_id,
                "artifact_version": None,
                "next_level": nxt.level,
                "required_role": nxt.required_role.value,
            })

    recent = unified_audit_query(AuditFilter(), db_path=db_path)[:recent_limit]

    return {
        "sets_by_state": sets_by_state,
        "live_set_per_lineage": live_set_per_lineage,
        "pending_approvals": pending,
        "recent_activity": recent,
    }


# --------------------------------------------------------------------------- #
# FR-4-24 — compliance pack export                                            #
# --------------------------------------------------------------------------- #

def _signoff_rows(db_path: str, artifact_type: str, artifact_id: str) -> list:
    """All sign-offs for one artifact, chain order (by seq), with actor names."""
    users = _user_display_map(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT chain_level, required_role, actor_user_id, actor_role, decision, "
            "comment, attestation_text, delta_tev, signoff_ts "
            "FROM gold_governance_signoffs "
            "WHERE artifact_type = ? AND artifact_id = ? ORDER BY seq",
            [artifact_type, artifact_id],
        ).fetchall()
    finally:
        con.close()
    out = []
    for (level, req_role, actor_id, actor_role, decision, comment,
         attest, delta_tev, ts) in rows:
        display, _role = users.get(actor_id, (actor_id, actor_role))
        out.append({
            "chain_level": level,
            "required_role": req_role,
            "actor": display,
            "actor_role": actor_role,
            "decision": decision,
            "comment": comment,
            "attestation_text": attest,
            "delta_tev": _fmt_num(delta_tev),
            "signoff_ts": _fmt_ts(ts),
        })
    return out


def _audit_rows(artifact_type: ArtifactType, artifact_id: str, db_path: str) -> list:
    """Pre-formatted per-artifact audit excerpt (chronological)."""
    out = []
    for ev in artifact_timeline(artifact_type, artifact_id, db_path=db_path):
        out.append({
            "ts": _fmt_ts(ev.get("ts")),
            "actor": ev.get("actor") or "",
            "role": ev.get("role") or "",
            "action": ev.get("action") or "",
            "source": ev.get("source") or "",
            "detail": ev.get("detail") or "",
        })
    return out


def _assumption_lineage(db_path: str, artifact_id: str) -> tuple:
    """Return (lineage_versions, parent_id) for an assumption set.

    ``lineage_versions`` is every set sharing the lineage root, ordered by
    version; ``parent_id`` is the direct parent of ``artifact_id`` (or None).
    """
    rows = _read_assumption_set_rows(db_path)
    parent_map = {r[0]: r[3] for r in rows}
    root = _root_from_parent_map(artifact_id, parent_map)
    members = []
    for sid, version, status, parent_id, ef, et in rows:
        if _root_from_parent_map(sid, parent_map) == root:
            members.append({
                "assumption_set_id": sid,
                "version": version,
                "status": status,
                "effective_from": _fmt_ts(ef),
                "effective_to": _fmt_ts(et),
                "is_current": sid == artifact_id,
            })
    members.sort(key=lambda m: m["version"])
    return members, parent_map.get(artifact_id)


def _rationale_rows(parent_id: Optional[str], artifact_id: str, db_path: str) -> list:
    """Per-change rationale vs the parent version.

    Empty when there is no parent. If the diff itself fails (e.g. the parent
    version's YAML cannot be loaded), a compliance document must **not** silently
    render a reassuring "no changes" — instead a single marker row surfaces that
    the comparison was unavailable.
    """
    if not parent_id:
        return []
    try:
        diff = _lineage.compare_versions(parent_id, artifact_id, db_path=db_path)
    except Exception:  # noqa: BLE001 - never crash the pack; surface, don't hide
        return [{
            "decrement": "—",
            "dimension": "(comparison unavailable — the parent version could not be loaded)",
            "old": "—",
            "new": "—",
            "rationale": "",
        }]
    out = []
    for cell in diff.changed_cells:
        out.append({
            "decrement": cell.get("decrement", ""),
            "dimension": _fmt_dimension(cell.get("dimension")),
            "old": _fmt_num(cell.get("old")),
            "new": _fmt_num(cell.get("new")),
            "rationale": cell.get("rationale", "") or "",
        })
    return out


def _supporting_reports(db_path: str, study_run_id: Optional[str],
                        assumption_set_id: Optional[str]) -> list:
    """Reference links to the supporting A/E and TEV reports (by filename stem)."""
    reports = []
    if study_run_id:
        reports.append({
            "label": "Working Actuary Report (A/E)",
            "reference": f"working_actuary_{study_run_id[:8]}.html",
        })
        reports.append({
            "label": "Chief Actuary Summary (A/E)",
            "reference": f"chief_actuary_{study_run_id[:8]}.html",
        })
    if assumption_set_id:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            tev_runs = con.execute(
                "SELECT tev_run_id FROM gold_tev_run_log WHERE assumption_set_id = ? "
                "ORDER BY run_ts",
                [assumption_set_id],
            ).fetchall()
        finally:
            con.close()
        if tev_runs:
            # All TEV runs for a set share one impact-report stem — list it once,
            # noting how many runs it covers (rather than N identical links).
            reports.append({
                "label": f"TEV impact report ({len(tev_runs)} run(s))",
                "reference": f"tev_impact_report_{assumption_set_id[:8]}.html",
            })
    return reports


def export_compliance_pack(
    artifact_type,
    artifact_id: str,
    fmt: str = "html",
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    output_dir=None,
) -> str:
    """Export a defensible compliance pack for an APPROVED artifact (FR-4-24).

    For an **assumption set** (must be APPROVED): full version lineage, every
    sign-off with its attestation, the per-artifact audit excerpt, the
    per-change rationale vs the parent version, the reproducibility stamp, and
    links to the supporting TEV/A/E reports. For an **A/E study run** (must be
    "fit for assumption-setting", FR-4-14): sign-offs + attestations + audit
    excerpt + supporting-report links.

    ``fmt='html'`` renders via the existing ``autoescape=True`` Jinja2 machinery
    and returns the written path. ``fmt='pdf'`` is a deferred contract surface
    (owner decision, Session 27) and raises ``NotImplementedError``. Exporting a
    non-APPROVED / not-yet-fit artifact raises ``ValueError``.
    """
    at = _as_artifact_type(artifact_type)
    fmt = (fmt or "html").lower()
    if fmt == "pdf":
        raise NotImplementedError("PDF export deferred; use fmt='html'")
    if fmt != "html":
        raise ValueError(f"unsupported fmt {fmt!r}; expected 'html' or 'pdf'")

    cfg = _load_config(config_path)
    attestation_statement = cfg.get("attestation_text", "")

    # Each helper below opens and closes its own short-lived read-only
    # connection; they run sequentially so no two DuckDB connections to the same
    # file are ever open at once (a read-write helper opening over a held
    # read-only handle would raise).
    lineage_versions: list = []
    rationale: list = []
    repro: Optional[dict] = None
    source_study_run_id: Optional[str] = None
    not_yet_effective: bool = False
    source_run_unfit: bool = False

    if at is ArtifactType.ASSUMPTION_SET:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            row = con.execute(
                "SELECT status, source_study_run_id, effective_from, effective_to "
                "FROM gold_assumption_sets WHERE assumption_set_id = ?",
                [artifact_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"assumption set {artifact_id!r} not found")
        status, source_study_run_id = row[0], row[1]
        effective_from, effective_to = row[2], row[3]
        if status != "APPROVED":
            raise ValueError(
                f"compliance pack requires an APPROVED assumption set; "
                f"{artifact_id!r} is {status}"
            )
        # An APPROVED set that was never "published" has no effective range and is not
        # the live set (chain approval does not effective-date; see approve_and_supersede).
        # Surface that loudly rather than letting the pack imply the set is in force.
        not_yet_effective = effective_from is None or effective_to is None
        # The pack must not silently vouch for a set built on an ungoverned study run.
        # Default is to WARN in the pack; set compliance.require_fit_source_run: true to
        # hard-refuse instead.
        source_run_unfit = bool(source_study_run_id) and not _workflow.is_study_run_fit(
            source_study_run_id, db_path=db_path, config_path=config_path
        )
        if source_run_unfit and bool(
            (cfg.get("compliance") or {}).get("require_fit_source_run", False)
        ):
            raise ValueError(
                f"compliance pack requires a governance-approved (fit) source study run; "
                f"source run {source_study_run_id!r} of set {artifact_id!r} is not yet fit "
                f"(set compliance.require_fit_source_run: false to warn instead of block)"
            )
        lineage_versions, parent_id = _assumption_lineage(db_path, artifact_id)
        rationale = _rationale_rows(parent_id, artifact_id, db_path)
        repro = _lineage.reproducibility_stamp(artifact_id, db_path=db_path)
        reports = _supporting_reports(db_path, source_study_run_id, artifact_id)
        title = "Assumption Set Compliance Pack"
    else:  # STUDY_RUN
        if not _workflow.is_study_run_fit(
            artifact_id, db_path=db_path, config_path=config_path
        ):
            raise ValueError(
                f"compliance pack requires an approved (fit) study run; "
                f"{artifact_id!r} is not yet fit for assumption-setting"
            )
        source_study_run_id = artifact_id
        reports = _supporting_reports(db_path, artifact_id, None)
        title = "Study Run Compliance Pack"

    signoffs = _signoff_rows(db_path, at.value, artifact_id)

    context = {
        "title": title,
        "artifact_type": at.value,
        "artifact_id": artifact_id,
        "generated_ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "attestation_statement": attestation_statement,
        "lineage": lineage_versions,
        "signoffs": signoffs,
        "audit": _audit_rows(at, artifact_id, db_path),
        "rationale": rationale,
        "repro": repro,
        "reports": reports,
        "source_study_run_id": source_study_run_id,
        "not_yet_effective": not_yet_effective,
        "source_run_unfit": source_run_unfit,
    }

    html = _get_jinja_env().get_template(_COMPLIANCE_TEMPLATE).render(**context)

    out_dir = Path(output_dir) if output_dir else _DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"compliance_pack_{at.value.lower()}_{artifact_id[:8]}_{ts}.html"
    out_path = out_dir / fname
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())
