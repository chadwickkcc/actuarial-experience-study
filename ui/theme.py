"""Shared UI theme (demo refresh P7).

One place for the page chrome and chart look: ``page_setup()`` replaces the
per-page ``st.set_page_config`` duplication and registers a consistent Plotly
template + categorical palette, so every view renders with the same visual
language.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

#: Categorical palette used across every chart (colour-blind-safe ordering).
PALETTE = [
    "#2563eb",  # blue
    "#f59e0b",  # amber
    "#059669",  # green
    "#dc2626",  # red
    "#7c3aed",  # violet
    "#0891b2",  # cyan
    "#db2777",  # pink
    "#65a30d",  # lime
]

GOOD = "#059669"
BAD = "#dc2626"
NEUTRAL = "#9ca3af"

_TEMPLATE_NAME = "experience_demo"


def _register_template() -> None:
    """Register (idempotently) the demo's Plotly template and make it default."""
    if _TEMPLATE_NAME in pio.templates:
        pio.templates.default = _TEMPLATE_NAME
        return
    pio.templates[_TEMPLATE_NAME] = go.layout.Template(
        layout=dict(
            colorway=PALETTE,
            font=dict(family="Helvetica, Arial, sans-serif", size=13),
            margin=dict(l=40, r=20, t=48, b=40),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        )
    )
    pio.templates.default = _TEMPLATE_NAME


def page_setup(title: str, icon: str = "📊") -> None:
    """Standard page chrome: wide layout, titled tab, themed charts.

    Call once at the top of every view instead of ``st.set_page_config``.
    Safe when Streamlit has already configured the page (the first configured
    page wins; later calls are ignored rather than raising).
    """
    try:
        st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    except Exception:  # noqa: BLE001 - set_page_config may already have run
        pass
    _register_template()
