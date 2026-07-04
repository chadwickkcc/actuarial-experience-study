"""Phase-4 additive-regression smoke (Session 27, §I.3 / NFR-G-08).

The authoritative regression is the full ``pytest tests/`` run (which stays green
across Phases 1–3). This lightweight guard asserts the governance layer imports
cleanly alongside the core engine (no import-time breakage / cycle) and that the
shipped tree passes the tenancy-readiness conformance check — a fast signal that
Session 27's additions did not disturb the existing modules.
"""

from __future__ import annotations

import importlib

import pytest

_MODULES = [
    # New Session-27 governance modules
    "src.governance.reporting",
    "src.governance.readiness",
    # Existing governance layer (Sessions 23–26)
    "src.governance.auth",
    "src.governance.users",
    "src.governance.rbac",
    "src.governance.lineage",
    "src.governance.workflow",
    "src.governance.audit",
    # Core engine (must be importable independently of governance)
    "src.calculation.ae_engine",
    "src.tev.tev_core",
    "src.reporting.generator",
    "src.utils.db_init",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_imports_cleanly(module):
    importlib.import_module(module)


def test_tenancy_readiness_clean_on_shipped_tree():
    from src.governance.readiness import check_tenancy_readiness

    assert check_tenancy_readiness() == []


def test_core_engine_does_not_import_governance():
    """The core engine must not depend on src/governance (one-way boundary, cf. FR-3A-07)."""
    import ast
    from pathlib import Path

    core = [
        Path("src/calculation"), Path("src/tev"), Path("src/exposure"),
        Path("src/aggregation"), Path("src/data_quality"), Path("src/ingestion"),
    ]
    offenders = []
    for pkg in core:
        for py in pkg.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                elif isinstance(node, ast.Import):
                    mod = ",".join(a.name for a in node.names)
                if mod and "src.governance" in mod:
                    offenders.append(f"{py}:{node.lineno}")
    assert not offenders, "core engine imports src.governance: " + ", ".join(offenders)
