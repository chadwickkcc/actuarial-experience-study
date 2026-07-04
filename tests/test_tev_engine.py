"""Tests for TEV projection engine and sensitivity grid.

Covers:
- project_cashflows() survivorship and discounting
- compute_pvfp() mid-year discounting
- compute_pvcoc() end-of-year discounting
- Directional TEV correctness per NFR-C-07 and FR-2-22
- Sensitivity shock application (apply_sensitivity_shock)
- Impact matrix structure
"""
from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tev.tev_core import (
    compute_pvfp,
    compute_pvcoc,
    project_cashflows,
)
from src.tev.sensitivities import (
    SENSITIVITY_DEFINITIONS,
    apply_sensitivity_shock,
    run_sensitivity_grid,
)
from src.tev.assumption_set import AssumptionSet, DecrementMultiplier
from src.utils.types import AssumptionSetStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DB_PATH = Path("data/experience_study.duckdb")


def _db_available() -> bool:
    return DB_PATH.exists()


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
        "WHERE sensitivity_id IS NULL ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else ""


@pytest.fixture(scope="module")
def tev_baseline(tmp_path_factory):
    """Self-contained TEV baseline built in an isolated COPY of the production DB.

    Other integration tests write assumption sets / baselines to the shared production DB, so
    relying on its 'latest' assumption set / baseline is order-dependent and flaky (and pollutes
    the real DB). This fixture copies the DB, builds its own deterministic baseline, and never
    writes to the real DB. Returns ``(db_path, assumption_set_id, baseline_tev_run_id)``.
    """
    if not _db_available():
        pytest.skip("Production DB not available")
    import json
    import shutil
    import duckdb
    from src.tev.assumption_set import create_assumption_set_from_ae_run
    from src.tev.model_points import build_model_points
    from src.tev.tev_core import run_tev

    # The TEV functions resolve config/ relative to the DB's parent.parent, so the isolated
    # copy must mirror the <root>/data + <root>/config layout.
    base = tmp_path_factory.mktemp("tev_baseline")
    (base / "data").mkdir()
    shutil.copy2(DB_PATH, base / "data" / "experience_study.duckdb")
    shutil.copytree(Path("config"), base / "config")
    dest = base / "data" / "experience_study.duckdb"

    con = duckdb.connect(str(dest), read_only=True)
    rows = con.execute(
        "SELECT run_id, product_codes FROM gold_study_runs "
        "WHERE status = 'COMPLETE' ORDER BY run_ts"
    ).fetchall()
    con.close()
    if not rows:
        pytest.skip("No completed study run in DB")
    # Pick the MOST COMPLETE run (most products; earliest on ties) rather than the
    # latest. The directional sensitivity tests need every product (esp. TERM); keying
    # off "latest" makes them break whenever a later partial-product study run is added
    # to the shared DB — the exact flakiness this fixture is meant to avoid.
    study_run_id, products = max(
        ((r[0], json.loads(r[1])) for r in rows), key=lambda rp: len(rp[1])
    )

    aset = create_assumption_set_from_ae_run(
        study_run_id, "test", dest, Path("config/tev_config.yaml"), dest.parent
    )
    mp_run = str(uuid.uuid4())
    for pc in products:
        build_model_points(pc, dest, study_run_id, mp_run, aset)
    baseline = run_tev(dest, aset.id)
    return dest, aset.id, baseline.tev_run_id


def _make_minimal_assumption_set(**overrides) -> AssumptionSet:
    """Minimal AssumptionSet for unit tests (no DB required)."""
    defaults = dict(
        id=str(uuid.uuid4()),
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date="2024-01-01",
        author_id="test",
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
        mortality_multipliers=[],
        lapse_multipliers=[],
        surrender_multipliers=[],
        ci_incidence_multipliers=[],
        premium_persistency=[],
        shock_lapse_plt={},
        yaml_file_path="",
    )
    defaults.update(overrides)
    return AssumptionSet(**defaults)


def _make_term_model_point(n: int = 5, plan: str = "T20",
                           age: float = 40.0, dur: float = 5.0,
                           policies: int = 100) -> pd.DataFrame:
    """Synthetic TERM model point DataFrame for unit tests."""
    rows = []
    for i in range(n):
        rows.append({
            "model_point_id": str(uuid.uuid4()),
            "tev_run_id": str(uuid.uuid4()),
            "product_code": "TERM",
            "plan_code": plan,
            "gender": "M",
            "smoker_status": "NS",
            "risk_class": "STD_NS",
            "issue_age_band": "35-39",
            "attained_age_band": "40-44",
            "wtd_avg_attained_age": age + i * 2,
            "wtd_avg_issue_age": age - dur + i * 2,
            "wtd_avg_duration": dur,
            "duration_band": "1-5",
            "is_plt_flag": False,
            "premium_jump_ratio_band": None,
            "is_ulsg_flag": None,
            "av_band": None,
            "equity_allocation_band": None,
            "glwb_elected_flag": None,
            "surrender_charge_yr_band": None,
            "participating_flag": None,
            "policy_count": policies,
            "face_amount_total": 250_000.0 * policies,
            "reserve_total": 5_000.0 * policies,
            "account_value_total": 0.0,
            "premium_total": 600.0 * policies,
            "ci_rider_count": int(policies * 0.25),
            "ci_rider_sa_total": 125_000.0 * int(policies * 0.25),
            "required_capital": 5_000.0 * policies * 0.03,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Unit tests — compute_pvfp
# ---------------------------------------------------------------------------

class TestComputePVFP:

    def test_zero_bp_gives_zero_pvfp(self):
        bp = np.zeros((3, 20))
        weights = np.ones(3)
        assert compute_pvfp(bp, weights, rdr=0.09) == pytest.approx(0.0)

    def test_single_period_mid_year_discounting(self):
        """Single $1 BP at t=0 should discount at (1+RDR)^-0.5."""
        bp = np.zeros((1, 5))
        bp[0, 0] = 1.0
        weights = np.array([1.0])
        rdr = 0.09
        expected = 1.0 / (1.09 ** 0.5)
        assert compute_pvfp(bp, weights, rdr) == pytest.approx(expected, rel=1e-6)

    def test_constant_bp_over_many_periods(self):
        """Sum of discounted constant BP should match geometric series formula."""
        T = 30
        bp_val = 1000.0
        rdr = 0.09
        bp = np.full((1, T), bp_val)
        weights = np.array([1.0])
        # Mid-year: Σ bp_val * (1+r)^-(t+0.5) for t=0..T-1
        disc = (1 + rdr) ** (-(np.arange(T) + 0.5))
        expected = bp_val * disc.sum()
        result = compute_pvfp(bp, weights, rdr)
        assert result == pytest.approx(expected, rel=1e-6)

    def test_two_model_points_sum_correctly(self):
        bp = np.array([[100.0, 50.0, 0.0], [200.0, 100.0, 0.0]])
        weights = np.ones(2)
        rdr = 0.09
        disc = (1 + rdr) ** (-(np.arange(3) + 0.5))
        expected = (100 + 200) * disc[0] + (50 + 100) * disc[1]
        assert compute_pvfp(bp, weights, rdr) == pytest.approx(expected, rel=1e-6)

    def test_pvfp_decreases_with_higher_rdr(self):
        bp = np.full((2, 20), 1000.0)
        weights = np.ones(2)
        pvfp_low = compute_pvfp(bp, weights, rdr=0.05)
        pvfp_high = compute_pvfp(bp, weights, rdr=0.12)
        assert pvfp_low > pvfp_high

    def test_empty_bp_returns_zero(self):
        bp = np.zeros((0, 10))
        weights = np.zeros(0)
        assert compute_pvfp(bp, weights, rdr=0.09) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Unit tests — compute_pvcoc
# ---------------------------------------------------------------------------

class TestComputePVCoC:

    def test_zero_rc_gives_zero_pvcoc(self):
        rc = np.zeros((3, 20))
        weights = np.ones(3)
        assert compute_pvcoc(rc, weights, rdr=0.09, earned_rate_after_tax=0.04) == pytest.approx(0.0)

    def test_pvcoc_positive_when_rdr_gt_earned(self):
        rc = np.full((2, 10), 100_000.0)
        weights = np.ones(2)
        result = compute_pvcoc(rc, weights, rdr=0.09, earned_rate_after_tax=0.04)
        assert result > 0.0

    def test_pvcoc_increases_with_rc(self):
        weights = np.ones(1)
        rc_small = np.full((1, 10), 50_000.0)
        rc_large = np.full((1, 10), 200_000.0)
        pvcoc_small = compute_pvcoc(rc_small, weights, rdr=0.09, earned_rate_after_tax=0.04)
        pvcoc_large = compute_pvcoc(rc_large, weights, rdr=0.09, earned_rate_after_tax=0.04)
        assert pvcoc_large > pvcoc_small

    def test_pvcoc_increases_with_rdr(self):
        rc = np.full((2, 15), 100_000.0)
        weights = np.ones(2)
        pvcoc_low = compute_pvcoc(rc, weights, rdr=0.07, earned_rate_after_tax=0.04)
        pvcoc_high = compute_pvcoc(rc, weights, rdr=0.12, earned_rate_after_tax=0.04)
        assert pvcoc_high > pvcoc_low


# ---------------------------------------------------------------------------
# Unit tests — project_cashflows
# ---------------------------------------------------------------------------

class TestProjectCashflows:

    def test_returns_expected_keys(self):
        mp = _make_term_model_point(n=2)
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=10)
        assert "bp" in result
        assert "rc" in result
        assert "in_force" in result
        assert "initial_in_force" in result

    def test_output_shapes(self):
        n, T = 3, 15
        mp = _make_term_model_point(n=n)
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=T)
        assert result["bp"].shape == (n, T)
        assert result["rc"].shape == (n, T)

    def test_in_force_monotonically_declines(self):
        mp = _make_term_model_point(n=1)
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=15)
        inf = result["in_force"][0]
        # After first non-zero element, should be non-increasing
        nonzero = inf[inf > 1e-6]
        if len(nonzero) > 1:
            assert np.all(np.diff(nonzero) <= 1e-9), "in_force should not increase"

    def test_in_force_starts_at_policy_count(self):
        mp = _make_term_model_point(n=1, policies=200)
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=10)
        assert result["initial_in_force"][0] == pytest.approx(200.0)

    def test_empty_model_points_returns_zero_arrays(self):
        mp = pd.DataFrame()
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=10)
        assert result["bp"].shape[0] == 0

    def test_high_lapse_quickly_extinguishes_block(self):
        """With 99% annual lapse, block should be near-zero after a few years."""
        mp = _make_term_model_point(n=1, plan="T30", dur=1.0, age=30.0, policies=1000)
        aset = _make_minimal_assumption_set(
            lapse_multipliers=[
                DecrementMultiplier(
                    product="TERM", gender="M", risk_class="STD_NS",
                    duration_band=[1, 60], multiplier=33.0,  # ×33 → effectively 99%+
                    credibility_z=1.0, credibility_lower=0.5, credibility_upper=2.0,
                )
            ]
        )
        result = project_cashflows(mp, aset, "TERM", max_projection_years=20)
        # After 5 years with near-100% lapse, should be essentially zero
        assert result["in_force"][0, 4] < 1.0, "Block should be nearly extinct after 5yr of 99% lapse"

    def test_plt_shock_reduces_inforce_at_level_period_end(self):
        """TERM T10 at duration 8: PLT shock fires at projection index r=2 (remaining=10-8=2)."""
        mp = _make_term_model_point(n=1, plan="T10", dur=8.0, age=43.0, policies=1000)
        aset = _make_minimal_assumption_set()
        result = project_cashflows(mp, aset, "TERM", max_projection_years=10)
        inf = result["in_force"][0]
        # r = level_period - duration_0 = 10 - 8 = 2; shock fires at index 2.
        # Survival ratio at shock year (index 2) must be much lower than pre-shock year (index 1).
        frac_pre_shock = inf[1] / inf[0] if inf[0] > 0 else 1.0   # yr1→yr2: normal lapse
        frac_at_shock  = inf[2] / inf[1] if inf[1] > 0 else 1.0   # yr2→yr3: PLT shock
        assert frac_at_shock < frac_pre_shock * 0.5, (
            f"PLT shock should cause large in-force drop at index 2: "
            f"pre-shock survival {frac_pre_shock:.4f}, shock-year survival {frac_at_shock:.4f}"
        )


# ---------------------------------------------------------------------------
# Unit tests — apply_sensitivity_shock
# ---------------------------------------------------------------------------

class TestApplySensitivityShock:

    def _base_aset(self):
        lapse_mult = DecrementMultiplier(
            product="TERM", gender="M", risk_class="STD_NS",
            duration_band=[1, 20], multiplier=1.0,
            credibility_z=0.8, credibility_lower=0.8, credibility_upper=1.2,
        )
        mort_mult = DecrementMultiplier(
            product="TERM", gender="M", risk_class="STD_NS",
            duration_band=[1, 20], multiplier=0.92,
            credibility_z=0.85, credibility_lower=0.85, credibility_upper=1.05,
        )
        da_mort_mult = DecrementMultiplier(
            product="DA", gender="M", risk_class="STD_NS",
            duration_band=[1, 60], multiplier=1.0,
            credibility_z=0.7, credibility_lower=0.85, credibility_upper=1.15,
        )
        return _make_minimal_assumption_set(
            rdr=0.09,
            maintenance_per_policy=72.0,
            maintenance_pct_premium=0.02,
            lapse_multipliers=[lapse_mult],
            mortality_multipliers=[mort_mult, da_mort_mult],
        )

    def test_unknown_sensitivity_raises_key_error(self):
        aset = self._base_aset()
        with pytest.raises(KeyError, match="Unknown sensitivity_id"):
            apply_sensitivity_shock(aset, "SENS-99")

    def test_does_not_mutate_original(self):
        aset = self._base_aset()
        orig_mult = aset.lapse_multipliers[0].multiplier
        _ = apply_sensitivity_shock(aset, "SENS-01")
        assert aset.lapse_multipliers[0].multiplier == orig_mult

    def test_returns_fresh_id(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-01")
        assert perturbed.id != aset.id

    def test_sens01_lapse_minus10pct(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-01")
        assert perturbed.lapse_multipliers[0].multiplier == pytest.approx(0.9)

    def test_sens02_lapse_plus10pct(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-02")
        assert perturbed.lapse_multipliers[0].multiplier == pytest.approx(1.1)

    def test_sens03_mortality_life_minus5pct(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-03")
        term_mult = next(m for m in perturbed.mortality_multipliers if m.product == "TERM")
        assert term_mult.multiplier == pytest.approx(0.92 * 0.95, rel=1e-6)

    def test_sens03_does_not_affect_da_mortality(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-03")
        da_mult = next(m for m in perturbed.mortality_multipliers if m.product == "DA")
        assert da_mult.multiplier == pytest.approx(1.0)

    def test_sens05_longevity_affects_da_only(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-05")
        da_mult = next(m for m in perturbed.mortality_multipliers if m.product == "DA")
        assert da_mult.multiplier == pytest.approx(1.0 * 0.95, rel=1e-6)
        term_mult = next(m for m in perturbed.mortality_multipliers if m.product == "TERM")
        assert term_mult.multiplier == pytest.approx(0.92)

    def test_sens08_expense_minus10pct(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-08")
        assert perturbed.maintenance_per_policy == pytest.approx(72.0 * 0.90, rel=1e-6)
        assert perturbed.maintenance_pct_premium == pytest.approx(0.02 * 0.90, rel=1e-6)

    def test_sens09_expense_plus10pct(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-09")
        assert perturbed.maintenance_per_policy == pytest.approx(72.0 * 1.10, rel=1e-6)

    def test_sens10_rdr_plus100bp(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-10")
        assert perturbed.rdr == pytest.approx(0.09 + 0.01, rel=1e-6)

    def test_sens11_rdr_minus100bp(self):
        aset = self._base_aset()
        perturbed = apply_sensitivity_shock(aset, "SENS-11")
        assert perturbed.rdr == pytest.approx(0.09 - 0.01, rel=1e-6)

    def test_all_11_sensitivities_defined(self):
        assert len(SENSITIVITY_DEFINITIONS) == 11
        for i in range(1, 12):
            assert f"SENS-{i:02d}" in SENSITIVITY_DEFINITIONS


# ---------------------------------------------------------------------------
# Integration tests — sensitivity grid directional checks (requires live DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _db_available(), reason="DuckDB not available")
class TestSensitivityGridDirectional:
    """Directional tests per NFR-C-07 and FR-2-22/23."""

    @pytest.fixture(scope="class")
    def sensitivity_grid(self, tev_baseline):
        db_path, aset_id, baseline_id = tev_baseline
        return run_sensitivity_grid(
            db_path=db_path,
            assumption_set_id=aset_id,
            baseline_tev_run_id=baseline_id,
        )

    def test_impact_matrix_has_correct_columns(self, sensitivity_grid):
        df = sensitivity_grid.impact_matrix_df
        for sens_id in SENSITIVITY_DEFINITIONS:
            assert sens_id in df.columns, f"Missing column {sens_id}"
        assert "total_sensitivity_range" in df.columns

    def test_impact_matrix_has_total_row(self, sensitivity_grid):
        assert "TOTAL" in sensitivity_grid.impact_matrix_df.index

    def test_impact_matrix_has_all_products(self, sensitivity_grid):
        df = sensitivity_grid.impact_matrix_df
        for prod in ["DA", "TERM", "UL", "ULSG", "VUL", "WL"]:
            assert prod in df.index, f"Missing product row {prod}"

    def test_11_sensitivity_results(self, sensitivity_grid):
        assert len(sensitivity_grid.sensitivity_results) == 11

    # NFR-C-07: Lower lapse → lower TEV for pure protection (TERM)
    def test_nfr_c07_lapse_minus_hurts_term(self, sensitivity_grid):
        """SENS-01 (lapse -10%) should reduce TERM TEV (fewer profitable lapses)."""
        delta = sensitivity_grid.impact_matrix_df.loc["TERM", "SENS-01"]
        assert delta < 0, f"Expected negative ΔTEV for TERM on SENS-01, got {delta:,.0f}"

    # NFR-C-07: Lower lapse → higher TEV for lapse-supported (ULSG)
    def test_nfr_c07_lapse_minus_helps_ulsg(self, sensitivity_grid):
        """SENS-01 (lapse -10%) should increase ULSG TEV (lower lapse = better for ULSG)."""
        delta = sensitivity_grid.impact_matrix_df.loc["ULSG", "SENS-01"]
        assert delta > 0, f"Expected positive ΔTEV for ULSG on SENS-01, got {delta:,.0f}"

    def test_sens01_and_sens02_directionally_opposite_total(self, sensitivity_grid):
        """SENS-01 and SENS-02 should move total TEV in opposite directions."""
        df = sensitivity_grid.impact_matrix_df
        delta01 = df.loc["TOTAL", "SENS-01"]
        delta02 = df.loc["TOTAL", "SENS-02"]
        assert delta01 * delta02 < 0, "SENS-01 and SENS-02 should move TEV in opposite directions"

    # FR-2-22: Mortality -5% (SENS-03) improves TERM TEV (fewer death claims)
    def test_mort_minus_helps_term(self, sensitivity_grid):
        """Lower mortality reduces death claims for TERM — TEV should improve."""
        delta = sensitivity_grid.impact_matrix_df.loc["TERM", "SENS-03"]
        assert delta > 0, f"Expected positive ΔTEV for TERM on SENS-03, got {delta:,.0f}"

    def test_sens03_and_sens04_directionally_opposite_term(self, sensitivity_grid):
        df = sensitivity_grid.impact_matrix_df
        d3 = df.loc["TERM", "SENS-03"]
        d4 = df.loc["TERM", "SENS-04"]
        assert d3 * d4 < 0, "SENS-03 and SENS-04 should have opposite signs for TERM"

    # FR-2-20/21: Higher RDR → lower PVCoC but also lower PVFP → net lower TEV
    def test_rdr_plus_reduces_total_tev(self, sensitivity_grid):
        """SENS-10 (RDR +100bp) should reduce total TEV (higher discount rate)."""
        delta = sensitivity_grid.impact_matrix_df.loc["TOTAL", "SENS-10"]
        assert delta < 0, f"Expected negative total ΔTEV on SENS-10, got {delta:,.0f}"

    def test_rdr_minus_increases_total_tev(self, sensitivity_grid):
        """SENS-11 (RDR -100bp) should increase total TEV (lower discount rate)."""
        delta = sensitivity_grid.impact_matrix_df.loc["TOTAL", "SENS-11"]
        assert delta > 0, f"Expected positive total ΔTEV on SENS-11, got {delta:,.0f}"

    def test_sens10_sens11_approximately_symmetric(self, sensitivity_grid):
        """SENS-10 and SENS-11 should produce approximately symmetric ΔTEV."""
        df = sensitivity_grid.impact_matrix_df
        d10 = abs(df.loc["TOTAL", "SENS-10"])
        d11 = abs(df.loc["TOTAL", "SENS-11"])
        ratio = max(d10, d11) / max(min(d10, d11), 1e-9)
        assert ratio < 2.0, f"SENS-10/11 asymmetry too large: {d10:,.0f} vs {d11:,.0f}"

    def test_expense_sens08_and_sens09_opposite(self, sensitivity_grid):
        """SENS-08 (expense -10%) and SENS-09 (expense +10%) should have opposite signs."""
        df = sensitivity_grid.impact_matrix_df
        d8 = df.loc["TOTAL", "SENS-08"]
        d9 = df.loc["TOTAL", "SENS-09"]
        assert d8 * d9 < 0, "SENS-08 and SENS-09 should move TEV in opposite directions"

    def test_expense_minus_increases_tev(self, sensitivity_grid):
        """SENS-08 (lower expenses) should increase TEV."""
        d8 = sensitivity_grid.impact_matrix_df.loc["TOTAL", "SENS-08"]
        assert d8 > 0, f"Expected positive ΔTEV for SENS-08, got {d8:,.0f}"

    def test_total_sensitivity_range_column_positive(self, sensitivity_grid):
        df = sensitivity_grid.impact_matrix_df
        assert (df["total_sensitivity_range"] >= 0).all()

    def test_runtime_within_spec(self, sensitivity_grid):
        """11 sensitivities should complete well within the 30s spec target."""
        total_sec = sum(
            r.duration_sec for r in sensitivity_grid.sensitivity_results
        )
        assert total_sec < 30.0, f"Sensitivity grid took {total_sec:.1f}s (limit: 30s)"


# ---------------------------------------------------------------------------
# Integration test — baseline TEV sanity (requires live DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _db_available(), reason="DuckDB not available")
class TestBaselineTEVSanity:

    @pytest.fixture(scope="class")
    def baseline_result(self, tev_baseline):
        import duckdb
        db_path, _, baseline_id = tev_baseline
        con = duckdb.connect(str(db_path))
        row = con.execute(
            "SELECT tev_run_id, total_anw, total_pvfp, total_pvcoc, total_vif, total_tev "
            "FROM gold_tev_run_log WHERE tev_run_id = ?",
            [baseline_id],
        ).fetchone()
        con.close()
        if row is None:
            pytest.skip("No baseline TEV run in DB")
        return {
            "db_path": db_path,
            "tev_run_id": row[0],
            "total_anw": row[1],
            "total_pvfp": row[2],
            "total_pvcoc": row[3],
            "total_vif": row[4],
            "total_tev": row[5],
        }

    def test_anw_positive(self, baseline_result):
        assert baseline_result["total_anw"] > 0

    def test_tev_identity_holds(self, baseline_result):
        """TEV = ANW + VIF = ANW + (PVFP - PVCoC)."""
        r = baseline_result
        vif = r["total_pvfp"] - r["total_pvcoc"]
        tev = r["total_anw"] + vif
        assert tev == pytest.approx(r["total_tev"], rel=1e-4)

    def test_pvcoc_positive(self, baseline_result):
        """Cost of capital must be positive (RC is positive, RDR > earned_after_tax)."""
        assert baseline_result["total_pvcoc"] > 0

    def test_anw_product_sum_matches_total(self, baseline_result):
        """Sum of per-product ANW should equal total ANW."""
        import duckdb
        con = duckdb.connect(str(baseline_result["db_path"]))
        rows = con.execute(
            "SELECT SUM(anw) FROM gold_tev_results WHERE tev_run_id = ? AND sensitivity_id IS NULL",
            [baseline_result["tev_run_id"]],
        ).fetchone()
        con.close()
        product_sum = rows[0] if rows and rows[0] is not None else 0.0
        assert product_sum == pytest.approx(baseline_result["total_anw"], rel=1e-3)

    def test_da_vif_positive(self, baseline_result):
        """DA (annuity) should have positive VIF (profitable spread business)."""
        import duckdb
        con = duckdb.connect(str(baseline_result["db_path"]), read_only=True)
        row = con.execute(
            "SELECT pvfp, pvcoc FROM gold_tev_results "
            "WHERE tev_run_id = ? AND product_code = 'DA' AND sensitivity_id IS NULL",
            [baseline_result["tev_run_id"]],
        ).fetchone()
        con.close()
        if row:
            assert row[0] > row[1], "DA PVFP should exceed PVCoC"

    def test_ul_vif_positive(self, baseline_result):
        import duckdb
        con = duckdb.connect(str(baseline_result["db_path"]), read_only=True)
        row = con.execute(
            "SELECT pvfp, pvcoc FROM gold_tev_results "
            "WHERE tev_run_id = ? AND product_code = 'UL' AND sensitivity_id IS NULL",
            [baseline_result["tev_run_id"]],
        ).fetchone()
        con.close()
        if row:
            assert row[0] > row[1], "UL PVFP should exceed PVCoC"
