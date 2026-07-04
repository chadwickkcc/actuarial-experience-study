"""CLI runner for the ETL pipeline.

Usage::

    python src/ingestion/run_etl.py \\
        --product TERM \\
        --source  synthetic_data/output/term_policies.csv \\
        --mapping config/products/term.yaml \\
        --db      data/experience_study.duckdb
"""

import argparse
import logging
import uuid
from pathlib import Path

import duckdb

from src.ingestion.pipeline import run_etl_pipeline
from src.utils.db_init import init_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _row_count(db_path: Path, table: str) -> int:
    """Return the number of rows in a DuckDB table."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        result = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return result[0] if result else 0
    finally:
        con.close()


def main() -> None:
    """Parse CLI arguments and run the ETL pipeline."""
    parser = argparse.ArgumentParser(
        description="Load a product CSV into Bronze/Silver layers."
    )
    parser.add_argument(
        "--product", required=True,
        help="Product code, e.g. TERM, WL, UL",
    )
    parser.add_argument(
        "--source", required=True,
        help="Path to the source CSV file",
    )
    parser.add_argument(
        "--mapping", required=True,
        help="Path to the product YAML mapping config",
    )
    parser.add_argument(
        "--db",
        default="data/experience_study.duckdb",
        help="Path to the DuckDB file (default: data/experience_study.duckdb)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    source_path = Path(args.source)
    mapping_path = Path(args.mapping)
    product_code = args.product.upper()

    # Ensure DB is initialised
    logger.info("Initialising database at %s", db_path)
    init_database(db_path)

    run_id = str(uuid.uuid4())
    logger.info(
        "Starting ETL — product=%s  source=%s  run_id=%s",
        product_code, source_path, run_id,
    )

    result = run_etl_pipeline(
        product_code=product_code,
        source_path=source_path,
        mapping_config_path=mapping_path,
        db_path=db_path,
        run_id=run_id,
    )

    # Print ETLResult summary
    print("\n" + "=" * 60)
    print(f"  ETL Result — {product_code}")
    print("=" * 60)
    print(f"  run_id           : {result.run_id}")
    print(f"  records_ingested : {result.records_ingested}")
    print(f"  records_conformed: {result.records_conformed}")
    print(f"  error_count      : {result.error_count}")
    print(f"  success          : {result.success}")
    print(f"  duration_sec     : {result.duration_sec:.3f}")

    if result.warnings:
        print("\n  Warnings:")
        for w in result.warnings:
            print(f"    - {w}")

    # Row count verification
    bronze_map = {
        "TERM": "bronze_term_policies",
        "WL":   "bronze_wl_policies",
        "UL":   "bronze_ul_policies",
        "ULSG": "bronze_ul_policies",
        "VUL":  "bronze_vul_policies",
        "DA":   "bronze_annuity_contracts",
    }
    silver_map = {
        "TERM": "silver_term_policies",
        "WL":   "silver_wl_policies",
        "UL":   "silver_ul_policies",
        "ULSG": "silver_ul_policies",
        "VUL":  "silver_vul_policies",
        "DA":   "silver_annuity_contracts",
    }

    bronze_table = bronze_map.get(product_code)
    silver_table = silver_map.get(product_code)

    print("\n  Row counts:")
    if bronze_table:
        print(f"    {bronze_table:40s} : {_row_count(db_path, bronze_table):>6}")
    if silver_table:
        print(f"    {silver_table:40s} : {_row_count(db_path, silver_table):>6}")
    print(f"    {'silver_policy_events':40s} : {_row_count(db_path, 'silver_policy_events'):>6}")
    print("=" * 60)


if __name__ == "__main__":
    main()
