"""Headless render smoke for the redesigned Home page.

Executes the page end-to-end via Streamlit's AppTest, which also validates that
every ``st.page_link`` target resolves (an unresolved page path raises at run).
Patches src.governance.auth.current_user so the render proceeds past require_auth.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.utils.types import Role, User

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_USER = User(
    user_id="test-analyst", username="a.analyst", display_name="A. Analyst",
    role=Role.ANALYST, active=True,
)


def test_home_renders_workflow_and_links():
    with patch("src.governance.auth.current_user", return_value=_USER):
        at = AppTest.from_file("ui/views/00_home.py", default_timeout=60)
        at.run()
    # No exception implies every st.page_link target resolved.
    assert not at.exception, f"home raised on render: {list(at.exception)}"
    assert any("Actuarial Experience Study Tool" in t.value for t in at.title)
    subs = [s.value for s in at.subheader]
    assert any("How it fits together" in s for s in subs)         # workflow diagram section
    assert any("How to run the model" in s for s in subs)         # numbered steps section
    # The two explainer cards are present.
    md = " ".join(m.value for m in at.markdown)
    assert "Where the AI pages fit" in md
    assert "Where Governance fits" in md
