"""Headless AI model fit (UAT rebuild).

Replicates the "Fit AI models" action on the Assumption Comparison page
(ui/views/15_assumption_comparison.py) without Streamlit: for the latest COMPLETE
study run it fits the GLM proposal + GBM challenge (+ SHAP) for every
product x modelled-decrement, registering each to gold_ai_model_registry and
materialising the published factors to gold_ai_proposed_factors. Sub-threshold /
non-converging combos surface the "No AI proposal available" guardrail and are skipped.

Usage:  .venv/bin/python scripts/_uat_ai_fit.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

from ui.config import DB_PATH
from ui.ai_comparison_logic import fit_models
from src.utils.types import DecrementType

PRODUCTS = ["TERM", "WL", "UL", "ULSG", "VUL", "DA"]
# SURRENDER is experience/memo-only (no GLM/GBM), so it is intentionally excluded.
DECREMENTS = [DecrementType.MORTALITY, DecrementType.LAPSE, DecrementType.CI_INCIDENCE]


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    row = con.execute(
        "SELECT run_id FROM gold_study_runs WHERE status = 'COMPLETE' "
        "ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit("No COMPLETE study run — run scripts/_uat_rerun.py first.")
    run_id = row[0]
    print(f"study_run_id={run_id}")

    fitted, skipped = 0, 0
    for product in PRODUCTS:
        for dec in DECREMENTS:
            res = fit_models(DB_PATH, run_id, dec, product, register=True)
            glm = res.get("glm")
            if glm is not None and getattr(glm, "converged", False):
                n = len(glm.factors)
                gbm = res.get("gbm")
                ng = len(gbm.factors) if gbm is not None else 0
                fitted += 1
                print(f"[{product:5s} {dec.value:12s}] GLM factors={n} GBM factors={ng} "
                      f"shap={'yes' if res.get('shap_json_path') else 'no'}")
            else:
                skipped += 1
                reason = "; ".join(res.get("reasons", {}).values()) or "no proposal"
                print(f"[{product:5s} {dec.value:12s}] SKIP — {reason}")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    reg = con.execute("SELECT COUNT(*) FROM gold_ai_model_registry").fetchone()[0]
    prop = con.execute("SELECT COUNT(*) FROM gold_ai_proposed_factors").fetchone()[0]
    con.close()
    print(f"\nDONE: fitted={fitted} skipped={skipped} | "
          f"gold_ai_model_registry rows={reg} gold_ai_proposed_factors rows={prop}")


if __name__ == "__main__":
    main()
