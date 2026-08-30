"""Fraud-detection rules (demo refresh P5).

Each rule is a pure function ``rule(claims_df, cfg) -> FraudRuleResult`` over
the claims frame built by :func:`build_claims_frame`. A rule returns the
claim ids it fires on plus a per-claim evidence dict; it never writes to the
database (persistence is the runner's job). Thresholds and weights come from
``config/fraud_config.yaml``.

Claims frame grain: ONE ROW PER CLAIM — every DEATH / CI_CLAIM event in
``silver_policy_events`` (which carries the claim amount and illness code)
joined to its Silver policy row (premium + office/agent/claimant/hospital/
region identity). The generator's claims are terminal, so policy ↔ claim is
1:1 and ``claim_event_id == policy_id``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

_SILVER_TABLES = (
    "silver_term_policies",
    "silver_wl_policies",
    "silver_ul_policies",
    "silver_vul_policies",
    "silver_annuity_contracts",
)

_CLAIM_SQL = """
    SELECT
        e.policy_id                            AS claim_event_id,
        e.policy_id,
        p.product_code,
        e.event_type,
        e.event_date,
        p.issue_date,
        date_diff('day', p.issue_date, e.event_date) AS policy_days,
        e.claim_amount,
        {premium_expr}                         AS annual_premium,
        e.illness_code,
        p.agency_office_id,
        p.agent_id,
        p.claimant_id,
        p.hospital_id,
        p.claim_region
    FROM silver_policy_events e
    JOIN {table} p ON p.{pid_col} = e.policy_id
    WHERE e.event_type IN ('DEATH', 'CI_CLAIM')
      AND e.event_date IS NOT NULL
"""


@dataclass
class FraudRuleResult:
    """Outcome of one fraud rule over the claims frame."""

    rule_id: str
    description: str
    weight: float
    hit_claim_ids: list[str]
    evidence: dict[str, dict] = field(default_factory=dict)  # claim_id -> evidence

    @property
    def hit_count(self) -> int:
        return len(self.hit_claim_ids)


def build_claims_frame(db_path: Path) -> pd.DataFrame:
    """One row per claim (DEATH / CI_CLAIM) across every Silver policy table."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        frames = []
        for table in _SILVER_TABLES:
            is_da = table == "silver_annuity_contracts"
            premium = "0.0" if is_da else "COALESCE(p.annual_premium, 0.0)"
            pid_col = "contract_id" if is_da else "policy_id"
            frames.append(
                con.execute(
                    _CLAIM_SQL.format(
                        table=table, premium_expr=premium, pid_col=pid_col
                    )
                ).df()
            )
    finally:
        con.close()
    df = pd.concat(frames, ignore_index=True)
    # The UL CSV is loaded twice upstream (as UL and as ULSG), duplicating its
    # rows in silver_policy_events — a claim is one policy here, so de-dup.
    df = df.drop_duplicates(subset=["claim_event_id"], keep="first")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# The six rules                                                               #
# --------------------------------------------------------------------------- #

def rule_first_policy_year_claim(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-01 — claim inside the first policy year."""
    rcfg = cfg["rules"]["first_policy_year_claim"]
    max_days = int(rcfg.get("max_policy_days", 366))
    hits = claims[claims["policy_days"] <= max_days]
    return FraudRuleResult(
        rule_id="FR-RULE-01",
        description="Claim within the first policy year",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            r.claim_event_id: {"policy_days": int(r.policy_days)}
            for r in hits.itertuples()
        },
    )


def rule_claim_exceeds_premiums(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-02 — claim amount far exceeds the premiums paid to date."""
    rcfg = cfg["rules"]["claim_exceeds_premiums"]
    min_ratio = float(rcfg.get("min_ratio", 25))
    years_paid = (claims["policy_days"].clip(lower=1) / 365.25).clip(lower=1.0)
    premiums_paid = claims["annual_premium"].fillna(0.0) * years_paid
    ratio = claims["claim_amount"] / premiums_paid.replace(0, float("nan"))
    hits = claims[ratio > min_ratio]
    return FraudRuleResult(
        rule_id="FR-RULE-02",
        description="Claim amount exceeds premiums paid by the configured ratio",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            cid: {"claim_to_premium_ratio": round(float(rt), 1)}
            for cid, rt in zip(hits["claim_event_id"], ratio[hits.index])
        },
    )


def rule_claim_above_materiality(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-03 — claim amount above the per-type materiality threshold."""
    rcfg = cfg["rules"]["claim_above_materiality"]
    thresholds = rcfg.get("thresholds") or {}
    default = float(thresholds.get("default", float("inf")))
    limits = claims["event_type"].map(
        lambda t: float(thresholds.get(t, default))
    )
    hits = claims[claims["claim_amount"] > limits]
    return FraudRuleResult(
        rule_id="FR-RULE-03",
        description="Claim amount above the materiality threshold",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            r.claim_event_id: {"claim_amount": float(r.claim_amount)}
            for r in hits.itertuples()
        },
    )


def rule_agency_office_concentration(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-04 — early-claim clustering at one agency office.

    An office whose FIRST-POLICY-YEAR claim count is at least the configured
    multiple of the office median (and above a minimum count) flags every one
    of its first-year claims. Total-claim share dilutes under organic volume;
    early-duration clustering is the sharp signal.
    """
    rcfg = cfg["rules"]["agency_office_concentration"]
    multiple = float(rcfg.get("min_multiple_of_median", 3))
    min_claims = int(rcfg.get("min_claims", 5))
    max_days = int(cfg["rules"]["first_policy_year_claim"].get("max_policy_days", 366))

    early = claims[claims["policy_days"] <= max_days]
    counts = early.groupby("agency_office_id")["claim_event_id"].count()
    if counts.empty:
        return FraudRuleResult(
            "FR-RULE-04", "Early-claim concentration at an agency office",
            float(rcfg["weight"]), [], {},
        )
    median = float(counts.median())
    threshold = max(multiple * max(median, 1.0), float(min_claims))
    hot_offices = set(counts[counts >= threshold].index)
    hits = early[early["agency_office_id"].isin(hot_offices)]
    return FraudRuleResult(
        rule_id="FR-RULE-04",
        description="Early-claim concentration at an agency office",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            r.claim_event_id: {
                "office": r.agency_office_id,
                "office_first_year_claims": int(counts[r.agency_office_id]),
                "office_median": median,
            }
            for r in hits.itertuples()
        },
    )


def rule_similar_claims_same_claimant(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-05 — multiple similar claims sharing one claimant.

    Same ``claimant_id`` on more than one claim, same illness code, amounts
    within the configured tolerance of the claimant-group mean.
    """
    rcfg = cfg["rules"]["similar_claims_same_claimant"]
    tol = float(rcfg.get("amount_tolerance_pct", 10)) / 100.0

    hits: list[str] = []
    evidence: dict[str, dict] = {}
    with_claimant = claims[claims["claimant_id"].notna()]
    for claimant, grp in with_claimant.groupby("claimant_id"):
        if len(grp) < 2:
            continue
        for illness, sub in grp.groupby("illness_code", dropna=False):
            if len(sub) < 2:
                continue
            mean_amt = float(sub["claim_amount"].mean())
            close = sub[
                (sub["claim_amount"] - mean_amt).abs() <= tol * mean_amt
            ]
            if len(close) < 2:
                continue
            for r in close.itertuples():
                hits.append(r.claim_event_id)
                evidence[r.claim_event_id] = {
                    "claims_in_cluster": int(len(close)),
                    "illness_code": None if pd.isna(illness) else str(illness),
                }
    return FraudRuleResult(
        rule_id="FR-RULE-05",
        description="Similar claims sharing one claimant",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits,
        evidence=evidence,
    )


def rule_high_risk_region_or_hospital(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-06 — claim from a configured high-risk region or hospital."""
    rcfg = cfg["rules"]["high_risk_region_or_hospital"]
    hospitals = set(rcfg.get("hospitals") or [])
    regions = set(rcfg.get("regions") or [])
    hits = claims[
        claims["hospital_id"].isin(hospitals) | claims["claim_region"].isin(regions)
    ]
    return FraudRuleResult(
        rule_id="FR-RULE-06",
        description="Claim from a high-risk region or hospital",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            r.claim_event_id: {
                "hospital": r.hospital_id if r.hospital_id in hospitals else None,
                "region": r.claim_region if r.claim_region in regions else None,
            }
            for r in hits.itertuples()
        },
    )


ALL_RULES = [
    rule_first_policy_year_claim,
    rule_claim_exceeds_premiums,
    rule_claim_above_materiality,
    rule_agency_office_concentration,
    rule_similar_claims_same_claimant,
    rule_high_risk_region_or_hospital,
]
