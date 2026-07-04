"""Static guards for the Assumption Comparison page (Session 17).

* FR-3A-44: the page wires NO adopt/apply affordance — it must not call any
  assumption-set write path (save/create/record).
* FR-3A-46: every DB connection the page opens itself is read-only.
"""
from __future__ import annotations

import re
from pathlib import Path

_PAGE = Path("ui/views/15_assumption_comparison.py")

# Assumption-set write paths that must never appear on the read-only page.
_FORBIDDEN_ADOPT_TOKENS = [
    "save_assumption_set",
    "create_assumption_set",
    "record_ai_provenance",
    "save_yaml",
    "transition_assumption_set_status",
]


def test_page_exists():
    assert _PAGE.exists(), "Assumption Comparison page is missing."


def test_page_has_no_adopt_affordance():
    src = _PAGE.read_text(encoding="utf-8")
    for token in _FORBIDDEN_ADOPT_TOKENS:
        assert token not in src, (
            f"FR-3A-44 violation: page references adopt/write path '{token}'. "
            "Adoption belongs in Stage 2, not on the read-only comparison page."
        )


def test_page_connections_are_read_only():
    src = _PAGE.read_text(encoding="utf-8")
    # Every duckdb.connect(...) call must pass read_only=True. The regex allows one
    # level of nested parens (e.g. duckdb.connect(str(DB_PATH), read_only=True)).
    pattern = re.compile(r"duckdb\.connect\((?P<args>(?:[^()]|\([^()]*\))*)\)")
    found = pattern.findall(src)
    assert found, "Expected at least one duckdb.connect(...) call on the page."
    for args in found:
        assert "read_only=True" in args, (
            f"FR-3A-46 violation: page opens a non-read-only connection: "
            f"duckdb.connect({args})"
        )


def test_logic_module_importable_without_streamlit():
    # The page's logic lives in an import-safe module (no Streamlit at import).
    import importlib

    mod = importlib.import_module("ui.ai_comparison_logic")
    assert hasattr(mod, "build_comparison_table")
    assert hasattr(mod, "build_whatif_assumption_set")
    assert mod.WHAT_IF_SENSITIVITY_ID == "what_if_ai_proposal"
