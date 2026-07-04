"""Standing-guard tests for the additive AI-layer architecture (Phase 3a entry).

These hold from Session 14 (when src/ai/ is an empty skeleton) and remain green
as later sessions populate the layer:

* FR-3A-02  No SQL string interpolation anywhere in src/ai/.
* FR-3A-03  Jinja2 environments set autoescape=True.
* FR-3A-07  One-way import rule: the core engine never imports from src/ai/.
* FR-3A-09  Write contract: src/ai/ writes only to data/ai_models/ and the
            three AI Gold tables.

Each guard ships with a *negative self-test* that feeds it a deliberate
violation, proving the guard actually fires rather than passing vacuously
because the layer is still empty.
"""
import ast
import re
from pathlib import Path

_SRC = Path("src")
_AI = _SRC / "ai"

# Core-engine packages that must never depend on the AI layer (FR-3A-07).
_CORE_PACKAGES = [
    _SRC / "calculation",
    _SRC / "tev",
    _SRC / "utils",
    _SRC / "reporting",
    _SRC / "etl",
    _SRC / "exposure",
    _SRC / "aggregation",
    _SRC / "data_quality",
    _SRC / "ingestion",
]

# SQL keywords used to decide whether a dynamic string is "feeding SQL".
_SQL_KEYWORD = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|FROM|WHERE|JOIN|"
    r"GROUP\s+BY|ORDER\s+BY|INTO|VALUES)\b",
    re.IGNORECASE,
)

# The only write targets permitted to the AI layer (FR-3A-09; the fourth table
# gold_ai_proposed_factors added by the 2026-06-27 governed-maximum amendment so
# the AI Analyst can read published GLM/GBM proposed factors by grain).
_ALLOWED_AI_TABLES = {"gold_ai_model_registry", "gold_ai_eval_results",
                      "gold_ai_audit_log", "gold_ai_proposed_factors",
                      # spec short forms
                      "ai_model_registry", "ai_eval_results", "ai_audit_log"}
_WRITE_SQL = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Reusable scanners (shared by the standing guards and their self-tests)     #
# --------------------------------------------------------------------------- #

def _scan_sql_interpolation(py_files) -> list:
    """Return violation strings for f-string/%/.format()/'+' SQL building."""
    violations: list = []
    for path in py_files:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                literal = "".join(
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                if _SQL_KEYWORD.search(literal):
                    violations.append(f"{path}:{node.lineno}: f-string SQL")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                if isinstance(node.left, ast.Constant) and isinstance(
                    node.left.value, str
                ) and _SQL_KEYWORD.search(node.left.value):
                    violations.append(f"{path}:{node.lineno}: %-format SQL")
            elif isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ) and node.func.attr == "format":
                tgt = node.func.value
                if isinstance(tgt, ast.Constant) and isinstance(
                    tgt.value, str
                ) and _SQL_KEYWORD.search(tgt.value):
                    violations.append(f"{path}:{node.lineno}: .format() SQL")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and isinstance(
                        side.value, str
                    ) and _SQL_KEYWORD.search(side.value):
                        violations.append(
                            f"{path}:{node.lineno}: '+'-concatenated SQL"
                        )
                        break
    return violations


def _scan_ai_imports(py_files) -> list:
    """Return violation strings where a file imports from src.ai."""
    offenders: list = []
    for path in py_files:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "src.ai" or mod.startswith("src.ai."):
                    offenders.append(f"{path}:{node.lineno}: from {mod}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src.ai" or alias.name.startswith("src.ai."):
                        offenders.append(f"{path}:{node.lineno}: import {alias.name}")
    return offenders


def _ai_py_files() -> list:
    return sorted(_AI.rglob("*.py"))


def _iter_py_files(root: Path) -> list:
    return sorted(root.rglob("*.py")) if root.exists() else []


# --------------------------------------------------------------------------- #
# FR-3A-02 — no SQL string interpolation in src/ai/                          #
# --------------------------------------------------------------------------- #

def test_no_sql_string_interpolation():
    """Scan src/ai/ for f-string / %-format / .format() / '+' concatenation
    feeding SQL. The ONLY permitted SQL path is src.utils.sql_boundary."""
    violations = _scan_sql_interpolation(_ai_py_files())
    assert not violations, "SQL string interpolation in src/ai/:\n" + "\n".join(
        violations
    )


def test_interpolation_guard_fires_on_violation(tmp_path):
    """Negative self-test: the guard catches a deliberate f-string SQL build."""
    bad = tmp_path / "bad_module.py"
    bad.write_text(
        'def q(p):\n    return f"SELECT * FROM gold_ae_results WHERE x={p}"\n',
        encoding="utf-8",
    )
    assert _scan_sql_interpolation([bad]), "guard failed to detect f-string SQL"
    # A non-SQL f-string must NOT trip the guard (avoids false positives).
    ok = tmp_path / "ok_module.py"
    ok.write_text('def g(n):\n    return f"processed {n} rows"\n', encoding="utf-8")
    assert not _scan_sql_interpolation([ok])


# --------------------------------------------------------------------------- #
# FR-3A-07 — one-way import rule                                             #
# --------------------------------------------------------------------------- #

def test_core_engine_does_not_import_ai_layer():
    """No module under the core engine may import from src.ai (FR-3A-07)."""
    offenders = []
    for package in _CORE_PACKAGES:
        offenders += _scan_ai_imports(_iter_py_files(package))
    assert not offenders, "Core engine imports the AI layer:\n" + "\n".join(
        offenders
    )


def test_import_guard_fires_on_violation(tmp_path):
    """Negative self-test: the guard catches a deliberate src.ai import."""
    bad = tmp_path / "core_module.py"
    bad.write_text("from src.ai.glm import fit\n", encoding="utf-8")
    assert _scan_ai_imports([bad]), "guard failed to detect src.ai import"
    bad2 = tmp_path / "core_module2.py"
    bad2.write_text("import src.ai.chatbot.pipeline\n", encoding="utf-8")
    assert _scan_ai_imports([bad2])


# --------------------------------------------------------------------------- #
# FR-3A-09 — write-contract scaffold                                         #
# --------------------------------------------------------------------------- #

def _scan_write_contract(py_files) -> list:
    """Return violations: data writes outside ai_models/ or non-AI table writes."""
    offenders = []
    data_path = re.compile(r"""["']((?:\./)?data/[^"']*)["']""")
    for path in py_files:
        src = Path(path).read_text(encoding="utf-8")
        for m in data_path.finditer(src):
            target = m.group(1).lstrip("./")
            if not target.startswith("data/ai_models"):
                offenders.append(f"{path}: writes outside ai_models: {target}")
        for m in _WRITE_SQL.finditer(src):
            table = m.group(2)
            if table not in _ALLOWED_AI_TABLES:
                offenders.append(f"{path}: write to non-AI table: {table}")
    return offenders


def test_ai_layer_write_contract():
    """src/ai/ may write only to data/ai_models/ and the three AI Gold tables.

    Trivially green now (empty layer); grows as modules land.
    """
    assert not _scan_write_contract(_ai_py_files())


def test_write_contract_guard_fires_on_violation(tmp_path):
    """Negative self-test: the guard catches a forbidden write target."""
    bad = tmp_path / "writer.py"
    bad.write_text(
        'PATH = "data/results/leak.parquet"\n'
        'SQL = "INSERT INTO gold_assumption_sets VALUES (1)"\n',
        encoding="utf-8",
    )
    offenders = _scan_write_contract([bad])
    assert any("leak.parquet" in o for o in offenders)
    assert any("gold_assumption_sets" in o for o in offenders)
    # A compliant module passes.
    ok = tmp_path / "ok_writer.py"
    ok.write_text(
        'PATH = "data/ai_models/glm/model.pkl"\n'
        'SQL = "INSERT INTO gold_ai_model_registry VALUES (1)"\n',
        encoding="utf-8",
    )
    assert not _scan_write_contract([ok])


# --------------------------------------------------------------------------- #
# FR-3A-03 — Jinja autoescape                                                #
# --------------------------------------------------------------------------- #

def test_report_jinja_env_autoescape_enabled():
    from src.reporting.generator import _get_jinja_env

    assert _get_jinja_env().autoescape is True


def test_report_jinja_env_escapes_markup():
    """A value containing HTML markup is escaped (no injection)."""
    from src.reporting.generator import _get_jinja_env

    rendered = _get_jinja_env().from_string("{{ value }}").render(
        value="<script>x</script>"
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
