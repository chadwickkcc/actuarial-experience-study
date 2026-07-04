"""Headless render smoke for the Governance Dashboard page (Session 27).

Drives page 27 end-to-end via Streamlit's ``AppTest`` — both the unauthenticated
gate (renders a warning, no crash) and, when the local DB is present, an
authenticated render (injecting a chief-actuary session identity) that exercises
the real ``dashboard_data`` path. A source guard confirms the page wires the
reporting functions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ui.config import DB_PATH
from src.utils.types import Role, User

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_PAGE = "ui/views/27_governance_dashboard.py"

_CHIEF = User(
    user_id="test-chief", username="c.chief", display_name="C. Chief",
    role=Role.CHIEF_ACTUARY, active=True,
)


def test_page_renders_unauthenticated():
    """No session identity -> warning + st.stop(), never an exception."""
    at = AppTest.from_file(_PAGE, default_timeout=60)
    at.run()
    assert not at.exception, f"page raised on render: {list(at.exception)}"
    assert any("Governance Dashboard" in t.value for t in at.title)


@pytest.mark.skipif(not DB_PATH.exists(), reason="local experience_study.duckdb not present")
def test_page_renders_authenticated():
    """With a signed-in chief actuary, the dashboard body renders against the real DB.

    The page binds ``current_user`` via ``from src.governance.auth import
    current_user`` when AppTest imports it during ``run()`` — patching the source
    attribute before the run makes the page see the authenticated identity
    (AppTest session-state injection does not reliably reach ``current_user``).
    """
    with patch("src.governance.auth.current_user", return_value=_CHIEF):
        at = AppTest.from_file(_PAGE, default_timeout=90)
        at.run()
    assert not at.exception, f"page raised on render: {list(at.exception)}"
    headers = " ".join(s.value for s in at.subheader)
    assert "Artifact states" in headers
    assert "Pending approvals" in headers


def test_page_source_wires_reporting():
    import pathlib

    src = pathlib.Path(_PAGE).read_text(encoding="utf-8")
    assert "dashboard_data" in src
    assert "export_compliance_pack" in src
    assert "retention_policy" in src
