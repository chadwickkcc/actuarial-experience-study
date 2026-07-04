"""Tests for the versioned prompt-template loader/hasher (Session 19; §F.3).

Every prompt template under ``config/prompts/`` carries a version identifier and
is hashed; the hash is what the audit log records (FR-3B-08) so any response ties
to the exact prompt that produced it.
"""
from __future__ import annotations

import hashlib

from src.ai.prompts import load_prompt_template, PromptTemplate


def test_loads_skill_templates_with_version_and_stable_hash():
    for name in ("skills/memo.md", "skills/shap_explain.md"):
        tpl = load_prompt_template(name)
        assert isinstance(tpl, PromptTemplate)
        assert tpl.name == name
        assert tpl.text.strip()                 # non-empty body
        assert tpl.version                       # a version identifier was parsed
        assert len(tpl.sha256) == 64             # full sha256 hex
        # Hash is the hash of the file bytes and is stable across reloads.
        assert load_prompt_template(name).sha256 == tpl.sha256


def test_hash_matches_file_bytes():
    name = "skills/memo.md"
    tpl = load_prompt_template(name)
    raw = (tpl.path).read_bytes()
    assert tpl.sha256 == hashlib.sha256(raw).hexdigest()


def test_missing_template_raises():
    import pytest

    with pytest.raises(FileNotFoundError):
        load_prompt_template("skills/does_not_exist.md")


def test_template_without_version_raises(tmp_path):
    import pytest

    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "noversion.md").write_text("# Body with no version line\n")
    with pytest.raises(ValueError):
        load_prompt_template("skills/noversion.md", prompts_dir=tmp_path)

