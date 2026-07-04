"""Versioned prompt-template loader + hasher (Session 19; Tech Spec §F.3).

Introduces ``config/prompts/`` as the home for every version-controlled prompt
template. Used by the two Skills now (``config/prompts/skills/``) and by the
Session 20–21 chatbot prompts (routing, sql_generation, commentary,
faithfulness_judge) later — so the loader lives at the ``src/ai/`` package root,
not under ``skills/``.

Each template carries a version identifier in a leading
``<!-- version: X.Y -->`` line; the loader returns that version plus the sha256
of the **full file bytes** (FR-3B-08), which the Skills surface in their
``hashes`` mapping so any response ties to the exact prompt that produced it.
This module performs no SQL and makes no network/LLM calls.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"

_VERSION_RE = re.compile(r"^\s*<!--\s*version:\s*([^\s]+)\s*-->\s*$", re.MULTILINE)


@dataclass
class PromptTemplate:
    """A loaded, versioned prompt template."""
    name:    str        # relative name under config/prompts/, e.g. "skills/memo.md"
    text:    str        # body with the version comment stripped
    version: str        # parsed from the leading <!-- version: X.Y --> line
    sha256:  str        # sha256 of the full file bytes (audit identity, FR-3B-08)
    path:    Path


def load_prompt_template(name: str, prompts_dir: Path = PROMPTS_DIR) -> PromptTemplate:
    """Load a template by its relative ``name`` under ``config/prompts/``.

    Args:
        name: relative path, e.g. ``"skills/memo.md"``.
        prompts_dir: override for the prompts root (tests).

    Returns:
        A :class:`PromptTemplate`.

    Raises:
        FileNotFoundError: if no such template exists.
        ValueError: if the template carries no version identifier.
    """
    path = Path(prompts_dir) / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    content = raw.decode("utf-8")
    match = _VERSION_RE.search(content)
    if match is None:
        raise ValueError(
            f"Prompt template {name} is missing a leading "
            f"'<!-- version: X.Y -->' identifier (FR-3B-08)."
        )
    version = match.group(1)
    text = _VERSION_RE.sub("", content, count=1).strip()
    return PromptTemplate(name=name, text=text, version=version, sha256=sha256, path=path)
