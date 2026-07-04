"""RAG grounding for commentary (Session 21; FR-3B-36).

``assemble_rag_context`` grounds only in the tool's OWN generated artifacts
(reports + methodology docs), is length-bounded, strips HTML, and degrades
gracefully when a run has no report. ``resolve_rag_artifacts`` finds only files
that exist. No DB access. Keys-unset, no network.
"""
from __future__ import annotations

from pathlib import Path

from src.ai.chatbot.context import assemble_rag_context, resolve_rag_artifacts


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_empty_artifacts_returns_empty_string():
    # Preserves the Session-20 seam contract: no artifacts -> no grounding.
    assert assemble_rag_context(["run-1"], {}) == ""
    assert assemble_rag_context(["run-1"], {"reports": [], "methodology": []}) == ""


def test_grounds_in_report_and_methodology_text(tmp_path):
    report = _write(
        tmp_path / "working_actuary_abcd1234.html",
        "<html><head><style>x{}</style></head><body><h1>A/E Report</h1>"
        "<p>Term mortality A/E is 0.92.</p><script>ignored()</script></body></html>",
    )
    method = _write(tmp_path / "method.md", "Exposure uses the Balducci method.")
    text = assemble_rag_context(
        ["abcd1234"], {"reports": [str(report)], "methodology": [str(method)]}
    )
    assert "A/E Report" in text
    assert "Term mortality A/E is 0.92." in text
    assert "Balducci" in text
    # HTML markup and script/style content are stripped.
    assert "<h1>" not in text and "ignored()" not in text and "x{}" not in text


def test_grounding_is_length_bounded(tmp_path):
    big = _write(tmp_path / "big.md", "word " * 100_000)
    text = assemble_rag_context(["r"], {"methodology": [str(big)]}, max_chars=500)
    assert len(text) <= 520  # cap + the truncation marker/header


def test_missing_report_degrades_to_methodology(tmp_path):
    method = _write(tmp_path / "m.md", "Methodology only grounding.")
    # No report path supplied at all — still grounds on methodology.
    text = assemble_rag_context(["r"], {"reports": [], "methodology": [str(method)]})
    assert "Methodology only grounding." in text


def test_resolve_finds_only_existing_files(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write(reports / "chief_actuary_ed193b59.html", "<p>hi</p>")
    method_present = _write(tmp_path / "present.md", "x")
    method_absent = tmp_path / "absent.md"
    resolved = resolve_rag_artifacts(
        ["ed193b59-c5d6-48cd-b5e6-43d33464dff8"],
        reports_dir=reports,
        methodology_paths=[str(method_present), str(method_absent)],
    )
    assert any("chief_actuary_ed193b59.html" in p for p in resolved["reports"])
    assert str(method_present) in resolved["methodology"]
    assert str(method_absent) not in resolved["methodology"]


def test_resolve_handles_run_with_no_report(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    resolved = resolve_rag_artifacts(
        ["no-report-run"], reports_dir=reports, methodology_paths=[]
    )
    assert resolved == {"reports": [], "methodology": []}
