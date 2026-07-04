"""Parametric bootstrap confidence intervals for GLM factors (Session 15).

Realises FR-3A-21 (95% CIs by parametric bootstrap) and FR-3A-22 / NFR-T-05
(resample arrays never persisted). Determinism-first: a master RNG derives one
child seed per resample, so the result is independent of execution order and is
identical on re-run with the same seed.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import numpy as np
import pandas as pd

from src.utils.types import DecrementType, GLMFitResult
from src.ai.glm.fit import (
    _MEASURES,
    _fit_core,
    _fit_from_fitting_cells,
    _factors_at_output_grain,
)


def _grain_id(grain_key: dict) -> tuple:
    """Order-independent identity for an output-grain cell."""
    return tuple(sorted(grain_key.items()))


def bootstrap_cis(
    cells: pd.DataFrame,
    decrement: DecrementType,
    product_code: str,
    covariates: list[str],
    output_grain: list[str],
    fitted: GLMFitResult,
    n_resamples: int = 1000,
    ci_level: float = 0.95,
    seed: int = 42,
) -> GLMFitResult:
    """Populate bootstrap 95% CIs on each published factor (FR-3A-21).

    For each resample, event counts are drawn from the fitted distribution
    (``Poisson(mu_hat)`` for mortality; ``Binomial(n, p_hat)`` for lapse/CI),
    the GLM is refit, and the output-grain factors are recomputed. The per-cell
    CI is the percentile interval over the resampled factors. Resample arrays
    live only in memory and are discarded (FR-3A-22 / NFR-T-05).

    Returns ``fitted`` with ``ci_low``/``ci_high`` set on each factor. A
    non-converged or empty input is returned unchanged.
    """
    decrement = DecrementType(decrement)
    if not fitted.converged or not fitted.factors:
        return fitted

    core = _fit_core(cells, decrement, covariates, output_grain, seed)
    base = core["fit_cells"]
    used = core["used_covariates"]
    actual_col = _MEASURES[decrement][0]
    mu = np.clip(base["_predicted_events"].to_numpy(dtype=float), 0.0, None)
    exposure = base["_exposure"].to_numpy(dtype=float)

    master = np.random.default_rng(seed)
    child_seeds = master.integers(0, 2 ** 63 - 1, size=n_resamples)

    samples: dict[tuple, list[float]] = defaultdict(list)
    for child in child_seeds:
        rng_i = np.random.default_rng(int(child))
        if decrement is DecrementType.MORTALITY:
            new_actual = rng_i.poisson(mu).astype(float)
        else:
            n = np.maximum(1, np.round(exposure).astype(np.int64))
            with np.errstate(divide="ignore", invalid="ignore"):
                p = np.where(exposure > 0, mu / exposure, 0.0)
            p = np.clip(p, 0.0, 1.0)
            new_actual = rng_i.binomial(n, p).astype(float)

        resampled = base.copy()
        resampled[actual_col] = new_actual
        try:
            refit = _fit_from_fitting_cells(resampled, used, decrement)
        except Exception:   # noqa: BLE001 — a degenerate resample is simply dropped
            continue
        if not refit["converged"]:
            continue
        for fc in _factors_at_output_grain(refit["fit_cells"], output_grain):
            samples[_grain_id(fc.grain_key)].append(fc.factor)

    lo_pct = (1.0 - ci_level) / 2.0 * 100.0
    hi_pct = 100.0 - lo_pct
    new_factors = []
    for fc in fitted.factors:
        vals = samples.get(_grain_id(fc.grain_key), [])
        if vals:
            lo = float(np.percentile(vals, lo_pct))
            hi = float(np.percentile(vals, hi_pct))
        else:
            lo = hi = float("nan")
        new_factors.append(replace(fc, ci_low=lo, ci_high=hi))
    return replace(fitted, factors=new_factors)
