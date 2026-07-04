"""Headless render smoke for the Assumption Comparison page (Session 17).

Uses Streamlit's AppTest to execute the page script end-to-end in its initial
(no-fit) state, catching runtime errors that a plain py_compile cannot (Streamlit
API misuse, session-state handling, import wiring). Skipped when the local
DuckDB is absent, since the page reads run options from it (read-only).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ui.config import DB_PATH
from src.utils.types import Role, User

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="local experience_study.duckdb not present"
)

# The page mounts the shared require_auth() gate, which reads
# src.governance.auth.current_user at call time; patch it so the render proceeds
# past the login gate (AppTest starts with no session identity).
_USER = User(
    user_id="test-analyst", username="a.analyst", display_name="A. Analyst",
    role=Role.ANALYST, active=True,
)


def test_page_renders_initial_state():
    with patch("src.governance.auth.current_user", return_value=_USER):
        at = AppTest.from_file("ui/views/15_assumption_comparison.py", default_timeout=60)
        at.run()
    # at.exception is an (empty) ElementList when no error occurred.
    assert not at.exception, f"page raised on render: {list(at.exception)}"
    # Title + the three selectors + the fit action must be present.
    assert any("Assumption Comparison" in t.value for t in at.title)
    labels = {sb.label for sb in at.selectbox}
    assert {"Study run", "Decrement", "Product"} <= labels
    assert any(b.label == "Fit AI models" for b in at.button)
    # Initial state: an info prompt to fit, no crash, no proposal table yet.
    assert at.info, "expected an initial 'fit first' info prompt"


def test_skill_buttons_are_live_not_greyed():
    """Session 19: the two Skill buttons are wired live (no `disabled=True`,
    no 'Available in Phase 3b' placeholder). The buttons only render after a fit,
    so this is a source guard rather than an AppTest render assertion.
    """
    import pathlib

    src = pathlib.Path("ui/views/15_assumption_comparison.py").read_text(encoding="utf-8")
    assert 'Draft A/E memo' in src
    assert 'Explain SHAP results' in src
    assert "Available in Phase 3b" not in src        # greyed placeholder removed
    assert "disabled=True" not in src                 # neither Skill button disabled
    assert "interpret_ae_and_draft_memo" in src       # memo Skill wired
    assert "explain_shap_results" in src              # SHAP Skill wired


def test_stage4_has_live_memo_skill():
    """Session 19 (FR-3B-20): the memo Skill is reachable from Stage-4 governance."""
    import pathlib

    src = pathlib.Path("ui/views/23_tev_stage4.py").read_text(encoding="utf-8")
    assert "interpret_ae_and_draft_memo" in src
    assert "Draft A/E memo (AI)" in src
