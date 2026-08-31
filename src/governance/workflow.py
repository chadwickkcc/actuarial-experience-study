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
completing assumption-set APPROVE the artifact is locked. Org-specific values
(chain, materiality threshold,
``final_level_below_threshold``, attestation text, segregation policy) come from
``config/governance_config.yaml`` (FR-4-27).

Chain state is evaluated per **round**: the sign-off rows since the last RETURN
(a RETURN resets the artifact to its editable state and starts a fresh round).
``required_final_level`` is fixed at the first sign-off of a round (from the
materiality metric — the max absolute multiplier change vs the prior approved
version) and reused thereafter, so the materiality decision is stable across
the chain.
"""

from __future__ import annotations

import logging

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.governance import rbac
from src.governance.audit import append_event, record_ae_event
from src.governance.lineage import (
    compare_versions,
    create_version,
    supersede_other_approved,
)
from src.governance.rbac import Action, PermissionDenied
from src.governance.users import DEFAULT_CONFIG_PATH
from src.assumptions.workflow import transition_assumption_set_status
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

logger = logging.getLogger(__name__)


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


def required_final_level(materiality_value: Optional[float], cfg: dict) -> int:
    """The minimum required *final* sign-off level (FR-4-16).

    A study run (``materiality_value is None``) always runs the **full** chain
    (FR-4-14) — as does an assumption set with no prior approved version to
    compare against. For an assumption set, a materiality metric (the max
    absolute multiplier change vs the prior approved version, see
    ``materiality_vs_prior_approved``) above
    ``materiality.max_multiplier_delta_threshold`` requires the
    ``chief_actuary`` level; at/below it the chain may complete at
    ``materiality.final_level_below_threshold``.
    """
    chain = load_chain(cfg)
    if not chain:
        raise ValueError("No approval_chain configured.")
    last_level = chain[-1].level  # the final level (robust to non-contiguous numbering)
    if materiality_value is None:
        return last_level
    # NaN compares False against everything, so `abs(nan) > threshold` used to read
    # as "immaterial" and let a senior sign alone. An unknown materiality is not a
    # small one — fail to the full chain (adversarial review m-9).
    if materiality_value != materiality_value:  # NaN
        return last_level
    mat = cfg.get("materiality") or {}
    threshold = float(mat.get("max_multiplier_delta_threshold", 0.05))
    if abs(materiality_value) > threshold:
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
    materiality_value: Optional[float],
    cfg: dict,
    db_path: str,
) -> int:
    """Fix the required final level at the round's first sign-off; reuse thereafter."""
    round_rows = _round_signoffs(artifact_type, artifact_id, db_path)
    if round_rows and round_rows[-1].get("required_final_level") is not None:
        return int(round_rows[-1]["required_final_level"])
    return required_final_level(materiality_value, cfg)


_APPROVED_STATUSES = (
    AssumptionSetStatus.APPROVED.value,
    AssumptionSetStatus.SUPERSEDED.value,
)


def _approved_ancestors(assumption_set_id: str, db_path: str) -> list[str]:
    """Ancestors of a set that reached APPROVED, nearest first (excludes the set)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        current: Optional[str] = assumption_set_id
        approved: list[str] = []
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            row = con.execute(
                "SELECT parent_set_id, status FROM gold_assumption_sets "
                "WHERE assumption_set_id = ?",
                [current],
            ).fetchone()
            if row is None:
                break
            if current != assumption_set_id and row[1] in _APPROVED_STATUSES:
                approved.append(current)
            current = row[0]
    finally:
        con.close()
    return approved


def _fully_reviewed_baseline(
    candidates: list[str], final_level: int, db_path: str
) -> Optional[str]:
    """The nearest ancestor whose chain reached the FINAL level with an APPROVE.

    That set is the last one a chief actuary comprehensively reviewed, so it is the
    baseline cumulative drift is measured against. ``None`` when no ancestor was
    ever signed at the final level (then the caller falls back to the oldest
    approved ancestor — the original approved basis of the lineage).
    """
    if not candidates:
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for set_id in candidates:
            hit = con.execute(
                f"SELECT 1 FROM {_SIGNOFF_TABLE} "
                "WHERE artifact_type = ? AND artifact_id = ? "
                "AND decision = ? AND chain_level >= ? LIMIT 1",
                [
                    ArtifactType.ASSUMPTION_SET.value,
                    set_id,
                    Decision.APPROVE.value,
                    final_level,
                ],
            ).fetchone()
            if hit is not None:
                return set_id
    finally:
        con.close()
    return None


def materiality_vs_prior_approved(
    assumption_set_id: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> Optional[float]:
    """Materiality metric for a set: max |Δ multiplier| vs its approved baselines.

    Measured against **two** baselines and reported as the larger of the two:

    * the nearest approved ancestor — the size of *this* step; and
    * the last ancestor comprehensively reviewed (signed at the chain's final
      level), or failing that the oldest approved ancestor — the *cumulative*
      drift since the last full review.

    The second baseline exists because a purely step-wise metric is evadable by
    splitting: ten successive 0.04 moves each pass a 0.05 threshold at senior
    level while shifting the assumption by 0.40 with no chief-actuary review
    (adversarial review M-11). Returns ``None`` when the set has no approved
    ancestor (a lineage root or an all-draft chain) — the chain then runs in
    full (FR-4-16 conservative default).
    """
    approved = _approved_ancestors(assumption_set_id, db_path)
    if not approved:
        return None
    chain = load_chain(_load_config(config_path))
    final_level = chain[-1].level if chain else 0
    baseline = _fully_reviewed_baseline(approved, final_level, db_path) or approved[-1]

    values: list[float] = []
    for basis in {approved[0], baseline}:
        value = compare_versions(basis, assumption_set_id, db_path=db_path).materiality_value
        if value is not None and value == value:  # skip None / NaN
            values.append(abs(value))
    if not values:
        return None
    return max(values)


# ---------------------------------------------------------------------------
# Segregation of duties (FR-4-05)
# ---------------------------------------------------------------------------

def _artifact_author(artifact_type: ArtifactType, artifact_id: str, db_path: str) -> Optional[str]:
    """The recorded author/proposer of an artifact (None when not tracked yet).

    For an assumption set this is ``gold_assumption_sets.author_id`` (set to the
    session username under FR-4-03). For a study run it is the submitter captured by
    the ``gold_ae_governance_events`` STUDY_RUN_SUBMITTED event (the earliest such
    event's ``actor_user_id``); this is what lets ``check_segregation`` enforce
    proposer ≠ approver for study runs (FR-4-05). A run that has not been submitted
    has no author; ``record_signoff`` refuses to sign such a run outright
    (``_require_submitted``) rather than proceeding with the proposer≠approver check
    silently disabled (adversarial review m-8).
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


def _require_submitted(artifact_type: ArtifactType, artifact_id: str, db_path: str) -> None:
    """Refuse to sign a study run that was never submitted for approval (FR-4-14).

    Study-run fitness is derived from sign-off rows alone, and the submitter is the
    only recorded author, so an unsubmitted run could be signed straight to "fit for
    assumption-setting" with nobody having proposed it and proposer ≠ approver
    silently inapplicable (adversarial review m-8). Assumption sets are unaffected —
    their author is recorded at creation.
    """
    if artifact_type != ArtifactType.STUDY_RUN:
        return
    if _artifact_author(artifact_type, artifact_id, db_path) is None:
        raise ValueError(
            f"Study run '{artifact_id}' has not been submitted for approval; "
            f"submit it first so a proposer is on record (FR-4-14 / FR-4-05)."
        )


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

    **Known limitation (accepted, adversarial review m-7).** Segregation keys on the
    *account* (``user_id``/``username``), not on a person: the system has no person
    entity distinct from the login. One human holding two accounts could therefore
    propose under one and approve under the other. Exploitability is low — there is
    no self-service account creation (FR-4-01), so an administrator would have to
    deliberately issue one person two logins — and closing it properly needs an
    identity model this prototype does not have. Recorded rather than half-built,
    since a partial person model would imply a guarantee it could not enforce.
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
    except Exception:  # noqa: BLE001 - never let an audit failure break a sign-off
        # Still a non-fatal path (the sign-off itself must commit), but a lost
        # governance event is not nothing: log it loudly so the gap is visible
        # rather than silent (adversarial review m-9).
        logger.exception(
            "governance audit event could not be written; the primary action "
            "committed but the trail is incomplete"
        )


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
    materiality_value: Optional[float] = None,
) -> SignoffRecord:
    """Record one chain-level sign-off; return the ``SignoffRecord`` (FR-4-13/15).

    Validates the SIGN_OFF permission (server-side, FR-4-04), the role-for-level
    and chain order (``may_sign_off_at`` against the next required level — out-of-order
    or wrong-role attempts raise ``PermissionDenied``), and segregation
    (``check_segregation``). The comment is mandatory. The row is written
    hash-chained via ``audit.append_event``. On a completing assumption-set APPROVE
    (the signed level equals the round's ``required_final_level``) the set is locked
    (status APPROVED); a RETURN resets an assumption set to PROPOSED (editable). A
    study run's "fit for assumption-setting" state is derived from its sign-off rows
    (``is_study_run_fit``), so nothing is mutated in a table for it.

    A **study run must have been submitted** (``audit.submit_study_run``) before any
    level may sign it, so a proposer is always on record and proposer ≠ approver
    actually applies to it (m-8).

    ``materiality_value`` (FR-4-16) is the max absolute multiplier change vs the
    approved baselines. When not supplied for an assumption set it is computed
    automatically via ``materiality_vs_prior_approved`` (which measures both the
    step and the cumulative drift since the last full review); a set with no
    approved ancestor gets ``None`` → the full chain.
    """
    rbac.require(user, Action.SIGN_OFF, config_path=config_path)
    if not comment or not comment.strip():
        raise ValueError("A sign-off comment is mandatory (FR-4-13/15).")
    _require_submitted(artifact_type, artifact_id, db_path)

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

    if materiality_value is None and artifact_type == ArtifactType.ASSUMPTION_SET:
        materiality_value = materiality_vs_prior_approved(artifact_id, db_path=db_path)
    rfl = _effective_final_level(artifact_type, artifact_id, materiality_value, cfg, db_path)
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
            "materiality_value": materiality_value,
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
            # A completing chain makes this the current approved set, so any earlier
            # one in the lineage is superseded here rather than only on the publish
            # path — "at most one APPROVED-current per lineage" is an invariant
            # (FR-4-08 / NFR-G-05; adversarial review OBS-7).
            supersede_other_approved(artifact_id, db_path=db_path)
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
