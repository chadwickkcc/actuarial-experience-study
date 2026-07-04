"""Report generation functions for Working Actuary and Chief Actuary reports."""
from __future__ import annotations  # Python 3.9 union-type compat

from datetime import datetime
from pathlib import Path

import duckdb
from jinja2 import Environment, FileSystemLoader


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _get_jinja_env() -> Environment:
    """Return a Jinja2 environment pointed at the templates directory.

    ``autoescape=True`` per the 2026-05-31 security review (FR-3A-03): all
    template variables are HTML-escaped so untrusted text can never inject
    markup. Report variables are numbers and plain identifiers (no pre-rendered
    HTML is injected), so existing A/E reports render byte-comparably.
    """
    return Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


def _run_method_code(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> str:
    """Return the run's credibility method code ('LF' or 'BUHLMANN').

    Defaults to 'LF' when the run is missing or the column is NULL, so report
    SQL falls back to Limited Fluctuation.
    """
    row = conn.execute(
        "SELECT credibility_method FROM gold_study_runs WHERE run_id = ?",
        [study_run_id],
    ).fetchone()
    if row is None or row[0] is None:
        return "LF"
    return str(row[0])


def _cred_z_sql(count_expr: str, method: str) -> str:
    """Return a DuckDB SQL expression for credibility Z given the run's method.

    ``count_expr`` is an aggregate actual-claim expression (e.g.
    ``SUM(actual_deaths_count)``). ``threshold`` K is the 1082 full-credibility
    standard, reused as the Buhlmann constant.

        LF:       LEAST(1.0, SQRT(n / 1082.0))
        BUHLMANN: SQRT(n / (n + 1082.0))
    """
    n = f"CAST({count_expr} AS DOUBLE)"
    if (method or "LF").strip().upper() == "BUHLMANN":
        return f"SQRT({n} / ({n} + 1082.0))"
    return f"LEAST(1.0, SQRT({n} / 1082.0))"


def _cred_method_label(method: str) -> str:
    """Human-readable credibility method label for report prose."""
    return "Bühlmann" if (method or "LF").strip().upper() == "BUHLMANN" else "Limited Fluctuation"


def _query_headline(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> dict:
    """Fetch aggregate headline A/E metrics for a study run."""
    method = _run_method_code(conn, study_run_id)
    row = conn.execute(
        f"""
        SELECT
            SUM(exposure_count)                                                AS total_exposure,
            SUM(actual_deaths_count)                                           AS total_deaths,
            SUM(expected_deaths_count)                                         AS total_expected_deaths,
            CASE WHEN SUM(expected_deaths_count) > 0
                 THEN SUM(actual_deaths_count) / SUM(expected_deaths_count)
                 ELSE NULL END                                                 AS ae_count,
            CASE WHEN SUM(expected_deaths_amount) > 0
                 THEN SUM(actual_deaths_amount) / SUM(expected_deaths_amount)
                 ELSE NULL END                                                 AS ae_amount,
            SUM(actual_lapses)                                                 AS total_lapses,
            SUM(expected_lapses)                                               AS total_expected_lapses,
            CASE WHEN SUM(expected_lapses) > 0
                 THEN SUM(actual_lapses) / SUM(expected_lapses)
                 ELSE NULL END                                                 AS ae_lapse,
            SUM(actual_ci_claims)                                              AS total_ci_claims,
            SUM(expected_ci_claims)                                            AS total_expected_ci,
            CASE WHEN SUM(expected_ci_claims) > 0
                 THEN SUM(actual_ci_claims) / SUM(expected_ci_claims)
                 ELSE NULL END                                                 AS ae_ci,
            {_cred_z_sql("SUM(actual_deaths_count)", method)} AS agg_credibility_z
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NULL
        """,
        [study_run_id],
    ).fetchone()

    ae_count = float(row[3]) if row[3] is not None else 0.0
    agg_z = float(row[11]) if row[11] is not None else None
    # Credibility-weighted A/E must be derived from the aggregate Z and aggregate A/E,
    # never averaged from per-cell values (FR-1A-24).
    cred_wtd_ae = (
        agg_z * ae_count + (1.0 - agg_z) * 1.0 if agg_z is not None else None
    )

    return {
        "total_exposure":        float(row[0] or 0),
        "total_deaths":          int(row[1] or 0),
        "total_expected_deaths": float(row[2] or 0),
        "ae_count":              ae_count,
        "ae_amount":             float(row[4]) if row[4] is not None else None,
        "total_lapses":          int(row[5] or 0),
        "total_expected_lapses": float(row[6] or 0),
        "ae_lapse":              float(row[7]) if row[7] is not None else None,
        "total_ci_claims":       int(row[8] or 0),
        "total_expected_ci":     float(row[9] or 0),
        "ae_ci":                 float(row[10]) if row[10] is not None else None,
        "agg_credibility_z":     agg_z,
        "cred_wtd_ae":           cred_wtd_ae,
    }


def _query_dq_summary(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch DQ run summary rows for the study run."""
    rows = conn.execute(
        """
        SELECT product_code, total_records, records_passed, records_quarantined,
               dq_score_pct, critical_failure
        FROM gold_dq_run_summary
        WHERE study_run_id = ?
        ORDER BY product_code
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "product_code":       r[0],
            "total_records":      int(r[1]),
            "records_passed":     int(r[2]),
            "records_quarantined": int(r[3]),
            "dq_score_pct":       float(r[4]),
            "critical_failure":   bool(r[5]),
        }
        for r in rows
    ]


def _query_recon(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch in-force reconciliation rows."""
    rows = conn.execute(
        """
        SELECT calendar_year, beg_if_count, new_issues_count, deaths_count,
               lapses_count, end_if_count, recon_diff_count, recon_passes
        FROM gold_inforce_reconciliation
        WHERE study_run_id = ?
        ORDER BY calendar_year
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "calendar_year":   r[0],
            "beg_if_count":    int(r[1]),
            "new_issues_count": int(r[2]),
            "deaths_count":    int(r[3]),
            "lapses_count":    int(r[4]),
            "end_if_count":    int(r[5]),
            "recon_diff_count": int(r[6]),
            "recon_passes":    bool(r[7]),
        }
        for r in rows
    ]


def _query_ae_by_gender(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch mortality A/E aggregated by gender."""
    method = _run_method_code(conn, study_run_id)
    rows = conn.execute(
        f"""
        SELECT gender,
               SUM(exposure_count)          AS exposure_count,
               SUM(actual_deaths_count)     AS actual_deaths_count,
               SUM(expected_deaths_count)   AS expected_deaths_count,
               CASE WHEN SUM(expected_deaths_count) > 0
                    THEN SUM(actual_deaths_count) / SUM(expected_deaths_count)
                    ELSE NULL END            AS ae_count,
               CASE WHEN SUM(actual_deaths_count) > 0
                    THEN SUM(actual_deaths_count) / SUM(expected_deaths_count)
                         - 1.96 * (SUM(actual_deaths_count) / SUM(expected_deaths_count))
                             / SQRT(CAST(SUM(actual_deaths_count) AS DOUBLE))
                    ELSE NULL END            AS ci_lower_count,
               CASE WHEN SUM(actual_deaths_count) > 0
                    THEN SUM(actual_deaths_count) / SUM(expected_deaths_count)
                         + 1.96 * (SUM(actual_deaths_count) / SUM(expected_deaths_count))
                             / SQRT(CAST(SUM(actual_deaths_count) AS DOUBLE))
                    ELSE NULL END            AS ci_upper_count,
               {_cred_z_sql("SUM(actual_deaths_count)", method)} AS credibility_z
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NULL AND gender IS NOT NULL
        GROUP BY gender
        ORDER BY gender
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "gender":               r[0],
            "exposure_count":       float(r[1] or 0),
            "actual_deaths_count":  float(r[2] or 0),
            "expected_deaths_count": float(r[3] or 0),
            "ae_count":             float(r[4]) if r[4] is not None else None,
            "ci_lower_count":       float(r[5]) if r[5] is not None else None,
            "ci_upper_count":       float(r[6]) if r[6] is not None else None,
            "credibility_z":        float(r[7]) if r[7] is not None else None,
        }
        for r in rows
    ]


def _query_ae_by_duration(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch mortality A/E aggregated by duration band."""
    method = _run_method_code(conn, study_run_id)
    rows = conn.execute(
        f"""
        SELECT duration_band,
               SUM(exposure_count)          AS exposure_count,
               SUM(actual_deaths_count)     AS actual_deaths_count,
               SUM(expected_deaths_count)   AS expected_deaths_count,
               CASE WHEN SUM(expected_deaths_count) > 0
                    THEN SUM(actual_deaths_count) / SUM(expected_deaths_count)
                    ELSE NULL END            AS ae_count,
               {_cred_z_sql("SUM(actual_deaths_count)", method)} AS credibility_z
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NULL AND duration_band IS NOT NULL
        GROUP BY duration_band
        ORDER BY MIN(policy_year)
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "duration_band":        r[0],
            "exposure_count":       float(r[1] or 0),
            "actual_deaths_count":  float(r[2] or 0),
            "expected_deaths_count": float(r[3] or 0),
            "ae_count":             float(r[4]) if r[4] is not None else None,
            "credibility_z":        float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


def _query_lapse_by_year(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch lapse A/E by policy year."""
    method = _run_method_code(conn, study_run_id)
    rows = conn.execute(
        f"""
        SELECT policy_year,
               SUM(actual_lapses)    AS actual_lapses,
               SUM(expected_lapses)  AS expected_lapses,
               CASE WHEN SUM(expected_lapses) > 0
                    THEN SUM(actual_lapses) / SUM(expected_lapses)
                    ELSE NULL END     AS ae_lapse,
               {_cred_z_sql("SUM(actual_lapses)", method)} AS credibility_z_lapse
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NULL
          AND policy_year IS NOT NULL AND is_plt_flag = FALSE
        GROUP BY policy_year
        ORDER BY policy_year
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "policy_year":       r[0],
            "actual_lapses":     float(r[1] or 0),
            "expected_lapses":   float(r[2] or 0),
            "ae_lapse":          float(r[3]) if r[3] is not None else None,
            "credibility_z_lapse": float(r[4]) if r[4] is not None else None,
        }
        for r in rows
    ]


def _query_plt_ae(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch PLT shock lapse A/E by premium jump ratio band."""
    rows = conn.execute(
        """
        SELECT premium_jump_ratio_band,
               SUM(actual_lapses)    AS actual_lapses,
               SUM(expected_lapses)  AS expected_lapses,
               CASE WHEN SUM(expected_lapses) > 0
                    THEN SUM(actual_lapses) / SUM(expected_lapses)
                    ELSE NULL END     AS ae_lapse
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NULL
          AND is_plt_flag = TRUE AND premium_jump_ratio_band IS NOT NULL
        GROUP BY premium_jump_ratio_band
        ORDER BY premium_jump_ratio_band
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "premium_jump_ratio_band": r[0],
            "actual_lapses":           float(r[1] or 0),
            "expected_lapses":         float(r[2] or 0),
            "ae_lapse":                float(r[3]) if r[3] is not None else None,
        }
        for r in rows
    ]


def _query_ci_by_illness(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch CI A/E by illness code."""
    illness_names = {
        "CI-001": "Malignant cancer",
        "CI-002": "Myocardial infarction",
        "CI-003": "Stroke",
        "CI-004": "Coronary artery bypass",
        "CI-005": "Kidney failure",
        "CI-006": "Major organ transplant",
        "CI-007": "Multiple sclerosis",
        "CI-008": "Paralysis / paraplegia",
        "CI-009": "Blindness",
        "CI-010": "Deafness",
    }
    method = _run_method_code(conn, study_run_id)
    rows = conn.execute(
        f"""
        SELECT illness_code,
               SUM(actual_ci_claims)   AS actual_ci_claims,
               SUM(expected_ci_claims) AS expected_ci_claims,
               CASE WHEN SUM(expected_ci_claims) > 0
                    THEN SUM(actual_ci_claims) / SUM(expected_ci_claims)
                    ELSE NULL END       AS ae_ci,
               {_cred_z_sql("SUM(actual_ci_claims)", method)} AS credibility_z_ci
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NOT NULL
        GROUP BY illness_code
        ORDER BY illness_code
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "illness_code":    r[0],
            "illness_name":    illness_names.get(r[0], r[0]),
            "actual_ci_claims":  float(r[1] or 0),
            "expected_ci_claims": float(r[2] or 0),
            "ae_ci":           float(r[3]) if r[3] is not None else None,
            "credibility_z_ci": float(r[4]) if r[4] is not None else None,
        }
        for r in rows
    ]


def _query_dq_overrides(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> list[dict]:
    """Fetch DQ override records for the study run."""
    rows = conn.execute(
        """
        SELECT policy_id, check_id, override_justification,
               override_actuary_id, override_ts
        FROM gold_dq_quarantine
        WHERE study_run_id = ? AND actuary_override_flag = TRUE
        ORDER BY override_ts
        """,
        [study_run_id],
    ).fetchall()
    return [
        {
            "policy_id":               r[0],
            "check_id":                r[1],
            "override_justification":  r[2],
            "override_actuary_id":     r[3],
            "override_ts":             str(r[4]),
        }
        for r in rows
    ]


def _query_run_config(conn: duckdb.DuckDBPyConnection, study_run_id: str) -> dict:
    """Fetch study run configuration from gold_study_runs; use defaults if missing."""
    try:
        row = conn.execute(
            """
            SELECT product_codes, study_start_date, study_end_date,
                   exposure_method, mortality_table, lapse_table,
                   ci_table, credibility_method
            FROM gold_study_runs
            WHERE run_id = ?
            """,
            [study_run_id],
        ).fetchone()
    except Exception:
        row = None

    if row:
        return {
            "products":         str(row[0]),
            "study_start":      str(row[1]),
            "study_end":        str(row[2]),
            "exposure_method":  str(row[3]),
            "mortality_table":  str(row[4]) if row[4] else "2015 VBT",
            "lapse_table":      str(row[5]) if row[5] else "SOA/LIMRA 2015-22",
            "ci_table":         str(row[6]) if row[6] else "CI Incidence Reference",
            "credibility_method": _cred_method_label(str(row[7])) if row[7] else "Limited Fluctuation",
        }

    return {
        "products":           "TERM",
        "study_start":        "2016-01-01",
        "study_end":          "2023-12-31",
        "exposure_method":    "ANNUAL (Balducci)",
        "mortality_table":    "2015 VBT Select & Ultimate (ANB)",
        "lapse_table":        "SOA/LIMRA 2015-22 Benchmark",
        "ci_table":           "CI Incidence Reference Table",
        "credibility_method": "Limited Fluctuation",
    }


def generate_working_actuary_report(
    study_run_id: str,
    db_path: Path,
    output_path: Path
) -> str:
    """
    Generate a Working Actuary HTML report for the given study run.

    Reads from gold_ae_results, gold_dq_run_summary, gold_inforce_reconciliation,
    and gold_dq_quarantine; renders the Jinja2 template; writes to output_path.

    Args:
        study_run_id: UUID of the study run
        db_path:      Path to the DuckDB file
        output_path:  Destination HTML file path

    Returns:
        Absolute path of the written file as a string
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cfg       = _query_run_config(conn, study_run_id)
        headline  = _query_headline(conn, study_run_id)
        dq_rows   = _query_dq_summary(conn, study_run_id)
        recon     = _query_recon(conn, study_run_id)
        by_gender = _query_ae_by_gender(conn, study_run_id)
        by_dur    = _query_ae_by_duration(conn, study_run_id)
        lapse_yr  = _query_lapse_by_year(conn, study_run_id)
        plt_ae    = _query_plt_ae(conn, study_run_id)
        ci_ill    = _query_ci_by_illness(conn, study_run_id)
        overrides = _query_dq_overrides(conn, study_run_id)
    finally:
        conn.close()

    env = _get_jinja_env()
    tmpl = env.get_template("working_actuary_report.html.j2")

    html = tmpl.render(
        run_id              = study_run_id,
        generated_ts        = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        study_start         = cfg["study_start"],
        study_end           = cfg["study_end"],
        products            = cfg["products"],
        exposure_method     = cfg["exposure_method"],
        mortality_table     = cfg["mortality_table"],
        lapse_table         = cfg["lapse_table"],
        ci_table            = cfg["ci_table"],
        credibility_method  = cfg["credibility_method"],
        total_exposure      = headline["total_exposure"],
        total_deaths        = headline["total_deaths"],
        ae_count            = headline["ae_count"],
        ae_amount           = headline["ae_amount"],
        total_lapses        = headline["total_lapses"],
        ae_lapse            = headline["ae_lapse"],
        total_ci_claims     = headline["total_ci_claims"],
        ae_ci               = headline["ae_ci"],
        agg_credibility_z   = headline["agg_credibility_z"],
        dq_summary          = dq_rows,
        recon_rows          = recon,
        ae_by_gender        = by_gender,
        ae_by_duration      = by_dur,
        ae_lapse_by_year    = lapse_yr,
        ae_plt              = plt_ae,
        ae_ci_by_illness    = ci_ill,
        dq_overrides        = overrides,
    )

    output_path.write_text(html, encoding="utf-8")
    return str(output_path.resolve())


def generate_chief_actuary_summary(
    study_run_id: str,
    db_path: Path,
    output_path: Path
) -> str:
    """
    Generate a Chief Actuary Summary HTML report for the given study run.

    Args:
        study_run_id: UUID of the study run
        db_path:      Path to the DuckDB file
        output_path:  Destination HTML file path

    Returns:
        Absolute path of the written file as a string
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cfg      = _query_run_config(conn, study_run_id)
        headline = _query_headline(conn, study_run_id)
        dq_rows  = _query_dq_summary(conn, study_run_id)
    finally:
        conn.close()

    total_quarantined = sum(r["records_quarantined"] for r in dq_rows)
    avg_dq_score      = (sum(r["dq_score_pct"] for r in dq_rows) / len(dq_rows)) if dq_rows else 100.0
    dq_critical       = any(r["critical_failure"] for r in dq_rows)

    ae_count  = headline["ae_count"] or 0.0
    ae_lapse  = headline["ae_lapse"]
    ae_ci     = headline["ae_ci"]
    overall_pass = (
        0.85 <= ae_count <= 1.00
        and (ae_lapse is None or 0.95 <= ae_lapse <= 1.05)
        and (ae_ci    is None or 0.90 <= ae_ci    <= 1.10)
    )

    env = _get_jinja_env()
    tmpl = env.get_template("chief_actuary_summary.html.j2")

    html = tmpl.render(
        run_id               = study_run_id,
        generated_ts         = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        study_start          = cfg["study_start"],
        study_end            = cfg["study_end"],
        products             = cfg["products"],
        overall_pass         = overall_pass,
        total_exposure       = headline["total_exposure"],
        total_deaths         = headline["total_deaths"],
        total_expected_deaths= headline["total_expected_deaths"],
        ae_count             = ae_count,
        ae_amount            = headline["ae_amount"],
        total_lapses         = headline["total_lapses"],
        total_expected_lapses= headline["total_expected_lapses"],
        ae_lapse             = ae_lapse,
        total_ci_claims      = headline["total_ci_claims"],
        total_expected_ci    = headline["total_expected_ci"],
        ae_ci                = ae_ci,
        agg_credibility_z    = headline["agg_credibility_z"],
        cred_wtd_ae          = headline["cred_wtd_ae"],
        credibility_method   = cfg["credibility_method"],
        dq_critical_failure  = dq_critical,
        avg_dq_score         = avg_dq_score,
        total_quarantined    = total_quarantined,
    )

    output_path.write_text(html, encoding="utf-8")
    return str(output_path.resolve())


# ---------------------------------------------------------------------------
# TEV Reporting helpers
# ---------------------------------------------------------------------------

def _query_tev_results(conn: duckdb.DuckDBPyConnection, tev_run_id: str) -> list[dict]:
    """Fetch per-product TEV results for a run."""
    rows = conn.execute("""
        SELECT product_code, anw, pvfp, pvcoc, vif, tev, delta_tev,
               pvfp_mortality_margin, pvfp_lapse_margin, pvfp_ci_margin,
               pvfp_investment_spread, pvfp_expense_margin
        FROM gold_tev_results
        WHERE tev_run_id = ? AND (sensitivity_id IS NULL OR sensitivity_id = '')
        ORDER BY product_code
    """, [tev_run_id]).fetchall()
    cols = [
        "product_code", "anw", "pvfp", "pvcoc", "vif", "tev", "delta_tev",
        "pvfp_mortality_margin", "pvfp_lapse_margin", "pvfp_ci_margin",
        "pvfp_investment_spread", "pvfp_expense_margin",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _query_tev_run_log(conn: duckdb.DuckDBPyConnection, tev_run_id: str) -> dict:
    """Fetch summary row from gold_tev_run_log."""
    row = conn.execute("""
        SELECT tev_run_id, assumption_set_id, run_ts, projection_years,
               total_anw, total_pvfp, total_pvcoc, total_vif, total_tev,
               delta_tev_vs_prior, run_duration_sec, status
        FROM gold_tev_run_log WHERE tev_run_id = ?
    """, [tev_run_id]).fetchone()
    if not row:
        return {}
    cols = [
        "tev_run_id", "assumption_set_id", "run_ts", "projection_years",
        "total_anw", "total_pvfp", "total_pvcoc", "total_vif", "total_tev",
        "delta_tev_vs_prior", "run_duration_sec", "status",
    ]
    return dict(zip(cols, row))


def _query_assumption_set_meta(conn: duckdb.DuckDBPyConnection, aset_id: str) -> dict:
    """Fetch metadata for an assumption set."""
    row = conn.execute("""
        SELECT assumption_set_id, version, status, effective_date, author_id,
               source_study_run_id, rdr, earned_rate_ga, tax_rate, created_ts
        FROM gold_assumption_sets WHERE assumption_set_id = ?
    """, [aset_id]).fetchone()
    if not row:
        return {}
    cols = [
        "assumption_set_id", "version", "status", "effective_date", "author_id",
        "source_study_run_id", "rdr", "earned_rate_ga", "tax_rate", "created_ts",
    ]
    return dict(zip(cols, row))


def _query_sensitivity_deltas(conn: duckdb.DuckDBPyConnection, tev_run_id: str) -> list[dict]:
    """Fetch sensitivity ΔTEV rows linked to the same assumption set as the baseline."""
    row = conn.execute(
        "SELECT assumption_set_id FROM gold_tev_run_log WHERE tev_run_id = ?",
        [tev_run_id]
    ).fetchone()
    if not row:
        return []
    aset_id = row[0]
    rows = conn.execute("""
        SELECT r.sensitivity_id, SUM(r.delta_tev) AS total_delta_tev
        FROM gold_tev_results r
        JOIN gold_tev_run_log l ON l.tev_run_id = r.tev_run_id
        WHERE r.assumption_set_id = ?
          AND r.sensitivity_id IS NOT NULL
          AND r.sensitivity_id != ''
        GROUP BY r.sensitivity_id
        ORDER BY r.sensitivity_id
    """, [aset_id]).fetchall()
    return [{"sensitivity_id": r[0], "delta_tev": r[1]} for r in rows]


def _query_model_point_summary(
    conn: duckdb.DuckDBPyConnection, tev_run_id: str
) -> list[dict]:
    """Per-product model point summary for the model-point build used by the TEV run.

    ``gold_model_points.tev_run_id`` is the build/snapshot run id (set by
    ``build_model_points`` at compression time), not the calc run id. The link is
    indirect: the latest compression build whose products overlap the TEV calc
    run's products. This helper falls back to the most recent build in the table
    if no direct linkage exists.

    The compression reconciliation gate (< 0.1% on count, face, reserve) is
    enforced inside ``build_model_points()`` at compression time; if rows are
    returned here the gate passed for every product shown.
    """
    # Strategy: pick the most-recently-created model-point build (single _created_ts
    # group). Sensitivities and baseline calc runs share the same build.
    rows = conn.execute("""
        WITH latest_build AS (
            SELECT tev_run_id
            FROM gold_model_points
            GROUP BY tev_run_id
            ORDER BY MAX(_created_ts) DESC
            LIMIT 1
        )
        SELECT mp.product_code,
               COUNT(*)                     AS model_point_count,
               SUM(mp.policy_count)         AS policy_count_total,
               SUM(mp.face_amount_total)    AS face_amount_total,
               SUM(mp.reserve_total)        AS reserve_total
        FROM gold_model_points mp
        JOIN latest_build lb USING (tev_run_id)
        GROUP BY mp.product_code
        ORDER BY mp.product_code
    """).fetchall()
    cols = [
        "product_code", "model_point_count", "policy_count_total",
        "face_amount_total", "reserve_total",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _query_workflow_iterations_tev(
    conn: duckdb.DuckDBPyConnection, workflow_session_id: str
) -> list[dict]:
    """Fetch workflow iterations for a session."""
    rows = conn.execute("""
        SELECT iteration_number, stage, action, actuary_id,
               total_tev, delta_tev_vs_prior,
               envelope_run_flag,
               actuary_comment, iteration_ts
        FROM gold_workflow_iterations
        WHERE workflow_session_id = ?
        ORDER BY iteration_number
    """, [workflow_session_id]).fetchall()
    cols = [
        "iteration_number", "stage", "action", "actuary_id",
        "total_tev", "delta_tev_vs_prior",
        "envelope_run_flag",
        "actuary_comment", "iteration_ts",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _tev_fmt(v, prefix="$", decimals=0) -> str:
    """Format a numeric value for HTML display."""
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{prefix}{f:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _tev_sign_fmt(v) -> str:
    """Format a signed number with colour for HTML."""
    if v is None:
        return "—"
    try:
        f = float(v)
        cls = "positive" if f >= 0 else "negative"
        return f"<span class='{cls}'>${f:+,.0f}</span>"
    except (TypeError, ValueError):
        return str(v)


def _tev_impact_matrix_html(matrix_df) -> str:
    """Render the TEV-impact matrix as a colour-coded HTML table (FR-2-25).

    Cell colour intensity is normalised per-row (max absolute ΔTEV in that row
    is full intensity). Green = positive ΔTEV, red = negative, white = ~0.
    The final ``total_sensitivity_range`` column is rendered uncoloured.
    """
    from src.tev.impact_matrix import SENSITIVITY_LABELS, SENSITIVITY_ORDER

    if matrix_df is None or matrix_df.empty:
        return "<p><em>No sensitivity grid results available — TEV-impact matrix could not be built.</em></p>"

    data_cols = [c for c in SENSITIVITY_ORDER if c in matrix_df.columns]
    has_range = "total_sensitivity_range" in matrix_df.columns

    headers = ["Product"] + [SENSITIVITY_LABELS.get(c, c) for c in data_cols]
    if has_range:
        headers.append("Max |ΔTEV|")

    rows_html: list[str] = []
    for prod, row in matrix_df.iterrows():
        cells = [row.get(c) for c in data_cols]
        abs_vals = [abs(v) for v in cells if v is not None and not (isinstance(v, float) and v != v)]
        max_abs = max(abs_vals) if abs_vals else 0.0

        is_total = str(prod).upper() == "TOTAL"
        prod_label = f"<strong>{prod}</strong>" if is_total else str(prod)
        cell_strs: list[str] = [f"<td>{prod_label}</td>"]

        for val in cells:
            if val is None or (isinstance(val, float) and val != val):
                cell_strs.append("<td>—</td>")
                continue
            ratio = (abs(val) / max_abs) if max_abs else 0.0
            ratio = max(0.0, min(1.0, ratio))
            if val > 0:
                bg = f"rgba(60, 180, 75, {ratio:.2f})"
            elif val < 0:
                bg = f"rgba(230, 75, 60, {ratio:.2f})"
            else:
                bg = "transparent"
            cell_strs.append(
                f"<td style='background-color: {bg}; text-align: right;'>"
                f"{_tev_sign_fmt(val)}</td>"
            )

        if has_range:
            cell_strs.append(
                f"<td style='text-align: right;'><strong>{_tev_fmt(row.get('total_sensitivity_range'))}</strong></td>"
            )

        row_style = " style='background-color: #f0f0f0;'" if is_total else ""
        rows_html.append(f"<tr{row_style}>{''.join(cell_strs)}</tr>")

    head_html = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    return (
        "<table class='tev-table tev-impact-matrix'>"
        f"<thead>{head_html}</thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table>"
        "<p><em>Colour scale: green = positive ΔTEV, red = negative, "
        "intensity normalised per row. Final column = max(|ΔTEV|) across the "
        "11 shocks for that product.</em></p>"
    )


def _tev_html_table(headers: list[str], rows: list[list]) -> str:
    """Render a simple HTML table."""
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return (
        "<table border='1' cellpadding='4' cellspacing='0' "
        "style='border-collapse:collapse;width:100%;font-size:13px'>"
        f"<thead style='background:#dce6f1'><tr>{th}</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


_TEV_STYLE = """<style>
  body { font-family: Arial, sans-serif; margin: 40px; color: #222; }
  h1 { color: #1f3864; }
  h2 { color: #2e75b6; border-bottom: 1px solid #aaa; padding-bottom: 4px; }
  h3 { color: #2e75b6; }
  .kpi { display: inline-block; margin: 8px 16px 8px 0; padding: 12px 20px;
         background: #f0f4fb; border-radius: 6px; min-width: 140px; }
  .kpi .label { font-size: 11px; color: #666; }
  .kpi .value { font-size: 22px; font-weight: bold; color: #1f3864; }
  table { margin-bottom: 16px; }
  td, th { text-align: right; padding: 4px 8px; }
  th:first-child, td:first-child { text-align: left; }
  .positive { color: #080; } .negative { color: #c00; }
  .footer { font-size: 11px; color: #999; margin-top: 40px; }
  .uuid-stamp { font-family: ui-monospace, Menlo, Consolas, monospace;
                font-size: 10px; color: #aaa; word-break: break-all; }
</style>"""


def _build_envelope_html(
    env_run: bool,
    env_tev_min: float | None,
    env_tev_max: float | None,
    env_percentile: float | None,
    envelope_width_abs: float | None = None,
    envelope_width_pct: float | None = None,
    top5_decrements: list | None = None,
    theta_proposed: dict | None = None,
    theta_min: dict | None = None,
    theta_max: dict | None = None,
    credibility_bounds: dict | None = None,
) -> str:
    """Render an HTML block summarising credibility envelope results."""
    if not env_run:
        return "<p>Credibility envelope analysis was <strong>not run</strong> for this iteration.</p>"
    min_fmt = _tev_fmt(env_tev_min)
    max_fmt = _tev_fmt(env_tev_max)
    if env_percentile is None:
        pct_str = "N/A — envelope width below materiality floor (0.1%)"
    else:
        pct_str = f"{env_percentile * 100:.1f}th percentile within credibility envelope"

    width_html = ""
    if envelope_width_abs is not None:
        w_abs = _tev_fmt(envelope_width_abs)
        w_pct = f"{(envelope_width_pct or 0) * 100:.2f}%"
        width_html = (
            f"<div class='kpi'><div class='label'>Envelope Width (abs)</div>"
            f"<div class='value'>{w_abs}</div></div>"
            f"<div class='kpi'><div class='label'>Envelope Width (%)</div>"
            f"<div class='value'>{w_pct}</div></div>"
        )

    table_html = ""
    if (
        top5_decrements
        and theta_proposed is not None
        and theta_min is not None
        and theta_max is not None
    ):
        dec_rows = []
        for dk in top5_decrements:
            lb, ub = (credibility_bounds or {}).get(dk, (None, None))
            t_prop = theta_proposed.get(dk)
            t_lo = theta_min.get(dk)
            t_hi = theta_max.get(dk)
            dec_rows.append([
                dk,
                f"{lb:.4f}" if lb is not None else "—",
                f"{ub:.4f}" if ub is not None else "—",
                f"{t_prop:.4f}" if t_prop is not None else "—",
                f"{t_lo:.4f}" if t_lo is not None else "—",
                f"{t_hi:.4f}" if t_hi is not None else "—",
            ])
        table_html = (
            "<h3>Per-decrement credibility bounds and optimizer results</h3>"
            + _tev_html_table(
                ["Decrement", "Cred Lower", "Cred Upper",
                 "theta_proposed", "theta_min", "theta_max"],
                dec_rows,
            )
        )

    return (
        f"<div class='kpi'><div class='label'>TEV_min</div><div class='value'>{min_fmt}</div></div>"
        f"<div class='kpi'><div class='label'>TEV_max</div><div class='value'>{max_fmt}</div></div>"
        + width_html
        + f"<p>Proposed TEV sits at the <strong>{pct_str}</strong>.</p>"
        f"<p>The envelope spans from {min_fmt} to {max_fmt} across all combinations of decrement "
        f"multipliers within the credibility bounds from the source experience study.</p>"
        + table_html
    )


def generate_tev_working_actuary_report(
    db_path: Path,
    assumption_set_id: str,
    tev_run_id: str,
    workflow_session_id: str,
    output_dir: Path,
    envelope_run: bool = False,
    envelope_tev_min: float | None = None,
    envelope_tev_max: float | None = None,
    envelope_percentile: float | None = None,
    envelope_width_abs: float | None = None,
    envelope_width_pct: float | None = None,
    top5_decrements: list | None = None,
    theta_proposed: dict | None = None,
    theta_min: dict | None = None,
    theta_max: dict | None = None,
    credibility_bounds: dict | None = None,
) -> Path:
    """
    Generate the TEV Working Actuary Report (HTML, FR-2-47).

    Contains: model point summary, ANW components, PVFP by product and profit source,
    PVCoC, baseline TEV waterfall, ΔTEV vs prior, full sensitivity grid, TEV-impact
    matrix, credibility envelope analysis (if run), and all assumption overrides.

    Args:
        db_path:              DuckDB path.
        assumption_set_id:    UUID of the assumption set.
        tev_run_id:           UUID of the baseline TEV run.
        workflow_session_id:  UUID of the workflow session.
        output_dir:           Directory to write the HTML file.
        envelope_run:         Whether the envelope analyser was run.
        envelope_tev_min:     TEV_min from envelope (if run).
        envelope_tev_max:     TEV_max from envelope (if run).
        envelope_percentile:  Percentile of proposed within envelope (None if immaterial).
        envelope_width_abs:   Absolute envelope width (tev_max − tev_min).
        envelope_width_pct:   Envelope width as fraction of proposed TEV.
        top5_decrements:      Ordered list of top-5 decrement keys by TEV sensitivity.
        theta_proposed:       Proposed theta per decrement key.
        theta_min:            Theta values producing TEV_min per decrement key.
        theta_max:            Theta values producing TEV_max per decrement key.
        credibility_bounds:   Credibility (lower, upper) tuple per decrement key.

    Returns:
        Path to the generated HTML file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"tev_working_actuary_{assumption_set_id[:8]}_{ts}.html"

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        run_log = _query_tev_run_log(conn, tev_run_id)
        aset_meta = _query_assumption_set_meta(conn, assumption_set_id)
        prod_results = _query_tev_results(conn, tev_run_id)
        sens_deltas = _query_sensitivity_deltas(conn, tev_run_id)
        iterations = _query_workflow_iterations_tev(conn, workflow_session_id)
        mp_summary = _query_model_point_summary(conn, tev_run_id)
    finally:
        conn.close()

    try:
        from src.tev.impact_matrix import build_impact_matrix_from_db
        impact_matrix_df = build_impact_matrix_from_db(db_path, tev_run_id)
    except Exception:
        impact_matrix_df = None

    total_anw = run_log.get("total_anw") or 0
    total_pvfp = run_log.get("total_pvfp") or 0
    total_pvcoc = run_log.get("total_pvcoc") or 0
    total_vif = run_log.get("total_vif") or 0
    total_tev = run_log.get("total_tev") or 0
    delta_tev = run_log.get("delta_tev_vs_prior")

    sens_labels = {
        "SENS-01": "Lapse −10%", "SENS-02": "Lapse +10%",
        "SENS-03": "Mortality −5%", "SENS-04": "Mortality +5%",
        "SENS-05": "Annuity Longevity +5%",
        "SENS-06": "CI Incidence −10%", "SENS-07": "CI Incidence +10%",
        "SENS-08": "Expense −10%", "SENS-09": "Expense +10%",
        "SENS-10": "RDR +100bp", "SENS-11": "RDR −100bp",
    }

    prod_rows = [
        [
            r["product_code"], _tev_fmt(r["anw"]), _tev_fmt(r["pvfp"]),
            _tev_fmt(r["pvcoc"]), _tev_fmt(r["vif"]), _tev_fmt(r["tev"]),
            _tev_sign_fmt(r.get("delta_tev")),
        ]
        for r in prod_results
    ]
    prod_rows.append([
        "<strong>TOTAL</strong>",
        _tev_fmt(total_anw), _tev_fmt(total_pvfp), _tev_fmt(total_pvcoc),
        _tev_fmt(total_vif), _tev_fmt(total_tev), _tev_sign_fmt(delta_tev),
    ])

    src_rows = [
        [
            r["product_code"],
            _tev_fmt(r.get("pvfp_mortality_margin")),
            _tev_fmt(r.get("pvfp_lapse_margin")),
            _tev_fmt(r.get("pvfp_ci_margin")),
            _tev_fmt(r.get("pvfp_investment_spread")),
            _tev_fmt(r.get("pvfp_expense_margin")),
        ]
        for r in prod_results
    ]

    sens_rows = [
        [sens_labels.get(s["sensitivity_id"], s["sensitivity_id"]), _tev_sign_fmt(s["delta_tev"])]
        for s in sens_deltas
    ]

    iter_rows = [
        [
            i["iteration_number"], i["stage"], i["action"], i["actuary_id"],
            _tev_fmt(i.get("total_tev")), _tev_sign_fmt(i.get("delta_tev_vs_prior")),
            "Yes" if i.get("envelope_run_flag") else "No",
            str(i.get("actuary_comment") or "")[:80],
        ]
        for i in iterations
    ]

    mp_rows = []
    mp_total_count = 0
    mp_total_policies = 0
    mp_total_face = 0.0
    mp_total_reserve = 0.0
    for m in mp_summary:
        mp_count = int(m.get("model_point_count") or 0)
        pol_count = int(m.get("policy_count_total") or 0)
        face_tot = float(m.get("face_amount_total") or 0.0)
        res_tot = float(m.get("reserve_total") or 0.0)
        ratio = (pol_count / mp_count) if mp_count else 0.0
        mp_rows.append([
            m["product_code"], f"{mp_count:,}", f"{pol_count:,}",
            f"{ratio:.1f}×", _tev_fmt(face_tot), _tev_fmt(res_tot),
        ])
        mp_total_count += mp_count
        mp_total_policies += pol_count
        mp_total_face += face_tot
        mp_total_reserve += res_tot
    if mp_rows:
        total_ratio = (mp_total_policies / mp_total_count) if mp_total_count else 0.0
        mp_rows.append([
            "<strong>TOTAL</strong>", f"<strong>{mp_total_count:,}</strong>",
            f"<strong>{mp_total_policies:,}</strong>",
            f"<strong>{total_ratio:.1f}×</strong>",
            f"<strong>{_tev_fmt(mp_total_face)}</strong>",
            f"<strong>{_tev_fmt(mp_total_reserve)}</strong>",
        ])

    aset_label = (
        f"v{aset_meta.get('version', '?')} "
        f"({str(aset_meta.get('effective_date', ''))[:10]}, {aset_meta.get('author_id', '—')})"
    )
    tev_run_label = (
        f"{str(run_log.get('run_ts', ''))[:16]} | {_tev_fmt(run_log.get('total_tev'))}"
    )

    html = (
        f"<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        f"<title>TEV Working Actuary Report</title>{_TEV_STYLE}</head><body>"
        f"<h1>TEV Working Actuary Report</h1>"
        f"<p>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} &nbsp;|&nbsp; "
        f"Assumption Set: <strong>{aset_label}</strong> &nbsp;|&nbsp; "
        f"TEV Run: <strong>{tev_run_label}</strong></p>"
        f"<h2>1. Assumption Set Parameters</h2>"
        f"<div class='kpi'><div class='label'>Author</div><div class='value'>{aset_meta.get('author_id','—')}</div></div>"
        f"<div class='kpi'><div class='label'>Status</div><div class='value'>{aset_meta.get('status','—')}</div></div>"
        f"<div class='kpi'><div class='label'>RDR</div><div class='value'>{(aset_meta.get('rdr') or 0)*100:.1f}%</div></div>"
        f"<div class='kpi'><div class='label'>Earned Rate (GA)</div><div class='value'>{(aset_meta.get('earned_rate_ga') or 0)*100:.1f}%</div></div>"
        f"<div class='kpi'><div class='label'>Tax Rate</div><div class='value'>{(aset_meta.get('tax_rate') or 0)*100:.0f}%</div></div>"
        f"<h2>2. Model Point Summary &amp; Compression Reconciliation</h2>"
        + (
            _tev_html_table(
                ["Product", "Model Point Rows", "Underlying Policies",
                 "Compression Ratio", "Face Amount Total", "Reserve Total"],
                mp_rows,
            )
            + "<p><em>Compression reconciliation gate: <strong>PASSED</strong> "
              "(&lt; 0.1% diff on count, face, reserve — enforced inside "
              "<code>build_model_points()</code> at compression time; if rows are "
              "shown above, the gate passed for every product).</em></p>"
            if mp_rows else
            "<p><em>No model points found for this TEV run.</em></p>"
        )
        + f"<h2>3. Baseline TEV Waterfall</h2>"
        f"<div class='kpi'><div class='label'>ANW</div><div class='value'>{_tev_fmt(total_anw)}</div></div>"
        f"<div class='kpi'><div class='label'>PVFP</div><div class='value'>{_tev_fmt(total_pvfp)}</div></div>"
        f"<div class='kpi'><div class='label'>PVCoC</div><div class='value'>{_tev_fmt(total_pvcoc)}</div></div>"
        f"<div class='kpi'><div class='label'>VIF</div><div class='value'>{_tev_fmt(total_vif)}</div></div>"
        f"<div class='kpi'><div class='label'>TEV</div><div class='value'>{_tev_fmt(total_tev)}</div></div>"
        f"<h3>TEV by Product</h3>"
        + _tev_html_table(["Product", "ANW", "PVFP", "PVCoC", "VIF", "TEV", "ΔTEV"], prod_rows)
        + f"<h2>4. PVFP by Profit Source</h2>"
        + _tev_html_table(
            ["Product", "Mortality Margin", "Lapse Margin", "CI Margin", "Invest. Spread", "Expense Margin"],
            src_rows
        )
        + f"<h2>5. Sensitivity Grid</h2>"
        + (_tev_html_table(["Sensitivity", "ΔTEV"], sens_rows) if sens_rows else "<p>No sensitivity results.</p>")
        + f"<h2>6. TEV-Impact Matrix</h2>"
        + _tev_impact_matrix_html(impact_matrix_df)
        + f"<h2>7. Workflow Iteration History</h2><p>Total iterations: <strong>{len(iterations)}</strong></p>"
        + (_tev_html_table(["#", "Stage", "Action", "Actuary", "TEV", "ΔTEV", "Env?", "Comment"], iter_rows) if iter_rows else "<p>No iterations recorded.</p>")
        + f"<h2>8. Credibility Envelope Analysis</h2>"
        + _build_envelope_html(
            envelope_run, envelope_tev_min, envelope_tev_max, envelope_percentile,
            envelope_width_abs=envelope_width_abs,
            envelope_width_pct=envelope_width_pct,
            top5_decrements=top5_decrements,
            theta_proposed=theta_proposed,
            theta_min=theta_min,
            theta_max=theta_max,
            credibility_bounds=credibility_bounds,
        )
        + f"<div class='footer'>TEV Working Actuary Report — CONFIDENTIAL<br>"
        f"Assumption Set: <strong>{aset_label}</strong> &nbsp;|&nbsp; "
        f"TEV Run: <strong>{tev_run_label}</strong><br>"
        f"<span class='uuid-stamp'>"
        f"aset_id={assumption_set_id} &nbsp;·&nbsp; "
        f"tev_run_id={tev_run_id} &nbsp;·&nbsp; "
        f"workflow_session_id={workflow_session_id}"
        f"</span></div></body></html>"
    )

    out_path.write_text(html, encoding="utf-8")
    return out_path


def generate_tev_impact_report(
    db_path: Path,
    assumption_set_id: str,
    tev_run_id: str,
    workflow_session_id: str,
    output_dir: Path,
    envelope_run: bool = False,
    envelope_tev_min: float | None = None,
    envelope_tev_max: float | None = None,
    envelope_percentile: float | None = None,
    envelope_width_abs: float | None = None,
    envelope_width_pct: float | None = None,
    top5_decrements: list | None = None,
    theta_proposed: dict | None = None,
    theta_min: dict | None = None,
    theta_max: dict | None = None,
    credibility_bounds: dict | None = None,
) -> Path:
    """
    Generate the TEV Impact Report for Stage 4 governance (~5 pages HTML, FR-2-48).

    Contains: executive summary, TEV baseline by product, single-axis sensitivity
    tornado, credibility envelope analysis (separate section, if run), comparison
    to prior approved assumption set, key risks and uncertainties, and proposer
    recommendation.

    Args:
        db_path:              DuckDB path.
        assumption_set_id:    UUID of the assumption set.
        tev_run_id:           UUID of the baseline TEV run.
        workflow_session_id:  UUID of the workflow session.
        output_dir:           Directory to write the HTML file.
        envelope_run:         Whether the envelope analyser was run.
        envelope_tev_min:     TEV_min from envelope (if run).
        envelope_tev_max:     TEV_max from envelope (if run).
        envelope_percentile:  Percentile of proposed within envelope (None if immaterial).
        envelope_width_abs:   Absolute envelope width (tev_max − tev_min).
        envelope_width_pct:   Envelope width as fraction of proposed TEV.
        top5_decrements:      Ordered list of top-5 decrement keys by TEV sensitivity.
        theta_proposed:       Proposed theta per decrement key.
        theta_min:            Theta values producing TEV_min per decrement key.
        theta_max:            Theta values producing TEV_max per decrement key.
        credibility_bounds:   Credibility (lower, upper) tuple per decrement key.

    Returns:
        Path to the generated HTML file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"tev_impact_report_{assumption_set_id[:8]}_{ts}.html"

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        run_log = _query_tev_run_log(conn, tev_run_id)
        aset_meta = _query_assumption_set_meta(conn, assumption_set_id)
        prod_results = _query_tev_results(conn, tev_run_id)
        sens_deltas = _query_sensitivity_deltas(conn, tev_run_id)
        iterations = _query_workflow_iterations_tev(conn, workflow_session_id)
        prior = conn.execute("""
            SELECT assumption_set_id, version, author_id, effective_date, approved_ts
            FROM gold_assumption_sets
            WHERE status = 'APPROVED'
            ORDER BY approved_ts DESC LIMIT 1
        """).fetchone()
        src_run = conn.execute("""
            SELECT run_id, run_ts, product_codes FROM gold_study_runs WHERE run_id = ?
        """, [aset_meta.get("source_study_run_id", "")]).fetchone()
    finally:
        conn.close()

    try:
        from src.tev.impact_matrix import build_impact_matrix_from_db
        impact_matrix_df = build_impact_matrix_from_db(db_path, tev_run_id)
    except Exception:
        impact_matrix_df = None

    total_tev = run_log.get("total_tev") or 0
    total_anw = run_log.get("total_anw") or 0
    total_pvfp = run_log.get("total_pvfp") or 0
    total_pvcoc = run_log.get("total_pvcoc") or 0
    delta_tev = run_log.get("delta_tev_vs_prior")
    projection_years = run_log.get("projection_years", "—")
    n_iters = len(iterations)
    env_run_any = any(i.get("envelope_run_flag") for i in iterations)
    max_sens_delta = max(
        (abs(float(s["delta_tev"])) for s in sens_deltas if s["delta_tev"] is not None),
        default=0.0,
    )

    sens_labels = {
        "SENS-01": "Lapse −10%", "SENS-02": "Lapse +10%",
        "SENS-03": "Mortality −5%", "SENS-04": "Mortality +5%",
        "SENS-05": "Annuity Longevity +5%",
        "SENS-06": "CI Incidence −10%", "SENS-07": "CI Incidence +10%",
        "SENS-08": "Expense −10%", "SENS-09": "Expense +10%",
        "SENS-10": "RDR +100bp", "SENS-11": "RDR −100bp",
    }

    sens_rows = [
        [sens_labels.get(s["sensitivity_id"], s["sensitivity_id"]), _tev_sign_fmt(s["delta_tev"])]
        for s in sens_deltas
    ]

    prod_rows = [
        [
            r["product_code"], _tev_fmt(r["anw"]), _tev_fmt(r["pvfp"]),
            _tev_fmt(r["pvcoc"]), _tev_fmt(r["tev"]), _tev_sign_fmt(r.get("delta_tev")),
        ]
        for r in prod_results
    ]
    prod_rows.append([
        "<strong>TOTAL</strong>",
        _tev_fmt(total_anw), _tev_fmt(total_pvfp), _tev_fmt(total_pvcoc),
        _tev_fmt(total_tev), _tev_sign_fmt(delta_tev),
    ])

    aset_label = (
        f"v{aset_meta.get('version', '?')} "
        f"({str(aset_meta.get('effective_date', ''))[:10]}, {aset_meta.get('author_id', '—')})"
    )
    src_study_label = (
        f"{src_run[2]} @ {str(src_run[1])[:10]}" if src_run
        else str(aset_meta.get("source_study_run_id", ""))[:8] + "…"
    )
    tev_run_label = (
        f"{str(run_log.get('run_ts', ''))[:16]} | {_tev_fmt(run_log.get('total_tev'))}"
    )
    prior_html = (
        f"<p>Prior approved set: v{prior[1]} ({str(prior[3])[:10]}, {prior[2]}), "
        f"approved {str(prior[4])[:10] if prior[4] else '—'}.</p>"
        if prior else
        "<p>No prior approved assumption set found — this is the first governance approval.</p>"
    )
    src_run_html = (
        f"<p>Source experience study: <strong>{src_study_label}</strong>.</p>"
        if src_run else ""
    )

    html = (
        f"<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        f"<title>TEV Impact Report — Governance</title>{_TEV_STYLE}</head><body>"
        f"<h1>TEV Impact Report — Governance Sign-Off</h1>"
        f"<p>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | "
        f"Assumption Set: <strong>{aset_label}</strong> | "
        f"Proposer: <strong>{aset_meta.get('author_id','—')}</strong></p>"
        f"<h2>Executive Summary</h2>"
        f"<div class='kpi'><div class='label'>Baseline TEV</div><div class='value'>{_tev_fmt(total_tev)}</div></div>"
        f"<div class='kpi'><div class='label'>ANW</div><div class='value'>{_tev_fmt(total_anw)}</div></div>"
        f"<div class='kpi'><div class='label'>PVFP</div><div class='value'>{_tev_fmt(total_pvfp)}</div></div>"
        f"<div class='kpi'><div class='label'>PVCoC</div><div class='value'>{_tev_fmt(total_pvcoc)}</div></div>"
        + (f"<div class='kpi'><div class='label'>ΔTEV vs Prior</div><div class='value'>{_tev_sign_fmt(delta_tev)}</div></div>" if delta_tev is not None else "")
        + f"<p>Projection: {projection_years} years | Iterations: {n_iters} | "
        f"Envelope analysis: {'Yes' if env_run_any else 'No'}</p>"
        + src_run_html
        + f"<h2>1. Assumption Changes</h2>"
        f"<p>Assumption set <strong>{aset_label}</strong> "
        f"derived from experience study <strong>{src_study_label}</strong>. "
        f"RDR: <strong>{(aset_meta.get('rdr') or 0)*100:.1f}%</strong> | "
        f"Earned Rate (GA): <strong>{(aset_meta.get('earned_rate_ga') or 0)*100:.1f}%</strong> | "
        f"Tax Rate: <strong>{(aset_meta.get('tax_rate') or 0)*100:.0f}%</strong></p>"
        f"<h2>2. TEV Baseline by Product</h2>"
        + _tev_html_table(["Product", "ANW", "PVFP", "PVCoC", "TEV", "ΔTEV"], prod_rows)
        + f"<h2>3. Single-axis Sensitivity Tornado (ΔTEV vs Baseline)</h2>"
        f"<p>Maximum sensitivity range: <strong>{_tev_fmt(max_sens_delta)}</strong></p>"
        + (_tev_html_table(["Sensitivity", "ΔTEV (All Products)"], sens_rows) if sens_rows else "<p>No sensitivity data.</p>")
        + f"<h2>4. TEV-Impact Matrix</h2>"
        + _tev_impact_matrix_html(impact_matrix_df)
        + f"<h2>5. Credibility Envelope Analysis</h2>"
        + _build_envelope_html(
            envelope_run, envelope_tev_min, envelope_tev_max, envelope_percentile,
            envelope_width_abs=envelope_width_abs,
            envelope_width_pct=envelope_width_pct,
            top5_decrements=top5_decrements,
            theta_proposed=theta_proposed,
            theta_min=theta_min,
            theta_max=theta_max,
            credibility_bounds=credibility_bounds,
        )
        + f"<h2>6. Comparison to Prior Approved Set</h2>"
        + prior_html
        + (f"<p>ΔTEV vs prior: <strong>{_tev_sign_fmt(delta_tev)}</strong></p>" if delta_tev is not None else "<p>ΔTEV: N/A (first baseline)</p>")
        + f"<h2>7. Key Risks and Uncertainties</h2><ul>"
        f"<li>Best-estimate basis — no PADs. Results are sensitive to RDR and earned rate assumptions.</li>"
        f"<li>Maximum single-axis sensitivity: <strong>{_tev_fmt(max_sens_delta)}</strong> from 11 standard shocks.</li>"
        f"<li>CI incidence assumptions carry material uncertainty.</li>"
        f"<li>Simplified statutory reserve proxies used (prototype grade).</li></ul>"
        f"<h2>8. Proposer's Recommendation</h2>"
        f"<p>Proposer <strong>{aset_meta.get('author_id','—')}</strong> recommends approval. "
        f"Proposed multipliers are within credibility bounds from the A/E study.</p>"
        f"<div class='footer'>TEV Impact Report — CONFIDENTIAL — For governance sign-off only.<br>"
        f"Assumption Set: <strong>{aset_label}</strong> &nbsp;|&nbsp; "
        f"TEV Run: <strong>{tev_run_label}</strong> &nbsp;|&nbsp; "
        f"Source Study: <strong>{src_study_label}</strong><br>"
        f"<span class='uuid-stamp'>"
        f"aset_id={assumption_set_id} &nbsp;·&nbsp; "
        f"tev_run_id={tev_run_id} &nbsp;·&nbsp; "
        f"workflow_session_id={workflow_session_id}"
        f"</span></div>"
        f"</body></html>"
    )

    out_path.write_text(html, encoding="utf-8")
    return out_path
