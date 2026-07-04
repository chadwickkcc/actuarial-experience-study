"""Phase-4 configurable approval-chain engine (Session 25).

Implements the Technical Spec v3.0 §H.6 contract, realising FR-4-05/12/13/14/16/17/18
and NFR-G-03/G-08: it generalises the Phase-2 single-reviewer Stage-4 sign-off
(FR-2-42/43) into a configurable multi-level chain, extends formal approval to A/E
study runs, and adds attestation, a materiality-driven required level, a
pending-approvals queue, and governed re-open. The Phase-2 four-stage workflow
shell (RS §6.9 / FR-2-34) is retained; configuring the chain to a single
``chief_actuary`` level reproduces the legacy single-reviewer behaviour (NFR-G-08).

Governance is ordinary application code outside ``src/ai/``: RBAC is enforced
server-side (``rbac.require`` / ``rbac.may_sign_off_at``); each chain-level sign-off
is written as a hash-chained row to ``gold_governance_signoffs`` via the §H.7
``audit.append_event`` write path (never a hand-written INSERT here); on a
completing assumption-set APPROVE the artifact is locked and the legacy Phase-2
``gold_assumption_approvals`` summary is still written (§G.2 note) so Phase-2
reporting keeps working. Org-specific values (chain, materiality threshold,
``final_level_below_threshold``, attestation text, segregation policy) come from
``config/governance_config.yaml`` (FR-4-27).

Chain state is evaluated per **round**: the sign-off rows since the last RETURN
(a RETURN resets the artifact to its editable state and starts a fresh round).
``required_final_level`` is fixed at the first sign-off of a round (from the
caller-supplied ΔTEV fraction) and reused thereafter, so the materiality decision
is stable across the chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.governance import rbac
from src.governance.audit import append_event, record_ae_event
from src.governance.lineage import create_version
from src.governance.rbac import Action, PermissionDenied
from src.governance.users import DEFAULT_CONFIG_PATH
from src.tev.workflow import (
    get_workflow_iterations,
    record_governance_approval,
    transition_assumption_set_status,
)
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import (
    ArtifactType,
    AssumptionSetStatus,
    ChainLevel,
    Decision,
    Role,
    SignoffRecord,
    User,
)

_SIGNOFF_TABLE = "gold_governance_signoffs"


class SegregationViolation(Exception):
    """Raised when a sign-off would breach segregation of duties (FR-4-05)."""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> dict:
    """Parse a governance config file into a dict (empty if absent)."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_chain(cfg: dict) -> list[ChainLevel]:
    """Ordered sign-off chain from a parsed config's ``approval_chain`` (FR-4-12)."""
    levels = [
        ChainLevel(level=int(item["level"]), required_role=Role(item["required_role"]))
        for item in (cfg.get("approval_chain") or [])
    ]
    levels.sort(key=lambda lvl: lvl.level)
    return levels


def _level_of_role_or(chain: list[ChainLevel], role_value: str, default: int) -> int:
    """The chain level whose required role is ``role_value``; ``default`` if absent.

    Falling back to ``default`` (the last/final level) keeps the materiality rule
    robust for a chain that does not contain the configured role — e.g. a single
    ``chief_actuary`` chain has no ``senior_actuary`` level, so a below-threshold
    change correctly requires the final (chief) level rather than raising.
    """
    for lvl in chain:
        if lvl.required_role.value == role_value:
            return lvl.level
    return default


def required_final_level(delta_tev: Optional[float], cfg: dict) -> int:
    """The minimum required *final* sign-off level (FR-4-16).

    A study run (``delta_tev is None``) always runs the **full** chain (FR-4-14).
    For an assumption set, ``|ΔTEV|`` above ``materiality.delta_tev_threshold``
    requires the ``chief_actuary`` level; at/below it the chain may complete at
    ``materiality.final_level_below_threshold``. ``delta_tev`` is the ΔTEV fraction
    vs the prior approved set, computed by the caller.
    """
    chain = load_chain(cfg)
    if not chain:
        raise ValueError("No approval_chain configured.")
    last_level = chain[-1].level  # the final level (robust to non-contiguous numbering)
    if delta_tev is None:
        return last_level
    mat = cfg.get("materiality") or {}
    threshold = float(mat.get("delta_tev_threshold", 0.01))
    if abs(delta_tev) > threshold:
        return _level_of_role_or(chain, Role.CHIEF_ACTUARY.value, last_level)
    below = mat.get("final_level_below_threshold", Role.SENIOR_ACTUARY.value)
    return _level_of_role_or(chain, str(below), last_level)


# ---------------------------------------------------------------------------
# Sign-off state (per-round)
# ---------------------------------------------------------------------------

def _round_signoffs(artifact_type: ArtifactType, artifact_id: str, db_path: str) -> list[dict]:
    """Sign-off rows for the artifact in the **current round** (after the last RETURN)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT chain_level, decision, actor_user_id, required_final_level, seq "
            f"FROM {_SIGNOFF_TABLE} "
            "WHERE artifact_type = ? AND artifact_id = ? ORDER BY seq",
            [artifact_type.value, artifact_id],
        ).fetchall()
    finally:
        con.close()
    cols = ["chain_level", "decision", "actor_user_id", "required_final_level", "seq"]
    parsed = [dict(zip(cols, r)) for r in rows]
    last_return = -1
    for i, r in enumerate(parsed):
        if r["decision"] == Decision.RETURN.value:
            last_return = i
    return parsed[last_return + 1:]


def next_required_level(
    artifact_type: ArtifactType,
    artifact_id: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> Optional[ChainLevel]:
    """The next unsigned chain level in order; ``None`` when the chain is complete (FR-4-13)."""
    cfg = _load_config(config_path)
    chain = load_chain(cfg)
    if not chain:
        return None
    round_rows = _round_signoffs(artifact_type, artifact_id, db_path)
    if round_rows and round_rows[-1].get("required_final_level") is not None:
        rfl = int(round_rows[-1]["required_final_level"])
    else:
        rfl = chain[-1].level  # full chain until the round fixes its required final level
    approved = {r["chain_level"] for r in round_rows if r["decision"] == Decision.APPROVE.value}
    for level in chain:
        if level.level > rfl:
            break
        if level.level not in approved:
            return level
    return None


def _effective_final_level(
    artifact_type: ArtifactType,
    artifact_id: str,
    delta_tev: Optional[float],
    cfg: dict,
    db_path: str,
) -> int:
    """Fix the required final level at the round's first sign-off; reuse thereafter."""
    round_rows = _round_signoffs(artifact_type, artifact_id, db_path)
    if round_rows and round_rows[-1].get("required_final_level") is not None:
        return int(round_rows[-1]["required_final_level"])
    return required_final_level(delta_tev, cfg)


# ---------------------------------------------------------------------------
# Segregation of duties (FR-4-05)
# ---------------------------------------------------------------------------

def _artifact_author(artifact_type: ArtifactType, artifact_id: str, db_path: str) -> Optional[str]:
    """The recorded author/proposer of an artifact (None when not tracked yet).

    For an assumption set this is ``gold_assumption_sets.author_id`` (set to the
    session username under FR-4-03). For a study run it is the submitter captured by
    the ``gold_ae_governance_events`` STUDY_RUN_SUBMITTED event (the earliest such
    event's ``actor_user_id``); this is what lets ``check_segregation`` enforce
    proposer ≠ approver for study runs (FR-4-05). When a run has not been submitted
    (no such event) the author is unknown and the proposer≠approver check is a no-op
    for it (the distinct-signer-per-level rule still applies).
    """
    if artifact_type == ArtifactType.ASSUMPTION_SET:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            row = con.execute(
                "SELECT author_id FROM gold_assumption_sets WHERE assumption_set_id = ?",
                [artifact_id],
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    if artifact_type == ArtifactType.STUDY_RUN:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            row = con.execute(
                "SELECT actor_user_id FROM gold_ae_governance_events "
                "WHERE event_type = 'STUDY_RUN_SUBMITTED' AND study_run_id = ? "
                "ORDER BY seq LIMIT 1",
                [artifact_id],
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    return None


def check_segregation(
    user: User,
    artifact_type: ArtifactType,
    artifact_id: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> None:
    """Raise ``SegregationViolation`` if this sign-off breaches duty segregation (FR-4-05).

    proposer ≠ approver is absolute (a user may never sign off on an artifact they
    authored, at any level). Additionally, unless ``segregation.allow_multi_level_signoff``
    is true, a user who already approved a level in the current round may not sign
    another.
    """
    author = _artifact_author(artifact_type, artifact_id, db_path)
    if author is not None and author in (user.username, user.user_id):
        raise SegregationViolation(
            f"User '{user.username}' authored this artifact and may not sign off on it "
            f"(proposer ≠ approver, FR-4-05)."
        )
    cfg = _load_config(config_path)
    allow_multi = bool((cfg.get("segregation") or {}).get("allow_multi_level_signoff", False))
    if allow_multi:
        return
    round_rows = _round_signoffs(artifact_type, artifact_id, db_path)
    prior_approvers = {
        r["actor_user_id"] for r in round_rows if r["decision"] == Decision.APPROVE.value
    }
    if user.user_id in prior_approvers:
        raise SegregationViolation(
            f"User '{user.username}' has already signed a level in this chain "
            f"(distinct-signer rule; FR-4-05)."
        )


# ---------------------------------------------------------------------------
# record_signoff (FR-4-13/15/16)
# ---------------------------------------------------------------------------

def _emit_study_run_event(
    event_type: str, run_id: str, user: User, level: int, comment: str, db_path: str
) -> None:
    """Best-effort A/E governance event for a study-run sign-off (FR-4-19).

    The sign-off itself is already durably written (above) before this is called;
    recording the milestone in ``gold_ae_governance_events`` is a secondary audit
    action, so any failure here is swallowed — it must never fail or roll back the
    completed sign-off.
    """
    try:
        detail = f"level {level}: {comment.strip()}" if comment else f"level {level}"
        record_ae_event(event_type, run_id, user.user_id, detail, db_path=db_path)
    except Exception:  # pragma: no cover - defensive; audit event is non-critical
        pass


def record_signoff(
    user: User,
    artifact_type: ArtifactType,
    artifact_id: str,
    artifact_version: Optional[int],
    decision: Decision,
    comment: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    delta_tev: Optional[float] = None,
    legacy_context: Optional[dict] = None,
) -> SignoffRecord:
    """Record one chain-level sign-off; return the ``SignoffRecord`` (FR-4-13/15).

    Validates the SIGN_OFF permission (server-side, FR-4-04), the role-for-level
    and chain order (``may_sign_off_at`` against the next required level — out-of-order
    or wrong-role attempts raise ``PermissionDenied``), and segregation
    (``check_segregation``). The comment is mandatory. The row is written
    hash-chained via ``audit.append_event``. On a completing assumption-set APPROVE
    (the signed level equals the round's ``required_final_level``) the set is locked
    (status APPROVED) and the legacy ``gold_assumption_approvals`` summary is written;
    a RETURN resets an assumption set to PROPOSED (editable). A study run's
    "fit for assumption-setting" state is derived from its sign-off rows
    (``is_study_run_fit``), so nothing is mutated in a table for it.
    """
    rbac.require(user, Action.SIGN_OFF, config_path=config_path)
    if not comment or not comment.strip():
        raise ValueError("A sign-off comment is mandatory (FR-4-13/15).")

    cfg = _load_config(config_path)
    if not load_chain(cfg):
        raise ValueError("No approval_chain is configured in the governance config.")
    level = next_required_level(
        artifact_type, artifact_id, db_path=db_path, config_path=config_path
    )
    if level is None:
        raise ValueError("The sign-off chain is already complete for this artifact.")
    if not rbac.may_sign_off_at(user, level):
        raise PermissionDenied(
            f"User '{user.username}' (role {user.role.value}) may not sign the current "
            f"required level {level.level} (requires {level.required_role.value})."
        )
    check_segregation(user, artifact_type, artifact_id, db_path=db_path, config_path=config_path)

    rfl = _effective_final_level(artifact_type, artifact_id, delta_tev, cfg, db_path)
    attestation = str(cfg.get("attestation_text") or "")
    signoff_id = str(uuid.uuid4())
    signoff_ts = datetime.utcnow()

    append_event(
        _SIGNOFF_TABLE,
        {
            "signoff_id": signoff_id,
            "artifact_type": artifact_type.value,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "chain_level": level.level,
            "required_role": level.required_role.value,
            "actor_user_id": user.user_id,
            "actor_role": user.role.value,
            "decision": decision.value,
            "comment": comment.strip(),
            "attestation_text": attestation,
            "delta_tev": delta_tev,
            "required_final_level": rfl,
            "signoff_ts": signoff_ts,
        },
        db_path=db_path,
    )

    if decision == Decision.RETURN:
        if artifact_type == ArtifactType.ASSUMPTION_SET:
            transition_assumption_set_status(Path(db_path), artifact_id, "PROPOSED")
        elif artifact_type == ArtifactType.STUDY_RUN:
            _emit_study_run_event(
                "STUDY_RUN_RETURNED", artifact_id, user, level.level, comment, db_path
            )
    elif decision == Decision.APPROVE and level.level == rfl:
        if artifact_type == ArtifactType.ASSUMPTION_SET:
            transition_assumption_set_status(
                Path(db_path), artifact_id, "APPROVED", approved_by=user.username
            )
            _write_legacy_summary(
                user, artifact_id, comment.strip(),
                db_path=db_path, legacy_context=legacy_context,
            )
        elif artifact_type == ArtifactType.STUDY_RUN:
            # Study run: "fit" is derived from sign-off rows; nothing to lock. Record
            # the lifecycle milestone in the A/E governance-events log (FR-4-19).
            _emit_study_run_event(
                "STUDY_RUN_APPROVED", artifact_id, user, level.level, comment, db_path
            )

    return SignoffRecord(
        signoff_id=signoff_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        chain_level=level.level,
        actor=user,
        decision=decision,
        comment=comment.strip(),
        attestation_text=attestation,
        signoff_ts=signoff_ts,
    )


def _write_legacy_summary(
    user: User,
    assumption_set_id: str,
    comment: str,
    *,
    db_path: str,
    legacy_context: Optional[dict],
) -> None:
    """Write the Phase-2 ``gold_assumption_approvals`` summary on a completing APPROVE.

    Reuses ``src.tev.workflow.record_governance_approval`` so Phase-2 reporting/UI
    keep working (§G.2 note). Fields not supplied in ``legacy_context`` are read from
    the DB (source run, author/proposer, latest baseline TEV run, workflow session)
    and otherwise defaulted, so the engine-level path works without full UI context.
    """
    ctx = legacy_context or {}
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        meta = con.execute(
            "SELECT source_study_run_id, author_id FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [assumption_set_id],
        ).fetchone()
        tev = con.execute(
            "SELECT tev_run_id, total_tev FROM gold_tev_run_log "
            "WHERE assumption_set_id = ? AND sensitivity_id IS NULL "
            "ORDER BY run_ts DESC LIMIT 1",
            [assumption_set_id],
        ).fetchone()
        wf = con.execute(
            "SELECT workflow_session_id FROM gold_workflow_iterations "
            "WHERE assumption_set_id = ? ORDER BY iteration_ts DESC LIMIT 1",
            [assumption_set_id],
        ).fetchone()
    finally:
        con.close()

    source_run = ctx.get("source_study_run_id") or (meta[0] if meta else "") or ""
    proposer_id = ctx.get("proposer_id") or (meta[1] if meta else "") or ""
    workflow_session_id = ctx.get("workflow_session_id") or (wf[0] if wf else None) or str(uuid.uuid4())
    tev_run_id = ctx.get("tev_baseline_run_id") or (tev[0] if tev else "") or ""

    baseline_tev = ctx.get("baseline_tev")
    if baseline_tev is None:
        baseline_tev = float(tev[1]) if tev and tev[1] is not None else 0.0

    iteration_history = ctx.get("iteration_history")
    total_iterations = ctx.get("total_iterations")
    if iteration_history is None:
        hist = get_workflow_iterations(Path(db_path), workflow_session_id)
        iteration_history = [{k: str(v) for k, v in h.items()} for h in hist]
        if total_iterations is None:
            total_iterations = len([h for h in hist if h.get("stage") in (2, 3)])

    record_governance_approval(
        db_path=Path(db_path),
        assumption_set_id=assumption_set_id,
        workflow_session_id=workflow_session_id,
        source_study_run_id=source_run,
        tev_baseline_run_id=tev_run_id,
        proposer_id=proposer_id,
        reviewer_id=user.username,
        reviewer_decision="APPROVE",
        reviewer_comment=comment,
        total_iterations=int(total_iterations or 0),
        envelope_run_flag=bool(ctx.get("envelope_run_flag", False)),
        baseline_tev=float(baseline_tev),
        delta_tev_vs_prior=ctx.get("delta_tev_vs_prior"),
        max_sensitivity_delta=ctx.get("max_sensitivity_delta"),
        iteration_history=iteration_history,
        envelope_tev_min=ctx.get("envelope_tev_min"),
        envelope_tev_max=ctx.get("envelope_tev_max"),
        proposed_envelope_percentile=ctx.get("proposed_envelope_percentile"),
    )


# ---------------------------------------------------------------------------
# Governed re-open (FR-4-18) + pending queue (FR-4-17) + derived state
# ---------------------------------------------------------------------------

def reopen(
    assumption_set_id: str,
    user: User,
    justification: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Re-open an APPROVED set by creating a new DRAFT child version (FR-4-18).

    Never mutates the original (immutable): the set must be APPROVED, a mandatory
    ``justification`` is required, ``lineage.create_version`` clones it into a new
    DRAFT child with the parent link, and the justification is recorded durably on
    the child's ``gold_assumption_sets.description`` (queryable now; Session 26 also
    logs a governance event). Adoption of the new version follows the normal chain
    (RBAC-gated). Returns the new id.
    """
    if not justification or not justification.strip():
        raise ValueError("A justification is mandatory to re-open an approved set (FR-4-18).")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT status, source_study_run_id FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [assumption_set_id],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"Assumption set {assumption_set_id} not found.")
    status, source_run = row
    if status != AssumptionSetStatus.APPROVED.value:
        raise ValueError(
            f"Only an APPROVED set can be re-opened; {assumption_set_id} is {status} (FR-4-18)."
        )

    new_id = create_version(
        parent_set_id=assumption_set_id,
        source_study_run_id=source_run,
        author=user,
        db_path=db_path,
    )

    # Record the re-open justification durably on the child (loud on failure — a
    # governance/ASOP-41 control must not silently drop its recorded rationale).
    note = f"Re-opened from {assumption_set_id} by {user.username}: {justification.strip()}"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "UPDATE gold_assumption_sets SET description = ? WHERE assumption_set_id = ?",
            [note, new_id],
        )
    finally:
        con.close()

    return new_id


def is_study_run_fit(
    run_id: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> bool:
    """True iff a study run's sign-off chain is complete with all-APPROVE (FR-4-14).

    "Fit for assumption-setting" is derived from the sign-off rows: the chain has
    no remaining required level and at least one sign-off was recorded. A run with
    no sign-offs, or with an unsigned required level, is "not yet fit".
    """
    round_rows = _round_signoffs(ArtifactType.STUDY_RUN, run_id, db_path)
    if not round_rows:
        return False
    nxt = next_required_level(
        ArtifactType.STUDY_RUN, run_id, db_path=db_path, config_path=config_path
    )
    return nxt is None


def pending_approvals(
    user: User,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> list[dict]:
    """Artifacts awaiting sign-off at the level the user's role occupies (FR-4-17).

    Lists assumption sets that have entered the chain and any in-progress study-run
    chain whose next required level matches the user's role. "In the chain" means a
    set is PROPOSED or STAGE3_APPROVED (the Phase-2 four-stage shell submits a set to
    the chain at STAGE3_APPROVED; a pure-FR-4 flow uses PROPOSED) — APPROVED /
    SUPERSEDED / DRAFT sets are excluded. No time-based escalation / notifications
    (out of scope, §8.1).
    """
    pending: list[dict] = []

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        aset_rows = con.execute(
            "SELECT assumption_set_id, version FROM gold_assumption_sets "
            "WHERE status IN ('PROPOSED', 'STAGE3_APPROVED')"
        ).fetchall()
        run_rows = con.execute(
            f"SELECT DISTINCT artifact_id FROM {_SIGNOFF_TABLE} "
            "WHERE artifact_type = 'STUDY_RUN'"
        ).fetchall()
    finally:
        con.close()

    for set_id, version in aset_rows:
        nxt = next_required_level(
            ArtifactType.ASSUMPTION_SET, set_id, db_path=db_path, config_path=config_path
        )
        if nxt is not None and nxt.required_role == user.role:
            pending.append({
                "artifact_type": ArtifactType.ASSUMPTION_SET.value,
                "artifact_id": set_id,
                "artifact_version": version,
                "next_level": nxt.level,
                "required_role": nxt.required_role.value,
            })

    for (run_id,) in run_rows:
        nxt = next_required_level(
            ArtifactType.STUDY_RUN, run_id, db_path=db_path, config_path=config_path
        )
        if nxt is not None and nxt.required_role == user.role:
            pending.append({
                "artifact_type": ArtifactType.STUDY_RUN.value,
                "artifact_id": run_id,
                "artifact_version": None,
                "next_level": nxt.level,
                "required_role": nxt.required_role.value,
            })

    return pending
