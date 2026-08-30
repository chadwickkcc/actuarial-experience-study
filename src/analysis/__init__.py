"""Deterministic management-commentary analytics (demo refresh P6).

Engine-side, plain parameterized DuckDB reads over the Gold layer — NOT part
of ``src/ai/`` (the AI layer only ever *narrates* numbers this package has
already computed). Importable by the UI and by reports.
"""

from src.analysis.commentary import (
    attribute_drivers,
    classify_trends,
    compute_yoy_movement,
    justification_metrics,
    load_commentary_config,
    movement_legs,
)

__all__ = [
    "attribute_drivers",
    "classify_trends",
    "compute_yoy_movement",
    "justification_metrics",
    "load_commentary_config",
    "movement_legs",
]
