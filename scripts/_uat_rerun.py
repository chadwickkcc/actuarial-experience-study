"""Headless full-study re-run (UAT remediation).

Replicates the orchestration in ui/views/01_study_setup.py without Streamlit, so
the pipeline can be re-run after the DA benefit_base fix. Inserts a new study run
into gold_study_runs and runs ETL -> DQ -> exposure per product, then A/E for all.

Usage:  python scripts/_uat_rerun.py
"""
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

from ui.config import (
    DB_PATH, DEFAULT_MORTALITY_TABLE, DEFAULT_LAPSE_TABLE, DEFAULT_CI_TABLE,
    TERM_SOURCE_CSV, TERM_MAPPING_YAML, WL_SOURCE_CSV, WL_MAPPING_YAML,
    UL_SOURCE_CSV, UL_MAPPING_YAML, VUL_SOURCE_CSV, VUL_MAPPING_YAML,
    DA_SOURCE_CSV, DA_MAPPING_YAML,
)
from src.calculation.ae_engine import calculate_ae
from src.data_quality.runner import DQCriticalFailure, run_dq_checks
from src.exposure.engine import build_exposure_file
from src.ingestion.pipeline import run_etl_pipeline
from src.utils.types import CredibilityMethod, ExposureMethod, StudyConfig

PRODUCTS = ["TERM", "WL", "UL", "ULSG", "VUL", "DA"]
ETL_CFG = {
    "TERM": (TERM_SOURCE_CSV, TERM_MAPPING_YAML),
    "WL":   (WL_SOURCE_CSV,   WL_MAPPING_YAML),
    "UL":   (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "ULSG": (UL_SOURCE_CSV,   UL_MAPPING_YAML),
    "VUL":  (VUL_SOURCE_CSV,  VUL_MAPPING_YAML),
    "DA":   (DA_SOURCE_CSV,   DA_MAPPING_YAML),
}
START, END = date(2016, 1, 1), date(2023, 12, 31)


def main() -> None:
    run_id = str(uuid.uuid4())
    print(f"NEW_RUN_ID={run_id}")
    cfg = StudyConfig(
        study_start_date=START,
        study_end_date=END,
        product_codes=PRODUCTS,
        exposure_method=ExposureMethod("ANNUAL"),
        mortality_table_path=DEFAULT_MORTALITY_TABLE,
        lapse_table_path=DEFAULT_LAPSE_TABLE,
        ci_table_path=DEFAULT_CI_TABLE,
        credibility_method=CredibilityMethod("LF"),
    )

    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        INSERT INTO gold_study_runs (
            run_id, run_ts, product_codes, study_start_date, study_end_date,
            exposure_method, mortality_table, lapse_table, ci_table,
            credibility_method, data_snapshot_hash, config_hash, code_version,
            run_duration_sec, status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'RUNNING', NULL)
        """,
        [run_id, datetime.utcnow(), json.dumps(PRODUCTS), str(START), str(END),
         "ANNUAL", Path(DEFAULT_MORTALITY_TABLE).name, Path(DEFAULT_LAPSE_TABLE).name,
         Path(DEFAULT_CI_TABLE).name, "LF", "synthetic-seed42", "phase1a-v1", "1.0.0"],
    )
    con.close()

    t0 = datetime.utcnow()
    try:
        for product in PRODUCTS:
            src_csv, mapping_yaml = ETL_CFG[product]
            etl = run_etl_pipeline(
                product_code=product, source_path=Path(src_csv),
                mapping_config_path=Path(mapping_yaml), db_path=DB_PATH, run_id=run_id,
            )
            if not etl.success:
                raise RuntimeError(f"ETL failed for {product}: {etl.warnings}")
            dq = run_dq_checks(product_code=product, db_path=DB_PATH,
                               study_run_id=run_id, halt_on_critical=True)
            print(f"[{product}] ETL {etl.records_ingested} ingested | "
                  f"DQ {dq.dq_score_pct:.1f}% quarantined={dq.records_quarantined}")
            exp = build_exposure_file(product_code=product, db_path=DB_PATH,
                                      study_config=cfg, study_run_id=run_id)
            print(f"[{product}] Exposure segments={exp.total_segments} "
                  f"recon={'PASS' if exp.recon_passes else 'FAIL'}")

        ae = calculate_ae(product_codes=PRODUCTS, db_path=DB_PATH,
                          study_config=cfg, study_run_id=run_id)
        print(f"A/E complete deaths={ae.total_deaths} ae_count={ae.total_ae_count:.3f}")

        dur = (datetime.utcnow() - t0).total_seconds()
        con = duckdb.connect(str(DB_PATH))
        con.execute("UPDATE gold_study_runs SET status='COMPLETE', run_duration_sec=? WHERE run_id=?",
                    [dur, run_id])
        con.close()
        print(f"STATUS=COMPLETE duration={dur:.1f}s RUN_ID={run_id}")
    except Exception as exc:
        con = duckdb.connect(str(DB_PATH))
        con.execute("UPDATE gold_study_runs SET status='FAILED', error_message=? WHERE run_id=?",
                    [str(exc), run_id])
        con.close()
        print(f"STATUS=FAILED error={exc}")
        raise


if __name__ == "__main__":
    main()
