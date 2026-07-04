"""Headless TEV baseline + sensitivity grid (UAT B5 investigation).

Mirrors ui/views/20_tev_stage1 + 22_tev_stage3 without Streamlit so the TEV directional
integration tests have a baseline to run against, and so we can verify SENS-01/SENS-02
directionality directly.

Usage:  python scripts/_uat_tev_baseline.py
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

from ui.config import DB_PATH
from src.tev.assumption_set import create_assumption_set_from_ae_run
from src.tev.model_points import build_model_points
from src.tev.tev_core import run_tev
from src.tev.sensitivities import run_sensitivity_grid, SENSITIVITY_DEFINITIONS

TEV_CONFIG = ROOT / "config" / "tev_config.yaml"
YAML_DIR = ROOT / "reports"


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    study_run_id, products_json = con.execute(
        "SELECT run_id, product_codes FROM gold_study_runs "
        "WHERE status = 'COMPLETE' ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    products = json.loads(products_json)
    print(f"study_run_id={study_run_id} products={products}")

    YAML_DIR.mkdir(parents=True, exist_ok=True)
    aset = create_assumption_set_from_ae_run(study_run_id, "ACTUARY_1", DB_PATH, TEV_CONFIG, YAML_DIR)
    print(f"assumption_set_id={aset.id}")

    mp_run = str(uuid.uuid4())
    for pc in products:
        r = build_model_points(pc, DB_PATH, study_run_id, mp_run, aset)
        print(f"[{pc}] model_points={r.model_point_count}")

    baseline = run_tev(DB_PATH, aset.id)
    print(f"baseline tev_run_id={baseline.tev_run_id} total_tev={baseline.total_tev:,.0f}")

    grid = run_sensitivity_grid(DB_PATH, aset.id, baseline.tev_run_id)
    df = grid.impact_matrix_df
    print("\n== directional check (TOTAL row) ==")
    for s in ["SENS-01", "SENS-02", "SENS-03", "SENS-04", "SENS-08", "SENS-09", "SENS-10", "SENS-11"]:
        if s in df.columns:
            print(f"  {s} {SENSITIVITY_DEFINITIONS[s]['description']:<18} => {float(df.loc['TOTAL', s]):>16,.2f}")
    d1, d2 = float(df.loc["TOTAL", "SENS-01"]), float(df.loc["TOTAL", "SENS-02"])
    print(f"\nSENS-01 * SENS-02 < 0 (opposite directions)? {(d1 * d2) < 0}")
    print(f"TERM SENS-01 (expect <0): {float(df.loc['TERM','SENS-01']):,.2f}")
    print(f"ULSG SENS-01 (expect >0): {float(df.loc['ULSG','SENS-01']):,.2f}")


if __name__ == "__main__":
    main()
