"""Exposure Summary must identify which product each recon row belongs to (B-5).

The page selected no ``product_code`` and did not group, so it rendered 56
identically-labelled rows — seven per calendar year, three of them near-duplicate
views of the same UL family — on a page the demo walkthrough sends the client to.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.utils.types import Role, User  # noqa: E402

VIEW = Path("ui/views/03_exposure_summary.py")
DB = Path("data/experience_study.duckdb")


def _user() -> User:
    return User(
        user_id="u-analyst", username="a.analyst", display_name="A. Analyst",
        role=Role.ANALYST, active=True,
    )


def test_recon_query_selects_and_orders_by_product() -> None:
    """Source-level guard: the query must carry product_code."""
    src = VIEW.read_text()
    assert "SELECT product_code, calendar_year" in src, (
        "recon query must select product_code so rows are identifiable"
    )
    assert "ORDER BY product_code, calendar_year" in src


@pytest.mark.skipif(not DB.exists(), reason="live demo DB not present")
def test_recon_table_renders_a_product_column() -> None:
    with patch("src.governance.auth.current_user", return_value=_user()):
        at = AppTest.from_file(str(VIEW), default_timeout=90)
        at.run()
    assert not at.exception, at.exception
    assert any(
        "Product" in list(df.value.columns)
        for df in at.dataframe
        if hasattr(df.value, "columns")
    ), "no rendered table carries a Product column"
