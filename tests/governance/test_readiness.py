"""Tests for the multi-tenancy readiness conformance check (Session 27, §H.9 / FR-4-26/27).

The real governance tree must pass (no tenant_id/RLS/SSO built; constants in
config), and the reusable scanner must actually fire on a planted violation — the
negative self-test that mirrors ``test_interpolation_guard_fires_on_violation``.
"""

from __future__ import annotations

from src.governance.readiness import _scan_governance_code, check_tenancy_readiness


def test_tenancy_readiness_passes_on_real_tree():
    """The shipped governance layer has no tenancy blockers (FR-4-26/27)."""
    assert check_tenancy_readiness() == []


def test_scan_fires_on_tenant_id_identifier(tmp_path):
    """Negative self-test: a tenant_id identifier in code is flagged."""
    bad = tmp_path / "bad_tenant.py"
    bad.write_text("def q(tenant_id):\n    return tenant_id\n", encoding="utf-8")
    violations = _scan_governance_code([str(bad)])
    assert violations, "guard failed to detect a tenant_id identifier"
    assert "tenant_id" in violations[0]


def test_scan_fires_on_sso_import(tmp_path):
    """Negative self-test: importing an SSO/SAML/OAuth library is flagged."""
    bad = tmp_path / "bad_sso.py"
    bad.write_text("import authlib\n", encoding="utf-8")
    violations = _scan_governance_code([str(bad)])
    assert violations and "authlib" in violations[0]


def test_scan_ignores_tenant_id_in_comments_and_strings(tmp_path):
    """A file that only *mentions* tenant_id in a string/comment is NOT flagged."""
    ok = tmp_path / "ok_mentions.py"
    ok.write_text(
        '# no tenant_id is built here\nMSG = "tenant_id would be additive"\n',
        encoding="utf-8",
    )
    assert _scan_governance_code([str(ok)]) == []


def test_check_flags_missing_config(tmp_path):
    """A missing governance config is itself a config-not-code violation."""
    missing = str(tmp_path / "nope.yaml")
    violations = check_tenancy_readiness(config_path=missing)
    assert any("missing" in v for v in violations)


# --------------------------------------------------------------------------- #
# Post-build review — close the scanner-evasion gaps (Session 27)             #
# --------------------------------------------------------------------------- #

def _scan(tmp_path, name, src) -> list:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return _scan_governance_code([str(p)])


def test_scan_fires_on_import_alias(tmp_path):
    """`from x import tenant_id` (an ast.alias) is caught."""
    assert _scan(tmp_path, "a.py", "from mod import tenant_id\n")


def test_scan_fires_on_attribute(tmp_path):
    """`self.tenant_id` (an ast.Attribute) is caught."""
    assert _scan(tmp_path, "b.py", "class C:\n    def f(self):\n        return self.tenant_id\n")


def test_scan_fires_on_getattr_string(tmp_path):
    """`getattr(x, 'tenant_id')` (string-keyed access) is caught."""
    v = _scan(tmp_path, "c.py", "def f(x):\n    return getattr(x, 'tenant_id')\n")
    assert v and "getattr" in v[0]


def test_scan_fires_on_dict_subscript(tmp_path):
    """`d['tenant_id']` (string subscript) is caught."""
    v = _scan(tmp_path, "d.py", "def f(d):\n    return d['tenant_id']\n")
    assert v and "subscript" in v[0]


def test_scan_fires_on_dynamic_sso_import(tmp_path):
    """`importlib.import_module('authlib')` is caught."""
    v = _scan(tmp_path, "e.py", "import importlib\nm = importlib.import_module('authlib')\n")
    assert v and "SSO" in v[0]


def test_scan_reports_unparseable_file_without_raising(tmp_path):
    """A syntactically-broken governance file is reported, not raised."""
    v = _scan(tmp_path, "f.py", "def broken(:\n")
    assert v and "unparseable" in v[0]


def test_check_flags_tenant_id_column_in_ddl(tmp_path):
    """The DDL branch fires when a governance table declares a tenant_id column."""
    fake_ddl = tmp_path / "db_init.py"
    fake_ddl.write_text(
        'DDL = "CREATE TABLE x (id VARCHAR, tenant_id INTEGER)"\n', encoding="utf-8"
    )
    violations = check_tenancy_readiness(db_init_path=str(fake_ddl))
    assert any("tenant_id" in v and "DDL" in v for v in violations)
