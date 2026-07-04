"""UAT 4.1b — the `propose` permission is enforced on TEV Stages 1-3.

Only the analyst role holds `propose` (config/governance_config.yaml); the actuary
roles are approvers, not proposers (segregation of duties, docs/phase4_locked_scope.md).
These tests lock (a) the permission-decision function used by the UI gate and
(b) that Stage 1's "Create Proposed Assumption Set" write button is disabled, with an
explanatory caption, for a non-proposer role while remaining enabled for an analyst —
view access is never blocked.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ui.config import DB_PATH, user_can
from src.governance.rbac import Action, is_permitted
from src.utils.types import Role, User


def _user(role: Role) -> User:
    return User(
        user_id=f"u-{role.value}", username=f"x.{role.value}",
        display_name=role.value, role=role, active=True,
    )


def test_every_stage3_write_button_is_propose_gated():
    """Source guard: every Stage-3 write action carries the propose gate.

    Stage 3 has five write buttons (Run TEV, Compute Credibility Envelope, Generate
    Working Actuary Report, Submit for sign-off, Refine/return). Each must render with
    ``disabled=not _can_propose`` and re-check ``require(_user, Action.PROPOSE)`` in its
    handler. This locks the fix for the three secondary actions that were initially
    ungated (envelope / report / refine).
    """
    import pathlib

    src = pathlib.Path("ui/views/22_tev_stage3.py").read_text(encoding="utf-8")
    for label in (
        "▶ Run Full TEV",
        "Compute Credibility Envelope",
        "Generate Working Actuary Report",
        "Submit for sign-off →",
        "← Refine assumptions (return to Stage 2)",
    ):
        assert label in src, f"Stage-3 button missing: {label}"
    # One gate + one server-side re-check per write button (>= 5 of each).
    assert src.count("disabled=not _can_propose") >= 5, "a Stage-3 write button lost its disabled gate"
    assert src.count("require(_user, Action.PROPOSE)") >= 5, "a Stage-3 write handler lost its server-side re-check"


def test_only_analyst_may_propose():
    """Against the real config: propose ∈ analyst only; actuaries cannot propose."""
    assert is_permitted(_user(Role.ANALYST), Action.PROPOSE) is True
    for role in (Role.JUNIOR_ACTUARY, Role.SENIOR_ACTUARY, Role.CHIEF_ACTUARY):
        assert is_permitted(_user(role), Action.PROPOSE) is False


def test_user_can_wraps_is_permitted():
    """The UI helper mirrors the authoritative RBAC decision."""
    for role in (Role.ANALYST, Role.JUNIOR_ACTUARY, Role.SENIOR_ACTUARY, Role.CHIEF_ACTUARY):
        u = _user(role)
        assert user_can(u, Action.PROPOSE) == is_permitted(u, Action.PROPOSE)


# --------------------------------------------------------------------------
# AppTest render: the Stage-1 write affordance is role-gated (view stays open)
# --------------------------------------------------------------------------
pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_CREATE_LABEL = "Create Proposed Assumption Set →"


@pytest.mark.skipif(not DB_PATH.exists(), reason="local experience_study.duckdb not present")
def test_stage1_create_enabled_for_analyst():
    analyst = User(
        user_id="test-analyst", username="a.analyst", display_name="A. Analyst",
        role=Role.ANALYST, active=True,
    )
    with patch("src.governance.auth.current_user", return_value=analyst):
        at = AppTest.from_file("ui/views/20_tev_stage1.py", default_timeout=60)
        at.run()
    assert not at.exception, f"Stage 1 raised on render: {list(at.exception)}"
    btns = [b for b in at.button if b.label == _CREATE_LABEL]
    assert btns, "expected the Create Proposed Assumption Set button to render"
    assert not btns[0].disabled, "analyst must be able to propose"


@pytest.mark.skipif(not DB_PATH.exists(), reason="local experience_study.duckdb not present")
def test_stage1_create_disabled_for_non_proposer():
    chief = User(
        user_id="test-chief", username="c.chief", display_name="C. Chief",
        role=Role.CHIEF_ACTUARY, active=True,
    )
    with patch("src.governance.auth.current_user", return_value=chief):
        at = AppTest.from_file("ui/views/20_tev_stage1.py", default_timeout=60)
        at.run()
    assert not at.exception, f"Stage 1 raised on render: {list(at.exception)}"
    btns = [b for b in at.button if b.label == _CREATE_LABEL]
    assert btns, "the button still renders (view is open), just disabled"
    assert btns[0].disabled, "a non-proposer role must not be able to propose"
    assert any("cannot propose" in c.value for c in at.caption), \
        "expected an explanatory 'cannot propose' caption"
