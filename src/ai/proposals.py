"""Materialise published GLM/GBM proposed adjustment factors to Gold (2026-06-27).

The GLM/GBM proposal *factor values* otherwise live only inside the serialized
model artifacts under ``data/ai_models/``, unreachable by SQL — so the guarded AI
Analyst could not answer "what are the proposed Term mortality assumptions by age
band?". This module writes each published ``FactorCell`` to the PII-free,
queryable ``gold_ai_proposed_factors`` Gold table (a fourth permitted AI Gold
write target, FR-3A-09 amended 2026-06-27), via a **static, parameterized**
statement (no string-interpolated SQL, FR-3A-02) on a writable connection — the
same controlled-write pattern as ``src/ai/glm/registry.py``. Reads stay on the SQL
boundary. No PII is written: the grain dims (sex/smoker/age-band/duration) and the
fitted factor + credibility are the only payload.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from pathlib import Path

import duckdb

from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import DecrementType

#: Column order for the static INSERT (mirrors the §DDL gold_ai_proposed_factors).
#: A test asserts the ``_INSERT_SQL`` column list equals this, in order.
_COLUMNS = [
    "proposed_factor_id", "model_id", "run_id", "model_type", "decrement",
    "product_code", "sex", "smoker", "attained_age_band", "duration_band",
    "grain_key", "factor", "ci_low", "ci_high", "expected_events",
    "credibility_z", "ae_derived_factor", "fit_ts",
]
# Static, parameterized INSERT written as adjacent string literals (NOT runtime
# concatenation) so the FR-3A-02 no-interpolation guard does not flag it — the
# same pattern as registry.py / audit.py. 18 columns -> 18 ``?`` placeholders.
_INSERT_SQL = (
    "INSERT INTO gold_ai_proposed_factors ("
    "proposed_factor_id, model_id, run_id, model_type, decrement, product_code, "
    "sex, smoker, attained_age_band, duration_band, grain_key, factor, ci_low, "
    "ci_high, expected_events, credibility_z, ae_derived_factor, fit_ts"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
# Replace any prior published set for the same (run, decrement, product, model
# type) so a re-fit supersedes rather than accumulates (each fit is a new model_id;
# FR-3A-25). Static + parameterized.
_DELETE_SQL = (
    "DELETE FROM gold_ai_proposed_factors "
    "WHERE run_id = ? AND decrement = ? AND product_code = ? AND model_type = ?"
)


def _num(value):
    """Float or None; NaN/None -> None (so an excluded cell stores SQL NULL)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def write_proposed_factors(
    model_id: str,
    run_id: str,
    model_type: str,
    decrement,
    product_code: str,
    factors,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Replace the proposed-factor rows for (run, decrement, product, model_type).

    Args:
        model_id: the registered model's id.
        run_id: the study run the proposal was fitted on.
        model_type: ``"GLM"`` or ``"GBM"``.
        decrement: a :class:`DecrementType` (or its value).
        product_code: the product the factors apply to.
        factors: a list of ``FactorCell`` (``grain_key`` dict + factor / ci /
            expected_events / credibility_z / ae_derived_factor).
        db_path: the DuckDB file.

    Returns:
        The number of factor rows written. A no-proposal model (``factors`` empty)
        is a no-op (no DELETE, no rows) — the loud "no AI proposal" state leaves
        nothing to publish.
    """
    decrement = DecrementType(decrement).value
    fit_ts = datetime.utcnow()
    rows: list[list] = []
    for fc in factors or []:
        factor = _num(getattr(fc, "factor", None))
        if factor is None:  # excluded / non-finite cell — never publish a NULL factor
            continue
        grain = dict(getattr(fc, "grain_key", {}) or {})
        rows.append([
            str(uuid.uuid4()), model_id, run_id, model_type, decrement, product_code,
            grain.get("sex"), grain.get("smoker"),
            grain.get("attained_age_band"), grain.get("duration_band"),
            json.dumps(grain, default=str, sort_keys=True),
            factor,
            _num(getattr(fc, "ci_low", None)),
            _num(getattr(fc, "ci_high", None)),
            _num(getattr(fc, "expected_events", None)),
            _num(getattr(fc, "credibility_z", None)),
            _num(getattr(fc, "ae_derived_factor", None)),
            fit_ts,
        ])
    if not rows:
        return 0
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_DELETE_SQL, [run_id, decrement, product_code, model_type])
        con.executemany(_INSERT_SQL, rows)
    finally:
        con.close()
    return len(rows)
