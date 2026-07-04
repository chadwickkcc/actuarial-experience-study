"""Authentication & session identity (Tech Spec v3.0 §H.2; FR-4-02/03; NFR-G-01).

Minimal username/password gate for single-org use. Passwords are stored only as
salted PBKDF2 hashes (stdlib ``hashlib`` — no third-party dependency). No SSO, no
password-reset/recovery, no account self-management. The authenticated Streamlit
session identity is the canonical actor for every governance-relevant action.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Optional

import duckdb

from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.types import Role, User

# PBKDF2-HMAC-SHA256 work factor. Tunable; constant so re-hashing is deterministic.
PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16

# Config placeholder for an un-set bootstrap password (§I.1/§I.2). A user seeded
# with this gets the UNUSABLE_HASH sentinel and cannot authenticate until set.
PLACEHOLDER_PASSWORD = "<set at first run>"

# Sentinel password_hash that no real PBKDF2 hex digest can equal (hex is [0-9a-f]),
# so verify_password always fails for accounts that have no usable password yet.
UNUSABLE_HASH = "!"

# Streamlit session-state key holding the authenticated User.
_SESSION_KEY = "gov_user"


def hash_password(plaintext: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Return ``(password_hash, salt)`` for ``plaintext`` using salted PBKDF2.

    A new random hex salt is generated when ``salt`` is None. The plaintext is
    never stored or logged. Returns the hash and salt as lowercase hex strings.
    """
    if salt is None:
        salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plaintext.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(plaintext: str, password_hash: str, salt: str) -> bool:
    """Constant-time check of ``plaintext`` against a stored hash/salt.

    Returns False for the UNUSABLE_HASH sentinel (no password set) and for any
    malformed salt, rather than raising.
    """
    if password_hash == UNUSABLE_HASH:
        return False
    try:
        candidate, _ = hash_password(plaintext, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, password_hash)


def authenticate(
    username: str, plaintext: str, db_path: str = DEFAULT_DB_PATH
) -> Optional[User]:
    """Return the active ``User`` on a correct password, else None.

    A wrong password, an inactive account, or an unknown username all return None
    with no distinguishing detail (no information leak — FR-4-02 / NFR-G-01).
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT user_id, username, display_name, role, active, "
            "password_hash, password_salt FROM gold_users WHERE username = ?",
            [username],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    user_id, uname, display_name, role, active, pw_hash, pw_salt = row
    if not active:
        return None
    if not verify_password(plaintext, pw_hash, pw_salt):
        return None
    return User(
        user_id=user_id,
        username=uname,
        display_name=display_name,
        role=Role(role),
        active=bool(active),
    )


def current_user() -> Optional[User]:
    """The authenticated user for the active Streamlit session, or None.

    Returns None outside a Streamlit runtime (e.g. tests, headless imports), so
    governed code can read the actor uniformly without a hard Streamlit dependency.
    """
    try:
        import streamlit as st
    except ModuleNotFoundError:
        return None
    try:
        user = st.session_state.get(_SESSION_KEY)
    except Exception:
        return None
    return user if isinstance(user, User) else None


def logout() -> None:
    """Clear the authenticated identity from the Streamlit session."""
    import streamlit as st

    st.session_state.pop(_SESSION_KEY, None)


def login_gate(db_path: str = DEFAULT_DB_PATH) -> User:
    """Streamlit entry gate: render the login form and block all pages until a
    valid session identity exists; return the current ``User``.

    Mounted ahead of every page in ``ui/app.py``. Nothing is reachable pre-auth:
    when unauthenticated, this renders the form and calls ``st.stop()``.
    """
    import streamlit as st

    user = current_user()
    if user is not None:
        return user

    st.title("Experience Study Tool — Sign in")
    with st.form("gov_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        authed = authenticate(username, password, db_path)
        if authed is not None:
            st.session_state[_SESSION_KEY] = authed
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()
