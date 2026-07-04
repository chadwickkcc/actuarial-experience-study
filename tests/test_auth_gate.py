"""Regression guards for the Streamlit login gate (FR-4-02 / NFR-G-01).

Phase-4 UAT (test 1.1) found the login gate was bypassable: page scripts lived in
``ui/pages/`` — a Streamlit-reserved directory name — so Streamlit auto-discovered
them into a second, directly-routable navigation that ran each page *without*
executing ``ui/app.py`` (where ``login_gate()`` lives). The fix renames the page
directory to the non-reserved ``ui/views/`` (so only the ``st.navigation`` menu
behind the login gate remains) and adds a defense-in-depth ``require_auth()`` guard
to every page. These tests lock both halves so the bypass cannot silently return.
"""

from __future__ import annotations

from pathlib import Path

from unittest.mock import patch

import pytest

_UI = Path(__file__).resolve().parent.parent / "ui"
_VIEWS = _UI / "views"


def test_no_reserved_pages_directory_exists():
    """The Streamlit-reserved ``ui/pages/`` auto-discovery directory must not exist.

    Its mere presence next to the entrypoint re-enables the automatic multipage
    convention that bypasses ``login_gate()`` — the root cause of the UAT finding.
    """
    assert not (_UI / "pages").exists(), (
        "ui/pages/ is a Streamlit-reserved directory that auto-registers "
        "directly-routable pages and bypasses the login gate; keep pages in ui/views/."
    )


def test_views_directory_present_with_pages():
    """The renamed, non-reserved page directory exists and holds the page scripts."""
    assert _VIEWS.is_dir(), "ui/views/ (the renamed page directory) is missing"
    assert list(_VIEWS.glob("*.py")), "ui/views/ contains no page scripts"


def test_app_navigation_points_at_views_not_pages():
    """The entrypoint builds st.navigation off ui/views/, not the reserved name."""
    app_src = (_UI / "app.py").read_text(encoding="utf-8")
    assert '/ "views"' in app_src, "app.py _PAGES_DIR should point at the 'views' dir"
    assert '/ "pages"' not in app_src, "app.py must not reference the reserved 'pages' dir"


def test_every_view_invokes_the_auth_guard():
    """Defense-in-depth: every page script calls require_auth() so it cannot render
    its content unauthenticated even if reached by any direct-execution path."""
    missing = [
        f.name
        for f in sorted(_VIEWS.glob("*.py"))
        if "require_auth()" not in f.read_text(encoding="utf-8")
    ]
    assert not missing, f"view pages missing the require_auth() guard: {missing}"


def test_require_auth_stops_when_unauthenticated():
    """require_auth() blocks (st.stop) and warns when there is no session identity."""
    from ui.config import require_auth

    with patch("src.governance.auth.current_user", return_value=None):
        # Streamlit's st.stop() raises StopException under a script run; outside a
        # runtime it is a no-op, so accept either a raised stop or a returned None.
        try:
            result = require_auth()
        except BaseException as exc:  # noqa: BLE001 - StopException is not public
            assert "stop" in type(exc).__name__.lower()
        else:
            assert result is None


def test_require_auth_returns_user_when_authenticated():
    """require_auth() returns the authenticated user without stopping."""
    from ui.config import require_auth
    from src.utils.types import Role, User

    user = User(
        user_id="u1", username="a.analyst", display_name="A. Analyst",
        role=Role.ANALYST, active=True,
    )
    with patch("src.governance.auth.current_user", return_value=user):
        assert require_auth() is user
