"""Phase-4 governance lifecycle UI — helper unit tests + AppTest render/role smokes.

Covers the two new pages (Study Run Sign-Off, Versioning & Lineage) and their pure
helper module. Render smokes patch src.governance.auth.current_user (the pages start
with no session identity) and assert no runtime error plus the RBAC gating of the
write affordances. Render-only — no button is clicked, so the live DB is never
mutated. Skipped when the local DuckDB is absent.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from ui.config import DB_PATH
from src.utils.types import Role, User

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="local experience_study.duckdb not present"
)


def _user(role: Role) -> User:
    return User(
        user_id=f"u-{role.value}", username=f"x.{role.value}",
        display_name=role.value.replace("_", " ").title(), role=role, active=True,
    )


# --------------------------------------------------------------------------
# ui/governance_logic.py — pure helpers
# --------------------------------------------------------------------------
def test_list_complete_study_runs_shape():
    from ui.governance_logic import list_complete_study_runs
    runs = list_complete_study_runs()
    assert isinstance(runs, list)
    for r in runs:
        assert {"run_id", "run_ts", "products", "label"} <= set(r)


def test_study_run_submitted_and_sets():
    from ui.governance_logic import study_run_submitted, list_complete_study_runs, list_assumption_sets
    runs = list_complete_study_runs()
    if runs:
        assert isinstance(study_run_submitted(runs[0]["run_id"]), bool)
    sets = list_assumption_sets()
    for s in sets:
        assert {"id", "version", "status", "parent_set_id", "label"} <= set(s)


def test_lineage_overview_shape():
    from ui.governance_logic import list_assumption_sets, lineage_overview
    sets = list_assumption_sets()
    if not sets:
        pytest.skip("no assumption sets in DB")
    ov = lineage_overview(sets[0]["id"], date.today())
    assert set(ov) == {"root", "members", "live_set_id"}
    assert ov["members"], "lineage must contain at least the selected set"
    # members carry the lineage fields and are version-sorted
    versions = [m["version"] for m in ov["members"]]
    assert versions == sorted(versions, key=lambda v: (v is None, v))
    for m in ov["members"]:
        assert {"id", "version", "status", "effective_from", "effective_to",
                "superseded_by", "is_selected"} <= set(m)
    assert any(m["is_selected"] for m in ov["members"])


# --------------------------------------------------------------------------
# AppTest render + role gating
# --------------------------------------------------------------------------
pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_SUBMIT = "Submit for governance approval"
_REOPEN = "Re-open → create DRAFT child"
_PUBLISH = "Publish version"


def _render(path: str, role: Role):
    with patch("src.governance.auth.current_user", return_value=_user(role)):
        at = AppTest.from_file(path, default_timeout=60)
        at.run()
    return at


def test_study_run_signoff_submit_gated_by_propose():
    # Render smoke for both roles. The Submit control only appears when the selected
    # run has NOT yet been submitted — and the live DB's governance state changes as
    # the app is used — so assert the role-gating only when the control is present.
    # (The deterministic propose-permission decision is covered by test_propose_gate.)
    at = _render("ui/views/28_study_run_signoff.py", Role.ANALYST)
    assert not at.exception, f"page 28 raised (analyst): {list(at.exception)}"
    a_submit = [b for b in at.button if b.label == _SUBMIT]
    if a_submit:  # run not yet submitted → analyst (propose) may submit
        assert not a_submit[0].disabled, "analyst must be able to submit a run"

    at2 = _render("ui/views/28_study_run_signoff.py", Role.CHIEF_ACTUARY)
    assert not at2.exception, f"page 28 raised (chief): {list(at2.exception)}"
    c_submit = [b for b in at2.button if b.label == _SUBMIT]
    if c_submit:  # non-proposer must not submit
        assert c_submit[0].disabled, "a non-proposer must not submit a run"
        assert any("cannot submit" in c.value for c in at2.caption)


def test_lineage_page_reopen_and_publish_role_gated():
    # Analyst: can re-open (propose), cannot publish (no sign_off).
    at = _render("ui/views/29_assumption_lineage.py", Role.ANALYST)
    assert not at.exception, f"page 29 raised (analyst): {list(at.exception)}"
    labels = {b.label: b for b in at.button}
    if _REOPEN in labels:  # only when the selected set is APPROVED
        assert not labels[_REOPEN].disabled, "analyst should be able to re-open"
    if _PUBLISH in labels:
        assert labels[_PUBLISH].disabled, "analyst must not be able to publish"

    # Chief: can publish (sign_off), cannot re-open (no propose).
    at2 = _render("ui/views/29_assumption_lineage.py", Role.CHIEF_ACTUARY)
    assert not at2.exception, f"page 29 raised (chief): {list(at2.exception)}"
    labels2 = {b.label: b for b in at2.button}
    if _PUBLISH in labels2:
        assert not labels2[_PUBLISH].disabled, "chief should be able to publish"
    if _REOPEN in labels2:
        assert labels2[_REOPEN].disabled, "chief must not be able to re-open"
