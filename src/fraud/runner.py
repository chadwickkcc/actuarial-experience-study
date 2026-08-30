"""Fraud-scan orchestrator (demo refresh P5).

Runs every rule in :data:`src.fraud.rules.ALL_RULES` over the claims frame,
computes the weighted composite score per claim, and persists the results to
the three ``gold_fraud_*`` tables via parameterized inserts (the standard
engine write path — never ``sql_boundary``, which is AI-only).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from src.fraud.rules import ALL_RULES, FraudRuleResult, build_claims_frame

DEFAULT_FRAUD_CONFIG = Path("config/fraud_config.yaml")


@dataclass
class FraudRunResult:
    """Summary of one fraud scan."""

    fraud_run_id: str
    study_run_id: str
    n_claims_scored: int
    n_claims_flagged: int
    composite_threshold: float
    rule_results: list[FraudRuleResult]
    scores_df: pd.DataFrame  # per-claim frame incl. composite_score / flagged


def load_fraud_config(config_path: Path = DEFAULT_FRAUD_CONFIG) -> dict:
    """Load and validate config/fraud_config.yaml (loud on a bad config)."""
    with Path(config_path).open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    rules = cfg.get("rules")
    if not isinstance(rules, dict) or not rules:
        raise ValueError(f"{config_path}: 'rules' block missing or empty")
    for rid, rcfg in rules.items():
        if "weight" not in (rcfg or {}):
            raise ValueError(f"{config_path}: rule '{rid}' has no weight")
        w = float(rcfg["weight"])
        if not 0 < w <= 1:
            raise ValueError(f"{config_path}: rule '{rid}' weight {w} outside (0, 1]")
    scoring = cfg.get("scoring") or {}
    if "flag_threshold" not in scoring:
        raise ValueError(f"{config_path}: 'scoring.flag_threshold' missing")
    return cfg


def _config_hash(config_path: Path) -> str:
    return hashlib.sha256(Path(config_path).read_bytes()).hexdigest()


def run_fraud_scan(
    db_path: Path,
    study_run_id: str,
    *,
    config_path: Path = DEFAULT_FRAUD_CONFIG,
    run_by: str = "system",
    persist: bool = True,
) -> FraudRunResult:
    """Score every claim against the six rules; optionally persist the results.

    Args:
        db_path:      DuckDB path (claims are read from the Silver tables).
        study_run_id: The study run this scan is associated with (context only —
                      the claims universe is the current Silver book).
        config_path:  Fraud config YAML (thresholds/weights/lists).
        run_by:       Username recorded on the summary row.
        persist:      When True, write the three gold_fraud_* tables.

    Returns:
        FraudRunResult with the per-claim score frame and per-rule results.
    """
    cfg = load_fraud_config(config_path)
    claims = build_claims_frame(db_path)

    rule_results = [rule(claims, cfg) for rule in ALL_RULES]

    scores = claims.copy()
    scores["composite_score"] = 0.0
    scores["n_rules_hit"] = 0
    hit_map: dict[str, list[str]] = {}
    for rr in rule_results:
        hit_set = set(rr.hit_claim_ids)
        mask = scores["claim_event_id"].isin(hit_set)
        scores.loc[mask, "composite_score"] += rr.weight
        scores.loc[mask, "n_rules_hit"] += 1
        for cid in rr.hit_claim_ids:
            hit_map.setdefault(cid, []).append(rr.rule_id)

    threshold = float(cfg["scoring"]["flag_threshold"])
    scores["flagged"] = scores["composite_score"] >= threshold

    result = FraudRunResult(
        fraud_run_id=str(uuid.uuid4()),
        study_run_id=study_run_id,
        n_claims_scored=int(len(scores)),
        n_claims_flagged=int(scores["flagged"].sum()),
        composite_threshold=threshold,
        rule_results=rule_results,
        scores_df=scores,
    )

    if persist:
        _persist(db_path, result, rule_results, config_path, run_by)
    return result


def _persist(
    db_path: Path,
    result: FraudRunResult,
    rule_results: list[FraudRuleResult],
    config_path: Path,
    run_by: str,
) -> None:
    """Write summary + per-claim scores + per-claim-per-rule flags."""
    scores = result.scores_df
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            INSERT INTO gold_fraud_run_summary (
                fraud_run_id, study_run_id, run_ts, run_by, config_hash,
                n_claims_scored, n_claims_flagged, composite_threshold,
                rule_hit_counts, score_p50, score_p95, score_max
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                result.fraud_run_id,
                result.study_run_id,
                datetime.utcnow(),
                run_by,
                _config_hash(config_path),
                result.n_claims_scored,
                result.n_claims_flagged,
                result.composite_threshold,
                json.dumps({rr.rule_id: rr.hit_count for rr in rule_results}),
                float(scores["composite_score"].quantile(0.50)),
                float(scores["composite_score"].quantile(0.95)),
                float(scores["composite_score"].max()),
            ],
        )

        score_rows = scores.copy()
        score_rows.insert(0, "fraud_run_id", result.fraud_run_id)
        score_cols = [
            "fraud_run_id", "claim_event_id", "policy_id", "product_code",
            "event_type", "event_date", "claim_amount", "agency_office_id",
            "agent_id", "claimant_id", "hospital_id", "claim_region",
            "composite_score", "n_rules_hit", "flagged",
        ]
        con.register("_fraud_scores_staging", score_rows[score_cols])
        con.execute(
            "INSERT INTO gold_fraud_scores SELECT * FROM _fraud_scores_staging"
        )
        con.unregister("_fraud_scores_staging")

        flag_rows = []
        for rr in rule_results:
            for cid in rr.hit_claim_ids:
                flag_rows.append({
                    "fraud_run_id": result.fraud_run_id,
                    "claim_event_id": cid,
                    "rule_id": rr.rule_id,
                    "weight": rr.weight,
                    "evidence": json.dumps(rr.evidence.get(cid, {})),
                })
        if flag_rows:
            con.register("_fraud_flags_staging", pd.DataFrame(flag_rows))
            con.execute(
                "INSERT INTO gold_fraud_flags SELECT * FROM _fraud_flags_staging"
            )
            con.unregister("_fraud_flags_staging")
    finally:
        con.close()
