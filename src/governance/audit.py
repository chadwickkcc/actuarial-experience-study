"""Phase-4 governance audit — hash-chained writes + tamper-evident read layer.

Implements the Technical Spec v3.0 §H.7 audit contract. The **write + hash-chain
half** (``append_event``, Session 25) realises FR-4-20: every governance-log row
is append-only and tamper-evident via a hash chain. The **read/verify half**
(Session 26, FR-4-19/21/22) adds: ``record_ae_event`` / ``submit_study_run`` (write
the new A/E governance-events log), ``verify_chain`` (recompute a chain and report
the first divergence), and ``unified_audit_query`` / ``artifact_timeline`` (one
filterable view + per-artifact timeline across the three physically-separate
governance logs). The three logs stay physically separate (FR-4-19); this is a
unified *read* layer, not merged storage.

Hash-chain content rule (§G.2): for every hash-chained governance row,
``entry_hash = sha256(canonical || prev_hash_or_empty)`` where ``canonical`` is the
UTF-8 JSON serialisation of all of the row's business columns — every column
*except* ``prev_hash`` and ``entry_hash``, and *including* ``seq`` — with object
keys sorted alphabetically, timestamps/dates rendered as ISO-8601 strings, and
SQL ``NULL`` as JSON ``null``. ``prev_hash`` is the ``entry_hash`` of the
immediately prior row in the same table ordered by ``seq`` (empty string for the
first row). ``seq`` is a monotonically increasing integer assigned at insert
(``MAX(seq) + 1``; the first row is ``seq = 1``).

Governance is ordinary application code OUTSIDE ``src/ai/``: this uses the standard
parameterized ``duckdb.connect()`` write path (NOT ``src/utils/sql_boundary``,
the AI read-only boundary). The table name is taken only from the trusted internal
``_HASH_CHAINED_TABLES`` registry — never from caller input — so the INSERT is
built from a known column list with ``?`` placeholders for every value. There is
no update or delete path for these logs (FR-4-20).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import duckdb

from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import AuditFilter, IntegrityResult

# Hash-chained governance tables -> their full ordered column list (§G.2 / §G.3).
# Session 25 registers the sign-off log; Session 26 adds gold_ae_governance_events.
_SIGNOFF_COLUMNS = [
    "signoff_id", "seq", "artifact_type", "artifact_id", "artifact_version",
    "chain_level", "required_role", "actor_user_id", "actor_role", "decision",
    "comment", "attestation_text", "delta_tev", "required_final_level",
    "signoff_ts", "prev_hash", "entry_hash",
]

_AE_EVENT_COLUMNS = [
    "event_id", "seq", "event_type", "study_run_id", "actor_user_id",
    "detail", "event_ts", "prev_hash", "entry_hash",
]

# WRITE allowlist: the only tables ``append_event`` may write. Kept minimal on
# purpose (prototype simplicity) — the Phase-2 logs are NOT here, so append_event
# can never open an unintended write path into them.
_HASH_CHAINED_TABLES: dict[str, list[str]] = {
    "gold_governance_signoffs": _SIGNOFF_COLUMNS,
    "gold_ae_governance_events": _AE_EVENT_COLUMNS,
}

# Full ordered column lists for the Phase-2 governance logs, with the §G.5
# migrated hash-chain columns appended (physical order is cosmetic — _canonical_row
# sorts keys — but the SELECT reads them in this order).
_WORKFLOW_ITER_COLUMNS = [
    "iteration_id", "workflow_session_id", "iteration_number", "assumption_set_id",
    "tev_baseline_run_id", "stage", "action", "actuary_id", "actuary_comment",
    "total_tev", "delta_tev_vs_prior", "envelope_run_flag", "iteration_ts",
    "seq", "prev_hash", "entry_hash",
]
_ASSUMPTION_APPROVAL_COLUMNS = [
    "approval_id", "assumption_set_id", "workflow_session_id", "source_study_run_id",
    "tev_baseline_run_id", "proposer_id", "reviewer_id", "reviewer_decision",
    "reviewer_comment", "total_iterations", "envelope_run_flag", "envelope_tev_min",
    "envelope_tev_max", "proposed_envelope_percentile", "baseline_tev",
    "delta_tev_vs_prior", "max_sensitivity_delta", "proposed_ts", "approved_ts",
    "iteration_history", "seq", "prev_hash", "entry_hash",
]

# VERIFY registry: tables ``verify_chain`` may recompute. A superset of the write
# allowlist — it also covers the Phase-2 logs (§G.5: hashed from Phase 4 onward;
# the verifier begins each chain at the first row that carries an entry_hash, so a
# log with no hashed rows verifies as ok / rows_checked=0). Registered here for
# verification only; their writers are NOT routed through append_event this session.
_VERIFIABLE_CHAINS: dict[str, list[str]] = {
    "gold_governance_signoffs": _SIGNOFF_COLUMNS,
    "gold_ae_governance_events": _AE_EVENT_COLUMNS,
    "gold_workflow_iterations": _WORKFLOW_ITER_COLUMNS,
    "gold_assumption_approvals": _ASSUMPTION_APPROVAL_COLUMNS,
}

# Columns assigned/computed by append_event itself, not supplied in ``content``.
_CHAIN_COLUMNS = ("seq", "prev_hash", "entry_hash")


def _json_default(value):
    """Render datetimes/dates as ISO-8601 strings for canonicalisation (§G.2)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _normalize_value(value):
    """Coerce a value to the form DuckDB will store, so the hash is reproducible.

    Critical for §G.2 reproducibility: ``append_event`` hashes ``content`` and then
    INSERTs it, but a later ``verify_chain`` (Session 26) recomputes the hash from
    the values DuckDB *returns*. DuckDB ``TIMESTAMP`` is timezone-naive, so a
    tz-aware datetime is stored with its offset dropped (its wall-clock shifted by
    the local zone) — hashing the pre-store tz-aware value would then never match
    the recomputed hash. Normalising a tz-aware datetime to **naive UTC** here makes
    the stored value and the hashed value identical and environment-independent;
    naive datetimes and dates pass through unchanged. Callers must otherwise pass
    JSON-stable scalars at the column's storage resolution (str/int/float/bool/None
    and Python ``datetime``/``date``).
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _canonical_row(row: dict) -> str:
    """Canonical UTF-8 JSON of a row's business columns (§G.2).

    Keys sorted alphabetically, compact separators, dates/timestamps as ISO-8601,
    SQL ``NULL`` as JSON ``null``. ``row`` must already exclude ``prev_hash`` /
    ``entry_hash`` and include ``seq``.
    """
    return json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _entry_hash(canonical: str, prev_hash: str) -> str:
    """``sha256(canonical || prev_hash_or_empty)`` as a lowercase hex digest."""
    return hashlib.sha256((canonical + prev_hash).encode("utf-8")).hexdigest()


def append_event(
    table: str,
    content: dict,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Append one hash-chained row to a governance log; return its ``entry_hash``.

    Assigns the next ``seq`` (``MAX(seq) + 1``, first row ``= 1``), sets
    ``prev_hash`` to the prior row's ``entry_hash`` (empty string for the first
    row), computes ``entry_hash`` per the §G.2 content rule, and INSERTs via a
    static ``?``-placeholder statement on a writable connection. ``content`` is
    keyed by the table's business columns (every column except ``seq`` /
    ``prev_hash`` / ``entry_hash``); missing keys are stored as NULL.

    Single-writer assumption (prototype): ``seq`` is ``MAX(seq)+1`` read on a fresh
    connection, so two truly-concurrent writers could compute the same ``seq`` — the
    ``seq UNIQUE`` constraint then fails the second INSERT (loud, no silent chain
    corruption) rather than serialising it. Acceptable for the single-org prototype.

    Raises ``ValueError`` for an unknown (non-hash-chained) table.
    """
    if table not in _HASH_CHAINED_TABLES:
        raise ValueError(f"append_event: {table!r} is not a hash-chained governance table")

    # Normalise every value to the form DuckDB will store, so the hash computed
    # here matches a later recompute-from-stored-columns (§G.2 reproducibility).
    content = {k: _normalize_value(v) for k, v in content.items()}
    columns = _HASH_CHAINED_TABLES[table]
    placeholders = ", ".join(["?"] * len(columns))
    # ``table`` and ``columns`` come only from the trusted internal registry above
    # (never caller input); every value is bound as a ``?`` placeholder.
    insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

    con = duckdb.connect(str(db_path))
    try:
        prior = con.execute(
            f"SELECT seq, entry_hash FROM {table} ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if prior is None:
            seq = 1
            prev_hash = ""
        else:
            seq = int(prior[0]) + 1
            prev_hash = prior[1] or ""

        # Canonical business columns: everything except prev_hash/entry_hash,
        # including the freshly-assigned seq.
        canonical_cols = [c for c in columns if c not in ("prev_hash", "entry_hash")]
        canonical_payload = {
            c: (seq if c == "seq" else content.get(c)) for c in canonical_cols
        }
        canonical = _canonical_row(canonical_payload)
        entry_hash = _entry_hash(canonical, prev_hash)

        full = dict(content)
        full["seq"] = seq
        full["prev_hash"] = prev_hash
        full["entry_hash"] = entry_hash
        values = [full.get(c) for c in columns]

        con.execute(insert_sql, values)
    finally:
        con.close()
    return entry_hash


# ============================================================
# A/E governance-events writers (Session 26, FR-4-19)
# ============================================================

def record_ae_event(
    event_type: str,
    study_run_id: str,
    actor_user_id: str,
    detail: Optional[str] = None,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Append one A/E governance event to ``gold_ae_governance_events`` (FR-4-19).

    Event types: STUDY_RUN_SUBMITTED, STUDY_RUN_APPROVED, STUDY_RUN_RETURNED,
    DQ_OVERRIDE. ``event_id`` / ``event_ts`` are generated here; ``seq`` / hashes
    are assigned by ``append_event``. Returns the row's ``entry_hash``.
    """
    return append_event(
        "gold_ae_governance_events",
        {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "study_run_id": study_run_id,
            "actor_user_id": actor_user_id,
            "detail": detail,
            "event_ts": datetime.utcnow(),
        },
        db_path=db_path,
    )


def submit_study_run(
    study_run_id: str,
    actor_user_id: str,
    *,
    detail: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Record a STUDY_RUN_SUBMITTED event (captures the submitter as author).

    Convenience wrapper over :func:`record_ae_event`; the submitter identity lets
    a later ``check_segregation`` enforce proposer != approver for study runs.
    """
    return record_ae_event(
        "STUDY_RUN_SUBMITTED", study_run_id, actor_user_id, detail, db_path=db_path
    )


# ============================================================
# Tamper-evidence — chain verification (Session 26, FR-4-21 / NFR-G-04)
# ============================================================

def verify_chain(table: str, *, db_path: str = DEFAULT_DB_PATH) -> IntegrityResult:
    """Recompute a governance log's hash chain and report the first divergence.

    Reads every hashed row (``entry_hash IS NOT NULL``) in ``seq`` order and, per
    row, checks (a) linkage — its stored ``prev_hash`` equals the prior hashed
    row's stored ``entry_hash`` (empty string for the first) — and (b) integrity —
    the ``entry_hash`` recomputed from the stored business columns (per the §G.2
    rule, exactly as ``append_event`` computed it) equals the stored ``entry_hash``.
    Passes on an untouched log; fails on a tampered business column or a broken
    link, reporting the ``seq`` of the first failing row (FR-4-21). A log with no
    hashed rows (pre-Phase-4 Phase-2 rows have NULL hashes) verifies as
    ``ok=True`` / ``rows_checked=0`` (§G.5: chain begins at the first hashed row).

    Raises ``ValueError`` for a table that is not a verifiable hash-chained log.
    """
    if table not in _VERIFIABLE_CHAINS:
        raise ValueError(
            f"verify_chain: {table!r} is not a verifiable hash-chained governance table"
        )
    columns = _VERIFIABLE_CHAINS[table]
    canonical_cols = [c for c in columns if c not in ("prev_hash", "entry_hash")]
    # ``table`` / ``columns`` come only from the trusted registry above (never
    # caller input); the connection is read-only.
    select_sql = (
        f"SELECT {', '.join(columns)} FROM {table} "
        "WHERE entry_hash IS NOT NULL ORDER BY seq"
    )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(select_sql).fetchall()
    finally:
        con.close()

    expected_prev = ""  # the first hashed row must carry prev_hash == ""
    rows_checked = 0
    for r in rows:
        stored = dict(zip(columns, r))
        rows_checked += 1
        seq_val = stored.get("seq")
        seq_int = int(seq_val) if seq_val is not None else None
        stored_prev = stored.get("prev_hash") or ""
        if stored_prev != expected_prev:
            return IntegrityResult(table, False, seq_int, rows_checked)
        payload = {c: _normalize_value(stored[c]) for c in canonical_cols}
        recomputed = _entry_hash(_canonical_row(payload), stored_prev)
        if recomputed != stored["entry_hash"]:
            return IntegrityResult(table, False, seq_int, rows_checked)
        expected_prev = stored["entry_hash"]

    return IntegrityResult(table, True, None, rows_checked)


# ============================================================
# Unified audit read layer (Session 26, FR-4-22)
# ============================================================
#
# Common event shape (one dict per event across all logs):
#   ts             event timestamp (naive UTC datetime, or None)
#   actor          human-readable actor (resolved display name, raw id, or "AI")
#   actor_user_id  gold_users.user_id when known, else None
#   role           role string when known, else None
#   artifact_type  ASSUMPTION_SET | STUDY_RUN | AI_SESSION
#   artifact_id    the artifact / session identifier
#   artifact       "TYPE:id" display string
#   action         the action / decision / event_type / intent
#   detail         free-text detail (comment / reason / response excerpt)
#   source         SIGNOFF | AE_EVENT | AI | WORKFLOW | APPROVAL


def _passes_filter(event: dict, f: AuditFilter) -> bool:
    """True if ``event`` satisfies every set dimension of ``f`` (§H.7)."""
    if f.actor_user_id is not None and event["actor_user_id"] != f.actor_user_id:
        return False
    if f.role is not None:
        want = f.role.value if hasattr(f.role, "value") else f.role
        if event["role"] != want:
            return False
    if f.artifact_id is not None and str(event.get("artifact_id") or "").lower() != f.artifact_id.lower():
        return False
    if f.action is not None and event["action"] != f.action:
        return False
    if f.date_from is not None or f.date_to is not None:
        ts = event["ts"]
        if ts is None:
            return False
        d = ts.date() if hasattr(ts, "date") else ts
        if f.date_from is not None and d < f.date_from:
            return False
        if f.date_to is not None and d > f.date_to:
            return False
    return True


def unified_audit_query(
    f: Optional[AuditFilter] = None, *, db_path: str = DEFAULT_DB_PATH
) -> list[dict]:
    """Read across the three governance logs into one common event shape (FR-4-22).

    Sources: the Phase-4 sign-off log (``gold_governance_signoffs``) and the legacy
    Phase-2 workflow/approval logs (``gold_workflow_iterations`` /
    ``gold_assumption_approvals``); the A/E governance-events log
    (``gold_ae_governance_events``); and the Phase-3 AI audit log
    (``gold_ai_audit_log``, projected via its §D.3 scheme). Each source is read
    defensively (a missing/empty table degrades to no rows, never an error).
    ``AuditFilter`` dimensions are applied in Python; results are sorted by
    timestamp descending (undated rows last).
    """
    if f is None:
        f = AuditFilter()

    events: list[dict] = []
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        users: dict[str, tuple] = {}
        try:
            for uid, dname, role in con.execute(
                "SELECT user_id, display_name, role FROM gold_users"
            ).fetchall():
                users[uid] = (dname, role)
        except Exception:
            users = {}

        def actor_of(uid):
            return users[uid][0] if uid in users else uid

        # Username → (user_id, display_name, role). The legacy APPROVAL log keys on
        # username, and (post-2026-07-04 fix) the WORKFLOW log's actuary_id is the
        # authenticated username. Resolving these to the same display_name the
        # SIGNOFF/AE_EVENT rows show makes one person read as one identity across all
        # sources. Kept in its own try so a DB without a username column can't degrade
        # the user_id resolution above.
        users_by_name: dict[str, tuple] = {}
        try:
            for uid, uname, dname, role in con.execute(
                "SELECT user_id, username, display_name, role FROM gold_users"
            ).fetchall():
                users_by_name[uname] = (uid, dname, role)
        except Exception:
            users_by_name = {}

        def resolve_named(name):
            """(display, user_id, role) for a free-text actor that may be a username."""
            if name in users_by_name:
                uid, dname, role = users_by_name[name]
                return dname, uid, role
            if name in users:  # already a user_id
                return users[name][0], name, users[name][1]
            return name, None, None

        # SIGNOFF rows carry a stored actor_role; AE_EVENT rows resolve the role
        # live from gold_users (None if the actor is not a governance user, e.g. a
        # free-text DQ-override actuary id) — a display/filter nicety, not a
        # correctness concern (both are PII-free).
        def role_of(uid):
            return users[uid][1] if uid in users else None

        # 1. Phase-4 sign-off log
        try:
            for ts, uid, arole, atype, aid, decision, comment in con.execute(
                "SELECT signoff_ts, actor_user_id, actor_role, artifact_type, "
                "artifact_id, decision, comment FROM gold_governance_signoffs"
            ).fetchall():
                events.append({
                    "ts": ts, "actor": actor_of(uid), "actor_user_id": uid,
                    "role": arole, "artifact_type": atype, "artifact_id": aid,
                    "artifact": f"{atype}:{aid}", "action": decision,
                    "detail": comment, "source": "SIGNOFF",
                })
        except Exception:
            pass

        # 2. A/E governance events
        try:
            for ts, uid, etype, run_id, detail in con.execute(
                "SELECT event_ts, actor_user_id, event_type, study_run_id, detail "
                "FROM gold_ae_governance_events"
            ).fetchall():
                events.append({
                    "ts": ts, "actor": actor_of(uid), "actor_user_id": uid,
                    "role": role_of(uid), "artifact_type": "STUDY_RUN",
                    "artifact_id": run_id, "artifact": f"STUDY_RUN:{run_id}",
                    "action": etype, "detail": detail, "source": "AE_EVENT",
                })
        except Exception:
            pass

        # 3. Phase-3 AI audit log (§D.3 projection — no governance user)
        try:
            for ts, sid, src, intent, reason, model, resp in con.execute(
                "SELECT entry_ts, session_id, source, intent, intent_reason, "
                "model_string, response_text FROM gold_ai_audit_log"
            ).fetchall():
                detail = reason or (resp[:200] if resp else None)
                events.append({
                    "ts": ts, "actor": "AI" + (f" ({model})" if model else ""),
                    "actor_user_id": None, "role": None,
                    "artifact_type": "AI_SESSION", "artifact_id": sid,
                    "artifact": f"AI_SESSION:{sid}" if sid else "AI_SESSION",
                    "action": intent or src, "detail": detail, "source": "AI",
                })
        except Exception:
            pass

        # 4. Legacy Phase-2 workflow iterations (free-text actuary_id)
        try:
            for ts, actuary, aid, action, comment in con.execute(
                "SELECT iteration_ts, actuary_id, assumption_set_id, action, "
                "actuary_comment FROM gold_workflow_iterations"
            ).fetchall():
                _disp, _uid, _role = resolve_named(actuary)
                events.append({
                    "ts": ts, "actor": _disp, "actor_user_id": _uid, "role": _role,
                    "artifact_type": "ASSUMPTION_SET", "artifact_id": aid,
                    "artifact": f"ASSUMPTION_SET:{aid}", "action": action,
                    "detail": comment, "source": "WORKFLOW",
                })
        except Exception:
            pass

        # 5. Legacy Phase-2 assumption approvals (free-text reviewer_id)
        try:
            for ts, reviewer, aid, decision, comment in con.execute(
                "SELECT COALESCE(approved_ts, proposed_ts), reviewer_id, "
                "assumption_set_id, reviewer_decision, reviewer_comment "
                "FROM gold_assumption_approvals"
            ).fetchall():
                _disp, _uid, _role = resolve_named(reviewer)
                events.append({
                    "ts": ts, "actor": _disp, "actor_user_id": _uid, "role": _role,
                    "artifact_type": "ASSUMPTION_SET", "artifact_id": aid,
                    "artifact": f"ASSUMPTION_SET:{aid}", "action": decision,
                    "detail": comment, "source": "APPROVAL",
                })
        except Exception:
            pass
    finally:
        con.close()

    events = [e for e in events if _passes_filter(e, f)]
    # Descending by timestamp; undated rows sort last.
    events.sort(key=lambda e: (e["ts"] is not None, e["ts"]), reverse=True)
    return events


def artifact_timeline(
    artifact_type, artifact_id: str, *, db_path: str = DEFAULT_DB_PATH
) -> list[dict]:
    """Chronological (ascending) governance history for one artifact (FR-4-22).

    ``artifact_type`` may be an ``ArtifactType`` enum or its string value. Reuses
    the unified projection filtered to the artifact, so an ASSUMPTION_SET yields
    its sign-offs + legacy workflow/approval rows and a STUDY_RUN yields its A/E
    events + study-run sign-offs.
    """
    at = artifact_type.value if hasattr(artifact_type, "value") else str(artifact_type)
    rows = unified_audit_query(AuditFilter(artifact_id=artifact_id), db_path=db_path)
    rows = [r for r in rows if r["artifact_type"] == at]
    rows.sort(key=lambda r: (r["ts"] is None, r["ts"]))  # ascending; undated last
    return rows
