"""Spot-check DQ-UL-01: UL account value roll-forward identity.

Picks a single in-force UL policy and runs two checks:

  Check A — Exact BOM→EOM identity (should pass within $1.00)
      AV_eom = AV_bom × (1 + credited_rate / 12)
      (BOM is derived backwards from EOM by the generator, so this must hold.)

  Check B — Annual roll-forward approximation (expected gap; documents DQ-UL-01 tolerance)
      computed = (AV_bom + est_prem - coi_charge - load) × (1 + credited_rate)
      This uses BOM as a proxy for start-of-year, which it is not — BOM is an
      intra-year point. The large delta explains why DQ-UL-01 uses ±30% not ±$1.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

DB_PATH = Path(__file__).parent.parent / "data" / "experience_study.duckdb"

SEP = "─" * 65


def main() -> None:
    """Run the spot-check and print results."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    # Most recent ETL run
    etl_run_id = conn.execute(
        "SELECT _etl_run_id FROM silver_ul_policies ORDER BY _load_ts DESC LIMIT 1"
    ).fetchone()
    if not etl_run_id:
        print("ERROR: No rows in silver_ul_policies.")
        sys.exit(1)
    etl_run_id = etl_run_id[0]

    row = conn.execute(
        """
        SELECT policy_id, product_code, specified_amount,
               account_value_bom, account_value_eom,
               current_coi_rate, credited_interest_rate,
               planned_premium,
               COALESCE(premium_persistency_ratio, 1.0) AS ppr
        FROM silver_ul_policies
        WHERE _etl_run_id = ?
          AND status_code = 'IF'
          AND account_value_bom > 0
        ORDER BY policy_id
        LIMIT 1
        """,
        [etl_run_id],
    ).fetchone()

    if not row:
        print("ERROR: No eligible in-force UL policies found.")
        sys.exit(1)

    (
        policy_id, product_code, spec_amount,
        av_bom, av_eom,
        coi_rate, cr,
        planned_prem, ppr,
    ) = row

    est_prem = planned_prem * ppr
    nar = max(0.0, spec_amount - av_bom)
    coi_charge = nar * coi_rate / 1000.0
    load = est_prem * 0.05 + 5.0

    # Check A
    expected_a = av_bom * (1 + cr / 12)
    delta_a = av_eom - expected_a
    pass_a = abs(delta_a) <= 1.00

    # Check B
    computed_b = (av_bom + est_prem - coi_charge - load) * (1 + cr)
    delta_b = av_eom - computed_b

    print(f"\nPolicy: {policy_id}  ({product_code}, IF)")
    print(SEP)
    print(f"  {'account_value_bom':<30}: ${av_bom:>12,.2f}")
    print(f"  {'account_value_eom':<30}: ${av_eom:>12,.2f}")
    print(f"  {'credited_interest_rate':<30}: {cr * 100:>11.4f}%")
    print(f"  {'current_coi_rate':<30}: {coi_rate:>11.6f}  per $1,000 NAR")
    print(f"  {'specified_amount':<30}: ${spec_amount:>12,.2f}")
    print(f"  {'est_premiums_paid_year':<30}: ${est_prem:>12,.2f}  (planned × ppr)")
    print(f"  {'premium_persistency_ratio':<30}: {ppr:>12.4f}")

    print(f"\nCHECK A — BOM→EOM (1-month interest only)")
    print(SEP)
    print(f"  {'expected_eom':<30}: ${expected_a:>12,.2f}")
    print(f"  {'delta (eom − expected)':<30}: ${delta_a:>12,.2f}")
    result_a = "PASS" if pass_a else "FAIL"
    print(f"  {'RESULT':<30}: {result_a}  (threshold ±$1.00)")
    if not pass_a:
        print("  *** FAIL: ETL may be corrupting BOM/EOM values — check src/etl/transformers/ul.py")

    print(f"\nCHECK B — Annual roll-forward (prem − load − COI + interest)")
    print(SEP)
    print(f"  {'net_amount_at_risk':<30}: ${nar:>12,.2f}")
    print(f"  {'coi_charge':<30}: ${coi_charge:>12,.2f}")
    print(f"  {'expense_load':<30}: ${load:>12,.2f}")
    print(f"  {'computed_eom':<30}: ${computed_b:>12,.2f}")
    print(f"  {'delta (eom − computed)':<30}: ${delta_b:>12,.2f}")
    pct_diff = (delta_b / av_eom * 100) if av_eom else 0
    print(f"  {'delta as % of eom':<30}: {pct_diff:>11.1f}%")
    in_band = -30.0 <= pct_diff <= 40.0
    print(f"  {'DQ-UL-01 band (−30%/+40%)':<30}: {'WITHIN' if in_band else 'OUTSIDE'}")
    print(f"\n  NOTE: BOM ≠ start-of-year; gap above is structural, not a data bug.")

    conn.close()
    print()


if __name__ == "__main__":
    main()
