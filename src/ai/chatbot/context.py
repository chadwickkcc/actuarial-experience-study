"""Multi-turn context assembly (Session 20/21; Tech Spec v2.0.1 §E.7).

``trim_history`` bounds the assembled conversation history to a token window
(FR-3B-39): the system prompt is always retained, and the most recent turns are
kept oldest-first up to the window.

``assemble_rag_context`` (Session 21, FR-3B-36) grounds commentary in the tool's
**own** generated artifacts — the Working/Chief Actuary report and the shipped
methodology documentation for the run(s) — never an external knowledge base. It
reads only files handed to it via ``artifact_paths`` (resolved by
``resolve_rag_artifacts``); it never opens the database (FR-3B-25).
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

_DEFAULT_MAX_GROUNDING_CHARS = 12_000
_PER_ARTIFACT_CHARS = 4_000


def _approx_tokens(text: str) -> int:
    """Deterministic ~4-chars/token estimate (matches the MockProvider heuristic)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _turn_tokens(turn: dict) -> int:
    return _approx_tokens(str(turn.get("content", "")))


def trim_history(
    turns: list[dict],
    system_prompt: str,
    token_window: int,
) -> list[dict]:
    """Retain the system prompt plus the most recent turns within ``token_window``.

    The system prompt's tokens are always reserved first (it is never dropped,
    FR-3B-39). Remaining turns are added newest-first until the window is reached,
    then returned in chronological order. The returned list contains only the
    conversation turns (the system prompt is supplied separately to the LLM call).

    Args:
        turns: chronological ``[{role, content, ...}]`` history.
        system_prompt: the system prompt whose budget is reserved first.
        token_window: the maximum assembled-history token budget.

    Returns:
        The retained turns, oldest-first.
    """
    budget = token_window - _approx_tokens(system_prompt)
    if budget <= 0:
        # The system prompt alone fills (or exceeds) the window — keep it only.
        return []

    kept_reversed: list[dict] = []
    used = 0
    for turn in reversed(turns):
        cost = _turn_tokens(turn)
        if used + cost > budget:
            break
        kept_reversed.append(turn)
        used += cost
    return list(reversed(kept_reversed))


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from an HTML report, dropping script/style/markup."""

    _SKIP = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.text()


def _read_artifact(path: Path, per_artifact_chars: int) -> str:
    """Read one grounding file as plain text (HTML stripped), bounded in length."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    text = _strip_html(raw) if str(path).lower().endswith((".html", ".htm")) else raw
    text = " ".join(text.split())
    if len(text) > per_artifact_chars:
        text = text[:per_artifact_chars].rstrip() + " …"
    return text


def resolve_rag_artifacts(
    run_ids: list[str],
    *,
    reports_dir,
    methodology_paths,
) -> dict:
    """Find the tool's own grounding artifacts for ``run_ids`` (FR-3B-36).

    Returns ``{"reports": [paths], "methodology": [paths]}`` containing only files
    that exist on disk. Report files are matched by the run-id prefix the report
    generator uses (``{working,chief}_actuary_{run_id[:8]}.html``). Methodology
    docs are the shipped paths that exist. Never reads the database.
    """
    reports_root = Path(reports_dir)
    reports: list[str] = []
    for run_id in run_ids or []:
        prefix = str(run_id)[:8]
        for stem in (f"working_actuary_{prefix}.html", f"chief_actuary_{prefix}.html"):
            candidate = reports_root / stem
            if candidate.exists():
                reports.append(str(candidate))
    methodology = [str(p) for p in (methodology_paths or []) if Path(p).exists()]
    return {"reports": reports, "methodology": methodology}


_SECTION_LABELS = {
    "reports": "Tool-generated study report",
    "methodology": "Methodology documentation",
}


def assemble_rag_context(
    run_ids: list[str],
    artifact_paths: dict,
    *,
    max_chars: int = _DEFAULT_MAX_GROUNDING_CHARS,
    per_artifact_chars: int = _PER_ARTIFACT_CHARS,
) -> str:
    """Assemble grounding text from the tool's own artifacts (FR-3B-36).

    ``artifact_paths`` is ``{section: [file_paths]}`` (e.g. from
    ``resolve_rag_artifacts``). Each file is read, HTML-stripped, bounded, and
    concatenated under a labelled header. The whole assembly is capped at
    ``max_chars``. Returns ``""`` when no artifacts are supplied, so the seam is
    safe to wire even when a run has no generated report.

    This never opens the database; it reads only the files it is given.
    """
    if not artifact_paths:
        return ""
    blocks: list[str] = []
    for section, paths in artifact_paths.items():
        label = _SECTION_LABELS.get(section, str(section))
        for path in paths or []:
            text = _read_artifact(Path(path), per_artifact_chars)
            if text:
                blocks.append(f"## {label}: {Path(path).name}\n{text}")
    if not blocks:
        return ""
    assembled = "\n\n".join(blocks)
    if len(assembled) > max_chars:
        assembled = assembled[:max_chars].rstrip() + " …"
    return assembled
