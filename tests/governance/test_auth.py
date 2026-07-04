"""Tests for governance auth + user store (FR-4-01/02/03; NFR-G-01).

Covers password hashing, the seed/authenticate path, the gold_users schema,
and the pre-auth no-identity guarantee.
"""

from __future__ import annotations

import duckdb

from src.governance.auth import (
    PLACEHOLDER_PASSWORD,
    UNUSABLE_HASH,
    authenticate,
    current_user,
    hash_password,
    verify_password,
)
from src.governance.users import get_user_by_username, list_users, seed_users_from_config
from src.utils.db_init import init_database
from src.utils.types import Role, User

from tests.governance.conftest import TEST_USERS


# --- gold_users schema (§G.1) ---

_EXPECTED_USER_COLS = [
    "user_id", "username", "display_name", "role",
    "password_hash", "password_salt", "active", "created_ts",
]


def test_init_creates_gold_users_with_expected_columns(tmp_path):
    db = tmp_path / "schema.duckdb"
    init_database(str(db))
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name='gold_users' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        con.close()
    assert cols == _EXPECTED_USER_COLS


def test_init_is_idempotent_for_gold_users(tmp_path):
    db = tmp_path / "idem.duckdb"
    init_database(str(db))
    init_database(str(db))  # second run must not error
    con = duckdb.connect(str(db), read_only=True)
    try:
        n = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name='gold_users'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1


# --- password hashing (FR-4-02) ---

def test_hash_verify_roundtrip():
    pw_hash, salt = hash_password("correct horse")
    assert verify_password("correct horse", pw_hash, salt) is True
    assert verify_password("wrong horse", pw_hash, salt) is False


def test_salts_differ_per_call():
    h1, s1 = hash_password("same")
    h2, s2 = hash_password("same")
    assert s1 != s2 and h1 != h2  # random per-call salt → distinct hashes


def test_no_plaintext_in_hash_output():
    secret = "SuperSecretP@ss"
    pw_hash, salt = hash_password(secret)
    assert secret not in pw_hash
    assert secret not in salt


def test_same_salt_reproduces_hash():
    h1, salt = hash_password("deterministic")
    h2, _ = hash_password("deterministic", salt)
    assert h1 == h2


def test_unusable_hash_never_verifies():
    assert verify_password("anything", UNUSABLE_HASH, "00" * 16) is False


def test_verify_malformed_salt_returns_false():
    # Non-hex salt must not raise; it fails closed.
    assert verify_password("x", "deadbeef", "not-hex-salt") is False


# --- authenticate (FR-4-02/03) ---

def test_authenticate_success_returns_user(gov_env):
    user = authenticate("c.chief", gov_env["creds"]["c.chief"], gov_env["db"])
    assert isinstance(user, User)
    assert user.username == "c.chief"
    assert user.role == Role.CHIEF_ACTUARY
    assert user.active is True


def test_authenticate_wrong_password_returns_none(gov_env):
    assert authenticate("c.chief", "not-the-password", gov_env["db"]) is None


def test_authenticate_unknown_user_returns_none(gov_env):
    assert authenticate("nobody", "whatever", gov_env["db"]) is None


def test_placeholder_password_user_cannot_authenticate(gov_env):
    # Seeded with the "<set at first run>" placeholder → UNUSABLE_HASH sentinel.
    assert authenticate(gov_env["placeholder"], PLACEHOLDER_PASSWORD, gov_env["db"]) is None
    assert authenticate(gov_env["placeholder"], "", gov_env["db"]) is None


def test_authenticate_inactive_user_returns_none(gov_env):
    con = duckdb.connect(gov_env["db"])
    try:
        con.execute("UPDATE gold_users SET active = FALSE WHERE username = 'a.analyst'")
    finally:
        con.close()
    assert authenticate("a.analyst", gov_env["creds"]["a.analyst"], gov_env["db"]) is None


# --- seeding & getters (FR-4-01) ---

def test_seed_is_idempotent(gov_env):
    # Re-seeding the same config must not duplicate or error.
    before = len(list_users(active_only=False, db_path=gov_env["db"]))
    seed_users_from_config(gov_env["config_path"], gov_env["db"])
    after = len(list_users(active_only=False, db_path=gov_env["db"]))
    assert before == after


def test_get_user_by_username_roundtrip(gov_env):
    u = get_user_by_username("j.junior", gov_env["db"])
    assert u is not None and u.role == Role.JUNIOR_ACTUARY


def test_every_seeded_user_holds_exactly_one_known_role(gov_env):
    users = list_users(active_only=False, db_path=gov_env["db"])
    assert len(users) == len(TEST_USERS) + 1  # + placeholder
    for u in users:
        assert isinstance(u.role, Role)  # exactly one of the four roles


def test_local_override_replaces_password(tmp_path):
    # A git-ignored local file overrides a committed placeholder with a real password.
    import yaml

    db = tmp_path / "override.duckdb"
    init_database(str(db))
    committed = tmp_path / "governance_config.yaml"
    with committed.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                "permissions": {"analyst": ["view"]},
                "users": [
                    {
                        "username": "o.user",
                        "display_name": "O. User",
                        "role": "analyst",
                        "bootstrap_password": "<set at first run>",
                    }
                ],
            },
            fh,
        )
    local = tmp_path / "governance_config.local.yaml"
    with local.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {"users": [{"username": "o.user", "role": "analyst",
                        "display_name": "O. User", "bootstrap_password": "real-pw-9!"}]},
            fh,
        )
    seed_users_from_config(str(committed), str(db))
    assert authenticate("o.user", "real-pw-9!", str(db)) is not None


# --- pre-auth identity guarantee (NFR-G-01) ---

def test_current_user_is_none_pre_auth():
    # Outside a Streamlit runtime there is no session identity.
    assert current_user() is None
