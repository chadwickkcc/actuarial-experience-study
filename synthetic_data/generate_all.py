"""
Synthetic data orchestrator — Phases 1A + 1B + 1C (all five products).

Usage:
    python synthetic_data/generate_all.py

Outputs:
    synthetic_data/output/term_policies.csv       (3,200 rows)
    synthetic_data/output/wl_policies.csv         (2,800 rows)
    synthetic_data/output/ul_policies.csv         (1,800 rows)
    synthetic_data/output/vul_policies.csv        (800 rows)
    synthetic_data/output/annuity_contracts.csv   (1,400 rows)
    config/reference_tables/mortality_2015vbt.parquet
    config/reference_tables/mortality_2012iar.parquet
    config/reference_tables/lapse_benchmarks.parquet
    config/reference_tables/ci_incidence.parquet
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Seed — must be 42 per technical spec C.1
# ---------------------------------------------------------------------------

RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR   = Path(__file__).resolve().parent / "output"
REF_DIR      = PROJECT_ROOT / "config" / "reference_tables"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REF_DIR.mkdir(parents=True, exist_ok=True)

# Ensure project root is importable
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from synthetic_data.generators.common import (
    CI_ILLNESS_CODES,
    CI_ILLNESS_WEIGHTS,
    CI_BASE_INCIDENCE_PER_1000,
    ci_age_factor,
    attained_age_band,
    MACRO_SCENARIO,
)
from synthetic_data.generators.term import (
    generate_term_policies,
    vbt_q_x,
    RISK_CLASS_NAMES,
    _base_lapse_rate,
)
from synthetic_data.generators.whole_life import generate_wl_policies
from synthetic_data.generators.ul import generate_ul_policies
from synthetic_data.generators.vul import generate_vul_policies
from synthetic_data.generators.annuity import generate_annuity_contracts


# ---------------------------------------------------------------------------
# Reference table generators
# ---------------------------------------------------------------------------

def build_mortality_table() -> pd.DataFrame:
    """
    Build a VBT-style mortality reference table.

    Columns: gender, smoker_status, risk_class, issue_age_anb, policy_year, q_x

    Selection factors are intentionally excluded: they are applied by the generator
    when simulating deaths, so A/E will reflect underwriting selection (expected ~0.85–1.00).
    Including selection factors here would suppress expected deaths and inflate A/E > 1.0.
    """
    # Makeham parameters matching synthetic_data/generators/term.py vbt_q_x()
    _MAKEHAM: dict[str, tuple[float, float, float]] = {
        "M": (0.00022, 0.000027, 1.104),
        "F": (0.00018, 0.000018, 1.102),
    }
    _CLASS_FACTORS: dict[str, float] = {
        "SUPER_PREF": 0.55,
        "PREF_NS":    0.75,
        "STD_NS":     1.00,
        "PREF_SM":    2.00,
        "STD_SM":     2.50,
    }

    rows: list[dict] = []
    genders   = ["M", "F"]
    smokers   = ["NS", "SM"]
    ages      = list(range(18, 86))       # issue ages 18–85
    durations = list(range(1, 41))        # policy years 1–40

    for gender in genders:
        A, B, c = _MAKEHAM[gender]
        for smoker in smokers:
            smoker_factor = 1.90 if smoker == "SM" else 1.0
            for risk_class in RISK_CLASS_NAMES:
                # Skip impossible combinations
                if smoker == "SM" and risk_class in ("SUPER_PREF", "PREF_NS", "STD_NS"):
                    continue
                if smoker == "NS" and risk_class in ("PREF_SM", "STD_SM"):
                    continue
                class_factor = _CLASS_FACTORS[risk_class]
                for age in ages:
                    for dur in durations:
                        attained_age = min(max(age + dur - 1, 0), 110)
                        q_base = A + B * (c ** attained_age)
                        q = min(q_base * smoker_factor * class_factor, 1.0)
                        rows.append({
                            "gender":         gender,
                            "smoker_status":  smoker,
                            "risk_class":     risk_class,
                            "issue_age_anb":  age,
                            "policy_year":    dur,
                            "q_x":            round(q, 8),
                        })

    return pd.DataFrame(rows)


def build_lapse_table() -> pd.DataFrame:
    """
    Build a lapse benchmark reference table.

    Columns: product_code, policy_year, lapse_rate, is_plt_flag, plt_jump_band

    Non-PLT rows: is_plt_flag=False, plt_jump_band=None
    PLT shock rows (year 1): is_plt_flag=True, plt_jump_band set per SOA 2021
    PLT continuing rows (year 2+): is_plt_flag=True, plt_jump_band=None
    """
    rows: list[dict] = []
    for product_code in ["TERM", "WL", "UL", "ULSG", "VUL", "DA"]:
        for yr in range(1, 31):
            if product_code == "TERM":
                rate = _base_lapse_rate(yr)
            elif product_code == "WL":
                if yr == 1:
                    rate = 0.11
                elif yr == 2:
                    rate = 0.07
                elif yr == 3:
                    rate = 0.05
                elif yr == 4:
                    rate = 0.04
                elif yr == 5:
                    rate = 0.03
                elif yr <= 10:
                    rate = 0.025
                else:
                    rate = 0.02
            elif product_code in ("UL", "IUL"):
                if yr == 1:
                    rate = 0.08
                elif yr == 2:
                    rate = 0.06
                elif yr == 3:
                    rate = 0.05
                elif yr == 4:
                    rate = 0.04
                elif yr == 5:
                    rate = 0.035
                else:
                    rate = 0.03
            elif product_code == "ULSG":
                if yr == 1:
                    rate = 0.04
                elif yr <= 5:
                    rate = 0.025
                else:
                    rate = 0.015
            elif product_code == "VUL":
                if yr == 1:
                    rate = 0.06
                elif yr == 2:
                    rate = 0.04
                elif yr == 3:
                    rate = 0.03
                elif yr <= 5:
                    rate = 0.025
                else:
                    rate = 0.02
            else:  # DA — surrender curve (not lapse)
                if yr <= 5:
                    rate = 0.015
                elif yr == 6:
                    rate = 0.03
                elif yr == 7:
                    rate = 0.60
                else:
                    rate = 0.12

            rows.append({
                "product_code":   product_code,
                "policy_year":    yr,
                "lapse_rate":     round(rate, 6),
                "is_plt_flag":    False,
                "plt_jump_band":  None,
            })

    # PLT shock lapse rows — year 1 of PLT, SOA 2021 benchmark rates by jump band
    plt_shock = [
        ("<=2x", 0.30),
        ("2-3x", 0.55),
        ("3-5x", 0.55),
        ("5-8x", 0.70),
        ("8-12x", 0.80),
        (">12x", 0.88),
    ]
    for band, rate in plt_shock:
        rows.append({
            "product_code":   "TERM",
            "policy_year":    1,
            "lapse_rate":     rate,
            "is_plt_flag":    True,
            "plt_jump_band":  band,
        })

    # PLT continuing lapse rows — years 2-30, no jump-band dependency
    plt_cont = [(2, 3, 0.15), (4, 5, 0.10), (6, 30, 0.08)]
    for yr_lo, yr_hi, rate in plt_cont:
        for yr in range(yr_lo, yr_hi + 1):
            rows.append({
                "product_code":   "TERM",
                "policy_year":    yr,
                "lapse_rate":     rate,
                "is_plt_flag":    True,
                "plt_jump_band":  None,
            })

    return pd.DataFrame(rows)


def build_annuity_mortality_table() -> pd.DataFrame:
    """
    Build 2012 IAR-calibrated annuity owner mortality reference table with Scale G2 improvement.

    Columns: gender, issue_age_anb, policy_year, q_x

    Uses a Makeham form calibrated to approximate 2012 IAR Select & Ultimate rates.
    Scale G2 improvement (~1% per year) is applied as an age-period factor.
    Annuity tables are lighter mortality than life insurance (annuitant selection).
    """
    rows: list[dict] = []
    genders   = ["M", "F"]
    ages      = list(range(40, 91))   # annuity owner ages 40–90
    durations = list(range(1, 51))    # policy years 1–50

    # Makeham parameters calibrated to 2012 IAR (lighter than VBT)
    _MAKEHAM_IAR = {
        "M": (0.00015, 0.000012, 1.105),
        "F": (0.00010, 0.000008, 1.102),
    }

    for gender in genders:
        A, B, c = _MAKEHAM_IAR[gender]
        for age in ages:
            for dur in durations:
                att_age = min(max(age + dur - 1, 40), 110)
                q_base = A + B * (c ** att_age)
                # G2 improvement: 1% per year, capped at 12%
                improvement = min(0.12, 0.01 * (dur - 1))
                q = q_base * (1 - improvement)
                rows.append({
                    "gender":        gender,
                    "issue_age_anb": age,
                    "policy_year":   dur,
                    "q_x":           round(min(float(q), 1.0), 8),
                })

    return pd.DataFrame(rows)


def build_ci_incidence_table() -> pd.DataFrame:
    """
    Build CI incidence reference table.

    Columns: illness_code, gender, attained_age_band, incidence_rate_per_1000
    """
    # Bands aligned to exposure engine: floor(age/5)*5, giving "15-19","20-24","25-29"...
    five_yr_bands = [f"{lo}-{lo+4}" for lo in range(15, 95, 5)]
    band_mids_5yr = {b: int(b.split("-")[0]) + 2 for b in five_yr_bands}

    rows: list[dict] = []
    for illness_code, weight in zip(CI_ILLNESS_CODES, CI_ILLNESS_WEIGHTS):
        for gender in ["M", "F"]:
            gender_factor = 1.0 if gender == "M" else 0.85
            for band, mid in band_mids_5yr.items():
                age_mult  = ci_age_factor(mid)
                base_rate = CI_BASE_INCIDENCE_PER_1000 * age_mult * weight * gender_factor
                rows.append({
                    "illness_code":            illness_code,
                    "gender":                  gender,
                    "attained_age_band":       band,
                    "incidence_rate_per_1000": round(base_rate, 6),
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_wl_summary(df: pd.DataFrame) -> None:
    """Print generation summary for Whole Life."""
    print("\n" + "=" * 60)
    print("WHOLE LIFE SYNTHETIC DATA — GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total records      : {len(df):,}")

    issue_dates = pd.to_datetime(df["issue_date"])
    print(f"Issue date range   : {issue_dates.min().date()} → {issue_dates.max().date()}")

    term_df = df[df["termination_date"].notna()]
    if not term_df.empty:
        term_dates = pd.to_datetime(term_df["termination_date"])
        print(f"Termination range  : {term_dates.min().date()} → {term_dates.max().date()}")

    print("\nStatus breakdown:")
    for status, count in df["status_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {status:<20} {count:>5}  ({pct:.1f}%)")

    ci_count = df["ci_rider_flag"].sum()
    print(f"\nCI rider policies  : {ci_count} ({ci_count/len(df)*100:.1f}%)")
    par_count = df["participating_flag"].sum()
    print(f"Participating      : {par_count} ({par_count/len(df)*100:.1f}%)")
    small_count = df["small_face_flag"].sum()
    print(f"Small face (<$25K) : {small_count} ({small_count/len(df)*100:.1f}%)")
    print()


def _print_ul_summary(df: pd.DataFrame) -> None:
    """Print generation summary for Universal Life."""
    print("\n" + "=" * 60)
    print("UNIVERSAL LIFE SYNTHETIC DATA — GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total records      : {len(df):,}")

    issue_dates = pd.to_datetime(df["issue_date"])
    print(f"Issue date range   : {issue_dates.min().date()} → {issue_dates.max().date()}")

    print("\nProduct code breakdown:")
    for code, count in df["product_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {code:<15} {count:>5}  ({pct:.1f}%)")

    print("\nStatus breakdown:")
    for status, count in df["status_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {status:<20} {count:>5}  ({pct:.1f}%)")

    ci_count = df["ci_rider_flag"].sum()
    print(f"\nCI rider policies  : {ci_count} ({ci_count/len(df)*100:.1f}%)")
    ulsg_count = df["is_ulsg_flag"].sum()
    print(f"ULSG policies      : {ulsg_count} ({ulsg_count/len(df)*100:.1f}%)")
    mec_count = df["mec_status_flag"].sum()
    print(f"MEC policies       : {mec_count} ({mec_count/len(df)*100:.1f}%)")
    print()


def _print_term_summary(df: pd.DataFrame) -> None:
    """Print required generation summary for Term Life."""
    print("\n" + "=" * 60)
    print("TERM LIFE SYNTHETIC DATA — GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total records      : {len(df):,}")

    issue_dates = pd.to_datetime(df["issue_date"])
    print(f"Issue date range   : {issue_dates.min().date()} → {issue_dates.max().date()}")

    term_df = df[df["termination_date"].notna()]
    if not term_df.empty:
        term_dates = pd.to_datetime(term_df["termination_date"])
        print(f"Termination range  : {term_dates.min().date()} → {term_dates.max().date()}")

    print("\nStatus breakdown:")
    for status, count in df["status_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {status:<20} {count:>5}  ({pct:.1f}%)")

    print("\nTermination causes (non-IF):")
    causes = df["termination_cause_code"].value_counts()
    for cause, count in causes.items():
        print(f"  {cause:<30} {count:>5}")

    ci_count = df["ci_rider_flag"].sum()
    ci_pct   = ci_count / len(df) * 100
    print(f"\nCI rider policies  : {ci_count} ({ci_pct:.1f}%)")

    plt_count = df["plt_structure_code"].notna().sum()
    print(f"PLT-eligible       : {plt_count} ({plt_count/len(df)*100:.1f}%)")

    print("\nFirst 5 rows (key columns):")
    cols = ["policy_id", "plan_code", "issue_age_anb", "gender", "risk_class",
            "face_amount", "status_code", "termination_date", "ci_rider_flag"]
    print(df[cols].head().to_string(index=False))
    print()


def _print_vul_summary(df: pd.DataFrame) -> None:
    """Print generation summary for Variable Universal Life."""
    print("\n" + "=" * 60)
    print("VARIABLE UNIVERSAL LIFE SYNTHETIC DATA — GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total records      : {len(df):,}")

    issue_dates = pd.to_datetime(df["issue_date"])
    print(f"Issue date range   : {issue_dates.min().date()} → {issue_dates.max().date()}")

    print("\nStatus breakdown:")
    for status, count in df["status_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {status:<20} {count:>5}  ({pct:.1f}%)")

    ci_count = df["ci_rider_flag"].sum()
    print(f"\nCI rider policies  : {ci_count} ({ci_count/len(df)*100:.1f}%)")
    wd_count = df["withdrawal_active_flag"].sum()
    print(f"Withdrawal active  : {wd_count} ({wd_count/len(df)*100:.1f}%)")
    avg_sa = df["separate_account_total_value"].mean()
    print(f"Avg separate acct  : ${avg_sa:,.0f}")
    print()


def _print_annuity_summary(df: pd.DataFrame) -> None:
    """Print generation summary for Deferred Annuities."""
    print("\n" + "=" * 60)
    print("DEFERRED ANNUITY SYNTHETIC DATA — GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total records      : {len(df):,}")

    issue_dates = pd.to_datetime(df["issue_date"])
    print(f"Issue date range   : {issue_dates.min().date()} → {issue_dates.max().date()}")

    print("\nProduct type breakdown:")
    for code, count in df["product_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {code:<15} {count:>5}  ({pct:.1f}%)")

    print("\nStatus breakdown:")
    for status, count in df["status_code"].value_counts().items():
        pct = count / len(df) * 100
        print(f"  {status:<20} {count:>5}  ({pct:.1f}%)")

    glwb_count = df["glwb_elected_flag"].sum()
    print(f"\nGLWB elected       : {glwb_count} ({glwb_count/len(df)*100:.1f}%)")
    sc_expired = df["is_surrender_charge_expired_flag"].sum()
    print(f"SC expired         : {sc_expired} ({sc_expired/len(df)*100:.1f}%)")
    avg_av = df["account_value"].mean()
    print(f"Avg account value  : ${avg_av:,.0f}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Generate all Phase 1A + 1B + 1C synthetic data and reference tables."""
    t0 = time.time()
    rng = np.random.default_rng(RANDOM_SEED)

    # --- Term Life policies ---
    print("Generating Term Life policies (n=3,200)…", end=" ", flush=True)
    term_df = generate_term_policies(rng)
    out_path = OUTPUT_DIR / "term_policies.csv"
    term_df.to_csv(out_path, index=False)
    print(f"done → {out_path}")
    _print_term_summary(term_df)

    # --- Whole Life policies ---
    print("Generating Whole Life policies (n=2,800)…", end=" ", flush=True)
    wl_df = generate_wl_policies(rng)
    wl_path = OUTPUT_DIR / "wl_policies.csv"
    wl_df.to_csv(wl_path, index=False)
    print(f"done → {wl_path}")
    _print_wl_summary(wl_df)

    # --- Universal Life policies ---
    print("Generating Universal Life policies (n=1,800)…", end=" ", flush=True)
    ul_df = generate_ul_policies(rng)
    ul_path = OUTPUT_DIR / "ul_policies.csv"
    ul_df.to_csv(ul_path, index=False)
    print(f"done → {ul_path}")
    _print_ul_summary(ul_df)

    # --- VUL policies ---
    print("Generating Variable Universal Life policies (n=800)…", end=" ", flush=True)
    vul_df = generate_vul_policies(rng)
    vul_path = OUTPUT_DIR / "vul_policies.csv"
    vul_df.to_csv(vul_path, index=False)
    print(f"done → {vul_path}")
    _print_vul_summary(vul_df)

    # --- Deferred Annuity contracts ---
    print("Generating Deferred Annuity contracts (n=1,400)…", end=" ", flush=True)
    ann_df = generate_annuity_contracts(rng)
    ann_path = OUTPUT_DIR / "annuity_contracts.csv"
    ann_df.to_csv(ann_path, index=False)
    print(f"done → {ann_path}")
    _print_annuity_summary(ann_df)

    # --- Reference tables ---
    print("Building mortality_2015vbt.parquet…", end=" ", flush=True)
    mort_df = build_mortality_table()
    mort_path = REF_DIR / "mortality_2015vbt.parquet"
    mort_df.to_parquet(mort_path, index=False)
    print(f"done ({len(mort_df):,} rows)")

    print("Building mortality_2012iar.parquet…", end=" ", flush=True)
    iar_df = build_annuity_mortality_table()
    iar_path = REF_DIR / "mortality_2012iar.parquet"
    iar_df.to_parquet(iar_path, index=False)
    print(f"done ({len(iar_df):,} rows)")

    print("Building lapse_benchmarks.parquet…", end=" ", flush=True)
    lapse_df = build_lapse_table()
    lapse_path = REF_DIR / "lapse_benchmarks.parquet"
    lapse_df.to_parquet(lapse_path, index=False)
    print(f"done ({len(lapse_df):,} rows)")

    print("Building ci_incidence.parquet…", end=" ", flush=True)
    ci_df = build_ci_incidence_table()
    ci_path = REF_DIR / "ci_incidence.parquet"
    ci_df.to_parquet(ci_path, index=False)
    print(f"done ({len(ci_df):,} rows)")

    elapsed = time.time() - t0
    print(f"\nTotal elapsed: {elapsed:.1f}s")

    # --- Validation checks ---
    print("\nValidation checks:")

    # Term Life
    assert len(term_df) == 3_200, f"Expected 3200 TERM rows, got {len(term_df)}"
    print(f"  ✓ TERM row count: {len(term_df)}")

    required_non_null = ["policy_id", "product_code", "issue_date", "gender",
                         "face_amount", "status_code"]
    for col in required_non_null:
        nulls = term_df[col].isna().sum()
        assert nulls == 0, f"TERM column '{col}' has {nulls} null values"
    print(f"  ✓ TERM no nulls in required fields")

    ci_count = term_df["ci_rider_flag"].sum()
    print(f"  ✓ TERM CI rider count: {ci_count} ({ci_count/len(term_df)*100:.1f}%)  [target ~800]")

    term_terminated = term_df[term_df["termination_date"].notna()]
    if not term_terminated.empty:
        t_dates = pd.to_datetime(term_terminated["termination_date"])
        assert t_dates.min() >= pd.Timestamp("2016-01-01"), \
            f"TERM termination date before study start: {t_dates.min()}"
        assert t_dates.max() <= pd.Timestamp("2023-12-31"), \
            f"TERM termination date after study end: {t_dates.max()}"
        print(f"  ✓ TERM all termination dates within study window")

    # Whole Life
    assert len(wl_df) == 2_800, f"Expected 2800 WL rows, got {len(wl_df)}"
    print(f"  ✓ WL row count: {len(wl_df)}")

    wl_required = ["policy_id", "product_code", "issue_date", "gender",
                   "face_amount", "status_code", "participating_flag", "small_face_flag"]
    for col in wl_required:
        nulls = wl_df[col].isna().sum()
        assert nulls == 0, f"WL column '{col}' has {nulls} null values"
    print(f"  ✓ WL no nulls in required fields")

    wl_ci_count = wl_df["ci_rider_flag"].sum()
    print(f"  ✓ WL CI rider count: {wl_ci_count} ({wl_ci_count/len(wl_df)*100:.1f}%)  [target ~20% non-small-face]")

    # Universal Life
    assert len(ul_df) == 1_800, f"Expected 1800 UL rows, got {len(ul_df)}"
    print(f"  ✓ UL row count: {len(ul_df)}")

    trad_ul = (ul_df["product_code"] == "UL").sum()
    ulsg = (ul_df["product_code"] == "ULSG").sum()
    iul = (ul_df["product_code"] == "IUL").sum()
    assert trad_ul == 800, f"Expected 800 Trad UL, got {trad_ul}"
    assert ulsg == 800, f"Expected 800 ULSG, got {ulsg}"
    assert iul == 200, f"Expected 200 IUL, got {iul}"
    print(f"  ✓ UL product mix: {trad_ul} Trad UL, {ulsg} ULSG, {iul} IUL")

    ul_required = ["policy_id", "product_code", "issue_date", "gender",
                   "specified_amount", "status_code", "is_ulsg_flag"]
    for col in ul_required:
        nulls = ul_df[col].isna().sum()
        assert nulls == 0, f"UL column '{col}' has {nulls} null values"
    print(f"  ✓ UL no nulls in required fields")

    ul_ci_count = ul_df["ci_rider_flag"].sum()
    print(f"  ✓ UL CI rider count: {ul_ci_count} ({ul_ci_count/len(ul_df)*100:.1f}%)  [target ~15% of UL/IUL]")

    # VUL
    assert len(vul_df) == 800, f"Expected 800 VUL rows, got {len(vul_df)}"
    print(f"  ✓ VUL row count: {len(vul_df)}")

    vul_required = ["policy_id", "product_code", "issue_date", "gender",
                    "specified_amount", "status_code", "separate_account_total_value",
                    "equity_allocation_pct", "withdrawal_active_flag"]
    for col in vul_required:
        nulls = vul_df[col].isna().sum()
        assert nulls == 0, f"VUL column '{col}' has {nulls} null values"
    print(f"  ✓ VUL no nulls in required fields")

    # Sub-account allocations: validate JSON parseable
    import json as _json
    for i, alloc_str in enumerate(vul_df["sub_account_allocations"].head(10)):
        parsed = _json.loads(alloc_str)
        total = sum(item["alloc_pct"] for item in parsed)
        assert abs(total - 1.0) < 0.01, f"VUL row {i} alloc sum = {total}"
    print(f"  ✓ VUL sub_account_allocations valid JSON, alloc sums ~1.0")

    vul_wd = vul_df["withdrawal_active_flag"].sum()
    print(f"  ✓ VUL withdrawal active: {vul_wd} ({vul_wd/len(vul_df)*100:.1f}%)  [target ~15%]")

    vul_ci = vul_df["ci_rider_flag"].sum()
    print(f"  ✓ VUL CI rider count: {vul_ci} ({vul_ci/len(vul_df)*100:.1f}%)  [target ~15%]")

    # Deferred Annuity
    assert len(ann_df) == 1_400, f"Expected 1400 DA rows, got {len(ann_df)}"
    print(f"  ✓ DA row count: {len(ann_df)}")

    da_required = ["contract_id", "product_code", "issue_date", "gender",
                   "account_value", "surrender_charge_year", "glwb_elected_flag",
                   "is_surrender_charge_expired_flag", "status_code"]
    for col in da_required:
        nulls = ann_df[col].isna().sum()
        assert nulls == 0, f"DA column '{col}' has {nulls} null values"
    print(f"  ✓ DA no nulls in required fields")

    # Validate no CI rider columns (annuities do not carry CI riders)
    assert "ci_rider_flag" not in ann_df.columns, "DA should not have ci_rider_flag"
    print(f"  ✓ DA correctly has no CI rider columns")

    # Validate DAF / DAV contract ID prefixes
    daf_count = ann_df["contract_id"].str.startswith("DAF-").sum()
    dav_count = ann_df["contract_id"].str.startswith("DAV-").sum()
    assert daf_count + dav_count == len(ann_df), "DA contract_id prefix mismatch"
    print(f"  ✓ DA contract_id: {daf_count} DAF, {dav_count} DAV")

    # Check GLWB elected
    glwb_count = ann_df["glwb_elected_flag"].sum()
    print(f"  ✓ DA GLWB elected: {glwb_count} ({glwb_count/len(ann_df)*100:.1f}%)")

    # Check SC expiry flag consistency
    sc_expired_flag = ann_df["is_surrender_charge_expired_flag"]
    sc_remaining    = ann_df["surrender_charge_remaining"]
    mismatch = ((sc_expired_flag == True) & (sc_remaining > 0)).sum()
    assert mismatch == 0, f"DA {mismatch} contracts have SC expired=True but SC_remaining>0"
    print(f"  ✓ DA SC expiry flag consistent with SC remaining = 0")

    print("\nAll validation checks passed.")


if __name__ == "__main__":
    main()
