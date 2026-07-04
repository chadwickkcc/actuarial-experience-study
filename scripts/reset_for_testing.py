"""Reset Gold, Silver, and Bronze tables for a clean test run.

Run this INSIDE the project venv (Python 3.12). The system `python3` no longer
has DuckDB or the project deps installed, so either activate the venv first
(`source .venv/bin/activate`) or invoke as `.venv/bin/python scripts/reset_for_testing.py`.

Clears by default:
  - All Bronze tables (raw ingested CSV loads)
  - All Silver tables
  - All Gold tables, INCLUDING the Phase 3 AI Gold tables (model registry, audit
    log, eval results)
A plain run therefore returns the database to an empty-schema state so old runs do
not pile up across reset/Run-Study cycles. Bronze is rebuilt from the source CSVs
under data/raw/ on the next "Run Study", so clearing it loses nothing recoverable.

Preserves:
  - Bronze tables                                [only if --keep-bronze is passed]
  - AI model artifacts (data/ai_models/* — the .pkl/.json/SHAP payloads on disk)
                                                 [unless --include-ai-models is passed]
  - Phase 4 governance logs (gold_governance_signoffs, gold_ae_governance_events)
                                                 [unless --include-governance is passed]
  - Login accounts (gold_users) — ALWAYS preserved; the app also re-seeds them from
    config/governance_config.yaml on start, so a reset never locks you out
  - Source CSVs (data/raw/) and reference Parquet files — never in the DB, never touched
  - Database schema (tables remain; only rows are deleted)

After running this script, use "Run Study" in the Study Setup page to repopulate Bronze,
Silver, and Gold. Note that "Run Study" does NOT refit AI models: the AI tables stay empty
until you next use the AI features (Fit AI models, AI Analyst, eval harness), which is the
desired clean state for a fresh end-to-end test.

Notes on disk usage:
  - DuckDB DELETE removes rows but does not shrink the .duckdb file on disk; this applies to
    every table, AI Gold tables included.
  - The on-disk model artifacts under data/ai_models/ live OUTSIDE the database and are never
    overwritten (each fit gets a unique model_id), so they grow indefinitely. A normal reset
    empties the registry that indexes them but leaves the files (harmless orphans); pass
    --include-ai-models to delete the files too.
  - For a fully pristine, minimal database file, delete the .duckdb and re-create it
    (`python -m src.utils.db_init`) then Run Study, instead of using this script. That
    rebuilds an empty schema (AI tables included) but still leaves data/ai_models/ on disk,
    so combine it with `rm -rf data/ai_models/*` if you also want the artifacts gone.

Usage:
    python scripts/reset_for_testing.py                        # clears Bronze + Silver + Gold (incl. AI)
    python scripts/reset_for_testing.py --keep-bronze          # preserve Bronze raw loads
    python scripts/reset_for_testing.py --include-ai-models    # also delete data/ai_models/* artifacts
    python scripts/reset_for_testing.py --include-governance   # also clear Phase 4 governance logs (users kept)
    python scripts/reset_for_testing.py --dry-run
    python scripts/reset_for_testing.py --db path/to/other.duckdb
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "experience_study.duckdb"

SILVER_TABLES = [
    "silver_term_policies",
    "silver_wl_policies",
    "silver_ul_policies",
    "silver_vul_policies",
    "silver_annuity_contracts",
    "silver_policy_events",
]

BRONZE_TABLES = [
    "bronze_term_policies",
    "bronze_wl_policies",
    "bronze_ul_policies",
    "bronze_vul_policies",
    "bronze_annuity_contracts",
]

GOLD_TABLES = [
    # Experience study
    "gold_study_runs",
    "gold_dq_run_summary",
    "gold_dq_quarantine",
    "gold_exposure_segments",
    "gold_inforce_reconciliation",
    "gold_ae_results",
    # TEV
    "gold_assumption_sets",
    "gold_model_points",
    "gold_tev_run_log",
    "gold_tev_results",
    "gold_workflow_iterations",
    "gold_assumption_approvals",
]

# Phase 3 (AI layer) Gold tables. Run-scoped logical state — cleared by default,
# same as the other Gold tables. (Tech Spec §D.1 / §D.2 / §D.3.)
GOLD_AI_TABLES = [
    "gold_ai_model_registry",   # §D.1 — one row per fitted GLM/GBM model
    "gold_ai_eval_results",     # §D.2 — one row per eval-harness run × model
    "gold_ai_audit_log",        # §D.3 — append-only chatbot / MCP / Skill turns
    "gold_ai_proposed_factors", # round 4 (2026-06-27) — materialised GLM/GBM proposed factor cells
]

# Phase 4 (Governance) hash-chained audit logs. These reference study runs and
# assumption sets by id, so after a plain reset (which deletes those artifacts)
# their rows become dangling history. NOT cleared by default — only when
# --include-governance is passed — so the governance trail is preserved unless
# you are deliberately resetting the governance lifecycle too. (Tech Spec §G.2 / §G.3.)
#
# gold_users is deliberately EXCLUDED: it holds login accounts and is re-seeded
# from config/governance_config.yaml on app start. Clearing it here would not
# lock you out (the app re-seeds), but there is no reason to churn it, and doing
# so would drop any accounts not present in config.
GOVERNANCE_TABLES = [
    "gold_governance_signoffs",   # §G.2 — hash-chained multi-level sign-off log
    "gold_ae_governance_events",  # §G.3 — hash-chained A/E governance events
]


def clear_ai_model_artifacts(ai_models_dir: Path, dry_run: bool = False) -> None:
    """Delete the on-disk model payloads under data/ai_models/ (files only; the
    directory scaffold is preserved so the app can write into it on the next fit)."""
    if not ai_models_dir.exists():
        print(f"  SKIP (not found): {ai_models_dir}")
        return

    files = [p for p in ai_models_dir.rglob("*") if p.is_file()]
    total_mb = sum(p.stat().st_size for p in files) / 1_000_000
    if not dry_run:
        for p in files:
            p.unlink()
    status = "DRY RUN" if dry_run else "cleared"
    print(f"  {status}: {ai_models_dir}  ({len(files):,} files, {total_mb:.1f} MB removed)")


def reset(
    db_path: Path,
    dry_run: bool = False,
    keep_bronze: bool = False,
    include_ai_models: bool = False,
    include_governance: bool = False,
) -> None:
    """Delete all rows from Bronze, Silver, and Gold tables (Bronze unless keep_bronze).

    Gold includes the Phase 3 AI tables. When include_ai_models is set, the on-disk
    model artifacts under data/ai_models/ are deleted as well. When include_governance
    is set, the Phase 4 hash-chained governance logs are cleared too (login accounts
    in gold_users are always preserved).
    """
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    con = duckdb.connect(str(db_path))

    all_tables = (
        GOLD_TABLES
        + GOLD_AI_TABLES
        + (GOVERNANCE_TABLES if include_governance else [])
        + SILVER_TABLES
        + ([] if keep_bronze else BRONZE_TABLES)
    )
    existing = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }

    print(f"Database: {db_path}")
    print(f"Dry run: {dry_run}")
    print(f"Keep Bronze: {keep_bronze}  "
          f"({'Bronze preserved' if keep_bronze else 'Bronze raw loads WILL be cleared'})")
    print(f"Include AI models: {include_ai_models}  "
          f"({'data/ai_models/ artifacts WILL be deleted' if include_ai_models else 'AI model artifacts preserved'})")
    print(f"Include governance: {include_governance}  "
          f"({'governance sign-off / event logs WILL be cleared (gold_users preserved)' if include_governance else 'governance logs preserved'})\n")

    cleared, skipped = 0, 0
    for table in all_tables:
        if table not in existing:
            print(f"  SKIP (not found): {table}")
            skipped += 1
            continue
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not dry_run:
            con.execute(f"DELETE FROM {table}")
        status = "DRY RUN" if dry_run else "cleared"
        print(f"  {status}: {table}  ({count:,} rows removed)")
        cleared += 1

    con.close()

    if include_ai_models:
        ai_models_dir = db_path.parent / "ai_models"
        clear_ai_model_artifacts(ai_models_dir, dry_run=dry_run)

    print(f"\n{'[DRY RUN] Would clear' if dry_run else 'Cleared'} {cleared} tables, skipped {skipped}.")
    if not dry_run:
        print("Re-run ETL via Study Setup → Run Study to repopulate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to DuckDB file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleared without deleting")
    parser.add_argument(
        "--keep-bronze",
        action="store_true",
        help="Preserve Bronze raw-load tables (by default Bronze is cleared along with Silver and Gold)",
    )
    parser.add_argument(
        "--include-ai-models",
        action="store_true",
        help="Also delete on-disk AI model artifacts under data/ai_models/ "
             "(the .pkl/.json/SHAP payloads; prevents them accumulating across resets)",
    )
    parser.add_argument(
        "--include-governance",
        action="store_true",
        help="Also clear the Phase 4 hash-chained governance logs "
             "(gold_governance_signoffs, gold_ae_governance_events) for a clean governance "
             "lifecycle; login accounts in gold_users are always preserved",
    )
    args = parser.parse_args()
    reset(
        args.db,
        dry_run=args.dry_run,
        keep_bronze=args.keep_bronze,
        include_ai_models=args.include_ai_models,
        include_governance=args.include_governance,
    )
