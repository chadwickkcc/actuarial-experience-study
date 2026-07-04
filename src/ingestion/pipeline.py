"""ETL pipeline: load raw product CSVs into Bronze, conform to Silver, build policy events."""

import hashlib
import logging
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
import yaml

from src.utils.types import ETLResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table routing
# ---------------------------------------------------------------------------

_BRONZE_TABLE_MAP: dict[str, str] = {
    "TERM":     "bronze_term_policies",
    "WL":       "bronze_wl_policies",
    "UL":       "bronze_ul_policies",
    "ULSG":     "bronze_ul_policies",
    "IUL":      "bronze_ul_policies",
    "VUL":      "bronze_vul_policies",
    "DA":       "bronze_annuity_contracts",
    "DA_FIXED": "bronze_annuity_contracts",
    "DA_FIA":   "bronze_annuity_contracts",
    "DA_VA":    "bronze_annuity_contracts",
}

_SILVER_TABLE_MAP: dict[str, str] = {
    "TERM":     "silver_term_policies",
    "WL":       "silver_wl_policies",
    "UL":       "silver_ul_policies",
    "ULSG":     "silver_ul_policies",
    "IUL":      "silver_ul_policies",
    "VUL":      "silver_vul_policies",
    "DA":       "silver_annuity_contracts",
    "DA_FIXED": "silver_annuity_contracts",
    "DA_FIA":   "silver_annuity_contracts",
    "DA_VA":    "silver_annuity_contracts",
}

# Map canonical status_code → silver_policy_events event_type
_STATUS_TO_EVENT_TYPE: dict[str, str] = {
    "LAPSE":      "LAPSE",
    "DEATH":      "DEATH",
    "CONV":       "CONVERSION",
    "CI_CLAIM":   "CI_CLAIM",
    "EXPIRY":     "EXPIRY",
    "SURRENDER":  "SURRENDER",
}

# Policy ID column name varies by product family
_POLICY_ID_COL: dict[str, str] = {
    "DA":       "contract_id",
    "DA_FIXED": "contract_id",
    "DA_FIA":   "contract_id",
    "DA_VA":    "contract_id",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_mapping_config(mapping_config_path: Path) -> dict:
    """Load and validate a product YAML mapping config.

    Expected YAML structure::

        source_product: TERM
        source_table:   bronze_term_policies
        target_table:   silver_term_policies
        field_mappings:
          - source_field: policy_id
            target_field: policy_id
            target_type:  VARCHAR
          - source_field: issue_date
            target_field: issue_date
            target_type:  DATE
            date_format:  "%Y-%m-%d"
        code_translations:
          status_code:
            "CONVERSION": "CONV"

    Returns:
        Parsed mapping config as dict.

    Raises:
        FileNotFoundError: if the path does not exist.
        ValueError: if required top-level keys are missing.
    """
    if not mapping_config_path.exists():
        raise FileNotFoundError(f"Mapping config not found: {mapping_config_path}")

    with mapping_config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    required_keys = {"field_mappings", "target_table"}
    missing = required_keys - set(config.keys())
    if missing:
        raise ValueError(f"Mapping config is missing required keys: {missing}")

    if not isinstance(config["field_mappings"], list) or not config["field_mappings"]:
        raise ValueError("field_mappings must be a non-empty list")

    return config


def run_etl_pipeline(
    product_code: str,
    source_path: Path,
    mapping_config_path: Path,
    db_path: Path,
    run_id: str,
) -> ETLResult:
    """Load a product's raw CSV into the Bronze layer, then conform to Silver.

    Steps:

    1. Load source_path (CSV) into bronze_{product}_policies with all metadata
       columns (_load_ts, _source_file, _product_code, _row_hash, _bronze_id).
    2. Apply the YAML mapping at mapping_config_path:
       - Rename columns to canonical names
       - Cast types (VARCHAR → DATE, DOUBLE, BOOLEAN, INTEGER)
       - Translate code lists
    3. Insert conformed records into silver_{product}_policies.
    4. Build silver_policy_events from the conformed records.
    5. Return ETLResult.

    Args:
        product_code:         One of ProductCode values (e.g. "TERM", "WL").
        source_path:          Path to the CSV file.
        mapping_config_path:  Path to the product YAML mapping config.
        db_path:              Path to the DuckDB file.
        run_id:               UUID string for this ETL run.

    Returns:
        ETLResult with counts and success flag.

    Raises:
        ValueError: if product_code is not recognised.
        FileNotFoundError: if source_path or mapping_config_path do not exist.
    """
    t0 = time.monotonic()
    warnings: list[str] = []
    error_count = 0

    if product_code not in _BRONZE_TABLE_MAP:
        raise ValueError(
            f"Unknown product_code '{product_code}'. "
            f"Valid codes: {sorted(_BRONZE_TABLE_MAP)}"
        )
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    config = load_mapping_config(mapping_config_path)

    # -----------------------------------------------------------------------
    # 1. Read raw CSV — all columns as strings to preserve original values
    # -----------------------------------------------------------------------
    raw_df = pd.read_csv(
        source_path,
        dtype=str,
        keep_default_na=False,
    )
    # Replace empty strings with None so they become SQL NULL
    raw_df = raw_df.replace("", None)

    records_ingested = len(raw_df)

    # -----------------------------------------------------------------------
    # 2. Detect and deduplicate duplicate policy IDs (warn, keep first)
    # -----------------------------------------------------------------------
    pid_col = _POLICY_ID_COL.get(product_code, "policy_id")
    if pid_col in raw_df.columns:
        dups = raw_df[raw_df.duplicated(subset=[pid_col], keep=False)]
        if not dups.empty:
            dup_ids = dups[pid_col].unique().tolist()
            msg = (
                f"Duplicate {pid_col} values found ({len(dup_ids)} IDs, "
                f"{len(dups)} rows total). Keeping first occurrence."
            )
            logger.warning(msg)
            warnings.append(msg)
            raw_df = raw_df.drop_duplicates(subset=[pid_col], keep="first")
            error_count += len(dups) - len(dup_ids)

    # -----------------------------------------------------------------------
    # 3. Build bronze DataFrame and insert into DuckDB
    # -----------------------------------------------------------------------
    load_ts = datetime.utcnow()
    bronze_df = _build_bronze_df(raw_df, source_path, product_code, load_ts)

    con = duckdb.connect(str(db_path))
    try:
        _insert_bronze(con, product_code, bronze_df)

        # -------------------------------------------------------------------
        # 4. Conform raw CSV to silver schema
        # -------------------------------------------------------------------
        silver_df = _conform_to_silver(raw_df, bronze_df, config, run_id, load_ts)
        records_conformed = len(silver_df)

        _insert_silver(con, config["target_table"], silver_df)

        # -------------------------------------------------------------------
        # 5. Build and insert policy events
        # -------------------------------------------------------------------
        events_df = _build_policy_events(silver_df, product_code, run_id)
        if not events_df.empty:
            _insert_events(con, events_df)

        con.commit()
    finally:
        con.close()

    duration = time.monotonic() - t0
    return ETLResult(
        run_id=run_id,
        product_code=product_code,
        records_ingested=records_ingested,
        records_conformed=records_conformed,
        error_count=error_count,
        warnings=warnings,
        success=True,
        duration_sec=round(duration, 3),
    )


# ---------------------------------------------------------------------------
# Bronze helpers
# ---------------------------------------------------------------------------

def _build_bronze_df(
    raw_df: pd.DataFrame,
    source_path: Path,
    product_code: str,
    load_ts: datetime,
) -> pd.DataFrame:
    """Return a DataFrame ready for insertion into the bronze table.

    Adds raw_ prefix to all source columns and appends the five metadata columns.
    """
    bronze = raw_df.copy()
    # Rename all raw columns with raw_ prefix
    bronze.columns = [f"raw_{c}" for c in bronze.columns]

    # Compute row hash (SHA-256 of the original raw column values)
    def _row_hash(row: pd.Series) -> str:
        combined = "|".join(str(v) if v is not None else "" for v in row)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    bronze["_row_hash"] = raw_df.apply(_row_hash, axis=1)
    bronze["_load_ts"] = load_ts
    bronze["_source_file"] = str(source_path)
    bronze["_product_code"] = product_code
    bronze["_bronze_id"] = [str(uuid.uuid4()) for _ in range(len(bronze))]

    return bronze


def _insert_bronze(con: duckdb.DuckDBPyConnection, product_code: str, bronze_df: pd.DataFrame) -> None:
    """Insert bronze_df into the appropriate bronze table.

    Uses information_schema to get the exact column order, then register + INSERT SELECT.
    """
    table = _BRONZE_TABLE_MAP[product_code]

    cols_result = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    table_cols = [r[0] for r in cols_result]

    # Only insert columns that exist in both the DataFrame and the table
    df_cols = set(bronze_df.columns)
    insert_cols = [c for c in table_cols if c in df_cols]
    df_to_insert = bronze_df[insert_cols]

    con.register("_bronze_staging", df_to_insert)
    col_list = ", ".join(f'"{c}"' for c in insert_cols)
    con.execute(f'INSERT INTO {table} ({col_list}) SELECT {col_list} FROM _bronze_staging')
    con.unregister("_bronze_staging")


# ---------------------------------------------------------------------------
# Silver conformance helpers
# ---------------------------------------------------------------------------

def _cast_column(
    series: pd.Series,
    target_type: str,
    date_format: Optional[str] = None,
) -> pd.Series:
    """Cast a pandas Series to the requested DuckDB target type.

    Args:
        series:      Input series (typically dtype=object/str).
        target_type: One of DATE, DOUBLE, INTEGER, BOOLEAN, VARCHAR.
        date_format: strftime format string, used only when target_type=DATE.

    Returns:
        A new Series with the requested Python types (None for NULLs).
    """
    if target_type == "DATE":
        parsed = pd.to_datetime(series, format=date_format, errors="coerce")
        # Convert to Python date, preserving NaT as None
        return parsed.apply(lambda v: v.date() if not pd.isna(v) else None)

    if target_type == "DOUBLE":
        return pd.to_numeric(series, errors="coerce")

    if target_type == "INTEGER":
        numeric = pd.to_numeric(series, errors="coerce")
        # Use Int64 (nullable integer) so NaN stays as pd.NA
        return numeric.astype("Int64")

    if target_type == "BOOLEAN":
        bool_map = {
            "true": True, "True": True, "TRUE": True, "1": True, "yes": True,
            "false": False, "False": False, "FALSE": False, "0": False, "no": False,
        }
        return series.map(lambda v: bool_map.get(v, None) if v is not None else None)

    # VARCHAR — pass through as-is
    return series


def _conform_to_silver(
    raw_df: pd.DataFrame,
    bronze_df: pd.DataFrame,
    config: dict,
    etl_run_id: str,
    load_ts: datetime,
) -> pd.DataFrame:
    """Apply field_mappings and code_translations; return conformed silver DataFrame.

    Builds the silver DataFrame column-by-column from the raw source columns,
    applying type casts and code list translations as specified in the YAML config.

    Args:
        raw_df:     Raw string DataFrame (original CSV data).
        bronze_df:  Bronze DataFrame (used for bronze_id linkage).
        config:     Parsed YAML mapping config.
        etl_run_id: UUID of the current ETL run.
        load_ts:    Ingestion timestamp.

    Returns:
        DataFrame matching the silver table schema (excluding columns not in mapping).
    """
    code_translations: dict[str, dict] = config.get("code_translations", {})
    silver: dict[str, pd.Series] = {}
    seen_targets: set[str] = set()

    for mapping in config["field_mappings"]:
        src = mapping["source_field"]
        tgt = mapping["target_field"]
        ttype = mapping.get("target_type", "VARCHAR")
        date_fmt = mapping.get("date_format")

        # Skip duplicate target mappings (the YAML has a ci_rider_flag duplicate —
        # the first VARCHAR entry was a comment artefact; the BOOLEAN entry is authoritative)
        if tgt in seen_targets:
            # Overwrite with the latest mapping (BOOLEAN wins over VARCHAR for ci_rider_flag)
            pass
        seen_targets.add(tgt)

        if src not in raw_df.columns:
            logger.warning("Source field '%s' not found in CSV; skipping.", src)
            continue

        casted = _cast_column(raw_df[src], ttype, date_fmt)

        # Apply code-list translation if configured for this target field
        if tgt in code_translations:
            trans = code_translations[tgt]
            casted = casted.map(lambda v, t=trans: t.get(v, v) if v is not None else None)

        silver[tgt] = casted

    silver_df = pd.DataFrame(silver)

    # Append ETL metadata columns
    silver_df["_load_ts"] = load_ts
    silver_df["_source_bronze_id"] = bronze_df["_bronze_id"].values
    silver_df["_etl_run_id"] = etl_run_id

    return silver_df


def _insert_silver(con: duckdb.DuckDBPyConnection, table: str, silver_df: pd.DataFrame) -> None:
    """Insert silver_df into the silver table using register + INSERT SELECT.

    Args:
        con:       Active DuckDB connection.
        table:     Target silver table name.
        silver_df: Conformed silver DataFrame.
    """
    cols_result = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    table_cols = [r[0] for r in cols_result]

    df_cols = set(silver_df.columns)
    insert_cols = [c for c in table_cols if c in df_cols]
    df_to_insert = silver_df[insert_cols]

    con.register("_silver_staging", df_to_insert)
    col_list = ", ".join(f'"{c}"' for c in insert_cols)
    con.execute(f'INSERT INTO {table} ({col_list}) SELECT {col_list} FROM _silver_staging ON CONFLICT DO NOTHING')
    con.unregister("_silver_staging")


# ---------------------------------------------------------------------------
# Policy events helpers
# ---------------------------------------------------------------------------

def _compute_policy_year(issue_date: date, event_date: date) -> int:
    """Return 1-based policy year for an event relative to issue_date.

    Policy year 1 runs from issue_date to the day before the first anniversary.

    Args:
        issue_date:  Date the policy was issued.
        event_date:  Date of the event.

    Returns:
        Policy year as a positive integer (minimum 1).
    """
    years = event_date.year - issue_date.year
    if (event_date.month, event_date.day) < (issue_date.month, issue_date.day):
        years -= 1
    return max(1, years + 1)


def _build_policy_events(
    silver_df: pd.DataFrame,
    product_code: str,
    etl_run_id: str,
) -> pd.DataFrame:
    """Construct silver_policy_events records from the conformed silver DataFrame.

    Generates:
    - One ISSUE event per policy (event_date = issue_date, policy_year = 1).
    - One termination event per non-IF policy (LAPSE, DEATH, CONVERSION, CI_CLAIM, EXPIRY).

    Args:
        silver_df:    Conformed silver DataFrame for the product.
        product_code: Product code string.
        etl_run_id:   UUID of the current ETL run.

    Returns:
        DataFrame of policy event rows ready for silver_policy_events.
    """
    pid_col = _POLICY_ID_COL.get(product_code, "policy_id")
    rows: list[dict] = []

    for _, pol in silver_df.iterrows():
        policy_id = pol.get(pid_col)
        issue_date = pol.get("issue_date")
        face_amount = pol.get("face_amount") or pol.get("specified_amount") or pol.get("account_value")
        status_code = pol.get("status_code")
        termination_date = pol.get("termination_date")

        # ISSUE event
        rows.append({
            "event_id":          str(uuid.uuid4()),
            "policy_id":         policy_id,
            "product_code":      product_code,
            "event_type":        "ISSUE",
            "event_date":        issue_date,
            "policy_year":       1,
            "face_amount_before": None,
            "face_amount_after": face_amount,
            "account_value":     pol.get("account_value_eom"),
            "claim_amount":      None,
            "illness_code":      None,
            "notes":             None,
            "_etl_run_id":       etl_run_id,
        })

        # Termination event
        if status_code and status_code != "IF" and termination_date is not None:
            event_type = _STATUS_TO_EVENT_TYPE.get(status_code)
            if event_type is None:
                logger.warning(
                    "Unknown status_code '%s' for policy %s; no termination event created.",
                    status_code, policy_id
                )
                continue

            # Determine claim amount
            claim_amount: Optional[float] = None
            illness_code: Optional[str] = None

            if event_type == "DEATH":
                claim_amount = face_amount
            elif event_type == "CI_CLAIM":
                claim_amount = pol.get("ci_rider_sum_assured")
                illness_code = pol.get("illness_code")

            policy_year = 1
            if issue_date is not None and termination_date is not None:
                policy_year = _compute_policy_year(issue_date, termination_date)

            rows.append({
                "event_id":          str(uuid.uuid4()),
                "policy_id":         policy_id,
                "product_code":      product_code,
                "event_type":        event_type,
                "event_date":        termination_date,
                "policy_year":       policy_year,
                "face_amount_before": face_amount,
                "face_amount_after": 0.0 if event_type in {"DEATH", "CI_CLAIM", "LAPSE", "EXPIRY"} else face_amount,
                "account_value":     pol.get("account_value_eom"),
                "claim_amount":      claim_amount,
                "illness_code":      illness_code,
                "notes":             None,
                "_etl_run_id":       etl_run_id,
            })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def _insert_events(con: duckdb.DuckDBPyConnection, events_df: pd.DataFrame) -> None:
    """Insert events_df into silver_policy_events.

    Args:
        con:       Active DuckDB connection.
        events_df: DataFrame of event rows.
    """
    cols_result = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'silver_policy_events' ORDER BY ordinal_position"
    ).fetchall()
    table_cols = [r[0] for r in cols_result]

    df_cols = set(events_df.columns)
    insert_cols = [c for c in table_cols if c in df_cols]
    df_to_insert = events_df[insert_cols]

    con.register("_events_staging", df_to_insert)
    col_list = ", ".join(f'"{c}"' for c in insert_cols)
    con.execute(
        f'INSERT INTO silver_policy_events ({col_list}) SELECT {col_list} FROM _events_staging'
    )
    con.unregister("_events_staging")
