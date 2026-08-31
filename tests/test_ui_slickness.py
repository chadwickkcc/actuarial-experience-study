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
    """Both reports must be downloadable, via the export-gated helper (m-2)."""
    src = (_VIEWS / "07_run_log.py").read_text(encoding="utf-8")
    assert src.count("export_button(") >= 2, (
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


def test_ci_explorer_does_not_present_experience_as_a_fault() -> None:
    """The demo's own headline CI story (A/E 1.2325) must not render as a warning,
    and no page may tell a client to update the engine (adversarial review m-14)."""
    from pathlib import Path as _P
    src = _P("ui/views/06_ci_explorer.py").read_text()
    assert "outside specification range" not in src, (
        "elevated experience is a finding to explain, not an out-of-spec fault"
    )
    assert "updating the A/E engine" not in src, "developer-facing copy in client UI"


def test_every_download_surface_gates_on_export() -> None:
    """m-2: the `export` right was enforced on only 2 of 9 download surfaces, so an
    analyst denied the compliance pack could still download claim-level fraud data,
    both actuary reports, the AI memo and the factors CSV."""
    import pathlib as _pl

    offenders = []
    for view in sorted(_pl.Path("ui/views").glob("*.py")):
        src = view.read_text()
        if "download_button(" not in src:
            continue
        # Either the page gates the whole block on EXPORT, or it uses the gated helper.
        gated = "Action.EXPORT" in src or "export_button" in src
        if not gated:
            offenders.append(view.name)
        # A raw st.download_button must not survive on a page using the helper.
        if "export_button" in src and "st.download_button(" in src:
            offenders.append(f"{view.name} (mixed raw/gated)")
    assert not offenders, f"ungated download surfaces: {offenders}"


# ---------------------------------------------------------------------------
# Claims the UI makes about its own guarantees (adversarial review OBS-6, OBS-10)
# ---------------------------------------------------------------------------

def test_integrity_panel_states_the_limit_of_an_unkeyed_chain():
    """"intact ✓" must not be readable as cryptographic proof.

    The hash chain is unkeyed, so an actor with database write access AND the
    source can re-chain after an edit. The docs have always said "tamper-evident"
    rather than "tamper-proof"; the page where a client reads the verdict has to
    say so too (OBS-6).
    """
    page = (_VIEWS / "26_governance_audit.py").read_text(encoding="utf-8")
    assert "tamper-**evident**, not tamper-proof" in page
    assert "unkeyed" in page
    assert "re-chain" in page


def test_ae_explorers_offer_the_sparse_cell_floor():
    """Both A/E explorers expose the minimum-claims control (OBS-10)."""
    for name in ("04_mortality_ae.py", "05_lapse_ae.py"):
        page = (_VIEWS / name).read_text(encoding="utf-8")
        assert "min_claims" in page, f"{name} has no sparse-cell floor"
        assert "0 = show every cell" in page, f"{name} does not say the default shows all"
