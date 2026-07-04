"""Stress Test 1 — Containment Violation.

Verifies that run_envelope_analysis() handles a corrupted/stale proposed_tev
gracefully: sets success=False, populates a diagnostic in both convergence
message fields, and returns a fully-formed EnvelopeResult without raising.

No DB data required — all four DB-dependent calls are patched.
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# Ensure src/ is on the path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.tev.envelope import run_envelope_analysis
from src.utils.types import AssumptionSetStatus

# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

CORRUPTED_PROPOSED_TEV = 999_999_999.0  # Extreme value — can't be inside any real envelope
REALISTIC_TEV = 5_000_000.0             # What run_tev_fast will return (optimizer mock)

DB_PATH = Path("data/experience_study.duckdb")


def _make_mult(
    product: str = "TERM",
    multiplier: float = 0.90,
    credibility_lower: float = 0.80,
    credibility_upper: float = 1.00,
) -> DecrementMultiplier:
    """Create a single synthetic DecrementMultiplier cell."""
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
    """Create a minimal synthetic AssumptionSet — no DB required."""
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
    """Synthetic TEV-impact matrix — same structure as test_envelope._make_impact_matrix().

    SENS-01/02 map to lapse, SENS-03/04 to mortality_life.
    Lapse dominates so it ranks first in identify_top5_decrements().
    """
    data = {
        "SENS-01": {"TERM": -4_000_000, "TOTAL": -4_000_000},
        "SENS-02": {"TERM":  3_500_000, "TOTAL":  3_500_000},
        "SENS-03": {"TERM":    -500_000, "TOTAL":    -500_000},
        "SENS-04": {"TERM":     450_000, "TOTAL":     450_000},
    }
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"
_results: list[tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    """Record and print one assertion result."""
    tag = _PASS if condition else _FAIL
    _results.append((name, condition, detail))
    detail_str = f"  ({detail})" if detail else ""
    print(f"  [{tag}] {name}{detail_str}")


# ---------------------------------------------------------------------------
# Main stress test
# ---------------------------------------------------------------------------

def run_stress_test() -> None:
    """Execute Stress Test 1: containment violation via corrupted proposed_tev."""
    print("=" * 65)
    print("STRESS TEST 1 — Containment Violation")
    print("=" * 65)
    print()
    print("Setup:")
    print(f"  Corrupted proposed_tev : ${CORRUPTED_PROPOSED_TEV:>20,.0f}")
    print(f"  Optimizer mock TEV     : ${REALISTIC_TEV:>20,.0f}")
    print(f"  Expected: tev_min ≈ tev_max ≈ {REALISTIC_TEV:,.0f}")
    print(f"  Expected: {CORRUPTED_PROPOSED_TEV:,.0f} >> tev_max → containment violation")
    print()

    synthetic_aset = _make_aset()
    impact_df = _make_impact_matrix()
    fake_aset_id = str(uuid.uuid4())
    fake_run_id = str(uuid.uuid4())

    result = None
    exception_raised = None

    # Patch all four DB-dependent internals
    with (
        patch("src.tev.envelope.load_assumption_set", return_value=synthetic_aset),
        patch("src.tev.envelope._load_proposed_tev", return_value=CORRUPTED_PROPOSED_TEV),
        patch("src.tev.envelope.run_tev_fast", return_value=REALISTIC_TEV),
        patch("src.tev.envelope._load_model_points_cache", return_value={}),
    ):
        try:
            result = run_envelope_analysis(
                db_path=DB_PATH,
                assumption_set_id=fake_aset_id,
                baseline_tev_run_id=fake_run_id,
                impact_matrix_df=impact_df,
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
        print(f"\n  [{_FAIL}] Cannot continue — result is None (exception was raised above)")
        _print_summary()
        return

    _check(
        "result.success == False",
        result.success is False,
        f"got: {result.success}",
    )

    _check(
        "'Containment violated' in convergence_message_min",
        "Containment violated" in result.convergence_message_min,
        f"got: '{result.convergence_message_min[:80]}...'",
    )

    _check(
        "'Containment violated' in convergence_message_max",
        "Containment violated" in result.convergence_message_max,
        f"got: '{result.convergence_message_max[:80]}...'",
    )

    _check(
        "result.proposed_tev == 999,999,999",
        result.proposed_tev == CORRUPTED_PROPOSED_TEV,
        f"got: {result.proposed_tev:,.0f}",
    )

    _check(
        "result.tev_min and tev_max are finite numbers",
        math.isfinite(result.tev_min) and math.isfinite(result.tev_max),
        f"tev_min={result.tev_min:,.0f}  tev_max={result.tev_max:,.0f}",
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
    print(f"  proposed_envelope_pctile   :  {str(result.proposed_envelope_percentile):>20}")
    print(f"  percentile_undefined_reason:  {result.percentile_undefined_reason}")
    print(f"  top5_decrements            :  {result.top5_decrements}")
    print(f"  n_evaluations_min          :  {result.n_evaluations_min}")
    print(f"  n_evaluations_max          :  {result.n_evaluations_max}")
    print(f"  convergence_message_min    :  {result.convergence_message_min}")
    print(f"  convergence_message_max    :  {result.convergence_message_max}")
    print(f"  envelope_yaml_path         :  {result.envelope_yaml_path}")

    _print_summary()


def _print_summary() -> None:
    """Print pass/fail summary and set exit code."""
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print()
    print("=" * 65)
    if passed == total:
        print(f"\033[32mSTRESS TEST 1 RESULT: PASS ({passed}/{total} assertions)\033[0m")
    else:
        failed = [name for name, ok, _ in _results if not ok]
        print(f"\033[31mSTRESS TEST 1 RESULT: FAIL ({passed}/{total} passed)\033[0m")
        print(f"  Failed: {', '.join(failed)}")
    print("=" * 65)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_stress_test()
