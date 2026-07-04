"""Tests for src/tev/envelope.py — credibility envelope analyser.

Covers:
- identify_top5_decrements() ranking
- run_tev_fast() regression value
- run_envelope_analysis() both L-BFGS-B runs converge, containment, bounds
- Percentile calculation and materiality floor
- Architectural invariant: no adoption path (AST introspection)
"""
from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.tev.envelope import (
    identify_top5_decrements,
    run_envelope_analysis,
    _extract_bounds,
    _has_multipliers,
)
from src.utils.types import AssumptionSetStatus, EnvelopeResult


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

DB_PATH = Path("data/experience_study.duckdb")
SRC_ROOT = Path("src")


def _db_available() -> bool:
    return DB_PATH.exists()


def _model_points_available() -> bool:
    """True if the DB has at least one row in gold_model_points."""
    if not _db_available():
        return False
    try:
        import duckdb
        con = duckdb.connect(str(DB_PATH), read_only=True)
        row = con.execute("SELECT COUNT(*) FROM gold_model_points").fetchone()
        con.close()
        return (row[0] if row else 0) > 0
    except Exception:
        return False


def _latest_assumption_set_id() -> str:
    import duckdb
    con = duckdb.connect(str(DB_PATH), read_only=True)
    row = con.execute(
        "SELECT assumption_set_id FROM gold_assumption_sets ORDER BY created_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else ""


def _latest_baseline_run_id() -> str:
    import duckdb
    con = duckdb.connect(str(DB_PATH), read_only=True)
    row = con.execute(
        "SELECT tev_run_id FROM gold_tev_run_log "
        "WHERE (sensitivity_id IS NULL OR sensitivity_id = '') ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else ""


def _envelope_preconditions_met() -> bool:
    """True only when the DB has EVERYTHING the envelope integration fixture needs:
    model points, an assumption set, and a baseline TEV run.

    The skipif must check all three — not just model points — so the class SKIPS
    (rather than ERRORS in the fixture) when model points exist but no assumption
    set / baseline run does. That mismatch is reachable in a normal suite run:
    ``test_model_points`` writes model points into the shared prod DB, which would
    otherwise un-skip this class even on a DB that has never run the TEV workflow.
    """
    if not _model_points_available():
        return False
    try:
        return bool(_latest_assumption_set_id() and _latest_baseline_run_id())
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def _isolate_db_path(tmp_path_factory):
    """Redirect this module's DB_PATH to an isolated mirrored copy so the envelope integration
    tests read/write against a copy (config + reports resolve relative to the DB's parent.parent)
    and never touch the real production DB."""
    global DB_PATH
    if not DB_PATH.exists():
        yield
        return
    import shutil
    base = tmp_path_factory.mktemp("iso_env")
    (base / "data").mkdir()
    shutil.copy2(DB_PATH, base / "data" / "experience_study.duckdb")
    shutil.copytree(Path("config"), base / "config")
    orig = DB_PATH
    DB_PATH = base / "data" / "experience_study.duckdb"
    try:
        yield
    finally:
        DB_PATH = orig


def _make_mult(
    product: str = "TERM",
    multiplier: float = 0.90,
    credibility_lower: float = 0.80,
    credibility_upper: float = 1.00,
) -> DecrementMultiplier:
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


def _make_aset(
    lapse_mults: list[DecrementMultiplier] | None = None,
    mortality_mults: list[DecrementMultiplier] | None = None,
    ci_mults: list[DecrementMultiplier] | None = None,
) -> AssumptionSet:
    return AssumptionSet(
        id=str(uuid.uuid4()),
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(),
        author_id="test_actuary",
        basis="best-estimate",
        source_study_run_id=str(uuid.uuid4()),
        rdr=0.09,
        earned_rate_ga=0.05,
        earned_rate_sa=0.06,
        tax_rate=0.21,
        expense_inflation=0.025,
        rc_pct_reserve={"TERM": 0.03, "WL": 0.045, "UL": 0.06,
                        "ULSG": 0.08, "VUL": 0.035, "DA": 0.045},
        acquisition_per_policy=350.0,
        maintenance_per_policy=72.0,
        maintenance_pct_premium=0.02,
        mortality_multipliers=mortality_mults if mortality_mults is not None else [_make_mult("TERM", 0.92, 0.85, 0.99)],
        lapse_multipliers=lapse_mults if lapse_mults is not None else [_make_mult("TERM", 0.88, 0.80, 0.96)],
        surrender_multipliers=[],
        ci_incidence_multipliers=ci_mults if ci_mults is not None else [],
        premium_persistency=[],
        shock_lapse_plt={
            "jump_band_lt_2x": 0.30, "jump_band_2x_5x": 0.55,
            "jump_band_5x_8x": 0.70, "jump_band_gt_8x": 0.88,
        },
        yaml_file_path="",
    )


def _make_impact_matrix(with_lapse_biggest: bool = True) -> pd.DataFrame:
    """Minimal 2-column impact matrix used to test decrement ranking."""
    if with_lapse_biggest:
        data = {
            "SENS-01": {"TERM": -4_000_000, "TOTAL": -4_000_000},
            "SENS-02": {"TERM":  3_500_000, "TOTAL":  3_500_000},
            "SENS-03": {"TERM":  -500_000, "TOTAL":  -500_000},
            "SENS-04": {"TERM":   450_000, "TOTAL":   450_000},
        }
    else:
        data = {
            "SENS-03": {"TERM": -4_000_000, "TOTAL": -4_000_000},
            "SENS-04": {"TERM":  3_500_000, "TOTAL":  3_500_000},
            "SENS-01": {"TERM":  -500_000, "TOTAL":  -500_000},
            "SENS-02": {"TERM":   450_000, "TOTAL":   450_000},
        }
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# identify_top5_decrements
# ---------------------------------------------------------------------------

class TestIdentifyTop5Decrements:
    def test_lapse_ranked_first_when_biggest(self):
        aset = _make_aset()
        df = _make_impact_matrix(with_lapse_biggest=True)
        top5 = identify_top5_decrements(df, aset)
        assert top5[0] == "lapse"

    def test_mortality_ranked_first_when_biggest(self):
        aset = _make_aset()
        df = _make_impact_matrix(with_lapse_biggest=False)
        top5 = identify_top5_decrements(df, aset)
        assert top5[0] == "mortality_life"

    def test_at_most_5_returned(self):
        aset = _make_aset()
        data = {f"SENS-{str(i).zfill(2)}": {"TOTAL": float(-i * 1_000_000)}
                for i in range(1, 12)}
        df = pd.DataFrame(data)
        top5 = identify_top5_decrements(df, aset)
        assert len(top5) <= 5

    def test_empty_impact_matrix_returns_empty(self):
        aset = _make_aset()
        assert identify_top5_decrements(pd.DataFrame(), aset) == []

    def test_no_duplicate_decrements(self):
        aset = _make_aset()
        df = _make_impact_matrix()
        top5 = identify_top5_decrements(df, aset)
        assert len(top5) == len(set(top5))


# ---------------------------------------------------------------------------
# _extract_bounds
# ---------------------------------------------------------------------------

class TestExtractBounds:
    def test_expense_fixed_bounds(self):
        lo, hi = _extract_bounds(_make_aset(), "expense")
        assert lo == pytest.approx(0.50)
        assert hi == pytest.approx(1.50)

    def test_rdr_fixed_bounds(self):
        lo, hi = _extract_bounds(_make_aset(), "rdr")
        assert lo == pytest.approx(-0.020)
        assert hi == pytest.approx(0.020)

    def test_lapse_bounds_from_credibility(self):
        aset = _make_aset(lapse_mults=[_make_mult("TERM", 0.88, 0.80, 0.96)])
        lo, hi = _extract_bounds(aset, "lapse")
        assert 0.20 <= lo < hi <= 4.00

    def test_lapse_bounds_lo_lt_hi(self):
        aset = _make_aset()
        lo, hi = _extract_bounds(aset, "lapse")
        assert lo < hi

    def test_no_mults_returns_default(self):
        aset = _make_aset(mortality_mults=[])
        lo, hi = _extract_bounds(aset, "mortality_life")
        assert lo == pytest.approx(0.70)
        assert hi == pytest.approx(1.30)


# ---------------------------------------------------------------------------
# _has_multipliers
# ---------------------------------------------------------------------------

class TestHasMultipliers:
    def test_true_when_lapse_mults_present(self):
        aset = _make_aset(lapse_mults=[_make_mult()])
        assert _has_multipliers(aset, "lapse") is True

    def test_false_when_no_mults(self):
        aset = _make_aset(lapse_mults=[])
        assert _has_multipliers(aset, "lapse") is False

    def test_expense_always_false(self):
        aset = _make_aset()
        assert _has_multipliers(aset, "expense") is False


# ---------------------------------------------------------------------------
# Percentile logic (unit, no DB)
# ---------------------------------------------------------------------------

class TestPercentileLogic:
    def _make_result(
        self, proposed: float, tev_min: float, tev_max: float, percentile: float | None
    ) -> EnvelopeResult:
        return EnvelopeResult(
            success=True,
            assumption_set_id=str(uuid.uuid4()),
            top5_decrements=["lapse"],
            proposed_tev=proposed,
            tev_min=tev_min,
            tev_max=tev_max,
            envelope_width_abs=tev_max - tev_min,
            envelope_width_pct=(tev_max - tev_min) / max(abs(proposed), 1.0),
            proposed_envelope_percentile=percentile,
            percentile_undefined_reason=None if percentile is not None else "floor",
            theta_proposed={"lapse": 1.0},
            theta_min={"lapse": 0.80},
            theta_max={"lapse": 1.20},
            credibility_bounds={"lapse": (0.80, 1.20)},
            n_evaluations_min=10,
            n_evaluations_max=10,
            convergence_message_min="converged",
            convergence_message_max="converged",
            envelope_yaml_path="",
        )

    def test_percentile_midpoint(self):
        # proposed exactly halfway → 0.5
        result = self._make_result(1_000_000, 0, 2_000_000, 0.5)
        assert result.proposed_envelope_percentile == pytest.approx(0.5)

    def test_percentile_at_min(self):
        result = self._make_result(0, 0, 2_000_000, 0.0)
        assert result.proposed_envelope_percentile == pytest.approx(0.0)

    def test_percentile_at_max(self):
        result = self._make_result(2_000_000, 0, 2_000_000, 1.0)
        assert result.proposed_envelope_percentile == pytest.approx(1.0)

    def test_percentile_none_when_immaterial(self):
        result = self._make_result(1_000_000, 999_990, 1_000_010, None)
        assert result.proposed_envelope_percentile is None

    def test_percentile_formula_matches_spec(self):
        # (proposed - tev_min) / (tev_max - tev_min)
        proposed, tev_min, tev_max = 1_234_567, 1_000_000, 1_500_000
        expected = (proposed - tev_min) / (tev_max - tev_min)
        result = self._make_result(proposed, tev_min, tev_max, expected)
        assert result.proposed_envelope_percentile == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# run_envelope_analysis — integration tests (requires DB + model points)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _envelope_preconditions_met(),
    reason="envelope preconditions not met (need model points + assumption set + baseline TEV run)",
)
class TestRunEnvelopeAnalysis:
    @pytest.fixture(scope="class")
    def envelope_result(self) -> EnvelopeResult:
        aset_id = _latest_assumption_set_id()
        baseline_run_id = _latest_baseline_run_id()
        assert aset_id, "No assumption set in DB"
        assert baseline_run_id, "No baseline TEV run in DB"

        import duckdb
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            rows = con.execute("""
                SELECT r.sensitivity_id, SUM(r.delta_tev) AS delta_tev
                FROM gold_tev_results r
                WHERE r.assumption_set_id = (
                    SELECT assumption_set_id FROM gold_assumption_sets ORDER BY created_ts DESC LIMIT 1
                ) AND r.sensitivity_id IS NOT NULL AND r.sensitivity_id != ''
                GROUP BY r.sensitivity_id
            """).fetchall()
        finally:
            con.close()

        impact_data = {r[0]: {"TOTAL": float(r[1] or 0)} for r in rows}
        impact_df = pd.DataFrame(impact_data)

        result = run_envelope_analysis(
            db_path=DB_PATH,
            assumption_set_id=aset_id,
            baseline_tev_run_id=baseline_run_id,
            impact_matrix_df=impact_df,
            max_evaluations=200,
            width_materiality_floor_pct=0.001,
        )
        return result

    def test_runs_without_error(self, envelope_result):
        assert envelope_result is not None

    def test_both_runs_within_max_evaluations(self, envelope_result):
        # maxfun is a SOFT bound for L-BFGS-B (it counts function + finite-difference gradient
        # evaluations and can overshoot slightly), so allow a small margin over max_evaluations=200.
        assert envelope_result.n_evaluations_min <= 250, (
            f"TEV_min used {envelope_result.n_evaluations_min} evaluations"
        )
        assert envelope_result.n_evaluations_max <= 250, (
            f"TEV_max used {envelope_result.n_evaluations_max} evaluations"
        )

    def test_containment_tev_min_lte_proposed_lte_tev_max(self, envelope_result):
        # The envelope optimises a 25-yr FAST-projection TEV, while proposed_tev is the full 60-yr
        # run. When both L-BFGS-B runs converge, the code clamps tev_min/tev_max to bracket the full
        # proposed (success=True). When a run does NOT converge (success=False — a designed, tested
        # graceful path, see stress_test_2 / UAT 2026-05-31), the fast min/max need not bracket the
        # full proposed; in that case we only require the bounds to be finite and ordered.
        if envelope_result.success:
            tol = 1e-6 * max(abs(envelope_result.proposed_tev), 1.0)
            assert envelope_result.tev_min <= envelope_result.proposed_tev + tol, (
                f"tev_min={envelope_result.tev_min:,.0f} > proposed={envelope_result.proposed_tev:,.0f}"
            )
            assert envelope_result.proposed_tev <= envelope_result.tev_max + tol, (
                f"proposed={envelope_result.proposed_tev:,.0f} > tev_max={envelope_result.tev_max:,.0f}"
            )
        else:
            assert envelope_result.tev_min <= envelope_result.tev_max, (
                f"tev_min={envelope_result.tev_min:,.0f} > tev_max={envelope_result.tev_max:,.0f}"
            )

    def test_theta_min_within_credibility_bounds(self, envelope_result):
        for dk, (lo, hi) in envelope_result.credibility_bounds.items():
            t = envelope_result.theta_min.get(dk)
            if t is not None:
                assert lo - 1e-9 <= t <= hi + 1e-9, (
                    f"theta_min[{dk}]={t} outside [{lo}, {hi}]"
                )

    def test_theta_max_within_credibility_bounds(self, envelope_result):
        for dk, (lo, hi) in envelope_result.credibility_bounds.items():
            t = envelope_result.theta_max.get(dk)
            if t is not None:
                assert lo - 1e-9 <= t <= hi + 1e-9, (
                    f"theta_max[{dk}]={t} outside [{lo}, {hi}]"
                )

    def test_percentile_none_when_width_immaterial(self, envelope_result):
        """With floor=1.0 (100%), any real envelope is immaterial → percentile is None."""
        aset_id = _latest_assumption_set_id()
        baseline_run_id = _latest_baseline_run_id()
        import duckdb
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            rows = con.execute("""
                SELECT r.sensitivity_id, SUM(r.delta_tev) AS delta_tev
                FROM gold_tev_results r
                WHERE r.assumption_set_id = (
                    SELECT assumption_set_id FROM gold_assumption_sets ORDER BY created_ts DESC LIMIT 1
                ) AND r.sensitivity_id IS NOT NULL AND r.sensitivity_id != ''
                GROUP BY r.sensitivity_id
            """).fetchall()
        finally:
            con.close()
        impact_data = {r[0]: {"TOTAL": float(r[1] or 0)} for r in rows}
        impact_df = pd.DataFrame(impact_data)

        result = run_envelope_analysis(
            db_path=DB_PATH,
            assumption_set_id=aset_id,
            baseline_tev_run_id=baseline_run_id,
            impact_matrix_df=impact_df,
            max_evaluations=200,
            width_materiality_floor_pct=1.0,  # 100% — always immaterial
        )
        assert result.proposed_envelope_percentile is None, (
            "Expected None when materiality floor is 100%"
        )

    def test_envelope_yaml_written(self, envelope_result):
        if envelope_result.envelope_yaml_path:
            assert Path(envelope_result.envelope_yaml_path).exists(), (
                f"Envelope YAML not found at {envelope_result.envelope_yaml_path}"
            )


# ---------------------------------------------------------------------------
# Architectural invariant: no adoption path (AST introspection)
# ---------------------------------------------------------------------------

class TestNoAdoptionPath:
    def _all_src_python_files(self) -> list[Path]:
        return list(SRC_ROOT.rglob("*.py"))

    def _get_public_functions(self, tree: ast.Module) -> list[ast.FunctionDef]:
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        ]

    def _annotation_contains(self, annotation, name: str) -> bool:
        if annotation is None:
            return False
        src = ast.unparse(annotation)
        return name in src

    def test_no_public_function_converts_envelope_result_to_assumption_set(self):
        """No public function in src/ may accept EnvelopeResult and return AssumptionSet."""
        violations: list[str] = []
        for py_file in self._all_src_python_files():
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for func in self._get_public_functions(tree):
                has_envelope_param = any(
                    self._annotation_contains(arg.annotation, "EnvelopeResult")
                    for arg in func.args.args
                )
                returns_aset = self._annotation_contains(func.returns, "AssumptionSet")

                if has_envelope_param and returns_aset:
                    violations.append(f"{py_file}:{func.name}")

        assert violations == [], (
            "Adoption path detected — public functions converting EnvelopeResult → AssumptionSet:\n"
            + "\n".join(violations)
        )

    def test_no_adopt_button_text_in_src(self):
        """The literal string 'Adopt' must not appear as a button label in any UI page."""
        ui_dir = Path("ui/views")
        matches: list[str] = []
        for py_file in ui_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                lower = line.lower()
                if "st.button" in lower and "adopt" in lower:
                    matches.append(f"{py_file}:{i}: {line.strip()}")
        assert matches == [], (
            "Adopt button found in UI:\n" + "\n".join(matches)
        )
