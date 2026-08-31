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

# NOTE on ``agent_id`` (adversarial review OBS-8): a selling agent is a natural
# person, so the id is pseudonymous personal data despite reading like
# institutional metadata beside ``agency_office_id`` (an organisation). It stays
# inside the engine for rule evidence and investigator drill-down, and is barred
# from the chatbot allowlist and from every LLM fact pack — both guard-tested in
# tests/test_data_surface.py and tests/test_fraud.py.
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
    #: Claims this rule could not evaluate at all (no usable basis). Reported so a
    #: rule that is inapplicable to a product family is visible as such, instead of
    #: a NaN comparison silently reading as "no hit" (adversarial review M-19).
    n_not_applicable: int = 0

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
            # Annuity contracts carry no annual_premium: a deferred annuity is a
            # deposit, and its death benefit IS the account value. Hardcoding 0.0
            # made the ratio NaN, and `NaN > 25` is silently False, so the whole
            # family was reported as scored against six rules while actually being
            # scored against five (adversarial review M-19).
            premium = (
                "COALESCE(p.account_value, 0.0)" if is_da
                else "COALESCE(p.annual_premium, 0.0)"
            )
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
    for frame, table in zip(frames, _SILVER_TABLES):
        frame["premium_basis"] = (
            "ACCOUNT_VALUE" if table == "silver_annuity_contracts" else "ANNUAL_PREMIUM"
        )
    df = pd.concat(frames, ignore_index=True)
    # A zero/absent basis means the leverage test cannot be evaluated at all —
    # say so explicitly rather than letting a NaN comparison read as "no hit".
    df.loc[df["annual_premium"].fillna(0.0) <= 0, "premium_basis"] = "NONE"
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
    percentile = float(rcfg.get("percentile", 95))
    min_product_claims = int(rcfg.get("min_product_claims", 20))

    basis = claims.get("premium_basis", pd.Series("ANNUAL_PREMIUM", index=claims.index))
    applicable = basis != "NONE"

    # A recurring premium accumulates with duration; a single deposit does not, so
    # only the annual-premium basis is annualised. Annualising an account value
    # would invent a payment history the contract never had.
    years_paid = (claims["policy_days"].clip(lower=1) / 365.25).clip(lower=1.0)
    paid = claims["annual_premium"].fillna(0.0).where(
        basis != "ANNUAL_PREMIUM", claims["annual_premium"].fillna(0.0) * years_paid
    )
    ratio = (claims["claim_amount"] / paid.replace(0, float("nan"))).where(applicable)

    # Product-relative threshold (m-12): the ratio a Term claim reaches routinely
    # is extreme for Whole Life, so compare each claim with its own product's
    # distribution. Products with too few claims to estimate a percentile fall
    # back to the absolute floor, which also floors every product threshold.
    product = claims["product_code"]
    by_product = ratio.groupby(product)
    quantiles = by_product.quantile(percentile / 100.0)
    sparse = by_product.count() < min_product_claims
    quantiles = quantiles.mask(sparse)
    thresholds = product.map(quantiles).astype(float).fillna(min_ratio).clip(lower=min_ratio)

    hits = claims[applicable & (ratio > thresholds)]
    return FraudRuleResult(
        rule_id="FR-RULE-02",
        description=(
            f"Claim/premium ratio above the {percentile:g}th percentile for its product"
        ),
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            cid: {
                "claim_to_premium_ratio": round(float(ratio.loc[idx]), 1),
                "product_threshold": round(float(thresholds.loc[idx]), 1),
                "premium_basis": str(basis.loc[idx]),
            }
            for cid, idx in zip(hits["claim_event_id"], hits.index)
        },
        n_not_applicable=int((~applicable).sum()),
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

    # Volume alone is not evidence: with ~3.5 early claims per office, a 40-office
    # book is EXPECTED to throw up an innocent office at the old threshold, and it
    # did — one organic office contributed 7 of 32 flags (M-17). Require a second
    # signal inside the office, and flag only the claims that carry it.
    min_share = float(rcfg.get("min_corroboration_share", 0.0))
    dims = list(rcfg.get("corroboration_dimensions") or [])

    hit_rows = []
    evidence: dict[str, dict] = {}
    for office in sorted(hot_offices):
        office_claims = early[early["agency_office_id"] == office]
        n = len(office_claims)
        if not n:
            continue

        best = None  # (share, dimension, value)
        for dim in dims:
            if dim not in office_claims.columns:
                continue
            vc = office_claims[dim].value_counts(dropna=True)
            if vc.empty:
                continue
            share = float(vc.iloc[0]) / n
            if best is None or share > best[0]:
                best = (share, dim, vc.index[0])

        if dims and (best is None or best[0] < min_share):
            continue  # concentrated but uncorroborated — an ordinary busy office

        if best is None:
            corroborated = office_claims
            share = dim_name = dim_value = None
        else:
            share, dim_name, dim_value = best
            corroborated = office_claims[office_claims[dim_name] == dim_value]

        for r in corroborated.itertuples():
            hit_rows.append(r.claim_event_id)
            evidence[r.claim_event_id] = {
                "office": office,
                "office_first_year_claims": int(counts[office]),
                "office_median": median,
                "corroborating_dimension": dim_name,
                "corroborating_value": None if dim_value is None else str(dim_value),
                "corroborating_share": None if share is None else round(share, 4),
            }

    return FraudRuleResult(
        rule_id="FR-RULE-04",
        description="Early-claim concentration at an agency office",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hit_rows,
        evidence=evidence,
    )


def rule_similar_claims_same_claimant(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-05 — multiple similar claims sharing one claimant.

    Same ``claimant_id`` on more than one claim, same illness code, amounts
    within the configured tolerance of the claimant-group mean.
    """
    rcfg = cfg["rules"]["similar_claims_same_claimant"]
    tol = float(rcfg.get("amount_tolerance_pct", 10)) / 100.0
    min_cluster = int(rcfg.get("min_cluster_size", 2))
    min_gap_days = int(rcfg.get("min_days_between_claims", 0))
    exclude_deaths = bool(rcfg.get("exclude_death_clusters", False))

    hits: list[str] = []
    evidence: dict[str, dict] = {}
    with_claimant = claims[claims["claimant_id"].notna()]
    for claimant, grp in with_claimant.groupby("claimant_id"):
        if len(grp) < min_cluster:
            continue
        for illness, sub in grp.groupby("illness_code", dropna=False):
            if len(sub) < min_cluster:
                continue
            # A person holding two policies legitimately produces two honest
            # claims: one death settling across both contracts, or one illness
            # triggering two accelerated riders. Those arrive together, in pairs,
            # and a death cluster is a data defect rather than fraud. What cannot
            # happen is the SAME illness claimed repeatedly, months apart (m-11).
            if exclude_deaths and pd.isna(illness):
                continue
            mean_amt = float(sub["claim_amount"].mean())
            close = sub[
                (sub["claim_amount"] - mean_amt).abs() <= tol * mean_amt
            ]
            if len(close) < min_cluster:
                continue
            if min_gap_days > 0 and "event_date" in close.columns:
                dates = pd.to_datetime(close["event_date"]).sort_values()
                gaps = dates.diff().dropna().dt.days
                # Every consecutive pair must be far enough apart to be a
                # genuinely separate event, not one settlement paid twice.
                if gaps.empty or float(gaps.min()) < min_gap_days:
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


def load_facility_roster(roster_file: Path | str) -> set[str]:
    """The set of legitimate facility ids from the configured roster.

    A pluggable reference file like every other basis in the tool: point the
    config at a different roster and the check follows, no code change.
    """
    path = Path(roster_file)
    if not path.exists():
        raise FileNotFoundError(
            f"facility roster not found at {path}; FR-RULE-06 cannot identify an "
            f"unknown facility without a roster of known ones"
        )
    frame = pd.read_csv(path)
    if "hospital_id" not in frame.columns:
        raise ValueError(f"facility roster {path} has no 'hospital_id' column")
    return set(frame["hospital_id"].dropna().astype(str))


def rule_unknown_facility(claims: pd.DataFrame, cfg: dict) -> FraudRuleResult:
    """FR-RULE-06 — claim settled at a facility that is not on the roster.

    This is the check the demo has always described: a claim routed through a
    facility nobody has on file. The previous implementation was a membership
    test against a YAML list naming the single facility already known to be
    planted, so it demonstrated nothing and could not catch an unknown one
    (adversarial review M-18).

    The bare high-risk-REGION clause was dropped with it: the configured region
    was the rarest in the book by construction rather than by risk, and it flagged
    innocent claims on geography alone.
    """
    rcfg = cfg["rules"]["unknown_or_captive_facility"]
    roster = load_facility_roster(rcfg["roster_file"])

    known = claims["hospital_id"].notna()
    off_roster = ~claims["hospital_id"].astype(str).isin(roster)
    hits = claims[known & off_roster]
    return FraudRuleResult(
        rule_id="FR-RULE-06",
        description="Claim settled at a facility not on the roster",
        weight=float(rcfg["weight"]),
        hit_claim_ids=hits["claim_event_id"].tolist(),
        evidence={
            r.claim_event_id: {
                "hospital": r.hospital_id,
                "reason": "not on the facility roster",
                "roster_size": len(roster),
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
    rule_unknown_facility,
]
