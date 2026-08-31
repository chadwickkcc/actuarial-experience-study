"""Policy events must be attributed to each policy's own product (review M-6).

UL, ULSG and IUL share one Silver table and one source CSV, so ingesting any of
them conforms all ~4,500 family policies. ``_build_policy_events`` stamped the
*invoked* ``product_code`` on every row and the insert simply appended, so the
family's events were written three times under three different labels:

    total 45,110 rows vs 33,642 distinct (policy_id, event_type, event_date)
    IUL 5,734 / UL 5,734 / ULSG 5,734  — the same 4,500 policies, three labels

FR-1A-04's event timeline was therefore wrong on its face, and any future
consumer inherits it. (A/E and fraud were insulated: A/E counts from exposure
segments and the fraud runner de-duplicates.)
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingestion.pipeline import _build_policy_events


def _family_silver() -> pd.DataFrame:
    """A UL-family conformed frame, as ingesting any one variant produces."""
    return pd.DataFrame({
        "policy_id": ["UL-1", "UL-2", "ULSG-1", "ULSG-2", "IUL-1"],
        "product_code": ["UL", "UL", "ULSG", "ULSG", "IUL"],
        "issue_date": [date(2018, 1, 1)] * 5,
        "specified_amount": [100000.0] * 5,
        "status_code": ["IF", "LAPSE", "IF", "DEATH", "IF"],
        "termination_date": [None, date(2021, 3, 1), None, date(2022, 6, 1), None],
    })


def test_events_carry_each_policys_own_product_code():
    events = _build_policy_events(_family_silver(), "UL", str(uuid.uuid4()))
    by_policy = dict(zip(events["policy_id"], events["product_code"]))
    assert by_policy["ULSG-1"] == "ULSG", "a ULSG policy's events must be labelled ULSG"
    assert by_policy["IUL-1"] == "IUL", "an IUL policy's events must be labelled IUL"
    assert by_policy["UL-1"] == "UL"


def test_invoked_product_is_only_a_fallback():
    """A frame without product_code falls back to the invoked code."""
    df = _family_silver().drop(columns=["product_code"])
    events = _build_policy_events(df, "UL", str(uuid.uuid4()))
    assert set(events["product_code"]) == {"UL"}


@pytest.mark.parametrize("runs", [1, 2, 3])
def test_reingesting_the_family_does_not_duplicate_events(tmp_path: Path, runs: int):
    """Ingesting UL then ULSG then IUL must leave ONE set of events, not three."""
    from src.ingestion.pipeline import _insert_events
    from src.utils.db_init import init_database

    db = tmp_path / "events.duckdb"
    init_database(db)
    con = duckdb.connect(str(db))
    try:
        for product in (["UL", "ULSG", "IUL"])[:runs]:
            events = _build_policy_events(_family_silver(), product, str(uuid.uuid4()))
            _insert_events(con, events)
        total, distinct = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT policy_id || '|' || event_type || '|' "
            "|| CAST(event_date AS VARCHAR)) FROM silver_policy_events"
        ).fetchone()
    finally:
        con.close()
    assert total == distinct, (
        f"after {runs} family ingests: {total} rows but only {distinct} distinct events"
    )
