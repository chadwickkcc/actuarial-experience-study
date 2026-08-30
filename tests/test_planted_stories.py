"""Story-lock tests (demo refresh P4) — the demo's talking points, test-locked.

These run against the LIVE demo DB (skip when absent) and pin the planted
experience stories and the fraud ring, so later phases cannot silently drift
the demo narrative. Parameters live in config/synthetic_data.yaml.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

_DB = Path("data/experience_study.duckdb")

pytestmark = pytest.mark.skipif(not _DB.exists(), reason="live demo DB not present")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(_DB), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def run_id(con) -> str:
    row = con.execute(
        "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
        "ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no COMPLETE study run in the live DB")
    return row[0]


def _mortality_ae_by_year(con, run_id) -> dict[int, float]:
    rows = con.execute(
        """
        SELECT calendar_year,
               SUM(actual_deaths_count) / NULLIF(SUM(expected_deaths_count), 0)
        FROM gold_ae_results
        WHERE study_run_id = ? AND product_code IN ('TERM','WL')
          AND illness_code IS NULL AND calendar_year IS NOT NULL
        GROUP BY 1
        """,
        [run_id],
    ).fetchall()
    return {int(y): float(ae) for y, ae in rows if ae is not None}


def test_mortality_deterioration_2021_to_2023(con, run_id):
    """Term+WL mortality A/E strictly increases 2021→2023 by a clear margin."""
    ae = _mortality_ae_by_year(con, run_id)
    assert ae[2021] < ae[2022] < ae[2023], f"expected strict rise, got {ae}"
    assert ae[2023] >= ae[2021] + 0.10, f"trend too weak to demo: {ae}"


def test_lapse_spike_2022_2023(con, run_id):
    """Term+UL-family lapse A/E in 2022–23 is ≥115% of the 2016–21 mean."""
    rows = con.execute(
        """
        SELECT calendar_year,
               SUM(actual_lapses) / NULLIF(SUM(expected_lapses), 0)
        FROM gold_ae_results
        WHERE study_run_id = ? AND product_code IN ('TERM','UL','ULSG','IUL')
          AND illness_code IS NULL AND calendar_year IS NOT NULL
        GROUP BY 1
        """,
        [run_id],
    ).fetchall()
    ae = {int(y): float(v) for y, v in rows if v is not None}
    base = sum(ae[y] for y in range(2016, 2022)) / 6
    assert ae[2022] >= 1.15 * base, f"2022 lapse spike missing: {ae[2022]:.3f} vs base {base:.3f}"
    assert ae[2023] >= 1.15 * base, f"2023 lapse spike missing: {ae[2023]:.3f} vs base {base:.3f}"


def test_ci_volume_and_breadth(con, run_id):
    """≥250 CI claims across ≥8 illness codes (usable segmentation + fraud)."""
    total, codes = con.execute(
        """
        SELECT SUM(actual_ci_claims),
               COUNT(DISTINCT CASE WHEN actual_ci_claims > 0 THEN illness_code END)
        FROM gold_ae_results
        WHERE study_run_id = ? AND illness_code IS NOT NULL
        """,
        [run_id],
    ).fetchone()
    assert total >= 250, f"CI claim volume too thin: {total}"
    assert codes >= 8, f"illness-code breadth too thin: {codes}"


def test_fraud_ring_planted(con):
    """The planted ring is present in silver: shared claimant, ghost hospital,
    rogue office with a first-policy-year claim cluster."""
    shared = con.execute(
        "SELECT COUNT(*) FROM silver_term_policies WHERE claimant_id = 'CLM-424242'"
    ).fetchone()[0]
    assert shared >= 4

    ghost = con.execute(
        "SELECT COUNT(*) FROM silver_term_policies WHERE hospital_id = 'HOSP-066'"
    ).fetchone()[0]
    assert ghost >= 10  # the ring claims all route through the ghost facility

    # Rogue-office early-claim concentration: OFF-013's first-policy-year claim
    # count is ≥3x the office median (the P5 rule-4 signal).
    rows = con.execute(
        """
        SELECT agency_office_id, COUNT(*) AS n
        FROM silver_term_policies
        WHERE status_code IN ('DEATH', 'CI_CLAIM')
          AND termination_date IS NOT NULL
          AND date_diff('day', issue_date, termination_date) <= 366
        GROUP BY 1
        """
    ).fetchall()
    counts = {office: n for office, n in rows}
    others = sorted(n for office, n in counts.items() if office != "OFF-013") or [0]
    median_others = others[len(others) // 2]
    assert counts.get("OFF-013", 0) >= max(3 * max(median_others, 1), 10), (
        f"ring office does not stand out: OFF-013={counts.get('OFF-013')}, "
        f"median others={median_others}"
    )


def test_fraud_identity_fields_flow_to_silver(con):
    """The five new identity fields land in silver for every product table."""
    for table in (
        "silver_term_policies", "silver_wl_policies", "silver_ul_policies",
        "silver_vul_policies", "silver_annuity_contracts",
    ):
        cols = {
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        for c in ("agency_office_id", "agent_id", "claimant_id",
                  "hospital_id", "claim_region"):
            assert c in cols, f"{table} missing {c}"
    # And claims actually carry them (Term has plenty of claims).
    populated = con.execute(
        "SELECT COUNT(*) FROM silver_term_policies "
        "WHERE status_code IN ('DEATH','CI_CLAIM') AND hospital_id IS NOT NULL"
    ).fetchone()[0]
    assert populated > 100
