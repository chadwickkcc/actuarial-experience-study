"""Exposure engine: build seriatim exposure segments from Silver tables.

Implements the Annual Exposure Method (Balducci) for mortality studies
as specified in the SOA Experience Study Calculations monograph (2016/2024).
"""

import logging
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from src.utils.types import ExposureMethod, ExposureResult, StudyConfig

logger = logging.getLogger(__name__)

DAYS_PER_YEAR = 365.25

# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------

class ReconciliationFailure(Exception):
    """Raised when in-force reconciliation fails beyond 0.01% tolerance."""
    pass


# ---------------------------------------------------------------------------
# Helper functions (exported per interface contract)
# ---------------------------------------------------------------------------

def compute_age_band(attained_age: float, band_size: int = 5) -> str:
    """Return the age band string for a given attained age.

    Examples:
        compute_age_band(52.3, 5)  -> "50-54"
        compute_age_band(52.3, 10) -> "50-59"
    """
    lower = int(attained_age // band_size) * band_size
    upper = lower + band_size - 1
    return f"{lower}-{upper}"


def compute_duration_band(policy_year: int) -> str:
    """Return the duration band string for a given policy year.

    Bands: "1", "2-5", "6-10", "11-15", "16-20", "21-25", "26+"
    """
    if policy_year == 1:
        return "1"
    elif policy_year <= 5:
        return "2-5"
    elif policy_year <= 10:
        return "6-10"
    elif policy_year <= 15:
        return "11-15"
    elif policy_year <= 20:
        return "16-20"
    elif policy_year <= 25:
        return "21-25"
    else:
        return "26+"


def compute_premium_jump_band(jump_ratio: float) -> str:
    """Return PLT premium jump ratio band string.

    Bands: "<=2x", "2-3x", "3-5x", "5-8x", "8-12x", ">12x"
    """
    if jump_ratio <= 2.0:
        return "<=2x"
    elif jump_ratio <= 3.0:
        return "2-3x"
    elif jump_ratio <= 5.0:
        return "3-5x"
    elif jump_ratio <= 8.0:
        return "5-8x"
    elif jump_ratio <= 12.0:
        return "8-12x"
    else:
        return ">12x"


# ---------------------------------------------------------------------------
# Internal date helpers
# ---------------------------------------------------------------------------

def _add_years(d: date, years: int) -> date:
    """Add whole years to a date, handling Feb-29 leap year edge case."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _compute_attained_age(dob: date, on_date: date) -> float:
    """Return age in fractional years on on_date."""
    return (on_date - dob).days / DAYS_PER_YEAR


def _get_anniversary_splits(
    issue_date: date, active_start: date, active_end: date
) -> list[date]:
    """Return policy anniversary dates strictly between active_start and active_end."""
    splits: list[date] = []
    yr = 1
    while True:
        ann = _add_years(issue_date, yr)
        if ann >= active_end:
            break
        if ann > active_start:
            splits.append(ann)
        yr += 1
    return splits


def _get_calendar_year_splits(active_start: date, active_end: date) -> list[date]:
    """Return Jan-1 boundaries strictly between active_start and active_end."""
    splits: list[date] = []
    for yr in range(active_start.year + 1, active_end.year + 1):
        jan1 = date(yr, 1, 1)
        if jan1 > active_start and jan1 < active_end:
            splits.append(jan1)
    return splits


# ---------------------------------------------------------------------------
# Status / event type mappings
# ---------------------------------------------------------------------------

_STATUS_TO_DECREMENT: dict[str, str] = {
    "LAPSE":    "LAPSE",
    "DEATH":    "DEATH",
    "CONV":     "CONVERSION",
    "CI_CLAIM": "CI_CLAIM",
    "EXPIRY":   "EXPIRY",
    "SURRENDER":"SURRENDER",
}


# ---------------------------------------------------------------------------
# Per-policy segment builder
# ---------------------------------------------------------------------------

def _build_segments_for_policy(
    policy: pd.Series,
    study_start: date,
    study_end: date,
    exposure_method: ExposureMethod,
    study_run_id: str,
    illness_code_map: dict[str, str],
    product_code: str = "TERM",
) -> list[dict]:
    """Build all exposure segments for a single policy.

    Splits at policy anniversaries, Jan-1 calendar boundaries, study start/end,
    and the termination event date.  Applies the Balducci (Annual) method:
    deaths receive exposure_years = 1.0; all other decrements receive fractional
    exposure proportional to time at risk.

    Args:
        policy:       Row from silver_{product}_policies (normalised to common columns).
        product_code: Product code string; controls PLT and account_value handling.
    """
    issue_date: date = policy["issue_date"]
    dob: date = policy["date_of_birth"]
    status_code: str = policy["status_code"]
    termination_date = policy["termination_date"]
    # level_period_years only applies to TERM; non-TERM products never enter PLT
    _lvl = policy.get("level_period_years", None)
    level_period_years: int = int(_lvl) if (_lvl is not None and not pd.isna(_lvl)) else 9999
    face_amount: float = float(policy["face_amount"])
    ci_rider_flag: bool = bool(policy["ci_rider_flag"])
    account_value_raw = policy.get("account_value", None)

    # Scalar helpers to avoid pandas NA comparisons
    premium_jump_ratio = policy.get("premium_jump_ratio", None)
    if premium_jump_ratio is not None and pd.isna(premium_jump_ratio):
        premium_jump_ratio = None
    plt_structure_code = policy.get("plt_structure_code", None)
    if plt_structure_code is not None and pd.isna(plt_structure_code):
        plt_structure_code = None
    ci_rider_sum_assured = policy["ci_rider_sum_assured"]
    if pd.isna(ci_rider_sum_assured):
        ci_rider_sum_assured = None
    # Does this policy terminate within the study window?
    is_terminated = (
        status_code != "IF"
        and termination_date is not None
        and not pd.isna(termination_date)
        and termination_date >= study_start
        and termination_date <= study_end
    )

    # Active exposure window within the study
    active_start = max(issue_date, study_start)
    active_end = termination_date if is_terminated else study_end
    active_end = min(active_end, study_end)

    # Skip policies with no exposure in study window
    if active_start >= active_end:
        return []

    # Build sorted split-point list
    splits: list[date] = sorted(set(
        [active_start, active_end]
        + _get_anniversary_splits(issue_date, active_start, active_end)
        + _get_calendar_year_splits(active_start, active_end)
    ))

    decrement_type = _STATUS_TO_DECREMENT.get(status_code) if is_terminated else None
    policy_id: str = policy["policy_id"]

    segments: list[dict] = []

    for i in range(len(splits) - 1):
        seg_start = splits[i]
        seg_end = splits[i + 1]
        is_last = i == len(splits) - 2
        is_decrement_seg = is_last and is_terminated

        # Policy year (1-based) — use relativedelta so that a seg_start exactly on
        # an anniversary gives the correct integer years-elapsed count.
        rd = relativedelta(seg_start, issue_date)
        policy_year = max(1, rd.years + 1)

        is_plt = policy_year > level_period_years

        # Exposure years — Balducci: deaths get full policy-year = 1.0
        days = (seg_end - seg_start).days
        pol_yr_start = _add_years(issue_date, policy_year - 1)
        pol_yr_end = _add_years(issue_date, policy_year)
        pol_yr_days = (pol_yr_end - pol_yr_start).days

        if exposure_method == ExposureMethod.ANNUAL and is_decrement_seg and decrement_type == "DEATH":
            exposure_years = 1.0
        else:
            # Normalise by actual days in this policy year to handle 366-day leap
            # years and satisfy the DB constraint (exposure_years <= 1.0001).
            exposure_years = min(days / pol_yr_days, 1.0)

        # Initial exposed to risk for lapse study (SOA convention): lapse segments
        # contribute up to pol_yr_end regardless of calendar year, giving 1.0 per
        # policy year. For segments that start at pol_yr_start the full year is used;
        # for tail segments (after a calendar-year split) pol_yr_end is still the
        # natural boundary and the prior non-decrement segment already captured the
        # earlier portion, so the two pieces sum to ≈ 1.0.
        if is_decrement_seg and decrement_type == "LAPSE":
            natural_end = min(pol_yr_end, study_end)
            natural_days = max((natural_end - seg_start).days, 1)
            lapse_initial_exp = min(natural_days / pol_yr_days, 1.0)
        elif is_decrement_seg and decrement_type == "DEATH":
            # Deaths under Balducci get full-year mortality exposure; for lapse
            # study use actual fractional time (deaths are not lapses).
            lapse_initial_exp = min(days / pol_yr_days, 1.0)
        else:
            lapse_initial_exp = exposure_years

        attained_age_start = _compute_attained_age(dob, seg_start)
        attained_age_end = _compute_attained_age(dob, seg_end)

        # CI rider in-force: True unless policy terminated as a CI claim
        ci_in_force = ci_rider_flag and not (is_decrement_seg and decrement_type == "CI_CLAIM")
        ci_sa = float(ci_rider_sum_assured) if (ci_rider_flag and ci_rider_sum_assured is not None) else None

        # PLT fields — for TERM only
        # For VUL: is_plt repurposed as withdrawal_active_flag
        # For DA: is_plt repurposed as approaching_expiry (final SC year)
        is_da = product_code in ("DA", "DA_FIXED", "DA_FIA", "DA_VA")
        is_vul = product_code == "VUL"

        if is_vul:
            withdrawal_active_raw = policy.get("withdrawal_active_flag", False)
            is_plt = bool(withdrawal_active_raw) if withdrawal_active_raw is not None else False
        elif is_da:
            sc_year_raw = policy.get("surrender_charge_year", 0)
            sc_expired_raw = policy.get("is_surrender_charge_expired_flag", False)
            # approaching_expiry: in the shock surrender-charge year (7th of 7 or 10th of 10)
            # We detect this by checking if sc_year is at a typical shock point (6-10)
            # and not yet expired. Simplified: tag as approaching_expiry if sc_year in [6,7,8,9,10]
            sc_year_int = int(sc_year_raw) if sc_year_raw is not None else 0
            sc_expired_bool = bool(sc_expired_raw) if sc_expired_raw is not None else False
            is_plt = (sc_year_int >= 6 and sc_year_int <= 10 and not sc_expired_bool)

        pjr = float(premium_jump_ratio) if (is_plt and premium_jump_ratio is not None and not is_vul and not is_da) else None
        pjr_band = compute_premium_jump_band(pjr) if pjr is not None else None
        plt_dur = (policy_year - level_period_years) if (is_plt and not is_vul and not is_da) else None
        plt_struct = plt_structure_code if (is_plt and not is_vul and not is_da) else None

        # illness_code only on CI decrement segment
        illness = illness_code_map.get(policy_id) if (is_decrement_seg and decrement_type == "CI_CLAIM") else None

        account_val = float(account_value_raw) if (account_value_raw is not None and not pd.isna(account_value_raw)) else None

        segments.append({
            "segment_id":               str(uuid.uuid4()),
            "study_run_id":             study_run_id,
            "policy_id":                policy_id,
            "product_code":             product_code,
            "segment_start_date":       seg_start,
            "segment_end_date":         seg_end,
            "exposure_years":           exposure_years,
            "lapse_exposure_years":     lapse_initial_exp,
            "face_amount_start":        face_amount,
            "face_amount_end":          face_amount,
            "face_amount_wtd_avg":      face_amount,
            "account_value":            account_val,
            "ci_rider_sum_assured":     ci_sa,
            "ci_rider_in_force_flag":   ci_in_force,
            "attained_age_start":       attained_age_start,
            "attained_age_end":         attained_age_end,
            "attained_age_band":        compute_age_band(attained_age_start),
            "issue_age_anb":            int(policy["issue_age_anb"]),
            "issue_age_band":           compute_age_band(float(policy["issue_age_anb"])),
            "policy_year":              policy_year,
            "duration_band":            compute_duration_band(policy_year),
            "calendar_year":            seg_start.year,
            "gender":                   policy["gender"],
            "smoker_status":            policy["smoker_status"],
            "risk_class":               policy["risk_class"],
            "plan_code":                policy["plan_code"],
            "is_plt_flag":              is_plt,
            "plt_duration":             plt_dur,
            "plt_structure_code":       plt_struct,
            "premium_jump_ratio":       pjr,
            "premium_jump_ratio_band":  pjr_band,
            "distribution_channel":     policy["distribution_channel"],
            "decrement_flag":           is_decrement_seg,
            "decrement_type":           decrement_type if is_decrement_seg else None,
            "illness_code":             illness,
            "face_amount_at_decrement": face_amount if is_decrement_seg else None,
            "exposure_method":          exposure_method.value,
        })

    return segments


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _load_silver_policies(
    con: duckdb.DuckDBPyConnection,
    product_code: str,
) -> pd.DataFrame:
    """Load and normalise silver policies for any supported product.

    Normalises UL's specified_amount → face_amount and account_value_eom → account_value
    so that downstream segment builders can use consistent column names.
    """
    if product_code == "TERM":
        return con.execute("""
            SELECT DISTINCT ON (policy_id)
                policy_id, product_code, plan_code,
                issue_date, date_of_birth, issue_age_anb,
                gender, smoker_status, risk_class,
                face_amount, annual_premium, premium_mode,
                status_code, termination_date, termination_cause_code,
                level_period_years, plt_premium_year_1, plt_structure_code,
                premium_jump_ratio, ci_rider_flag, ci_rider_sum_assured,
                ci_rider_premium, distribution_channel, issue_state,
                NULL::DOUBLE AS account_value,
                NULL::DOUBLE AS premium_persistency_ratio,
                FALSE::BOOLEAN AS withdrawal_active_flag,
                NULL::DOUBLE AS fund_value_to_spec_amount_ratio
            FROM silver_term_policies
            ORDER BY policy_id, _load_ts DESC
        """).df()

    if product_code == "WL":
        return con.execute("""
            SELECT DISTINCT ON (policy_id)
                policy_id, product_code, plan_code,
                issue_date, date_of_birth, issue_age_anb,
                gender, smoker_status, risk_class,
                face_amount, annual_premium, premium_mode,
                status_code, termination_date, termination_cause_code,
                ci_rider_flag, ci_rider_sum_assured, ci_rider_premium,
                distribution_channel, issue_state,
                NULL::INTEGER AS level_period_years,
                NULL::VARCHAR AS plt_structure_code,
                NULL::DOUBLE AS premium_jump_ratio,
                NULL::DOUBLE AS account_value,
                NULL::DOUBLE AS premium_persistency_ratio,
                FALSE::BOOLEAN AS withdrawal_active_flag,
                NULL::DOUBLE AS fund_value_to_spec_amount_ratio
            FROM silver_wl_policies
            ORDER BY policy_id, _load_ts DESC
        """).df()

    if product_code == "VUL":
        # VUL: map specified_amount → face_amount, separate_account_total_value → account_value
        # is_plt_flag repurposed for VUL as withdrawal_active_flag (PLT doesn't apply to VUL)
        return con.execute("""
            SELECT DISTINCT ON (policy_id)
                policy_id, product_code, plan_code,
                issue_date, date_of_birth, issue_age_anb,
                gender, smoker_status, risk_class,
                specified_amount AS face_amount,
                annual_premium, premium_mode,
                status_code, termination_date, termination_cause_code,
                ci_rider_flag, ci_rider_sum_assured, ci_rider_premium,
                distribution_channel, issue_state,
                NULL::INTEGER AS level_period_years,
                NULL::VARCHAR AS plt_structure_code,
                NULL::DOUBLE AS premium_jump_ratio,
                (separate_account_total_value + fixed_account_value) AS account_value,
                NULL::DOUBLE AS premium_persistency_ratio,
                withdrawal_active_flag,
                fund_value_to_spec_amount_ratio
            FROM silver_vul_policies
            ORDER BY policy_id, _load_ts DESC
        """).df()

    if product_code in ("DA", "DA_FIXED", "DA_FIA", "DA_VA"):
        # DA: contract_id aliased to policy_id for consistent downstream handling
        # account_value used as face_amount proxy
        # is_plt_flag repurposed as approaching_expiry (final SC year)
        return con.execute("""
            SELECT DISTINCT ON (contract_id)
                contract_id AS policy_id,
                product_code, product_type AS plan_code,
                issue_date, date_of_birth, issue_age_anb,
                gender,
                'NS'::VARCHAR AS smoker_status,
                'STD_NS'::VARCHAR AS risk_class,
                account_value AS face_amount,
                0.0::DOUBLE AS annual_premium,
                'ANNUAL'::VARCHAR AS premium_mode,
                status_code, termination_date, termination_cause_code,
                FALSE::BOOLEAN AS ci_rider_flag,
                NULL::DOUBLE AS ci_rider_sum_assured,
                NULL::DOUBLE AS ci_rider_premium,
                distribution_channel, issue_state,
                NULL::INTEGER AS level_period_years,
                NULL::VARCHAR AS plt_structure_code,
                NULL::DOUBLE AS premium_jump_ratio,
                account_value,
                NULL::DOUBLE AS premium_persistency_ratio,
                FALSE::BOOLEAN AS withdrawal_active_flag,
                moneyness_ratio AS fund_value_to_spec_amount_ratio,
                surrender_charge_year,
                is_surrender_charge_expired_flag
            FROM silver_annuity_contracts
            ORDER BY contract_id, _load_ts DESC
        """).df()

    # UL / ULSG / IUL — map specified_amount to face_amount
    return con.execute("""
        SELECT DISTINCT ON (policy_id)
            policy_id, product_code, plan_code,
            issue_date, date_of_birth, issue_age_anb,
            gender, smoker_status, risk_class,
            specified_amount AS face_amount,
            annual_premium, premium_mode,
            status_code, termination_date, termination_cause_code,
            ci_rider_flag, ci_rider_sum_assured, ci_rider_premium,
            distribution_channel, issue_state,
            NULL::INTEGER AS level_period_years,
            NULL::VARCHAR AS plt_structure_code,
            NULL::DOUBLE AS premium_jump_ratio,
            account_value_eom AS account_value,
            premium_persistency_ratio,
            FALSE::BOOLEAN AS withdrawal_active_flag,
            NULL::DOUBLE AS fund_value_to_spec_amount_ratio
        FROM silver_ul_policies
        ORDER BY policy_id, _load_ts DESC
    """).df()


def _run_reconciliation(
    con: duckdb.DuckDBPyConnection,
    policies_df: pd.DataFrame,
    product_code: str,
    study_run_id: str,
    study_start: date,
    study_end: date,
) -> bool:
    """Compute and persist in-force reconciliation rows for each calendar year.

    Returns True if every year passes within ±0.01% tolerance.
    """
    all_pass = True
    recon_rows: list[dict] = []

    for year in range(study_start.year, study_end.year + 1):
        jan1 = date(year, 1, 1)
        dec31 = date(year, 12, 31)
        jan1_next = date(year + 1, 1, 1)

        # In-force masks
        def _if_on(on_date: date) -> pd.Series:
            issued_before = policies_df["issue_date"] < on_date
            not_yet_dead = (
                policies_df["termination_date"].isna()
                | (policies_df["termination_date"] >= on_date)
            )
            return issued_before & not_yet_dead

        beg_mask = _if_on(jan1)
        end_mask = _if_on(jan1_next)

        beg_if_count = int(beg_mask.sum())
        end_if_count = int(end_mask.sum())

        new_mask = (policies_df["issue_date"] >= jan1) & (policies_df["issue_date"] <= dec31)
        new_issues_count = int(new_mask.sum())

        deaths_mask = (
            (policies_df["status_code"] == "DEATH")
            & (policies_df["termination_date"] >= jan1)
            & (policies_df["termination_date"] <= dec31)
        )
        lapses_mask = (
            (policies_df["status_code"] == "LAPSE")
            & (policies_df["termination_date"] >= jan1)
            & (policies_df["termination_date"] <= dec31)
        )
        surrenders_mask = (
            (policies_df["status_code"] == "SURRENDER")
            & (policies_df["termination_date"] >= jan1)
            & (policies_df["termination_date"] <= dec31)
        )
        other_mask = (
            (~policies_df["status_code"].isin(["IF", "DEATH", "LAPSE", "SURRENDER"]))
            & policies_df["termination_date"].notna()
            & (policies_df["termination_date"] >= jan1)
            & (policies_df["termination_date"] <= dec31)
        )

        deaths_count = int(deaths_mask.sum())
        lapses_count = int(lapses_mask.sum())
        surrenders_count = int(surrenders_mask.sum())
        other_decrements = int(other_mask.sum())
        total_decrements = deaths_count + lapses_count + surrenders_count + other_decrements

        recon_diff_count = beg_if_count + new_issues_count - total_decrements - end_if_count

        # Amount reconciliation
        beg_if_amount = float(policies_df.loc[beg_mask, "face_amount"].sum())
        end_if_amount = float(policies_df.loc[end_mask, "face_amount"].sum())
        new_issues_amount = float(policies_df.loc[new_mask, "face_amount"].sum())
        deaths_amount = float(policies_df.loc[deaths_mask, "face_amount"].sum())
        lapses_amount = float(policies_df.loc[lapses_mask, "face_amount"].sum())
        surrenders_amount = float(policies_df.loc[surrenders_mask, "face_amount"].sum())
        other_amount = float(policies_df.loc[other_mask, "face_amount"].sum())
        total_dec_amount = deaths_amount + lapses_amount + surrenders_amount + other_amount

        recon_diff_amount = beg_if_amount + new_issues_amount - total_dec_amount - end_if_amount

        # Tolerance: ±0.01% of beginning in-force amount
        tolerance_amount = max(beg_if_amount * 0.0001, 1.0)
        passes = (recon_diff_count == 0) and (abs(recon_diff_amount) <= tolerance_amount)

        if not passes:
            logger.warning(
                "Reconciliation FAILED year=%d diff_count=%d diff_amount=%.2f",
                year, recon_diff_count, recon_diff_amount,
            )
            all_pass = False

        recon_rows.append({
            "recon_id":           str(uuid.uuid4()),
            "study_run_id":       study_run_id,
            "product_code":       product_code,
            "calendar_year":      year,
            "beg_if_count":       beg_if_count,
            "new_issues_count":   new_issues_count,
            "deaths_count":       deaths_count,
            "lapses_count":       lapses_count,
            "surrenders_count":   surrenders_count,
            "other_decrements":   other_decrements,
            "end_if_count":       end_if_count,
            "recon_diff_count":   recon_diff_count,
            "beg_if_amount":      beg_if_amount,
            "new_issues_amount":  new_issues_amount,
            "deaths_amount":      deaths_amount,
            "lapses_amount":      lapses_amount,
            "surrenders_amount":  surrenders_amount,
            "other_amount":       other_amount,
            "end_if_amount":      end_if_amount,
            "recon_diff_amount":  recon_diff_amount,
            "recon_passes":       passes,
        })

    recon_df = pd.DataFrame(recon_rows)
    con.execute(
        "DELETE FROM gold_inforce_reconciliation WHERE study_run_id = ?",
        [study_run_id],
    )
    con.execute("INSERT INTO gold_inforce_reconciliation SELECT * FROM recon_df")

    return all_pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_exposure_file(
    product_code: str,
    db_path: Path,
    study_config: StudyConfig,
    study_run_id: str,
) -> ExposureResult:
    """Build seriatim exposure segments for one product.

    Loads silver_{product}_policies (excluding non-overridden quarantine records),
    generates one or more exposure segments per policy split at policy anniversaries,
    calendar year boundaries, and event dates, then writes to gold_exposure_segments.
    Runs in-force reconciliation and writes results to gold_inforce_reconciliation.

    Args:
        product_code:   Product code string (e.g., "TERM").
        db_path:        Path to the DuckDB file.
        study_config:   Full study configuration including dates and exposure method.
        study_run_id:   UUID for this study run.

    Returns:
        ExposureResult with segment counts, total exposure, and reconciliation status.

    Raises:
        ValueError:             If product_code is not supported.
        ReconciliationFailure:  If in-force reconciliation fails beyond tolerance.
    """
    _SUPPORTED = {"TERM", "WL", "UL", "ULSG", "IUL", "VUL", "DA", "DA_FIXED", "DA_FIA", "DA_VA"}
    if product_code not in _SUPPORTED:
        raise ValueError(f"Exposure engine supports {_SUPPORTED}; got '{product_code}'")

    t0 = time.time()
    study_start = study_config.study_start_date
    study_end = study_config.study_end_date

    con = duckdb.connect(str(db_path))
    try:
        # ------------------------------------------------------------------
        # 1. Load non-quarantined policies (latest ETL run per policy)
        # ------------------------------------------------------------------
        # DA uses contract_id stored in policy_id column of quarantine table
        quarantined_ids_df = con.execute("""
            SELECT DISTINCT policy_id
            FROM gold_dq_quarantine
            WHERE actuary_override_flag = FALSE
              AND study_run_id = ?
        """, [study_run_id]).df()
        quarantined_ids = set(quarantined_ids_df["policy_id"].tolist())

        policies_df = _load_silver_policies(con, product_code)

        # Normalise date columns to Python date objects regardless of DuckDB return type
        for col in ("issue_date", "date_of_birth"):
            policies_df[col] = pd.to_datetime(policies_df[col]).dt.date
        # termination_date is nullable
        policies_df["termination_date"] = pd.to_datetime(
            policies_df["termination_date"], errors="coerce"
        ).dt.date

        # Exclude quarantined
        before_count = len(policies_df)
        policies_df = policies_df[~policies_df["policy_id"].isin(quarantined_ids)].copy()
        logger.info(
            "Loaded %d policies (%d excluded by quarantine)",
            len(policies_df),
            before_count - len(policies_df),
        )

        # ------------------------------------------------------------------
        # 2. Load CI claim events for illness codes
        # ------------------------------------------------------------------
        ci_events_df = con.execute("""
            SELECT policy_id, illness_code
            FROM silver_policy_events
            WHERE product_code = ?
              AND event_type = 'CI_CLAIM'
              AND illness_code IS NOT NULL
              AND _etl_run_id = ?
        """, [product_code, study_run_id]).df()
        illness_code_map: dict[str, str] = dict(
            zip(ci_events_df["policy_id"], ci_events_df["illness_code"])
        )

        # ------------------------------------------------------------------
        # 3. Generate segments for each policy
        # ------------------------------------------------------------------
        all_segments: list[dict] = []
        for _, policy in policies_df.iterrows():
            # Use the policy's actual product_code (e.g. "ULSG", "IUL") when available,
            # so sub-product A/E can be computed separately downstream.
            actual_product_code = policy.get("product_code", product_code) or product_code
            segs = _build_segments_for_policy(
                policy, study_start, study_end,
                study_config.exposure_method, study_run_id, illness_code_map,
                product_code=actual_product_code,
            )
            all_segments.extend(segs)

        logger.info("Generated %d exposure segments", len(all_segments))

        if not all_segments:
            raise ReconciliationFailure("No exposure segments generated")

        # ------------------------------------------------------------------
        # 4. Write segments to gold_exposure_segments
        # ------------------------------------------------------------------
        segments_df = pd.DataFrame(all_segments)

        # Ensure boolean column types
        segments_df["ci_rider_in_force_flag"] = segments_df["ci_rider_in_force_flag"].astype(bool)
        segments_df["is_plt_flag"] = segments_df["is_plt_flag"].astype(bool)
        segments_df["decrement_flag"] = segments_df["decrement_flag"].astype(bool)

        # Delete all sub-product codes that will be re-inserted (e.g. UL, ULSG, IUL).
        product_codes_in_segments = segments_df["product_code"].unique().tolist()
        ph = ",".join(["?" for _ in product_codes_in_segments])
        con.execute(
            f"DELETE FROM gold_exposure_segments WHERE study_run_id = ? AND product_code IN ({ph})",
            [study_run_id] + product_codes_in_segments,
        )
        con.execute("INSERT INTO gold_exposure_segments SELECT * FROM segments_df")

        # Verify constraint: no exposure > 1.0001
        over_one = con.execute(
            "SELECT COUNT(*) FROM gold_exposure_segments WHERE study_run_id = ? AND exposure_years > 1.0001",
            [study_run_id],
        ).fetchone()[0]
        if over_one > 0:
            logger.error("%d segments have exposure_years > 1.0001", over_one)

        # ------------------------------------------------------------------
        # 5. In-force reconciliation
        # ------------------------------------------------------------------
        recon_passes = _run_reconciliation(
            con, policies_df, product_code, study_run_id, study_start, study_end
        )

        total_exposure = float(segments_df["exposure_years"].sum())
        face_col = "face_amount" if "face_amount" in policies_df.columns else "specified_amount"
        total_face = float(policies_df[face_col].sum())

        recon_diff = int(con.execute(
            "SELECT COALESCE(SUM(ABS(recon_diff_count)), 0) FROM gold_inforce_reconciliation WHERE study_run_id = ?",
            [study_run_id],
        ).fetchone()[0])

        if not recon_passes:
            raise ReconciliationFailure(
                f"In-force reconciliation failed for run_id={study_run_id}"
            )

        duration = time.time() - t0
        logger.info(
            "Exposure complete: %d segments, %.1f policy-years, recon_passes=%s, %.1fs",
            len(all_segments), total_exposure, recon_passes, duration,
        )

        return ExposureResult(
            run_id=study_run_id,
            product_code=product_code,
            total_segments=len(all_segments),
            total_exposure_years=total_exposure,
            total_face_amount=total_face,
            recon_passes=recon_passes,
            recon_diff_count=recon_diff,
            recon_diff_amount_pct=0.0,
            duration_sec=duration,
        )

    finally:
        con.close()
