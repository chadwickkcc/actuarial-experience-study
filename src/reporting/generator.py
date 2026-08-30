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
