"""Headless render smoke for the AI Analyst page (Session 21).

Executes the page script end-to-end in its initial state via Streamlit's AppTest,
catching runtime/wiring errors a plain py_compile cannot. Skipped when the local
DuckDB is absent (the page reads the run list from it, read-only). No LLM call is
made on initial render (the chat input is empty).
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
        at = AppTest.from_file("ui/views/16_ai_analyst.py", default_timeout=60)
        at.run()
    assert not at.exception, f"page raised on render: {list(at.exception)}"
    assert any("AI Analyst" in t.value for t in at.title)
    # Model + run selectors present; running cost metrics rendered.
    labels = {sb.label for sb in at.selectbox}
    assert "Model" in labels
    assert at.metric, "expected token/cost metrics"
    # Discoverability affordances: example/commentary buttons + faithfulness toggle.
    button_labels = " ".join(b.label for b in at.button)
    assert "commentary" in button_labels.lower(), "expected a commentary example button"
    toggle_labels = " ".join(t.label for t in at.toggle).lower()
    assert "aithfulness" in toggle_labels, "expected faithfulness toggle"
    assert "analyst mode" in toggle_labels, "expected Analyst-mode toggle"
    assert "deep analysis" in toggle_labels, "expected deep-analysis (multi-query) toggle"


def test_page_source_wires_audit_and_export():
    """Source guards: the page drives the audited turn and offers Markdown export."""
    import pathlib

    src = pathlib.Path("ui/views/16_ai_analyst.py").read_text(encoding="utf-8")
    assert "run_turn" in src                      # drives handle_turn + audit sink
    assert "export_conversation_markdown" in src  # export with banners
    assert "chat_input" in src
