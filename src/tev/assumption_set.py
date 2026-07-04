"""Assumption Set module for TEV Phase 2.

Implements the versioned assumption set artifact (FR-2-01 to FR-2-04),
exactly matching the interface contract in Technical Specification Section B.7.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.utils.types import AssumptionSetStatus


# ---------------------------------------------------------------------------
# Duration band helpers
# ---------------------------------------------------------------------------

_DURATION_BANDS = [
    ("1",     [1,   1]),
    ("2-5",   [2,   5]),
    ("6-10",  [6,  10]),
    ("11-15", [11, 15]),
    ("16-20", [16, 20]),
    ("21-25", [21, 25]),
    ("26+",   [26, 999]),
]


def _parse_duration_band(band_str: str) -> list[int]:
    """Parse a duration band string into [lower, upper] inclusive ints."""
    band_str = str(band_str).strip()
    if band_str.endswith("+"):
        return [int(band_str[:-1]), 999]
    if "-" in band_str:
        lo, hi = band_str.split("-")
        return [int(lo), int(hi)]
    return [int(band_str), int(band_str)]


def _policy_year_to_band(policy_year: int) -> list[int]:
    """Return the duration band [lo, hi] that contains policy_year."""
    for _, bounds in _DURATION_BANDS:
        if bounds[0] <= policy_year <= bounds[1]:
            return bounds
    return [26, 999]


# ---------------------------------------------------------------------------
# Core dataclasses (defined here per Section B.7)
# ---------------------------------------------------------------------------

@dataclass
class DecrementMultiplier:
    """A single credibility-weighted multiplier cell for one decrement type.

    For CI incidence multipliers, ``product`` holds the illness_code and
    ``duration_band`` holds the age band [lo, hi].
    """

    product: str
    gender: str
    risk_class: str
    duration_band: list[int]        # [lower_inclusive, upper_inclusive]
    multiplier: float
    credibility_z: float
    credibility_lower: float        # 95% CI lower bound from A/E study
    credibility_upper: float        # 95% CI upper bound from A/E study
    override_rationale: str = ""    # free text if actuary deviated from A/E

    def matches(self, product: str, gender: str, risk_class: str, value: int) -> bool:
        """Return True when this multiplier cell covers the given dimensions."""
        return (
            self.product == product
            and self.gender == gender
            and self.risk_class == risk_class
            and self.duration_band[0] <= value <= self.duration_band[1]
        )

    def to_dict(self) -> dict:
        """Serialise to a plain dict for YAML output."""
        return {
            "product": self.product,
            "gender": self.gender,
            "risk_class": self.risk_class,
            "duration_band": self.duration_band,
            "multiplier": round(self.multiplier, 6),
            "credibility_z": round(self.credibility_z, 6),
            "credibility_lower": round(self.credibility_lower, 6),
            "credibility_upper": round(self.credibility_upper, 6),
            "override_rationale": self.override_rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DecrementMultiplier":
        """Deserialise from a plain dict loaded from YAML."""
        return cls(
            product=d["product"],
            gender=d["gender"],
            risk_class=d["risk_class"],
            duration_band=d["duration_band"],
            multiplier=d["multiplier"],
            credibility_z=d["credibility_z"],
            credibility_lower=d["credibility_lower"],
            credibility_upper=d["credibility_upper"],
            override_rationale=d.get("override_rationale", ""),
        )


@dataclass
class AssumptionSet:
    """Versioned assumption set artifact linking experience study to TEV projection.

    Serialisable to YAML; metadata row stored in gold_assumption_sets.
    Implements FR-2-01 to FR-2-04.
    """

    id: str
    version: int
    status: AssumptionSetStatus
    effective_date: str             # ISO date string
    author_id: str
    basis: str
    source_study_run_id: str

    # Economic parameters
    rdr: float
    earned_rate_ga: float
    earned_rate_sa: float
    tax_rate: float
    expense_inflation: float

    # Required capital proxies keyed by product_code
    rc_pct_reserve: dict[str, float]

    # Expense assumptions
    acquisition_per_policy: float
    maintenance_per_policy: float
    maintenance_pct_premium: float

    # Decrement multipliers
    mortality_multipliers: list[DecrementMultiplier]
    lapse_multipliers: list[DecrementMultiplier]
    surrender_multipliers: list[DecrementMultiplier]
    ci_incidence_multipliers: list[DecrementMultiplier]
    premium_persistency: list[DecrementMultiplier]

    # PLT shock lapse keyed by premium_jump_ratio_band
    shock_lapse_plt: dict[str, float]

    yaml_file_path: str = ""

    # ------------------------------------------------------------------
    # YAML serialisation
    # ------------------------------------------------------------------

    def to_yaml_dict(self) -> dict:
        """Return the full assumption set as a nested dict for YAML serialisation."""
        return {
            "assumption_set": {
                "id": self.id,
                "version": self.version,
                "status": self.status.value,
                "effective_date": self.effective_date,
                "author": self.author_id,
                "basis": self.basis,
                "source_experience_study_run": self.source_study_run_id,
                "created_ts": datetime.utcnow().isoformat(),
                "approved_by": None,
                "approved_ts": None,

                "mortality": {
                    "table_base": "2015_VBT_ANB",
                    "improvement_scale": "G2",
                    "multipliers": [m.to_dict() for m in self.mortality_multipliers],
                },
                "lapse": {
                    "base_table": "SOA_LIMRA_2022",
                    "shock_lapse_plt": self.shock_lapse_plt,
                    "multipliers": [m.to_dict() for m in self.lapse_multipliers],
                },
                "surrender": {
                    "base_table": "SOA_LIMRA_FRDA_2022",
                    "multipliers": [m.to_dict() for m in self.surrender_multipliers],
                },
                "ci_incidence": {
                    "table_base": "CI_incidence_reference",
                    "multipliers": [m.to_dict() for m in self.ci_incidence_multipliers],
                },
                "premium_persistency": {
                    "by_duration": [m.to_dict() for m in self.premium_persistency],
                },
                "expenses": {
                    "acquisition_per_policy": self.acquisition_per_policy,
                    "maintenance_per_policy": self.maintenance_per_policy,
                    "maintenance_pct_premium": self.maintenance_pct_premium,
                    "expense_inflation": self.expense_inflation,
                },
                "economic": {
                    "rdr": self.rdr,
                    "earned_rate_ga": self.earned_rate_ga,
                    "earned_rate_sa": self.earned_rate_sa,
                    "tax_rate": self.tax_rate,
                    "rc_pct_reserve": self.rc_pct_reserve,
                },
            }
        }

    def save_yaml(self, output_path: Path) -> None:
        """Write the assumption set to a YAML file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as fh:
            yaml.dump(self.to_yaml_dict(), fh, default_flow_style=False, sort_keys=False)
        self.yaml_file_path = str(output_path)

    @classmethod
    def from_yaml_dict(cls, d: dict) -> "AssumptionSet":
        """Deserialise from the nested YAML dict structure."""
        data = d["assumption_set"]

        def _load_mults(lst: list) -> list[DecrementMultiplier]:
            return [DecrementMultiplier.from_dict(m) for m in (lst or [])]

        economic = data.get("economic", {})
        expenses = data.get("expenses", {})
        lapse_section = data.get("lapse", {})

        return cls(
            id=data["id"],
            version=data["version"],
            status=AssumptionSetStatus(data["status"]),
            effective_date=data["effective_date"],
            author_id=data["author"],
            basis=data["basis"],
            source_study_run_id=data["source_experience_study_run"],

            rdr=economic.get("rdr", 0.09),
            earned_rate_ga=economic.get("earned_rate_ga", 0.05),
            earned_rate_sa=economic.get("earned_rate_sa", 0.06),
            tax_rate=economic.get("tax_rate", 0.21),
            expense_inflation=expenses.get("expense_inflation", 0.025),
            rc_pct_reserve=economic.get("rc_pct_reserve", {}),
            acquisition_per_policy=expenses.get("acquisition_per_policy", 350.0),
            maintenance_per_policy=expenses.get("maintenance_per_policy", 72.0),
            maintenance_pct_premium=expenses.get("maintenance_pct_premium", 0.02),

            mortality_multipliers=_load_mults(
                data.get("mortality", {}).get("multipliers", [])
            ),
            lapse_multipliers=_load_mults(lapse_section.get("multipliers", [])),
            surrender_multipliers=_load_mults(
                data.get("surrender", {}).get("multipliers", [])
            ),
            ci_incidence_multipliers=_load_mults(
                data.get("ci_incidence", {}).get("multipliers", [])
            ),
            premium_persistency=_load_mults(
                data.get("premium_persistency", {}).get("by_duration", [])
            ),
            shock_lapse_plt=lapse_section.get("shock_lapse_plt", {}),
        )


# ---------------------------------------------------------------------------
# AE aggregation helpers
# ---------------------------------------------------------------------------

def _compute_ci_bounds(ae: float, actual: float, z: float = 1.96
                       ) -> tuple[float, float]:
    """Return (ci_lower, ci_upper) for a Poisson A/E using actual claim count."""
    if actual <= 0:
        return (max(0.0, ae - 1.0), ae + 1.0)
    se = ae / (actual ** 0.5)
    return (max(0.0, ae - z * se), ae + z * se)


def _credibility_z(actual: float, method: str = "LF", threshold: float = 1082.0) -> float:
    """Credibility factor Z for the source study run's configured method.

    LF (Limited Fluctuation):  Z = min(1, sqrt(n / threshold))
    BUHLMANN (simplified fixed-K): Z = sqrt(n / (n + threshold))

    ``method`` is case-insensitive; unrecognised values fall back to LF.
    """
    if actual <= 0:
        return 0.0
    if (method or "LF").strip().upper() == "BUHLMANN":
        return (actual / (actual + threshold)) ** 0.5
    return min(1.0, (actual / threshold) ** 0.5)


def _safe_ae(actual: float, expected: float) -> float:
    """Return A/E ratio, clipped to [0.1, 10.0] to avoid pathological values."""
    if expected <= 0:
        return 1.0
    raw = actual / expected
    return max(0.1, min(10.0, raw))


def _credibility_wtd_ae(ae: float, cred_z: float, complement: float = 1.0) -> float:
    """Credibility-weighted A/E = Z × AE + (1-Z) × complement."""
    return cred_z * ae + (1.0 - cred_z) * complement


# ---------------------------------------------------------------------------
# Public API — matches Technical Specification Section B.7 exactly
# ---------------------------------------------------------------------------

def create_assumption_set_from_ae_run(
    study_run_id: str,
    author_id: str,
    db_path: Path,
    tev_config_path: Path,
    output_yaml_dir: Path,
) -> AssumptionSet:
    """Pre-populate an AssumptionSet from an A/E study run.

    All multipliers initialised to credibility-weighted A/E ratios.
    Credibility bounds set from the 95% CI of the A/E results.
    Writes the YAML file to output_yaml_dir.
    Inserts metadata row into gold_assumption_sets.

    Args:
        study_run_id:       UUID of the source experience study run.
        author_id:          Identifier of the actuary creating the set.
        db_path:            Path to the DuckDB file.
        tev_config_path:    Path to tev_config.yaml.
        output_yaml_dir:    Directory to write the assumption set YAML.

    Returns:
        Populated AssumptionSet with all multiplier cells pre-filled.
    """
    with open(tev_config_path) as fh:
        tev_cfg = yaml.safe_load(fh)

    assumption_set_id = str(uuid.uuid4())
    con = duckdb.connect(str(db_path))
    try:
        method = _run_credibility_method(con, study_run_id)
        mort_mults = _build_mortality_multipliers(con, study_run_id, method)
        lapse_mults = _build_lapse_multipliers(con, study_run_id, method)
        surr_mults = _build_surrender_multipliers(con, study_run_id, method)
        ci_mults = _build_ci_multipliers(con, study_run_id, method)
        persist_mults = _build_persistency_multipliers(con, study_run_id)
    finally:
        con.close()

    aset = AssumptionSet(
        id=assumption_set_id,
        version=1,
        status=AssumptionSetStatus.PROPOSED,
        effective_date=date.today().isoformat(),
        author_id=author_id,
        basis="best-estimate",
        source_study_run_id=study_run_id,

        rdr=float(tev_cfg.get("rdr", 0.09)),
        earned_rate_ga=float(tev_cfg.get("earned_rate_ga", 0.05)),
        earned_rate_sa=float(tev_cfg.get("earned_rate_sa", 0.06)),
        tax_rate=float(tev_cfg.get("tax_rate", 0.21)),
        expense_inflation=float(tev_cfg.get("expense_inflation", 0.025)),
        rc_pct_reserve=dict(tev_cfg.get("rc_pct_reserve", {})),
        acquisition_per_policy=float(tev_cfg.get("acquisition_per_policy", 350.0)),
        maintenance_per_policy=float(tev_cfg.get("maintenance_per_policy", 72.0)),
        maintenance_pct_premium=float(tev_cfg.get("maintenance_pct_premium", 0.02)),

        mortality_multipliers=mort_mults,
        lapse_multipliers=lapse_mults,
        surrender_multipliers=surr_mults,
        ci_incidence_multipliers=ci_mults,
        premium_persistency=persist_mults,

        shock_lapse_plt={
            "jump_band_lt_2x":  0.30,
            "jump_band_2x_5x":  0.55,
            "jump_band_5x_8x":  0.70,
            "jump_band_gt_8x":  0.88,
        },
    )

    yaml_path = output_yaml_dir / f"{assumption_set_id}.yaml"
    aset.save_yaml(yaml_path)

    _insert_assumption_set_metadata(db_path, aset)
    return aset


def _run_credibility_method(con: duckdb.DuckDBPyConnection, study_run_id: str) -> str:
    """Return the source run's credibility method code ('LF' or 'BUHLMANN').

    Defaults to 'LF' when the run is missing or the column is NULL.
    """
    row = con.execute(
        "SELECT credibility_method FROM gold_study_runs WHERE run_id = ?",
        [study_run_id],
    ).fetchone()
    if row is None or row[0] is None:
        return "LF"
    return str(row[0])


def _build_mortality_multipliers(con: duckdb.DuckDBPyConnection,
                                  study_run_id: str,
                                  method: str = "LF") -> list[DecrementMultiplier]:
    """Aggregate mortality A/E by (product, gender, risk_class, duration_band)."""
    rows = con.execute("""
        SELECT product_code, gender, risk_class, duration_band,
               COALESCE(SUM(actual_deaths_count), 0)   AS actual,
               COALESCE(SUM(expected_deaths_count), 0) AS expected
        FROM gold_ae_results
        WHERE study_run_id = ?
          AND product_code IS NOT NULL
          AND gender IS NOT NULL
          AND risk_class IS NOT NULL
          AND duration_band IS NOT NULL
          AND illness_code IS NULL
        GROUP BY product_code, gender, risk_class, duration_band
        HAVING SUM(expected_deaths_count) > 0
        ORDER BY product_code, gender, risk_class, duration_band
    """, [study_run_id]).fetchall()

    mults = []
    for prod, gen, rc, db_str, actual, expected in rows:
        ae = _safe_ae(actual, expected)
        z = _credibility_z(actual, method=method)
        cw_ae = _credibility_wtd_ae(ae, z)
        raw_lo, raw_hi = _compute_ci_bounds(ae, actual)
        # Blend bounds the same way as the multiplier: Z×raw + (1-Z)×1.0
        # This guarantees ci_lower ≤ multiplier ≤ ci_upper always.
        lo = z * raw_lo + (1.0 - z) * 1.0
        hi = z * raw_hi + (1.0 - z) * 1.0
        mults.append(DecrementMultiplier(
            product=prod,
            gender=gen,
            risk_class=rc,
            duration_band=_parse_duration_band(db_str),
            multiplier=round(cw_ae, 6),
            credibility_z=round(z, 6),
            credibility_lower=round(lo, 6),
            credibility_upper=round(hi, 6),
        ))
    return mults


def _build_lapse_multipliers(con: duckdb.DuckDBPyConnection,
                              study_run_id: str,
                              method: str = "LF") -> list[DecrementMultiplier]:
    """Aggregate lapse A/E by (product, gender, risk_class, duration_band)."""
    rows = con.execute("""
        SELECT product_code, gender, risk_class, duration_band,
               COALESCE(SUM(actual_lapses), 0)    AS actual,
               COALESCE(SUM(expected_lapses), 0)  AS expected
        FROM gold_ae_results
        WHERE study_run_id = ?
          AND product_code IS NOT NULL
          AND gender IS NOT NULL
          AND risk_class IS NOT NULL
          AND duration_band IS NOT NULL
          AND illness_code IS NULL
        GROUP BY product_code, gender, risk_class, duration_band
        HAVING SUM(expected_lapses) > 0
        ORDER BY product_code, gender, risk_class, duration_band
    """, [study_run_id]).fetchall()

    mults = []
    for prod, gen, rc, db_str, actual, expected in rows:
        ae = _safe_ae(actual, expected)
        z = _credibility_z(actual, method=method, threshold=400.0)   # lower threshold for lapse
        cw_ae = _credibility_wtd_ae(ae, z)
        raw_lo, raw_hi = _compute_ci_bounds(ae, actual)
        lo = z * raw_lo + (1.0 - z) * 1.0
        hi = z * raw_hi + (1.0 - z) * 1.0
        mults.append(DecrementMultiplier(
            product=prod,
            gender=gen,
            risk_class=rc,
            duration_band=_parse_duration_band(db_str),
            multiplier=round(cw_ae, 6),
            credibility_z=round(z, 6),
            credibility_lower=round(lo, 6),
            credibility_upper=round(hi, 6),
        ))
    return mults


def _build_surrender_multipliers(con: duckdb.DuckDBPyConnection,
                                  study_run_id: str,
                                  method: str = "LF") -> list[DecrementMultiplier]:
    """Aggregate surrender A/E by (product, gender, risk_class, duration_band)."""
    rows = con.execute("""
        SELECT product_code, gender, risk_class, duration_band,
               COALESCE(SUM(actual_surrenders), 0)    AS actual,
               COALESCE(SUM(expected_surrenders), 0)  AS expected
        FROM gold_ae_results
        WHERE study_run_id = ?
          AND product_code IS NOT NULL
          AND gender IS NOT NULL
          AND risk_class IS NOT NULL
          AND duration_band IS NOT NULL
          AND illness_code IS NULL
        GROUP BY product_code, gender, risk_class, duration_band
        HAVING SUM(expected_surrenders) > 0
        ORDER BY product_code, gender, risk_class, duration_band
    """, [study_run_id]).fetchall()

    mults = []
    for prod, gen, rc, db_str, actual, expected in rows:
        ae = _safe_ae(actual, expected)
        z = _credibility_z(actual, method=method, threshold=400.0)
        cw_ae = _credibility_wtd_ae(ae, z)
        raw_lo, raw_hi = _compute_ci_bounds(ae, actual)
        lo = z * raw_lo + (1.0 - z) * 1.0
        hi = z * raw_hi + (1.0 - z) * 1.0
        mults.append(DecrementMultiplier(
            product=prod,
            gender=gen,
            risk_class=rc,
            duration_band=_parse_duration_band(db_str),
            multiplier=round(cw_ae, 6),
            credibility_z=round(z, 6),
            credibility_lower=round(lo, 6),
            credibility_upper=round(hi, 6),
        ))
    return mults


def _build_ci_multipliers(con: duckdb.DuckDBPyConnection,
                           study_run_id: str,
                           method: str = "LF") -> list[DecrementMultiplier]:
    """Aggregate CI incidence A/E by (illness_code, gender, attained_age_band).

    Uses DecrementMultiplier with product=illness_code and
    duration_band encoding the attained_age_band integers.
    """
    rows = con.execute("""
        SELECT illness_code,
               COALESCE(gender, 'U') AS gender,
               COALESCE(attained_age_band, issue_age_band, '40-44') AS age_band,
               COALESCE(SUM(actual_ci_claims), 0)    AS actual,
               COALESCE(SUM(expected_ci_claims), 0)  AS expected
        FROM gold_ae_results
        WHERE study_run_id = ?
          AND illness_code IS NOT NULL
        GROUP BY illness_code, gender, COALESCE(attained_age_band, issue_age_band, '40-44')
        HAVING SUM(expected_ci_claims) > 0
        ORDER BY illness_code, gender, age_band
    """, [study_run_id]).fetchall()

    mults = []
    for illness_code, gen, age_band, actual, expected in rows:
        ae = _safe_ae(actual, expected)
        z = _credibility_z(actual, method=method, threshold=100.0)   # lower threshold for CI
        cw_ae = _credibility_wtd_ae(ae, z)
        raw_lo, raw_hi = _compute_ci_bounds(ae, actual)
        lo = z * raw_lo + (1.0 - z) * 1.0
        hi = z * raw_hi + (1.0 - z) * 1.0
        mults.append(DecrementMultiplier(
            product=illness_code,       # illness_code stored in 'product'
            gender=gen,
            risk_class="ALL",           # CI does not segment by risk class
            duration_band=_parse_duration_band(age_band),  # age band as int pair
            multiplier=round(cw_ae, 6),
            credibility_z=round(z, 6),
            credibility_lower=round(lo, 6),
            credibility_upper=round(hi, 6),
        ))
    return mults


def _build_persistency_multipliers(con: duckdb.DuckDBPyConnection,
                                    study_run_id: str) -> list[DecrementMultiplier]:
    """Build premium persistency multipliers for UL/VUL products.

    Uses a simple neutral multiplier (1.0) if no persistency data exists.
    """
    # For now, create a single aggregate persistency multiplier per UL product
    rows = con.execute("""
        SELECT product_code, gender, risk_class, duration_band,
               1.0 AS actual, 1.0 AS expected
        FROM gold_ae_results
        WHERE study_run_id = ?
          AND product_code IN ('UL', 'ULSG', 'IUL', 'VUL')
          AND gender IS NOT NULL
          AND risk_class IS NOT NULL
          AND duration_band IS NOT NULL
          AND illness_code IS NULL
        GROUP BY product_code, gender, risk_class, duration_band
        ORDER BY product_code, gender, risk_class, duration_band
    """, [study_run_id]).fetchall()

    mults = []
    seen = set()
    for prod, gen, rc, db_str, *_ in rows:
        key = (prod, gen, rc, db_str)
        if key in seen:
            continue
        seen.add(key)
        mults.append(DecrementMultiplier(
            product=prod,
            gender=gen,
            risk_class=rc,
            duration_band=_parse_duration_band(db_str),
            multiplier=1.0,
            credibility_z=0.0,
            credibility_lower=0.5,
            credibility_upper=1.5,
        ))
    return mults


def _insert_assumption_set_metadata(db_path: Path, aset: AssumptionSet) -> None:
    """Insert or replace the assumption set metadata row in gold_assumption_sets.

    The DELETE+INSERT is idempotent on ``assumption_set_id``. Several additive
    columns are NOT in the INSERT column list, so they are carried forward across
    a re-save: any existing values are read before the DELETE and re-applied after
    the INSERT. This keeps them sticky against a later plain save (e.g. a Stage-2
    edit). Preserved columns:
      - §D.4 AI provenance (``ai_proposed_value``/``ai_model_id``, Session 17,
        FR-3A-30) — an adopted AI proposal's provenance.
      - §G.4 version lineage / effective-dating (``parent_set_id``/
        ``effective_from``/``effective_to``, Session 24, FR-4-07/09) — written by
        the lineage engine, must survive a plain re-save.
    """
    # Additive columns preserved across the DELETE+INSERT (column name order
    # is the read/UPDATE order). Each is only handled when present in the table.
    _PRESERVED_COLS = [
        "ai_proposed_value", "ai_model_id",
        "parent_set_id", "effective_from", "effective_to",
    ]
    con = duckdb.connect(str(db_path))
    try:
        # Read any previously-recorded values for the preserved columns that
        # actually exist in this DB, so a re-save does not silently wipe them.
        existing_cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'gold_assumption_sets'"
            ).fetchall()
        }
        preserved = [c for c in _PRESERVED_COLS if c in existing_cols]
        prior_vals: dict[str, object] = {}
        if preserved:
            row = con.execute(
                f"SELECT {', '.join(preserved)} FROM gold_assumption_sets "
                "WHERE assumption_set_id = ?",
                [aset.id],
            ).fetchone()
            if row is not None:
                prior_vals = {col: row[i] for i, col in enumerate(preserved)}

        # Lock guard: a completed (APPROVED) set is immutable. A plain re-save must
        # never silently revert it to a non-terminal status (the Stage-2 editor forces
        # status=PROPOSED before saving), which would unlock it while leaving the stale
        # approved_by/approved_ts on the row. This mirrors the guard in
        # src/tev/workflow.py::transition_assumption_set_status and closes the same
        # "silently unlock an APPROVED set" hole via the save path (governance audit
        # 2026-07-04). Re-saving as APPROVED (idempotent) or SUPERSEDED is permitted.
        existing_status = con.execute(
            "SELECT status FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset.id],
        ).fetchone()
        if (
            existing_status is not None
            and existing_status[0] == "APPROVED"
            and aset.status.value not in ("APPROVED", "SUPERSEDED")
        ):
            from src.tev.workflow import LockedStatusTransition

            raise LockedStatusTransition(
                f"assumption set {aset.id!r} is APPROVED (locked) and cannot be "
                f"re-saved with status {aset.status.value!r}; re-open it via the "
                f"lineage 'new version' path to make further changes"
            )

        # Remove any existing row with the same ID (idempotent save)
        con.execute(
            "DELETE FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [aset.id]
        )
        con.execute("""
            INSERT INTO gold_assumption_sets (
                assumption_set_id, version, status, effective_date, author_id,
                basis, source_study_run_id, yaml_file_path, created_ts,
                rdr, earned_rate_ga, earned_rate_sa, tax_rate, expense_inflation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            aset.id,
            aset.version,
            aset.status.value,
            aset.effective_date,
            aset.author_id,
            aset.basis,
            aset.source_study_run_id,
            aset.yaml_file_path,
            datetime.utcnow(),
            aset.rdr,
            aset.earned_rate_ga,
            aset.earned_rate_sa,
            aset.tax_rate,
            aset.expense_inflation,
        ])

        # Re-apply any preserved column values that were previously recorded.
        to_restore = {c: v for c, v in prior_vals.items() if v is not None}
        if to_restore:
            set_clause = ", ".join(f"{c} = ?" for c in to_restore)
            con.execute(
                f"UPDATE gold_assumption_sets SET {set_clause} "
                "WHERE assumption_set_id = ?",
                [*to_restore.values(), aset.id],
            )
    finally:
        con.close()


def load_assumption_set(assumption_set_id: str, db_path: Path) -> AssumptionSet:
    """Load an AssumptionSet from gold_assumption_sets plus its YAML file.

    Args:
        assumption_set_id:  UUID of the assumption set to load.
        db_path:            Path to the DuckDB file.

    Returns:
        Fully populated AssumptionSet object.

    Raises:
        ValueError:    if the assumption_set_id is not found in the DB.
        FileNotFoundError: if the YAML file path stored in the DB does not exist.
    """
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT yaml_file_path, status FROM gold_assumption_sets "
            "WHERE assumption_set_id = ?",
            [assumption_set_id]
        ).fetchone()
    finally:
        con.close()

    if row is None:
        raise ValueError(f"Assumption set {assumption_set_id} not found in DB.")

    yaml_path = Path(row[0])
    if not yaml_path.exists():
        raise FileNotFoundError(f"YAML file not found: {yaml_path}")

    with open(yaml_path) as fh:
        data = yaml.safe_load(fh)

    aset = AssumptionSet.from_yaml_dict(data)
    aset.status = AssumptionSetStatus(row[1])  # DB is authoritative for status
    aset.yaml_file_path = str(yaml_path)
    return aset


def save_assumption_set(assumption_set: AssumptionSet, db_path: Path) -> str:
    """Persist an AssumptionSet: writes YAML and upserts the DB metadata row.

    If yaml_file_path is empty, derives the path from
    ``data/assumption_sets/{id}.yaml`` relative to db_path's parent.

    Args:
        assumption_set: The AssumptionSet to save.
        db_path:        Path to the DuckDB file.

    Returns:
        The assumption_set_id.
    """
    if not assumption_set.yaml_file_path:
        yaml_dir = db_path.parent.parent / "data" / "assumption_sets"
        yaml_path = yaml_dir / f"{assumption_set.id}.yaml"
    else:
        yaml_path = Path(assumption_set.yaml_file_path)

    assumption_set.save_yaml(yaml_path)
    _insert_assumption_set_metadata(db_path, assumption_set)
    return assumption_set.id


def get_multiplier(
    assumption_set: AssumptionSet,
    decrement_type: str,
    product_code: str,
    gender: str,
    risk_class: str,
    policy_year: int,
) -> float:
    """Look up the applicable multiplier for a given policy cell.

    Matches on product_code, gender, risk_class, and duration_band
    (where policy_year falls within [lower, upper]).

    For decrement_type='ci_incidence': product_code is treated as illness_code
    and policy_year as attained_age.

    Args:
        assumption_set: The AssumptionSet to search.
        decrement_type: One of 'mortality', 'lapse', 'surrender',
                        'ci_incidence', 'premium_persistency'.
        product_code:   Product code (or illness_code for CI).
        gender:         'M', 'F', or 'U'.
        risk_class:     Risk class string.
        policy_year:    Policy year (or attained_age for CI) for band lookup.

    Returns:
        Matching multiplier, or 1.0 if no cell matches (neutral assumption).
    """
    decrement_map = {
        "mortality": assumption_set.mortality_multipliers,
        "lapse": assumption_set.lapse_multipliers,
        "surrender": assumption_set.surrender_multipliers,
        "ci_incidence": assumption_set.ci_incidence_multipliers,
        "premium_persistency": assumption_set.premium_persistency,
    }

    mults = decrement_map.get(decrement_type, [])

    for m in mults:
        if m.matches(product_code, gender, risk_class, policy_year):
            return m.multiplier

    # Try gender-agnostic fallback
    for m in mults:
        if m.product == product_code and m.risk_class == risk_class \
                and m.duration_band[0] <= policy_year <= m.duration_band[1]:
            return m.multiplier

    # Final fallback: product + duration band only
    for m in mults:
        if m.product == product_code \
                and m.duration_band[0] <= policy_year <= m.duration_band[1]:
            return m.multiplier

    # DA-family fallback: "DA" matches any DA_* subtype entry
    if product_code == "DA":
        da_subtypes = {"DA_FIXED", "DA_FIA", "DA_VA"}
        for m in mults:
            if m.product in da_subtypes \
                    and m.duration_band[0] <= policy_year <= m.duration_band[1]:
                return m.multiplier

    return 1.0  # neutral — no matching multiplier found


# ---------------------------------------------------------------------------
# AI-proposal provenance (Phase 3a, Session 17; FR-3A-30 / Tech Spec §D.4)
# ---------------------------------------------------------------------------
# These helpers live in src/tev/ (NOT src/ai/) on purpose: recording the
# adopted-AI provenance is part of the existing *human* assumption-set edit
# path, which is permitted to write the Phase 2 gold_assumption_sets table.
# The AI layer never writes here (FR-3A-09). Provenance is captured at the
# set level (ai_proposed_value, ai_model_id); per-cell factors stay in the
# assumption YAML (§D.4 note).


def record_ai_provenance(
    db_path: Path,
    assumption_set_id: str,
    ai_proposed_value: float,
    ai_model_id: str,
) -> None:
    """Record the AI provenance of an adopted proposal on an assumption set.

    Writes the §D.4 columns ``ai_proposed_value`` and ``ai_model_id`` onto the
    existing ``gold_assumption_sets`` row via a parameterized UPDATE. This is the
    sanctioned human-edit path (FR-3A-30): the actuary adopts an AI-proposed
    factor in the assumption-set editor, and both the proposed value and the
    model that produced it are stamped onto the set for audit.

    Args:
        db_path:            Path to the DuckDB file.
        assumption_set_id:  The assumption set being edited.
        ai_proposed_value:  The GLM-proposed factor that was adopted.
        ai_model_id:        gold_ai_model_registry.model_id that produced it.

    Raises:
        ValueError: if the assumption set does not exist.
    """
    con = duckdb.connect(str(db_path))
    try:
        exists = con.execute(
            "SELECT 1 FROM gold_assumption_sets WHERE assumption_set_id = ?",
            [assumption_set_id],
        ).fetchone()
        if exists is None:
            raise ValueError(f"Assumption set {assumption_set_id} not found in DB.")
        con.execute(
            "UPDATE gold_assumption_sets "
            "SET ai_proposed_value = ?, ai_model_id = ? "
            "WHERE assumption_set_id = ?",
            [ai_proposed_value, ai_model_id, assumption_set_id],
        )
    finally:
        con.close()


def find_ai_proposal_for_set(
    db_path: Path,
    source_study_run_id: str,
) -> Optional[dict]:
    """Return the latest registered GLM model for a study run, if any.

    Used by the Stage 2 editor to know whether an AI proposal exists for the
    assumption set's source experience-study run (so it can offer the adopt
    affordance). Reads only ``gold_ai_model_registry`` (read-only). Returns the
    most recently fitted *converged* GLM row as a dict, or ``None`` when no AI
    model has been fitted for the run.

    Args:
        db_path:             Path to the DuckDB file.
        source_study_run_id: The study run the assumption set was built from.

    Returns:
        ``{"model_id", "decrement", "product_code", "fit_ts"}`` or ``None``.
    """
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT model_id, decrement, product_code, fit_ts "
            "FROM gold_ai_model_registry "
            "WHERE run_id = ? AND model_type = 'GLM' AND converged = TRUE "
            "ORDER BY fit_ts DESC LIMIT 1",
            [source_study_run_id],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {
        "model_id": row[0],
        "decrement": row[1],
        "product_code": row[2],
        "fit_ts": row[3],
    }
