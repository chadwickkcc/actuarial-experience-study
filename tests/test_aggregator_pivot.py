"""Pivot/heat-map totals must align by index, not by position (review B-4).

``row_totals`` is grouped with ``dropna=False`` over the full frame, while
``pivot_table`` drops rows that are entirely NaN. When a dimension is NULL for
most products — ``premium_jump_ratio_band`` is Term-only — the two have different
lengths and the positional ``.values`` assignment raised

    ValueError: Length of values (18) does not match length of index (11)

on the Mortality A/E and Lapse A/E pages, one dropdown click away.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from src.aggregation.aggregator import aggregate_ae
from src.utils.db_init import init_database


@pytest.fixture()
def ae_db(tmp_path: Path) -> tuple[Path, str]:
    """A Gold A/E fixture where premium_jump_ratio_band is NULL for non-Term rows."""
    db = tmp_path / "data" / "ae.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    init_database(db)
    run_id = str(uuid.uuid4())

    # (product, attained_age_band, premium_jump_ratio_band, actual, expected)
    rows = [
        ("TERM", "30-34", "2-3x", 3, 4.0),
        ("TERM", "35-39", "3-5x", 5, 6.0),
        ("TERM", "40-44", ">12x", 2, 2.5),
        ("WL",   "45-49", None,   7, 8.0),   # NULL band — dropped by pivot_table
        ("WL",   "50-54", None,   1, 1.5),
        ("UL",   "55-59", None,   4, 5.0),
    ]
    con = duckdb.connect(str(db))
    try:
        for product, aab, band, actual, expected in rows:
            con.execute(
                "INSERT INTO gold_ae_results (result_id, study_run_id, product_code, "
                "attained_age_band, premium_jump_ratio_band, actual_deaths_count, "
                "expected_deaths_count, _created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [str(uuid.uuid4()), run_id, product, aab, band, actual, expected,
                 datetime(2026, 1, 1)],
            )
    finally:
        con.close()
    return db, run_id


def test_pivot_with_sparse_dimension_as_column(ae_db):
    db, run_id = ae_db
    pivot = aggregate_ae(
        db_path=db, study_run_id=run_id,
        row_dims=["attained_age_band"], col_dims=["premium_jump_ratio_band"],
        filters={}, measure="ae_count",
    )
    assert "Total" in pivot.columns


def test_pivot_with_sparse_dimension_as_row(ae_db):
    db, run_id = ae_db
    pivot = aggregate_ae(
        db_path=db, study_run_id=run_id,
        row_dims=["premium_jump_ratio_band"], col_dims=["product_code"],
        filters={}, measure="ae_count",
    )
    assert "Total" in pivot.columns


def test_row_totals_are_aligned_to_the_right_rows(ae_db):
    """The real risk of a positional assignment is silent misalignment: totals
    landing against the wrong row. Check a known value."""
    db, run_id = ae_db
    pivot = aggregate_ae(
        db_path=db, study_run_id=run_id,
        row_dims=["attained_age_band"], col_dims=["premium_jump_ratio_band"],
        filters={}, measure="ae_count",
    )
    # Every Term band row carries its own ratio, against the right age band.
    assert pivot.loc["30-34", "Total"] == pytest.approx(3 / 4)
    assert pivot.loc["35-39", "Total"] == pytest.approx(5 / 6)
    assert pivot.loc["40-44", "Total"] == pytest.approx(2 / 2.5)
    # Rows whose only column key is NULL (WL/UL have no jump band) are legitimately
    # dropped by pivot_table — they have no value in any band column.
    assert "45-49" not in pivot.index
    # And no column may be headed NaN.
    assert not any(str(c) == "nan" for c in pivot.columns), list(pivot.columns)


def test_every_dimension_pair_renders(ae_db):
    """No dimension pairing may raise — these are two dropdowns on a demo page."""
    db, run_id = ae_db
    dims = ["product_code", "attained_age_band", "premium_jump_ratio_band"]
    failures = []
    for r in dims:
        for c in dims:
            if r == c:
                continue
            try:
                aggregate_ae(
                    db_path=db, study_run_id=run_id, row_dims=[r], col_dims=[c],
                    filters={}, measure="ae_count",
                )
            except Exception as exc:  # noqa: BLE001 - the point of the test
                failures.append(f"{r} x {c}: {type(exc).__name__}: {exc}")
    assert not failures, "dimension pairs raised:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Sparse-cell floor (adversarial review OBS-10)
# ---------------------------------------------------------------------------

def test_min_claims_blanks_thin_cells_but_never_the_totals(ae_db):
    """A cell resting on one claim is arithmetically a ratio and statistically
    nothing. The floor blanks it; the totals still count it."""
    import numpy as np

    db, run_id = ae_db

    def _ae(min_claims: int):
        df = aggregate_ae(
            db_path=db, study_run_id=run_id, row_dims=["attained_age_band"],
            col_dims=[], filters={}, measure="ae_count", min_claims=min_claims,
        )
        return df.set_index("attained_age_band")["ae_count"]

    unfiltered, floored = _ae(0), _ae(3)
    # "50-54" rests on a single claim: shown by default, blanked at the floor.
    assert not np.isnan(unfiltered["50-54"])
    assert np.isnan(floored["50-54"])
    # A credible cell is untouched, and the total is identical either way.
    assert floored["45-49"] == pytest.approx(unfiltered["45-49"])
    assert floored["Total"] == pytest.approx(unfiltered["Total"])


def test_min_claims_defaults_to_showing_everything(ae_db):
    """Data is never hidden by default — the floor is an opt-in reading aid."""
    db, run_id = ae_db
    default = aggregate_ae(
        db_path=db, study_run_id=run_id, row_dims=["attained_age_band"],
        col_dims=[], filters={}, measure="ae_count",
    )
    explicit_zero = aggregate_ae(
        db_path=db, study_run_id=run_id, row_dims=["attained_age_band"],
        col_dims=[], filters={}, measure="ae_count", min_claims=0,
    )
    assert default.equals(explicit_zero)


def test_recon_and_ae_share_a_product_join_key(prod_db, prod_run_id):
    """In-force reconciliation and A/E must label products identically (OBS-10).

    Recon used to label every annuity ``DA`` while A/E split them three ways, so
    the two could not be joined at all. Fixed with the recon attribution bug
    (B-3); locked here because the fix is invisible until someone tries to join.
    """
    con = duckdb.connect(str(prod_db), read_only=True)
    try:
        recon = {
            r[0] for r in con.execute(
                "SELECT DISTINCT product_code FROM gold_inforce_reconciliation "
                "WHERE study_run_id = ?", [prod_run_id]).fetchall()
        }
        ae = {
            r[0] for r in con.execute(
                "SELECT DISTINCT product_code FROM gold_ae_results "
                "WHERE study_run_id = ? AND product_code IS NOT NULL",
                [prod_run_id]).fetchall()
        }
    finally:
        con.close()
    assert recon and ae
    assert recon == ae, f"recon-only={recon - ae}, ae-only={ae - recon}"
