"""Shared pytest fixtures.

`prod_db` returns a session-scoped **copy** of the production DuckDB. Tests must never
mutate the real `data/experience_study.duckdb`: several helpers (notably
``run_dq_checks``) persist rows to ``gold_dq_run_summary`` / ``gold_dq_quarantine`` as a
side effect, so running the suite against the real DB would pollute it with duplicate rows.
The copy is created once per session and shared; assertions that read the DQ tables filter
by the per-run ``dq_run_id`` (unique per ``run_dq_checks`` call), so the shared copy is safe.
"""
import shutil
from pathlib import Path

import pytest

_PROD_DB = Path("data/experience_study.duckdb")


@pytest.fixture(scope="session")
def prod_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped read copy of the production DB (never the real file)."""
    if not _PROD_DB.exists():
        pytest.skip("Production DB not found — run ETL pipeline first")
    dest = tmp_path_factory.mktemp("prod_db") / "experience_study.duckdb"
    shutil.copy2(_PROD_DB, dest)
    return dest


@pytest.fixture(scope="session")
def prod_run_id(prod_db: Path) -> str:
    """The most recent COMPLETE study run in the prod-DB copy.

    Resolved dynamically so real-data tests survive a DB rebuild (the production
    run id is not stable across recreations); skips if no completed run exists.
    """
    import duckdb

    con = duckdb.connect(str(prod_db), read_only=True)
    try:
        row = con.execute(
            "SELECT run_id FROM gold_study_runs WHERE status = 'COMPLETE' "
            "ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        pytest.skip("No COMPLETE study run in the production DB copy")
    return row[0]


# ---------------------------------------------------------------------------
# Phase 3 — AI-layer test artifact mechanics (Tech Spec v2.0.1 §F.4; NFR-T)
# ---------------------------------------------------------------------------
# All AI test artifacts are confined to ARTIFACT_ROOT (gitignored, NFR-T-01);
# tests never write to data/. The guard below cleans the directory on suite
# success (NFR-T-03) and fails the suite if artifacts exceed the size cap
# (NFR-T-04). The synthetic_db fixture (200-400 policies/product) lands with the
# GLM engine in Session 15; only the mechanics are established here.

ARTIFACT_ROOT = Path("tests/_artifacts")     # gitignored; NFR-T-01
SIZE_CAP_GB = 5.0                             # NFR-T-04 (configurable)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --keep-artifacts (NFR-T-03): retain ARTIFACT_ROOT after a run."""
    parser.addoption(
        "--keep-artifacts",
        action="store_true",
        default=False,
        help="Keep tests/_artifacts/ after a successful suite (NFR-T-03).",
    )


def _dir_size_bytes(root: Path) -> int:
    """Total size in bytes of all files under ``root`` (0 if absent)."""
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


@pytest.fixture(scope="session", autouse=True)
def _artifact_guard(request: pytest.FixtureRequest):
    """Size guard (NFR-T-04) + teardown cleanup (NFR-T-03).

    At session end: if ARTIFACT_ROOT exceeds SIZE_CAP_GB, fail loudly; otherwise,
    on a successful run, delete ARTIFACT_ROOT unless --keep-artifacts was passed.
    """
    yield
    size_gb = _dir_size_bytes(ARTIFACT_ROOT) / (1024 ** 3)
    if size_gb > SIZE_CAP_GB:
        pytest.fail(
            f"tests/_artifacts/ is {size_gb:.2f} GB (> {SIZE_CAP_GB} GB cap, "
            f"NFR-T-04)",
            pytrace=False,
        )
    if not request.config.getoption("--keep-artifacts") and ARTIFACT_ROOT.exists():
        shutil.rmtree(ARTIFACT_ROOT, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 3 — synthetic_db fixture for the GLM engine (Session 15; FR-3A-26/27)
# ---------------------------------------------------------------------------
# A small, self-contained Gold A/E fact table with KNOWN true A/E adjustment
# factors. Rather than re-run the full Bronze->Silver->exposure pipeline, the
# fixture synthesises gold_ae_results cells directly — which is exactly (and
# only) what the GLM reads (FR-3A-15) — injecting the known true factors from
# synthetic_data/true_factors.py and drawing actual decrement counts as
# Poisson(expected x true_factor) with the project seed (42). The same
# true-factor functions feed validate_against_truth, so the ground truth is
# consistent by construction. Written under ARTIFACT_ROOT (never data/).

from dataclasses import dataclass, field   # noqa: E402  (grouped with fixture)
import uuid                                # noqa: E402
from datetime import datetime              # noqa: E402

import numpy as np                         # noqa: E402
import pandas as pd                        # noqa: E402
import duckdb                              # noqa: E402

from synthetic_data import true_factors as tf   # noqa: E402

_SYNTH_SEED = 42


@dataclass
class SyntheticDB:
    """Handle to the synthetic Gold DB plus the fitting cells (with truth)."""
    db_path: Path
    run_id: str
    cells: dict = field(default_factory=dict)   # (decrement, product) -> DataFrame


#: Products carried in the detail rows. Mortality validation uses TERM only;
#: lapse pools across all three so its coarse (product x duration) output grain
#: has enough cells for the >=90% coverage check (FR-3A-27) to be meaningful.
SYNTH_PRODUCTS = ["TERM", "WL", "UL"]


def _build_mortality_lapse_cells(rng: np.random.Generator) -> pd.DataFrame:
    """Detail rows (illness_code NULL) carrying mortality AND lapse measures."""
    rows = []
    for product in SYNTH_PRODUCTS:
        for gender in ["M", "F"]:
            for smoker in ["NS", "SM"]:
                for risk in tf.RISK_CLASSES:
                    for ai, age_band in enumerate(tf.ATTAINED_AGE_BANDS):
                        for duration in tf.DURATION_BANDS:
                            mort_f = tf.mortality_true_factor(
                                gender, smoker, risk, age_band, duration)
                            lapse_f = tf.lapse_true_factor(product, duration, None)
                            exp_d = 12.0 + 2.0 * ai     # mortality reference expected
                            exp_l = 30.0                # lapse reference expected
                            rows.append({
                                "product_code": product,
                                "gender": gender,
                                "smoker_status": smoker,
                                "risk_class": risk,
                                "attained_age_band": age_band,
                                "duration_band": duration,
                                "premium_jump_ratio_band": None,
                                "illness_code": None,
                                "exposure_count": exp_d / 0.01,
                                "expected_deaths_count": exp_d,
                                "actual_deaths_count": int(rng.poisson(exp_d * mort_f)),
                                "lapse_exposure_count": exp_l / 0.05,
                                "expected_lapses": exp_l,
                                "actual_lapses": int(rng.poisson(exp_l * lapse_f)),
                                "_mort_true": mort_f,
                                "_lapse_true": lapse_f,
                            })
    return pd.DataFrame(rows)


def _build_ci_cells(rng: np.random.Generator) -> pd.DataFrame:
    """CI rows (illness_code set; smoker/risk/duration NULL) — total-incidence grain.

    Generated for every product so the age x sex output grain pools to enough
    cells for a stable coverage check, on its own RNG stream so it is unaffected
    by the detail-row generation.
    """
    rows = []
    for product in SYNTH_PRODUCTS:
        for gender in ["M", "F"]:
            for age_band in tf.ATTAINED_AGE_BANDS:
                ci_f = tf.ci_true_factor(age_band, gender)
                for illness in ["CI-001", "CI-002"]:
                    exp_ci = 150.0
                    rows.append({
                        "product_code": product,
                        "gender": gender,
                        "smoker_status": None,
                        "risk_class": None,
                        "attained_age_band": age_band,
                        "duration_band": None,
                        "illness_code": illness,
                        "ci_exposure_count": exp_ci / 0.005,
                        "expected_ci_claims": exp_ci,
                        "actual_ci_claims": int(rng.poisson(exp_ci * ci_f)),
                        "_ci_true": ci_f,
                    })
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> SyntheticDB:
    """Session-scoped synthetic Gold A/E DB with known true factors (FR-3A-26/27)."""
    from src.utils.db_init import init_database

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    db_dir = tmp_path_factory.mktemp("synthetic_db")
    db_path = db_dir / "synthetic.duckdb"
    init_database(str(db_path))

    # Independent RNG streams per decrement group (both seeded from 42) so that
    # changing one group's grid never perturbs the other's draws.
    master = np.random.default_rng(_SYNTH_SEED)
    detail_seed, ci_seed = master.integers(0, 2 ** 63 - 1, size=2)
    run_id = str(uuid.uuid4())
    now = datetime.utcnow()

    ml = _build_mortality_lapse_cells(np.random.default_rng(int(detail_seed)))
    ci = _build_ci_cells(np.random.default_rng(int(ci_seed)))

    insert = pd.concat([
        ml.drop(columns=["_mort_true", "_lapse_true"]),
        ci.drop(columns=["_ci_true"]),
    ], ignore_index=True)
    insert.insert(0, "result_id", [str(uuid.uuid4()) for _ in range(len(insert))])
    insert["study_run_id"] = run_id
    insert["anti_selection_flag"] = False
    insert["_created_ts"] = now

    con = duckdb.connect(str(db_path))
    try:
        con.register("synth_rows", insert)
        con.execute("INSERT INTO gold_ae_results BY NAME SELECT * FROM synth_rows")
    finally:
        con.close()

    # Cells carrying the per-cell truth, for validate_against_truth (per product).
    cells: dict = {}
    mort = ml.rename(columns={"expected_deaths_count": "expected", "_mort_true": "true_factor"})
    lap = ml.rename(columns={"expected_lapses": "expected", "_lapse_true": "true_factor"})
    ci_renamed = ci.rename(columns={"expected_ci_claims": "expected", "_ci_true": "true_factor"})
    for product in SYNTH_PRODUCTS:
        cells[("MORTALITY", product)] = mort[mort["product_code"] == product].copy()
        cells[("LAPSE", product)] = lap[lap["product_code"] == product].copy()
        cells[("CI_INCIDENCE", product)] = ci_renamed[ci_renamed["product_code"] == product].copy()

    return SyntheticDB(db_path=db_path, run_id=run_id, cells=cells)


@pytest.fixture(scope="session")
def glm_config() -> dict:
    """The `glm:` block of config/ai_config.yaml (Tech Spec §F.1)."""
    import yaml
    with open("config/ai_config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["glm"]


@pytest.fixture(scope="session")
def gbm_config() -> dict:
    """The `gbm:` block of config/ai_config.yaml (Session 16; Tech Spec §F.1).

    GBM output grains / covariates / validation tolerances are shared with the
    `glm:` block (the GBM is the challenge column at the same grain), so GBM tests
    read grains+covariates from `glm_config` and hyperparameters/threshold/seed
    from here.
    """
    import yaml
    with open("config/ai_config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["gbm"]


@pytest.fixture(scope="session")
def feature_to_assumption_map() -> dict:
    """The per-decrement feature→actuarial map (FR-3A-39)."""
    import yaml
    with open("config/feature_to_assumption.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["feature_to_assumption"]
