"""Multi-tenancy readiness conformance check (Session 27, §H.9).

Realises FR-4-26/27 and NFR-G-06: Phase 4 builds **no** multi-tenancy — no
``tenant_id``, no row-level security, no SSO — but the new governance layer must
be shaped so a future ``tenant_id`` retrofit is purely *additive*, and all
org-specific values must live in configuration, not code.

``check_tenancy_readiness`` is a test-style scan mirroring the FR-3A-02
SQL-interpolation guard in ``tests/test_ai_architecture.py``: it returns a list
of violation strings (empty = pass) and is wired as a pytest assertion in
``tests/governance/test_readiness.py``. The reusable scanner ``_scan_governance_code``
is exposed so the test's *negative self-test* can feed it a planted violation and
prove the guard actually fires.

The scan is deliberately identifier/import-based (not a naive text grep) so that
this module's own docstrings and the spec's explanatory prose — which necessarily
mention ``tenant_id`` / RLS / SSO to describe what is *absent* — never self-flag.
Only real code usage counts as "built".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from src.governance.users import DEFAULT_CONFIG_PATH

_GOV_DIR = "src/governance"
_DB_INIT_PATH = "src/utils/db_init.py"

# Third-party SSO / SAML / OAuth libraries that would indicate a login integration
# was actually built (readiness only — none may be imported by the governance layer).
_SSO_MODULES = {
    "saml", "python3_saml", "onelogin", "authlib", "oauthlib",
    "msal", "flask_saml", "django_saml2", "pysaml2", "saml2",
}


def _gov_py_files(gov_dir: str = _GOV_DIR) -> list:
    """All governance source files (sorted for stable output)."""
    return sorted(str(p) for p in Path(gov_dir).glob("*.py"))


def _is_tenant_id_const(node) -> bool:
    """True if ``node`` is the string constant ``"tenant_id"``."""
    return isinstance(node, ast.Constant) and node.value == "tenant_id"


def _scan_governance_code(py_files) -> list:
    """Return violation strings for single-org / tenancy blockers in code.

    Flags (via AST, so comments/docstrings/plain prose are ignored):
      * any identifier literally named ``tenant_id`` used in code — a Name,
        attribute (``self.tenant_id``), function arg, call keyword, **or an
        imported symbol** (``from x import tenant_id``);
      * a **string-keyed** tenant access that would drive a query even though it
        isn't an identifier — ``getattr(x, "tenant_id")`` or ``d["tenant_id"]``;
      * a known SSO/SAML/OAuth library imported statically **or** dynamically via
        ``importlib.import_module("<sso>")`` — SSO must not be built.

    A file that cannot be parsed is itself reported as a violation (a file the
    guard cannot clear is not clear). Note the remaining, documented limit: a
    tenancy access built entirely from *computed* strings the AST cannot see is
    out of scope — the DDL ``tenant_id``-column scan in ``check_tenancy_readiness``
    is the backstop for any real tenant column.
    """
    violations: list = []
    for path in py_files:
        src = Path(path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:  # a file we cannot parse cannot be cleared
            violations.append(f"{path}: unparseable — cannot clear ({exc})")
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.arg):
                name = node.arg
            elif isinstance(node, ast.keyword):
                name = node.arg
            elif isinstance(node, ast.alias):
                # `import tenant_id` / `from x import tenant_id [as y]`
                name = node.name
            if name == "tenant_id":
                violations.append(
                    f"{path}:{getattr(node, 'lineno', '?')}: 'tenant_id' identifier in code"
                )

            # String-keyed tenant access: getattr(x, "tenant_id") / d["tenant_id"]
            if isinstance(node, ast.Subscript) and _is_tenant_id_const(node.slice):
                violations.append(
                    f"{path}:{node.lineno}: 'tenant_id' string subscript in code"
                )
            if isinstance(node, ast.Call):
                func = node.func
                is_getattr = isinstance(func, ast.Name) and func.id == "getattr"
                if is_getattr and any(_is_tenant_id_const(a) for a in node.args):
                    violations.append(
                        f"{path}:{node.lineno}: getattr(..., 'tenant_id') in code"
                    )
                # importlib.import_module("<sso>") / import_module("<sso>")
                fn = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else ""
                )
                if fn == "import_module":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if arg.value.split(".")[0].lower() in _SSO_MODULES:
                                violations.append(
                                    f"{path}:{node.lineno}: dynamic SSO import ({arg.value})"
                                )

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                modules.extend(alias.name for alias in node.names)
                for mod in modules:
                    top = (mod or "").split(".")[0].lower()
                    if top in _SSO_MODULES:
                        violations.append(
                            f"{path}:{node.lineno}: SSO/SAML/OAuth import ({mod})"
                        )
    return violations


def check_tenancy_readiness(
    *,
    gov_dir: str = _GOV_DIR,
    db_init_path: str = _DB_INIT_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> list:
    """Return tenancy-readiness violations (empty list = pass; FR-4-26/27; NFR-G-06).

    Three checks:
      (a) **config-not-code** — the org-specific governance constants (roles,
          approval chain, materiality threshold + below-threshold level,
          retention, attestation text) must live in ``governance_config.yaml``,
          so a tenant retrofit edits config, not code;
      (b) **nothing tenant-related built** — no ``tenant_id`` identifier and no
          SSO/SAML/OAuth import anywhere in ``src/governance`` (via
          ``_scan_governance_code``);
      (c) **additive-retrofit shaping** — the governance DDL declares no
          ``tenant_id`` column, so introducing one later is purely additive.
    """
    violations: list = []

    # (b) code scan
    violations.extend(_scan_governance_code(_gov_py_files(gov_dir)))

    # (c) DDL must not already carry a tenant_id column
    ddl_text = Path(db_init_path).read_text(encoding="utf-8")
    if re.search(r"\btenant_id\b", ddl_text):
        violations.append(f"{db_init_path}: 'tenant_id' present in DDL (retrofit not additive)")

    # (a) config-not-code presence check
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        violations.append(
            f"{config_path}: governance config missing (constants would be code-bound)"
        )
        return violations
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    for key in ("roles", "approval_chain", "permissions", "attestation_text", "retention"):
        if not cfg.get(key):
            violations.append(f"{config_path}: '{key}' not in config (must be config-not-code)")
    materiality = cfg.get("materiality") or {}
    for mkey in ("delta_tev_threshold", "final_level_below_threshold"):
        if mkey not in materiality:
            violations.append(f"{config_path}: 'materiality.{mkey}' not in config")

    return violations
