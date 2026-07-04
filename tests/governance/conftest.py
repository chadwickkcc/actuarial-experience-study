"""Shared fixtures for Phase 4 governance tests.

Builds a hermetic temp DuckDB with ``gold_users`` seeded from an in-test config
that carries REAL test passwords — so the committed config stays secret-free and
tests never touch the production DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.governance.users import seed_users_from_config
from src.utils.db_init import init_database

# username -> (display_name, role, plaintext_password)
TEST_USERS = {
    "a.analyst": ("A. Analyst", "analyst", "pw-analyst-1!"),
    "j.junior":  ("J. Junior", "junior_actuary", "pw-junior-1!"),
    "s.senior":  ("S. Senior", "senior_actuary", "pw-senior-1!"),
    "c.chief":   ("C. Chief", "chief_actuary", "pw-chief-1!"),
}

PLACEHOLDER_USERNAME = "p.placeholder"

_PERMISSIONS = {
    "analyst":        ["propose", "view"],
    "junior_actuary": ["sign_off", "view", "export"],
    "senior_actuary": ["sign_off", "view", "export"],
    "chief_actuary":  ["sign_off", "view", "export"],
}


def _write_config(path: Path, include_placeholder: bool = True) -> None:
    users = [
        {
            "username": uname,
            "display_name": display,
            "role": role,
            "bootstrap_password": pw,
        }
        for uname, (display, role, pw) in TEST_USERS.items()
    ]
    if include_placeholder:
        users.append(
            {
                "username": PLACEHOLDER_USERNAME,
                "display_name": "P. Placeholder",
                "role": "analyst",
                "bootstrap_password": "<set at first run>",
            }
        )
    cfg = {
        "roles": ["analyst", "junior_actuary", "senior_actuary", "chief_actuary"],
        "permissions": _PERMISSIONS,
        "users": users,
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)


@pytest.fixture()
def gov_env(tmp_path) -> dict:
    """A temp DB with gold_users seeded, plus the creds and config path."""
    db = tmp_path / "gov.duckdb"
    init_database(str(db))
    cfg_path = tmp_path / "governance_config.yaml"
    _write_config(cfg_path, include_placeholder=True)
    n = seed_users_from_config(str(cfg_path), str(db))
    return {
        "db": str(db),
        "config_path": str(cfg_path),
        "creds": {u: p for u, (_, _, p) in TEST_USERS.items()},
        "placeholder": PLACEHOLDER_USERNAME,
        "seeded_count": n,
    }
