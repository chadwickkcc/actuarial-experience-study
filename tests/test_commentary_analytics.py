"""Management-commentary analytics + skill tests (demo refresh P6).

Attribution hand-calc (contributions sum EXACTLY to the YoY A/E change),
trend-classification edges, config validation, fact-pack v2 shape, the
planted-story realdata locks, and the skill's block-not-repair guardrail.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pytest

from src.analysis import (
    attribute_drivers,
    classify_trends,
    compute_yoy_movement,
    load_commentary_config,
    movement_legs,
)
from src.utils.db_init import init_database

_DB = Path("data/experience_study.duckdb")
_needs_db = pytest.mark.skipif(not _DB.exists(), reason="live demo DB not present")

_RUN = "run-commentary-test"


def _seed(db: Path, rows: list[tuple]) -> None:
    """rows: (calendar_year, gender, actual_deaths, expected_deaths)."""
    con = duckdb.connect(str(db))
    try:
        for year, gender, a, e in rows:
            con.execute(
                "INSERT INTO gold_ae_results (result_id, study_run_id, "
                "product_code, gender, calendar_year, actual_deaths_count, "
                "expected_deaths_count, anti_selection_flag, _created_ts) "
                "VALUES (?, ?, 'TERM', ?, ?, ?, ?, FALSE, CURRENT_TIMESTAMP)",
                [str(uuid.uuid4()), _RUN, gender, year, a, e],
            )
    finally:
        con.close()


@pytest.fixture()
def tiny_db(tmp_path: Path) -> Path:
    db = tmp_path / "commentary.duckdb"
    init_database(db)
    return db


class TestAttributionHandCalc:
    def test_contributions_sum_exactly_to_delta(self, tiny_db):
        # 2022: M 10/20, F 10/20  -> AE = 20/40 = 0.5
        # 2023: M 24/20, F 12/20  -> AE = 36/40 = 0.9   => delta = +0.4
        _seed(tiny_db, [
            (2022, "M", 10, 20.0), (2022, "F", 10, 20.0),
            (2023, "M", 24, 20.0), (2023, "F", 12, 20.0),
        ])
        attr = attribute_drivers(tiny_db, _RUN, "MORTALITY", 2023, "gender")
        assert attr["delta_ae"] == pytest.approx(0.4)
        by_seg = {c["segment"]: c["contribution"] for c in attr["contributions"]}
        # Hand-calc: M contributes 24/40 - 10/40 = 0.35; F contributes 12/40 - 10/40 = 0.05.
        assert by_seg["M"] == pytest.approx(0.35)
        assert by_seg["F"] == pytest.approx(0.05)
        assert sum(by_seg.values()) == pytest.approx(attr["delta_ae"])

    def test_segment_absent_in_one_year_counts_from_zero(self, tiny_db):
        _seed(tiny_db, [
            (2022, "M", 10, 20.0),
            (2023, "M", 10, 20.0), (2023, "F", 5, 10.0),
        ])
        attr = attribute_drivers(tiny_db, _RUN, "MORTALITY", 2023, "gender")
        by_seg = {c["segment"]: c["contribution"] for c in attr["contributions"]}
        # F was absent in 2022: contribution = 5/30 - 0 = 1/6.
        assert by_seg["F"] == pytest.approx(5 / 30, abs=1e-6)  # stored rounded to 6 dp
        assert sum(by_seg.values()) == pytest.approx(attr["delta_ae"])

    def test_missing_year_returns_none_delta(self, tiny_db):
        _seed(tiny_db, [(2023, "M", 10, 20.0)])
        attr = attribute_drivers(tiny_db, _RUN, "MORTALITY", 2023, "gender")
        assert attr["delta_ae"] is None and attr["contributions"] == []

    def test_disallowed_dimension_raises(self, tiny_db):
        with pytest.raises(ValueError):
            attribute_drivers(tiny_db, _RUN, "MORTALITY", 2023, "policy_id")


class TestYoYAndTrends:
    def test_yoy_ratio_of_sums_and_delta(self, tiny_db):
        _seed(tiny_db, [
            (2021, "M", 10, 20.0), (2022, "M", 15, 20.0), (2023, "M", 20, 20.0),
        ])
        yoy = compute_yoy_movement(tiny_db, _RUN, "MORTALITY")
        assert [r["ae"] for r in yoy] == [0.5, 0.75, 1.0]
        assert yoy[0]["delta_vs_prior"] is None
        assert yoy[2]["delta_vs_prior"] == pytest.approx(0.25)

    def test_trend_monotone_up_is_worsening(self, tiny_db):
        _seed(tiny_db, [(y, "M", a, 20.0) for y, a in
                        [(2021, 10), (2022, 15), (2023, 20)]])
        t = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert t["classification"] == "worsening"
        assert t["slope"] == pytest.approx(0.25, abs=1e-6)

    def test_trend_monotone_down_is_improving(self, tiny_db):
        _seed(tiny_db, [(y, "M", a, 20.0) for y, a in
                        [(2021, 20), (2022, 15), (2023, 10)]])
        t = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert t["classification"] == "improving"

    def test_trend_flat_is_stable(self, tiny_db):
        _seed(tiny_db, [(y, "M", 10, 20.0) for y in (2021, 2022, 2023)])
        t = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert t["classification"] == "stable"
        assert t["slope"] == pytest.approx(0.0, abs=1e-9)

    def test_trend_insufficient_years(self, tiny_db):
        _seed(tiny_db, [(2022, "M", 10, 20.0), (2023, "M", 12, 20.0)])
        t = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert t["classification"] == "insufficient_data"
        assert t["slope"] is None


class TestConfigValidation:
    def test_shipped_config_loads(self):
        cfg = load_commentary_config()
        assert "MORTALITY" in cfg["attribution_dimensions"]

    def test_bad_dimension_raises(self, tmp_path):
        bad = tmp_path / "c.yaml"
        bad.write_text(
            "attribution_dimensions:\n  MORTALITY: [policy_id]\n"
            "trend: {window_years: 3, stable_slope_threshold: 0.02}\n"
        )
        with pytest.raises(ValueError, match="not permitted"):
            load_commentary_config(bad)

    def test_missing_trend_block_raises(self, tmp_path):
        bad = tmp_path / "c.yaml"
        bad.write_text("attribution_dimensions:\n  MORTALITY: [gender]\n")
        with pytest.raises(ValueError, match="trend"):
            load_commentary_config(bad)


# --------------------------------------------------------------------------- #
# Realdata: planted stories drive the analytics (skip when DB absent)         #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def live_run(prod_db):
    con = duckdb.connect(str(prod_db), read_only=True)
    try:
        return prod_db, con.execute(
            "SELECT run_id FROM gold_study_runs ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        con.close()


@_needs_db
def test_planted_mortality_story_classified_worsening(live_run):
    db, run = live_run
    t = classify_trends(db, run, "MORTALITY")
    assert t["classification"] == "worsening"
    assert t["slope"] > 0


@_needs_db
def test_planted_lapse_spike_is_top_movement(live_run):
    """2022–23 carry the largest YoY lapse increases of the study window."""
    db, run = live_run
    yoy = compute_yoy_movement(db, run, "LAPSE")
    deltas = {r["year"]: r["delta_vs_prior"] for r in yoy if r["delta_vs_prior"] is not None}
    top_two = sorted(deltas, key=lambda y: deltas[y], reverse=True)[:2]
    assert set(top_two) & {2022, 2023}, f"lapse spike years missing from top movers: {deltas}"


@_needs_db
def test_live_attribution_sums_exactly(live_run):
    db, run = live_run
    for dim in ("attained_age_band", "gender"):
        attr = attribute_drivers(db, run, "MORTALITY", 2023, dim)
        total = sum(c["contribution"] for c in attr["contributions"])
        assert total == pytest.approx(attr["delta_ae"], abs=1e-9)


@_needs_db
def test_fact_pack_v2_shape(live_run):
    from ui.skills_logic import assemble_commentary_facts

    db, run = live_run
    facts = assemble_commentary_facts(db, run)
    for key in ("yoy", "trends", "justification", "movement"):
        assert key in facts, f"fact-pack v2 key missing: {key}"
    assert facts["yoy"]["MORTALITY"], "mortality YoY rows expected"
    latest = facts["yoy"]["MORTALITY"][-1]
    assert "top_drivers" in latest and latest["top_drivers"]
    assert any(t["product"] == "ALL" and t["decrement"] == "MORTALITY"
               and t["classification"] == "worsening" for t in facts["trends"])
    assert facts["movement"], "movement legs expected (per-product recon)"


# --------------------------------------------------------------------------- #
# Skill guardrails (offline stub provider)                                    #
# --------------------------------------------------------------------------- #

def _stub(text):
    from src.utils.types import LLMResponse

    class _S:
        name = "stub"

        def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
            return LLMResponse(
                text=text, input_tokens=8, output_tokens=16,
                provider="stub", model=model, latency_ms=0.0, stop_reason="end_turn",
            )

    return _S()


_FACTS = {
    "run_id": "r-1",
    "study_years": [2021, 2022, 2023],
    "yoy": {"MORTALITY": [
        {"year": 2022, "ae": 0.6969, "delta_vs_prior": 0.0974},
        {"year": 2023, "ae": 0.7691, "delta_vs_prior": 0.0722,
         "top_drivers": {"attained_age_band": [
             {"segment": "70-74", "contribution": 0.034}]}},
    ]},
    "trends": [{"product": "ALL", "decrement": "MORTALITY",
                "classification": "worsening", "slope": 0.0848,
                "years_used": [2021, 2022, 2023]}],
    "justification": [{"product": "WL", "decrement": "MORTALITY",
                       "overall_ae": 0.6561, "credibility_z": 0.7515,
                       "cred_wtd_ae": 0.7416, "current_multiplier": 1.0,
                       "be_gap": -0.3439}],
    "movement": [],
}


def test_management_commentary_clean_draft_carries_banner():
    from src.ai.llm.client import load_llm_config
    from src.ai.skills.management_commentary import draft_management_commentary

    cfg = load_llm_config(Path("config/llm_config.yaml"))
    body = (
        "## Year-on-Year Movement and Key Drivers\n"
        "Mortality A/E rose to 0.7691 in 2023 (0.0722 above prior), led by the "
        "70-74 band contributing 0.034.\n\n"
        "## Experience Trends\nThe classification is worsening with slope 0.0848.\n\n"
        "## Proposed Management Actions\nManagement could review underwriting for "
        "the older-age segments driving the deterioration.\n\n"
        "## Assumption Justification\nWL mortality runs at 0.6561 with credibility "
        "0.7515, giving a credibility-weighted 0.7416 against the current 1.0."
    )
    out = draft_management_commentary(_FACTS, cfg, "claude-sonnet-4-6", provider=_stub(body))
    assert out["blocked"] is False
    assert out["markdown"].startswith("**AI-DRAFT")
    assert "## Proposed Management Actions" in out["markdown"]


def test_management_commentary_blocks_invented_number():
    from src.ai.llm.client import load_llm_config
    from src.ai.skills.management_commentary import draft_management_commentary

    cfg = load_llm_config(Path("config/llm_config.yaml"))
    out = draft_management_commentary(
        _FACTS, cfg, "claude-sonnet-4-6",
        provider=_stub("Mortality deteriorated by 42.7 percent."),
    )
    assert out["blocked"] is True
    assert out["untraceable_nums"]


def test_management_commentary_blocks_empty():
    from src.ai.llm.client import load_llm_config
    from src.ai.skills.management_commentary import draft_management_commentary

    cfg = load_llm_config(Path("config/llm_config.yaml"))
    out = draft_management_commentary(
        _FACTS, cfg, "claude-sonnet-4-6", provider=_stub("  ")
    )
    assert out["blocked"] is True


# ---------------------------------------------------------------------------
# Trend classification needs a volume floor (adversarial review M-8)
# ---------------------------------------------------------------------------

class TestTrendVolumeGuard:
    """A trend badge asserts something about experience. With no claims there is
    no experience to have a trend, yet the classifier reported "DA lapse: stable"
    for products with literally zero lapses ever, and "DA_VA mortality: improving"
    off two claims — and those rows reached the AI fact pack as fact."""

    def test_zero_experience_is_insufficient_data(self, tiny_db):
        # three years of real exposure, but not a single claim
        _seed(tiny_db, [
            (2021, "M", 0, 100.0), (2022, "M", 0, 100.0), (2023, "M", 0, 100.0),
        ])
        res = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert res["classification"] == "insufficient_data", (
            f'zero claims must not yield a confident badge, got {res["classification"]!r}'
        )
        assert res["slope"] is None

    def test_below_floor_experience_is_insufficient_data(self, tiny_db):
        _seed(tiny_db, [
            (2021, "M", 1, 10.0), (2022, "M", 1, 10.0), (2023, "M", 0, 10.0),
        ])
        res = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert res["classification"] == "insufficient_data"

    def test_ample_experience_still_classifies(self, tiny_db):
        _seed(tiny_db, [
            (2021, "M", 60, 100.0), (2022, "M", 80, 100.0), (2023, "M", 100, 100.0),
        ])
        res = classify_trends(tiny_db, _RUN, "MORTALITY")
        assert res["classification"] == "worsening"
        assert res["slope"] is not None


class TestReturnedContributionsSumExactly:
    """m-5: the maths was exact but the ROUNDED payload was not — independent
    per-segment rounding drifted by up to n x 5e-7, exceeding the 1e-9 the
    docstring, the progress notes and the page caption all promised."""

    @_needs_db
    @pytest.mark.parametrize("decrement,dimension", [
        ("MORTALITY", "attained_age_band"),
        ("MORTALITY", "gender"),
        ("LAPSE", "product_code"),
        ("LAPSE", "policy_year"),
    ])
    def test_payload_sums_to_the_returned_delta(self, decrement, dimension):
        import duckdb

        con = duckdb.connect(str(_DB), read_only=True)
        try:
            run = con.execute(
                "SELECT run_id FROM gold_study_runs WHERE status='COMPLETE' "
                "ORDER BY run_ts DESC LIMIT 1"
            ).fetchone()[0]
            years = [r[0] for r in con.execute(
                "SELECT DISTINCT calendar_year FROM gold_ae_results "
                "WHERE calendar_year IS NOT NULL ORDER BY 1"
            ).fetchall()]
        finally:
            con.close()

        checked = 0
        for year in years[1:]:
            attr = attribute_drivers(_DB, run, decrement, year, dimension)
            if attr["delta_ae"] is None or not attr["contributions"]:
                continue
            total = sum(c["contribution"] for c in attr["contributions"])
            assert abs(total - attr["delta_ae"]) < 1e-9, (
                f"{decrement}/{dimension}/{year}: returned contributions sum to "
                f"{total!r}, delta_ae is {attr['delta_ae']!r}"
            )
            checked += 1
        assert checked, "no transitions exercised"


class TestCommentaryInputGuards:
    """m-16: an unknown decrement raised a bare KeyError where the dimension guard
    raised a descriptive ValueError, and ``years=[]`` silently returned everything."""

    def test_unknown_decrement_raises_a_clear_error(self, tiny_db):
        for fn, args in (
            (compute_yoy_movement, (tiny_db, _RUN, "NOT_A_DECREMENT")),
            (classify_trends, (tiny_db, _RUN, "NOT_A_DECREMENT")),
        ):
            with pytest.raises(ValueError, match="not recognised"):
                fn(*args)

    def test_empty_year_filter_means_no_years(self, tiny_db):
        assert movement_legs(tiny_db, _RUN, years=[]) == []

    def test_none_year_filter_means_all_years(self, tiny_db):
        assert movement_legs(tiny_db, _RUN, years=None) == []
