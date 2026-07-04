"""User store (Tech Spec v3.0 §H.3; FR-4-01).

Seeds the ``gold_users`` registry from ``config/governance_config.yaml`` and
provides read-only getters. There is no in-app account/role management UI
(single-org prototype). Seeding is idempotent (upsert by username) and never
stores plaintext passwords.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from src.governance.auth import (
    PLACEHOLDER_PASSWORD,
    UNUSABLE_HASH,
    hash_password,
)
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import Role, User

DEFAULT_CONFIG_PATH = "config/governance_config.yaml"
_LOCAL_OVERRIDE_NAME = "governance_config.local.yaml"


def _load_users_block(path: Path) -> list[dict]:
    """Return the ``users`` list from a governance config file (empty if absent)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return list(cfg.get("users") or [])


def _merged_user_entries(config_path: Path) -> list[dict]:
    """Committed user entries, overridden by username from the git-ignored local
    file if present (real-credentials path, §I.2)."""
    by_username: dict[str, dict] = {}
    order: list[str] = []
    for entry in _load_users_block(config_path):
        uname = entry.get("username")
        if uname is None:
            continue
        if uname not in by_username:
            order.append(uname)
        by_username[uname] = entry
    local_path = config_path.parent / _LOCAL_OVERRIDE_NAME
    for entry in _load_users_block(local_path):
        uname = entry.get("username")
        if uname is None:
            continue
        if uname not in by_username:
            order.append(uname)
        by_username[uname] = entry
    return [by_username[u] for u in order]


def seed_users_from_config(
    path: str = DEFAULT_CONFIG_PATH, db_path: str = DEFAULT_DB_PATH
) -> int:
    """Idempotently upsert ``gold_users`` from config; return the count processed.

    For each configured user (committed entries overridden by the git-ignored
    local file by username): a real ``bootstrap_password`` is hashed with a fresh
    per-user salt and the plaintext discarded; the ``<set at first run>``
    placeholder yields the UNUSABLE_HASH sentinel on first insert and leaves any
    existing password untouched on re-seed. Re-running converges display_name,
    role, and active to config (FR-4-01 / §I.2).
    """
    entries = _merged_user_entries(Path(path))
    con = duckdb.connect(str(db_path))
    try:
        for entry in entries:
            username = entry["username"]
            display_name = entry.get("display_name", username)
            role = Role(entry["role"]).value
            raw_pw = entry.get("bootstrap_password", PLACEHOLDER_PASSWORD)
            is_placeholder = (not raw_pw) or raw_pw == PLACEHOLDER_PASSWORD

            existing = con.execute(
                "SELECT user_id FROM gold_users WHERE username = ?", [username]
            ).fetchone()

            if existing is None:
                if is_placeholder:
                    pw_hash, pw_salt = UNUSABLE_HASH, uuid.uuid4().hex
                else:
                    pw_hash, pw_salt = hash_password(raw_pw)
                con.execute(
                    "INSERT INTO gold_users (user_id, username, display_name, "
                    "role, password_hash, password_salt, active, created_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        str(uuid.uuid4()), username, display_name, role,
                        pw_hash, pw_salt, True, datetime.utcnow(),
                    ],
                )
            elif is_placeholder:
                # Preserve any existing (possibly real) password; sync metadata only.
                con.execute(
                    "UPDATE gold_users SET display_name = ?, role = ?, active = ? "
                    "WHERE username = ?",
                    [display_name, role, True, username],
                )
            else:
                pw_hash, pw_salt = hash_password(raw_pw)
                con.execute(
                    "UPDATE gold_users SET display_name = ?, role = ?, "
                    "password_hash = ?, password_salt = ?, active = ? "
                    "WHERE username = ?",
                    [display_name, role, pw_hash, pw_salt, True, username],
                )
    finally:
        con.close()
    return len(entries)


def _row_to_user(row) -> User:
    user_id, username, display_name, role, active = row
    return User(
        user_id=user_id,
        username=username,
        display_name=display_name,
        role=Role(role),
        active=bool(active),
    )


def get_user(user_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[User]:
    """Return the ``User`` with ``user_id``, or None."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT user_id, username, display_name, role, active "
            "FROM gold_users WHERE user_id = ?",
            [user_id],
        ).fetchone()
    finally:
        con.close()
    return _row_to_user(row) if row else None


def get_user_by_username(username: str, db_path: str = DEFAULT_DB_PATH) -> Optional[User]:
    """Return the ``User`` with ``username``, or None."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT user_id, username, display_name, role, active "
            "FROM gold_users WHERE username = ?",
            [username],
        ).fetchone()
    finally:
        con.close()
    return _row_to_user(row) if row else None


def list_users(active_only: bool = True, db_path: str = DEFAULT_DB_PATH) -> list[User]:
    """Return all registered users (active only by default)."""
    sql = (
        "SELECT user_id, username, display_name, role, active "
        "FROM gold_users"
    )
    if active_only:
        sql += " WHERE active = TRUE"
    sql += " ORDER BY username"
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    return [_row_to_user(r) for r in rows]
