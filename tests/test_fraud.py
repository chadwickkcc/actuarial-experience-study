"""Fraud-detection engine tests (demo refresh P5).

Per-rule fire/null fixtures, composite hand-calc on the live ring, config
validation, the planted-ring realdata checks, the PII bright line on the
narrative fact pack, and the narrative skill's block-not-repair guardrail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.fraud.rules import (
    ALL_RULES,
    rule_agency_office_concentration,
    rule_claim_above_materiality,
    rule_claim_exceeds_premiums,
    rule_first_policy_year_claim,
    rule_high_risk_region_or_hospital,
    rule_similar_claims_same_claimant,
)
from src.fraud.runner import load_fraud_config, run_fraud_scan

_DB = Path("data/experience_study.duckdb")
_CFG_PATH = Path("config/fraud_config.yaml")


# --------------------------------------------------------------------------- #
# Unit fixtures                                                               #
# --------------------------------------------------------------------------- #

def _cfg() -> dict:
    return load_fraud_config(_CFG_PATH)


def _claims(rows: list[dict]) -> pd.DataFrame:
    base = {
        "claim_event_id": "C-1", "policy_id": "C-1", "product_code": "TERM",
        "event_type": "DEATH", "event_date": "2023-06-01",
        "issue_date": "2015-01-01", "policy_days": 3000,
        "claim_amount": 250000.0, "annual_premium": 2000.0,
        "illness_code": None, "agency_office_id": "OFF-001",
        "agent_id": "AGT-0011", "claimant_id": None,
        "hospital_id": "HOSP-001", "claim_region": "MIDWEST",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


class TestRuleUnits:
    def test_first_year_fires_and_stays_silent(self):
        df = _claims([
            {"claim_event_id": "EARLY", "policy_days": 120},
            {"claim_event_id": "LATE", "policy_days": 2000},
        ])
        rr = rule_first_policy_year_claim(df, _cfg())
        assert rr.hit_claim_ids == ["EARLY"]
        assert rr.evidence["EARLY"]["policy_days"] == 120

    def test_exceeds_premiums_ratio(self):
        # 250k claim / (2k x 1yr) = 125x fires; 250k / (50k x 5yr) = 1x silent.
        df = _claims([
            {"claim_event_id": "HI", "policy_days": 200, "annual_premium": 2000.0},
            {"claim_event_id": "LO", "policy_days": 1830, "annual_premium": 50000.0},
        ])
        rr = rule_claim_exceeds_premiums(df, _cfg())
        assert rr.hit_claim_ids == ["HI"]

    def test_materiality_thresholds_per_type(self):
        df = _claims([
            {"claim_event_id": "BIGD", "event_type": "DEATH", "claim_amount": 2_000_000.0},
            {"claim_event_id": "SMALLD", "event_type": "DEATH", "claim_amount": 400_000.0},
            {"claim_event_id": "BIGCI", "event_type": "CI_CLAIM", "claim_amount": 250_000.0},
        ])
        rr = rule_claim_above_materiality(df, _cfg())
        assert set(rr.hit_claim_ids) == {"BIGD", "BIGCI"}

    def test_office_concentration_first_year_cluster(self):
        rows = []
        # Hot office: 6 first-year claims; five other offices with 1 each.
        for i in range(6):
            rows.append({"claim_event_id": f"HOT-{i}", "policy_days": 100,
                         "agency_office_id": "OFF-099"})
        for i in range(5):
            rows.append({"claim_event_id": f"ORG-{i}", "policy_days": 100,
                         "agency_office_id": f"OFF-00{i + 1}"})
        rr = rule_agency_office_concentration(_claims(rows), _cfg())
        assert {c for c in rr.hit_claim_ids} == {f"HOT-{i}" for i in range(6)}
        assert rr.evidence["HOT-0"]["office"] == "OFF-099"

    def test_office_concentration_null_case(self):
        # Even spread: nobody clears max(3x median, min_claims).
        rows = [
            {"claim_event_id": f"C-{i}", "policy_days": 100,
             "agency_office_id": f"OFF-{i:03d}"}
            for i in range(8)
        ]
        rr = rule_agency_office_concentration(_claims(rows), _cfg())
        assert rr.hit_claim_ids == []

    def test_similar_claims_same_claimant(self):
        rows = [
            {"claim_event_id": "A", "event_type": "CI_CLAIM", "claimant_id": "X",
             "illness_code": "CI-001", "claim_amount": 60000.0},
            {"claim_event_id": "B", "event_type": "CI_CLAIM", "claimant_id": "X",
             "illness_code": "CI-001", "claim_amount": 62000.0},
            # different illness — not similar
            {"claim_event_id": "C", "event_type": "CI_CLAIM", "claimant_id": "X",
             "illness_code": "CI-004", "claim_amount": 61000.0},
            # amount far off — not similar
            {"claim_event_id": "D", "event_type": "CI_CLAIM", "claimant_id": "Y",
             "illness_code": "CI-002", "claim_amount": 10000.0},
            {"claim_event_id": "E", "event_type": "CI_CLAIM", "claimant_id": "Y",
             "illness_code": "CI-002", "claim_amount": 90000.0},
        ]
        rr = rule_similar_claims_same_claimant(_claims(rows), _cfg())
        assert set(rr.hit_claim_ids) == {"A", "B"}
        assert rr.evidence["A"]["claims_in_cluster"] == 2

    def test_high_risk_region_or_hospital(self):
        df = _claims([
            {"claim_event_id": "H", "hospital_id": "HOSP-066"},
            {"claim_event_id": "R", "claim_region": "SOUTHWEST"},
            {"claim_event_id": "N", "hospital_id": "HOSP-001",
             "claim_region": "MIDWEST"},
        ])
        rr = rule_high_risk_region_or_hospital(df, _cfg())
        assert set(rr.hit_claim_ids) == {"H", "R"}

    def test_all_six_rules_registered(self):
        assert len(ALL_RULES) == 6


# --------------------------------------------------------------------------- #
# Config validation — a bad config fails loudly                               #
# --------------------------------------------------------------------------- #

class TestConfigValidation:
    def test_missing_weight_raises(self, tmp_path):
        bad = tmp_path / "fraud.yaml"
        bad.write_text(
            "rules:\n  first_policy_year_claim: {max_policy_days: 366}\n"
            "scoring: {flag_threshold: 0.5}\n"
        )
        with pytest.raises(ValueError, match="no weight"):
            load_fraud_config(bad)

    def test_missing_flag_threshold_raises(self, tmp_path):
        bad = tmp_path / "fraud.yaml"
        bad.write_text("rules:\n  r1: {weight: 0.5}\nscoring: {}\n")
        with pytest.raises(ValueError, match="flag_threshold"):
            load_fraud_config(bad)

    def test_out_of_range_weight_raises(self, tmp_path):
        bad = tmp_path / "fraud.yaml"
        bad.write_text("rules:\n  r1: {weight: 1.5}\nscoring: {flag_threshold: 0.5}\n")
        with pytest.raises(ValueError, match="outside"):
            load_fraud_config(bad)

    def test_shipped_config_loads(self):
        cfg = _cfg()
        assert set(cfg["rules"]) >= {
            "first_policy_year_claim", "claim_exceeds_premiums",
            "claim_above_materiality", "agency_office_concentration",
            "similar_claims_same_claimant", "high_risk_region_or_hospital",
        }


# --------------------------------------------------------------------------- #
# Realdata: the planted ring is surfaced (skip when the live DB is absent)    #
# --------------------------------------------------------------------------- #

pytest.importorskip("duckdb")
_needs_db = pytest.mark.skipif(not _DB.exists(), reason="live demo DB not present")


@pytest.fixture(scope="module")
def live_scan(prod_db):
    """Run a non-persisting scan against the session prod-DB copy."""
    import duckdb

    con = duckdb.connect(str(prod_db), read_only=True)
    try:
        run_id = con.execute(
            "SELECT run_id FROM gold_study_runs ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        con.close()
    return run_fraud_scan(prod_db, run_id, run_by="pytest", persist=False)


@_needs_db
def test_ring_is_flagged(live_scan):
    flagged = live_scan.scores_df[live_scan.scores_df["flagged"]]
    ring = flagged[flagged["agency_office_id"] == "OFF-013"]
    assert len(ring) >= 4, f"ring under-detected: {len(ring)} OFF-013 flags"
    # The ghost hospital routes the ring claims.
    assert (flagged["hospital_id"] == "HOSP-066").sum() >= 10


@_needs_db
def test_ring_tops_the_concentrations(live_scan):
    flagged = live_scan.scores_df[live_scan.scores_df["flagged"]]
    top_office = flagged.groupby("agency_office_id").size().idxmax()
    top_hospital = (
        flagged[flagged["hospital_id"].notna()].groupby("hospital_id").size().idxmax()
    )
    assert top_office == "OFF-013"
    assert top_hospital == "HOSP-066"


@_needs_db
def test_shared_claimant_cluster_scores_highest(live_scan):
    """The four shared-claimant ring claims carry the maximum composite score,
    equal to the hand-computed sum of the weights of the rules they fire."""
    cfg = _cfg()
    r = cfg["rules"]
    expected = round(
        r["first_policy_year_claim"]["weight"]
        + r["claim_exceeds_premiums"]["weight"]
        + r["agency_office_concentration"]["weight"]
        + r["similar_claims_same_claimant"]["weight"]
        + r["high_risk_region_or_hospital"]["weight"],
        6,
    )
    df = live_scan.scores_df
    cluster = df[df["claimant_id"] == "CLM-424242"]
    assert len(cluster) == 4
    assert all(abs(cluster["composite_score"] - expected) < 1e-9)
    assert float(df["composite_score"].max()) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# PII bright line + narrative guardrail                                       #
# --------------------------------------------------------------------------- #

@_needs_db
def test_fraud_fact_pack_carries_no_person_identifiers(prod_db):
    """The narrative fact pack is aggregates + institutional ids only."""
    from ui.fraud_logic import assemble_fraud_facts, latest_scan
    from src.fraud.runner import run_fraud_scan as _scan

    import duckdb

    scan = latest_scan(prod_db)
    if scan is None:
        con = duckdb.connect(str(prod_db), read_only=True)
        try:
            run_id = con.execute(
                "SELECT run_id FROM gold_study_runs ORDER BY run_ts DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            con.close()
        _scan(prod_db, run_id, run_by="pytest", persist=True)
        scan = latest_scan(prod_db)
    facts = assemble_fraud_facts(prod_db, scan["fraud_run_id"])
    blob = json.dumps(facts)
    assert "policy_id" not in blob
    assert "claimant_id" not in blob
    assert "CLM-" not in blob          # no claimant identifier ever
    assert "TRM-" not in blob and "WL-" not in blob  # no policy identifiers
    # Institutional entities ARE allowed.
    assert any(o["office"] for o in facts["flagged_by_office"])


def test_narrative_blocks_untraceable_number():
    from src.ai.llm.client import load_llm_config
    from src.ai.skills.fraud_narrative import draft_fraud_narrative
    from src.utils.types import LLMResponse

    class _Stub:
        name = "stub"

        def __init__(self, text):
            self._text = text

        def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
            return LLMResponse(
                text=self._text, input_tokens=8, output_tokens=16,
                provider=self.name, model=model, latency_ms=0.0,
                stop_reason="end_turn",
            )

    facts = {
        "fraud_run_id": "fr-1", "study_run_id": "sr-1",
        "n_claims_scored": 1808, "n_claims_flagged": 32,
        "flag_threshold": 0.5,
        "rule_hit_counts": {"FR-RULE-01": 142},
        "score_p50": 0.15, "score_p95": 0.4, "score_max": 1.2,
        "flagged_by_office": [{"office": "OFF-013", "flagged_claims": 14}],
        "flagged_by_hospital": [{"hospital": "HOSP-066", "flagged_claims": 14}],
        "flagged_by_region": [{"region": "SOUTHWEST", "flagged_claims": 15}],
        "claims_in_shared_claimant_clusters": 4,
    }
    cfg = load_llm_config(Path("config/llm_config.yaml"))

    ok = draft_fraud_narrative(
        facts, cfg, "claude-sonnet-4-6",
        provider=_Stub(
            "The scan scored 1808 claims and flagged 32 at the 0.5 threshold. "
            "Office OFF-013 accounts for 14 flagged claims, all via hospital "
            "HOSP-066 in the SOUTHWEST region."
        ),
    )
    assert ok["blocked"] is False
    assert ok["markdown"].startswith("**AI-DRAFT")

    bad = draft_fraud_narrative(
        facts, cfg, "claude-sonnet-4-6",
        provider=_Stub("The scan flagged 99 claims worth $12,345,678."),
    )
    assert bad["blocked"] is True
    assert bad["untraceable_nums"]

    empty = draft_fraud_narrative(
        facts, cfg, "claude-sonnet-4-6", provider=_Stub("   ")
    )
    assert empty["blocked"] is True
