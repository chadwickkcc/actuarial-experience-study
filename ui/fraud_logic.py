"""Pure orchestration for the Fraud Monitor page (demo refresh P5).

Non-Streamlit logic the page calls, so it is unit-testable. Read-only DB
access except the fraud scan itself (which writes the sanctioned
``gold_fraud_*`` tables via ``src/fraud/runner``).

``assemble_fraud_facts`` builds the AGGREGATES-ONLY fact pack for the AI
narrative: rule hit counts, score distribution, entity concentrations
(office / hospital / region ids allowed) and cluster sizes — NEVER a
``policy_id`` or ``claimant_id`` (PII bright line, guard-tested).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd


def latest_scan(db_path: Path) -> Optional[dict]:
    """The most recent gold_fraud_run_summary row as a dict (None when absent)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            "SELECT * FROM gold_fraud_run_summary ORDER BY run_ts DESC LIMIT 1"
        ).df()
    finally:
        con.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def scan_scores(db_path: Path, fraud_run_id: str) -> pd.DataFrame:
    """Per-claim scores for one scan (claim-level; UI display only)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "SELECT * FROM gold_fraud_scores WHERE fraud_run_id = ? "
            "ORDER BY composite_score DESC, claim_event_id",
            [fraud_run_id],
        ).df()
    finally:
        con.close()


def claim_flags(db_path: Path, fraud_run_id: str, claim_event_id: str) -> pd.DataFrame:
    """The rules (and evidence) a single claim fired."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "SELECT rule_id, weight, evidence FROM gold_fraud_flags "
            "WHERE fraud_run_id = ? AND claim_event_id = ? ORDER BY rule_id",
            [fraud_run_id, claim_event_id],
        ).df()
    finally:
        con.close()


def assemble_fraud_facts(db_path: Path, fraud_run_id: str) -> dict:
    """Aggregates-only fact pack for the AI narrative (no person-level ids)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        summary = con.execute(
            "SELECT * FROM gold_fraud_run_summary WHERE fraud_run_id = ?",
            [fraud_run_id],
        ).df()
        if summary.empty:
            raise ValueError(f"No fraud scan with id {fraud_run_id!r}")
        srow = summary.iloc[0]

        flagged_by_office = con.execute(
            "SELECT agency_office_id AS office, COUNT(*) AS flagged_claims "
            "FROM gold_fraud_scores WHERE fraud_run_id = ? AND flagged "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
            [fraud_run_id],
        ).df()
        flagged_by_hospital = con.execute(
            "SELECT hospital_id AS hospital, COUNT(*) AS flagged_claims "
            "FROM gold_fraud_scores WHERE fraud_run_id = ? AND flagged "
            "AND hospital_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
            [fraud_run_id],
        ).df()
        flagged_by_region = con.execute(
            "SELECT claim_region AS region, COUNT(*) AS flagged_claims "
            "FROM gold_fraud_scores WHERE fraud_run_id = ? AND flagged "
            "AND claim_region IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
            [fraud_run_id],
        ).df()
        cluster = con.execute(
            "SELECT COUNT(*) AS n FROM gold_fraud_scores s "
            "WHERE s.fraud_run_id = ? AND s.flagged AND s.claimant_id IN ("
            "  SELECT claimant_id FROM gold_fraud_scores "
            "  WHERE fraud_run_id = ? AND claimant_id IS NOT NULL "
            "  GROUP BY claimant_id HAVING COUNT(*) > 1)",
            [fraud_run_id, fraud_run_id],
        ).fetchone()
    finally:
        con.close()

    return {
        "fraud_run_id": fraud_run_id,          # excluded from the traceable set
        "study_run_id": str(srow["study_run_id"]),
        "n_claims_scored": int(srow["n_claims_scored"]),
        "n_claims_flagged": int(srow["n_claims_flagged"]),
        "flag_threshold": float(srow["composite_threshold"]),
        "rule_hit_counts": json.loads(srow["rule_hit_counts"]),
        "score_p50": float(srow["score_p50"]),
        "score_p95": float(srow["score_p95"]),
        "score_max": float(srow["score_max"]),
        "flagged_by_office": flagged_by_office.to_dict("records"),
        "flagged_by_hospital": flagged_by_hospital.to_dict("records"),
        "flagged_by_region": flagged_by_region.to_dict("records"),
        "claims_in_shared_claimant_clusters": int(cluster[0]) if cluster else 0,
    }
