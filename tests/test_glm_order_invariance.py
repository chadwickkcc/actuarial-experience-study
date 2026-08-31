"""The GLM fit must not depend on physical row order (adversarial review M-20).

FR-3A-24 requires a re-fit on identical inputs and seed to reproduce identical
coefficients. It did not: the same data in a different physical order flipped the
mortality GLM between a sound fit, a non-convergence, and a hard crash
("lam value too large") — demonstrated by shuffling unchanged production data:

    seed=1  TERM: CRASH                WL: did not converge
    seed=2  TERM: converged 54 factors WL: converged 60 factors
    seed=3  TERM: converged 54 factors WL: converged 60 factors

Cause: cells with a negligible reference basis (~1e-05 expected events) carry no
information but sit at the edge of the design space, driving the separation that
destabilises the Poisson fit. They are now excluded before fitting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ai.glm.fit import _MIN_BASIS_PER_CELL, fit_glm, load_cells
from src.utils.types import DecrementType

DB = Path("data/experience_study.duckdb")
_needs_db = pytest.mark.skipif(not DB.exists(), reason="live demo DB not present")


def _run_id() -> str:
    import duckdb

    con = duckdb.connect(str(DB), read_only=True)
    try:
        return con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        con.close()


def test_negligible_basis_threshold_is_configured():
    assert _MIN_BASIS_PER_CELL > 0, "a zero threshold would re-admit degenerate cells"


@_needs_db
@pytest.mark.parametrize("product", ["TERM", "WL"])
def test_fit_is_invariant_to_row_order(product, glm_config):
    """Shuffling the input rows must not change the fit at all."""
    run = _run_id()
    cells = load_cells(DB, run, DecrementType.MORTALITY, product)
    if cells.empty:
        pytest.skip(f"no cells for {product}")

    cfg = glm_config
    covariates = cfg["covariates"]["mortality"]
    grain = cfg["output_grain"]["mortality"]
    min_events = int(cfg["min_events_to_fit"])

    results = []
    for seed_shuffle in (1, 2, 3):
        shuffled = cells.sample(frac=1.0, random_state=seed_shuffle).reset_index(drop=True)
        fit = fit_glm(
            shuffled, DecrementType.MORTALITY, product,
            covariates, grain, min_events, seed=42,
        )
        results.append((
            fit.converged,
            len(fit.factors),
            None if fit.dispersion != fit.dispersion else round(fit.dispersion, 9),
        ))

    assert len(set(results)) == 1, (
        f"{product} mortality fit changed with row order: {results}"
    )
    assert results[0][0], f"{product} mortality should converge, got {results[0]}"


@_needs_db
@pytest.mark.parametrize("product", ["TERM", "WL"])
def test_fit_is_not_degenerate(product, glm_config):
    """A sound Poisson fit sits near 1; the diverged fits ran ~1e18-1e41."""
    run = _run_id()
    cells = load_cells(DB, run, DecrementType.MORTALITY, product)
    if cells.empty:
        pytest.skip(f"no cells for {product}")
    cfg = glm_config
    fit = fit_glm(
        cells, DecrementType.MORTALITY, product,
        cfg["covariates"]["mortality"], cfg["output_grain"]["mortality"],
        int(cfg["min_events_to_fit"]), seed=42,
    )
    assert fit.converged, fit.message
    assert 0.1 < fit.dispersion < 10.0, (
        f"{product} dispersion {fit.dispersion:.4g} is not a credible fit"
    )
    assert fit.factors, "a converged fit must publish factors"
