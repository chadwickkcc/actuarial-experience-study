"""UI slickness guards (demo refresh P7).

Nav registration (every view registered exactly once), report download
buttons on the run log, no internal requirement IDs in client-visible
strings, and the shared theme in use.
"""
from __future__ import annotations

import ast
import pathlib
import re

_UI = pathlib.Path("ui")
_VIEWS = _UI / "views"


def _registered_pages() -> list[str]:
    src = (_UI / "app.py").read_text(encoding="utf-8")
    return re.findall(r'_page\("([0-9A-Za-z_]+\.py)"', src)


def test_every_view_registered_exactly_once():
    registered = _registered_pages()
    assert len(registered) == len(set(registered)), "a view is registered twice"
    view_files = {f.name for f in _VIEWS.glob("*.py")}
    missing = view_files - set(registered)
    orphans = set(registered) - view_files
    assert not missing, f"views missing from the nav: {sorted(missing)}"
    assert not orphans, f"nav points at non-existent views: {sorted(orphans)}"


def test_nav_has_five_groups():
    src = (_UI / "app.py").read_text(encoding="utf-8")
    for group in ("Overview", "Experience Results", "Risk & Fraud",
                  "Assumptions & AI", "Governance"):
        assert f'"{group}"' in src, f"nav group missing: {group}"


def test_run_log_reports_have_download_buttons():
    src = (_VIEWS / "07_run_log.py").read_text(encoding="utf-8")
    assert src.count("download_button") >= 2, (
        "the two generated reports must be downloadable, not just written to disk"
    )


_REQ_ID = re.compile(r"\b(?:FR|NFR)-(?!RULE\b)[0-9A-Z]")


def _rendered_string_leaks(path: pathlib.Path) -> list[str]:
    """Requirement IDs inside non-docstring string constants of a view."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    leaks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if _REQ_ID.search(node.value):
                leaks.append(f"{path.name}:{node.lineno}: {node.value[:70]!r}")
    return leaks


def test_no_requirement_ids_in_client_visible_strings():
    """FR-/NFR- identifiers are internal spec references; client-facing pages
    must not render them (fraud rule ids FR-RULE-nn are a deliberate domain
    naming scheme and are allowed)."""
    leaks: list[str] = []
    for view in sorted(_VIEWS.glob("*.py")):
        leaks.extend(_rendered_string_leaks(view))
    assert leaks == [], "requirement IDs leak into rendered strings:\n" + "\n".join(leaks)


def test_views_use_shared_page_setup():
    """Views standardise on ui.theme.page_setup (no bare st.set_page_config)."""
    offenders = [
        f.name for f in _VIEWS.glob("*.py")
        if "st.set_page_config(" in f.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"views still call st.set_page_config: {offenders}"
