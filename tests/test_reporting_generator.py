"""Unit tests for credibility-method helpers in src/reporting/generator.py.

These are pure-function tests (no DB): they confirm the report SQL and the
method label honour the run's credibility method (LF vs Bühlmann).
"""
from src.reporting.generator import _cred_z_sql, _cred_method_label


class TestCredZSql:
    def test_lf_expression(self):
        sql = _cred_z_sql("SUM(actual_deaths_count)", "LF")
        assert sql == "LEAST(1.0, SQRT(CAST(SUM(actual_deaths_count) AS DOUBLE) / 1082.0))"

    def test_buhlmann_expression(self):
        sql = _cred_z_sql("SUM(actual_deaths_count)", "BUHLMANN")
        n = "CAST(SUM(actual_deaths_count) AS DOUBLE)"
        assert sql == f"SQRT({n} / ({n} + 1082.0))"

    def test_method_case_insensitive(self):
        assert _cred_z_sql("SUM(x)", "buhlmann") == _cred_z_sql("SUM(x)", "BUHLMANN")

    def test_unknown_method_defaults_to_lf(self):
        assert _cred_z_sql("SUM(x)", "XYZ") == _cred_z_sql("SUM(x)", "LF")

    def test_lf_and_buhlmann_differ(self):
        assert _cred_z_sql("SUM(x)", "LF") != _cred_z_sql("SUM(x)", "BUHLMANN")


class TestCredMethodLabel:
    def test_lf_label(self):
        assert _cred_method_label("LF") == "Limited Fluctuation"

    def test_buhlmann_label(self):
        assert _cred_method_label("BUHLMANN") == "Bühlmann"

    def test_buhlmann_label_case_insensitive(self):
        assert _cred_method_label("buhlmann") == "Bühlmann"

    def test_unknown_defaults_to_lf_label(self):
        assert _cred_method_label("XYZ") == "Limited Fluctuation"
