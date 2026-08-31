"""The shipped demo DB must be able to demonstrate what the walkthrough promises.

OBS-3 — ``gold_ai_audit_log`` shipped empty, so the beat that says "show the AI
Activity Log" landed on an empty table, and without an API key nothing would
populate it during the demo either.

OBS-4 — the only assumption set was already APPROVED, so Step 3 rendered
"already APPROVED, no further action required": the sign-off chain, the
attestation and the RETURN path were all unreachable.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

DB = Path("data/experience_study.duckdb")
_needs_db = pytest.mark.skipif(not DB.exists(), reason="live demo DB not present")


def _q(sql: str, params: list | None = None):
    con = duckdb.connect(str(DB), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# OBS-3 — the AI Activity Log ships with content
# ---------------------------------------------------------------------------

@_needs_db
def test_ai_activity_log_is_not_empty():
    n = _q("SELECT COUNT(*) FROM gold_ai_audit_log")[0][0]
    assert n > 0, (
        "the AI Activity Log ships empty, so the walkthrough beat has nothing to show"
    )


@_needs_db
def test_seeded_turns_cover_answer_refusal_and_block():
    """A log showing only happy turns would undersell the guardrails."""
    rows = _q(
        "SELECT intent, blocked, block_reason FROM gold_ai_audit_log"
    )
    intents = {r[0] for r in rows}
    assert "OUT_OF_SCOPE" in intents, "no refusal recorded"
    assert any(r[1] for r in rows), "no blocked turn recorded"
    assert any(r[2] == "numeric_traceability" for r in rows), (
        "the numeric guardrail should be visible in the log, not just described"
    )


@_needs_db
def test_seeded_turns_declare_their_provider_honestly():
    """Offline-seeded rows must say so — an audit log that misstated how a turn
    was produced would be worse than an empty one."""
    providers = {r[0] for r in _q("SELECT DISTINCT provider FROM gold_ai_audit_log")}
    assert providers, "no provider recorded"
    assert all(p for p in providers), "a turn recorded no provider at all"


def test_run_log_page_surfaces_the_provider():
    src = Path("ui/views/07_run_log.py").read_text(encoding="utf-8")
    assert "provider, model_string" in src, (
        "the page must show which provider produced each turn, so an offline-seeded "
        "row cannot read as a live model call"
    )


# ---------------------------------------------------------------------------
# OBS-4 — Step 3 has something to act on
# ---------------------------------------------------------------------------

@_needs_db
def test_a_pending_assumption_set_awaits_signoff():
    rows = _q(
        "SELECT status, COUNT(*) FROM gold_assumption_sets GROUP BY 1"
    )
    by_status = dict(rows)
    assert by_status.get("APPROVED"), "the completed example workflow is missing"
    assert by_status.get("STAGE3_APPROVED"), (
        "no set awaiting sign-off — Step 3's chain, attestation and RETURN path "
        "cannot be demonstrated"
    )


@_needs_db
def test_the_pending_set_is_genuinely_unsigned():
    pending = _q(
        "SELECT assumption_set_id FROM gold_assumption_sets WHERE status='STAGE3_APPROVED'"
    )
    assert pending
    for (set_id,) in pending:
        n = _q(
            "SELECT COUNT(*) FROM gold_governance_signoffs WHERE artifact_id = ?",
            [set_id],
        )[0][0]
        assert n == 0, "the pending set already carries sign-offs"
