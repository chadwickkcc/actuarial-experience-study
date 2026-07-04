"""Stress Test 2 — Convergence Failure.

Verifies that run_envelope_analysis() handles optimizer non-convergence
gracefully: returns success=False with optimizer diagnostics in convergence
messages (not a containment-violation message), finite partial tev_min/tev_max,
correct percentile (envelope width is material at 8%), and an audit YAML —
without raising an exception.

Approach: patch scipy.minimize (imported as src.tev.envelope.minimize) to
return crafted non-converged OptimizeResult objects whose fun values bracket
proposed_tev so containment passes and only the convergence failure path fires.

  TEV_max partial result: fun = -5,200,000  →  tev_max = 5,200,000
  TEV_min partial result: fun =  4,800,000  →  tev_min = 4,800,000
  proposed_tev           =  5,000,000
  Containment: 4.8M ≤ 5M ≤ 5.2M  ✓
  Envelope width: 400,000 (8 % of 5M) — material  ✓
  Percentile: (5M − 4.8M) / 400k = 50th  ✓

max_evaluations=2 is still passed to document intent; the patched minimize
ignores it (returns immediately), exactly as a real exhausted optimizer would.

No DB data required — load_assumption_set, _load_proposed_tev, and
_load_model_points_cache are also patched.
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeResult

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.tev.envelope import run_envelope_analysis
from src.utils.types import AssumptionSetStatus

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROPOSED_TEV     = 5_000_000.0
PARTIAL_TEV_MAX  = 5_200_000.0   # Partial result from TEV_max run (above proposed)
PARTIAL_TEV_MIN  = 4_800_000.0   # Partial result from TEV_min run (below proposed)
MAX_EVALUATIONS  = 2
DB_PATH = Path("data/experience_study.duckdb")

_STOP_MSG = "STOP: TOTAL NO. of f AND g EVALUATIONS EXCEEDS LIMIT"


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_mult(
    product: str = "TERM",
    multiplier: float = 0.90,
    credibility_lower: float = 0.80,
    credibility_upper: float = 1.00,
) -> DecrementMultiplier:
    """Single synthetic DecrementMultiplier cell."""
    return DecrementMultiplier(
        product=product,
        gender="M",
        risk_class="STD_NS",
        duration_band=[1, 5],
        multiplier=multiplier,
        credibility_z=0.75,
        credibility_lower=credibility_lower,
        credibility_upper=credibility_upper,
    )


def _make_aset() -> AssumptionSet:
    """Minimal synthetic AssumptionSet — no DB required."""
    return AssumptionSet(
        id=str(uuid.uuid4()),
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(),
        author_id="stress_test",
        basis="best-estimate",
        source_study_run_id=str(uuid.uuid4()),
        rdr=0.09,
        earned_rate_ga=0.05,
        earned_rate_sa=0.06,
        tax_rate=0.21,
        expense_inflation=0.025,
        rc_pct_reserve={
            "TERM": 0.03, "WL": 0.045, "UL": 0.06,
            "ULSG": 0.08, "VUL": 0.035, "DA": 0.045,
        },
        acquisition_per_policy=350.0,
        maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=[_make_mult("TERM", 0.92, 0.85, 0.99)],
        lapse_multipliers=[_make_mult("TERM", 0.88, 0.80, 0.96)],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={"1.0-1.5": 0.25, "1.5+": 0.45},
    )


def _make_impact_matrix() -> pd.DataFrame:
    """Synthetic impact matrix with SENS-01/02 (lapse) and SENS-03/04 (mortality_life)."""
    data = {
        "SENS-01": {"TERM": -4_000_000, "TOTAL": -4_000_000},
        "SENS-02": {"TERM":  3_500_000, "TOTAL":  3_500_000},
        "SENS-03": {"TERM":    -500_000, "TOTAL":    -500_000},
        "SENS-04": {"TERM":     450_000, "TOTAL":     450_000},
    }
    return pd.DataFrame(data)


def _make_fake_minimize() -> object:
    """Return a callable that mimics two exhausted L-BFGS-B runs.

    First call  = TEV_max run: objective fun = -PARTIAL_TEV_MAX (minimising -TEV)
    Second call = TEV_min run: objective fun =  PARTIAL_TEV_MIN (minimising +TEV)
    Both report success=False with the evaluation-limit stop message.
    """
    _calls = [0]

    def _fake_minimize(fun, x0, **kwargs):
        _calls[0] += 1
        if _calls[0] == 1:
            # TEV_max optimiser: obj_max = -TEV, so fun = -PARTIAL_TEV_MAX
            obj_val = -PARTIAL_TEV_MAX
        else:
            # TEV_min optimiser: obj_min = +TEV
            obj_val = PARTIAL_TEV_MIN
        return OptimizeResult(
            x=np.array(x0, dtype=np.float64),
            fun=obj_val,
            success=False,
            message=_STOP_MSG,
            nit=1,
            nfev=MAX_EVALUATIONS,
        )

    return _fake_minimize


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"
_results: list[tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    tag = _PASS if condition else _FAIL
    _results.append((name, condition, detail))
    detail_str = f"  ({detail})" if detail else ""
    print(f"  [{tag}] {name}{detail_str}")


# ---------------------------------------------------------------------------
# Main stress test
# ---------------------------------------------------------------------------

def run_stress_test() -> None:
    """Execute Stress Test 2: convergence failure via patched non-converged optimizer."""
    print("=" * 70)
    print("STRESS TEST 2 — Convergence Failure (max_evaluations=2)")
    print("=" * 70)
    print()
    print("Setup:")
    print(f"  proposed_tev        : ${PROPOSED_TEV:>20,.0f}")
    print(f"  partial tev_max     : ${PARTIAL_TEV_MAX:>20,.0f}  (above proposed — containment holds)")
    print(f"  partial tev_min     : ${PARTIAL_TEV_MIN:>20,.0f}  (below proposed — containment holds)")
    print(f"  max_evaluations     :  {MAX_EVALUATIONS}")
    print(f"  optimizer mock msg  :  '{_STOP_MSG}'")
    print(f"  Expected: success=False, convergence messages = optimizer diagnostic (not containment)")
    print(f"  Expected: envelope_width = $400,000 (8% — material) → percentile computed")
    print()

    synthetic_aset = _make_aset()
    impact_df = _make_impact_matrix()
    fake_aset_id = str(uuid.uuid4())
    fake_run_id = str(uuid.uuid4())

    result = None
    exception_raised = None

    with (
        patch("src.tev.envelope.load_assumption_set", return_value=synthetic_aset),
        patch("src.tev.envelope._load_proposed_tev", return_value=PROPOSED_TEV),
        patch("src.tev.envelope._load_model_points_cache", return_value={}),
        patch("src.tev.envelope.minimize", side_effect=_make_fake_minimize()),
    ):
        try:
            result = run_envelope_analysis(
                db_path=DB_PATH,
                assumption_set_id=fake_aset_id,
                baseline_tev_run_id=fake_run_id,
                impact_matrix_df=impact_df,
                max_evaluations=MAX_EVALUATIONS,
            )
        except Exception as exc:
            exception_raised = exc

    print("Assertions:")

    _check(
        "No unhandled exception raised",
        exception_raised is None,
        str(exception_raised) if exception_raised else "",
    )

    if result is None:
        print(f"\n  [{_FAIL}] Cannot continue — result is None")
        _print_summary()
        return

    _check(
        "result.success == False",
        result.success is False,
        f"got: {result.success}",
    )

    _check(
        "'Containment violated' NOT in convergence_message_min",
        "Containment violated" not in result.convergence_message_min,
        f"got: '{result.convergence_message_min[:80]}'",
    )

    _check(
        "'Containment violated' NOT in convergence_message_max",
        "Containment violated" not in result.convergence_message_max,
        f"got: '{result.convergence_message_max[:80]}'",
    )

    _check(
        "Optimizer stop message present in convergence_message_min",
        "EXCEEDS LIMIT" in result.convergence_message_min or "STOP" in result.convergence_message_min,
        f"got: '{result.convergence_message_min}'",
    )

    _check(
        "result.tev_min and tev_max are finite (partial results present)",
        math.isfinite(result.tev_min) and math.isfinite(result.tev_max),
        f"tev_min=${result.tev_min:,.0f}  tev_max=${result.tev_max:,.0f}",
    )

    # Envelope width is material (8%) — percentile must be computed
    _check(
        "Envelope width is material (≥ 0.1 %)",
        result.envelope_width_pct >= 0.001,
        f"width={result.envelope_width_pct:.4%}",
    )

    _check(
        "Percentile computed when width is material",
        result.proposed_envelope_percentile is not None,
        f"percentile={result.proposed_envelope_percentile}",
    )

    _check(
        "result.envelope_yaml_path is populated",
        bool(result.envelope_yaml_path),
        f"path: {result.envelope_yaml_path}",
    )

    # --- Detailed result dump ---
    print()
    print("EnvelopeResult fields:")
    print(f"  success                    : {result.success}")
    print(f"  proposed_tev               : ${result.proposed_tev:>20,.0f}")
    print(f"  tev_min                    : ${result.tev_min:>20,.0f}")
    print(f"  tev_max                    : ${result.tev_max:>20,.0f}")
    print(f"  envelope_width_abs         : ${result.envelope_width_abs:>20,.0f}")
    print(f"  envelope_width_pct         :  {result.envelope_width_pct:>20.4%}")
    print(f"  proposed_envelope_pctile   :  {result.proposed_envelope_percentile}")
    print(f"  percentile_undefined_reason:  {result.percentile_undefined_reason}")
    print(f"  top5_decrements            :  {result.top5_decrements}")
    print(f"  n_evaluations_min          :  {result.n_evaluations_min}")
    print(f"  n_evaluations_max          :  {result.n_evaluations_max}")
    print(f"  convergence_message_min    :  {result.convergence_message_min}")
    print(f"  convergence_message_max    :  {result.convergence_message_max}")
    print(f"  envelope_yaml_path         :  {result.envelope_yaml_path}")

    print()
    print("UI rendering (Stage 3) — what the actuary would see:")
    print(f"  st.warning(): 'Analysis completed with warnings.")
    print(f"    Min convergence: {result.convergence_message_min}'")
    print(f"  Metric cards: TEV_min=${result.tev_min:,.0f}  TEV_proposed=${result.proposed_tev:,.0f}  TEV_max=${result.tev_max:,.0f}")
    if result.proposed_envelope_percentile is not None:
        print(f"  Percentile info: proposed is at the {result.proposed_envelope_percentile*100:.1f}th percentile of the credibility envelope")
    else:
        print(f"  Percentile info: Not meaningful — {result.percentile_undefined_reason}")
    print(f"  Convergence expander (both messages):")
    print(f"    max: '{result.convergence_message_max}'")
    print(f"    min: '{result.convergence_message_min}'")
    print(f"  YAML download: {result.envelope_yaml_path}")

    _print_summary()


def _print_summary() -> None:
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print()
    print("=" * 70)
    if passed == total:
        print(f"\033[32mSTRESS TEST 2 RESULT: PASS ({passed}/{total} assertions)\033[0m")
    else:
        failed = [name for name, ok, _ in _results if not ok]
        print(f"\033[31mSTRESS TEST 2 RESULT: FAIL ({passed}/{total} passed)\033[0m")
        print(f"  Failed: {', '.join(failed)}")
    print("=" * 70)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_stress_test()
