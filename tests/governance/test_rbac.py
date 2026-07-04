"""Tests for governance RBAC (FR-4-04/06; NFR-G-02).

require() is enforced server-side: a disallowed action invoked directly (no UI in
the loop) is rejected and logged. Permission matrix and role-to-level checks
follow the config.
"""

from __future__ import annotations

import logging

import pytest

from src.governance import rbac
from src.governance.rbac import (
    Action,
    PermissionDenied,
    is_permitted,
    load_permission_matrix,
    may_sign_off_at,
    require,
)
from src.utils.types import ChainLevel, Role, User

_CFG = {
    "permissions": {
        "analyst":        ["propose", "view"],
        "junior_actuary": ["sign_off", "view", "export"],
        "senior_actuary": ["sign_off", "view", "export"],
        "chief_actuary":  ["sign_off", "view", "export"],
    }
}
_MATRIX = load_permission_matrix(_CFG)


def _user(role: Role, active: bool = True) -> User:
    return User(
        user_id="u-" + role.value,
        username=role.value,
        display_name=role.value,
        role=role,
        active=active,
    )


# --- permission matrix (FR-4-04) ---

def test_matrix_maps_roles_to_actions():
    assert _MATRIX[Role.ANALYST] == {Action.PROPOSE, Action.VIEW}
    assert Action.SIGN_OFF in _MATRIX[Role.CHIEF_ACTUARY]
    assert Action.SIGN_OFF not in _MATRIX[Role.ANALYST]


def test_is_permitted_matches_matrix():
    assert is_permitted(_user(Role.ANALYST), Action.PROPOSE, matrix=_MATRIX) is True
    assert is_permitted(_user(Role.ANALYST), Action.SIGN_OFF, matrix=_MATRIX) is False
    assert is_permitted(_user(Role.SENIOR_ACTUARY), Action.SIGN_OFF, matrix=_MATRIX) is True


def test_inactive_user_permitted_nothing():
    assert is_permitted(_user(Role.CHIEF_ACTUARY, active=False), Action.VIEW, matrix=_MATRIX) is False


# --- require() server-side enforcement + logging (FR-4-04 / NFR-G-02) ---

def test_require_allows_permitted_action():
    # No raise.
    require(_user(Role.ANALYST), Action.PROPOSE, matrix=_MATRIX)


def test_require_blocks_and_logs_disallowed_action(caplog):
    user = _user(Role.ANALYST)
    with caplog.at_level(logging.WARNING, logger="governance.rbac"):
        with pytest.raises(PermissionDenied):
            # Direct call bypassing any UI — still rejected.
            require(user, Action.SIGN_OFF, matrix=_MATRIX)
    assert any("RBAC denied" in rec.message for rec in caplog.records)
    assert any(Action.SIGN_OFF.value in rec.getMessage() for rec in caplog.records)


def test_require_loads_matrix_from_config_path(tmp_path):
    import yaml

    cfg = tmp_path / "gc.yaml"
    with cfg.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(_CFG, fh)
    # No injected matrix → loads from config_path.
    require(_user(Role.JUNIOR_ACTUARY), Action.EXPORT, config_path=str(cfg))
    with pytest.raises(PermissionDenied):
        require(_user(Role.ANALYST), Action.EXPORT, config_path=str(cfg))


# --- chain-level role check (FR-4-06) ---

def test_may_sign_off_at_matches_role_to_level():
    lvl = ChainLevel(level=2, required_role=Role.SENIOR_ACTUARY)
    assert may_sign_off_at(_user(Role.SENIOR_ACTUARY), lvl) is True
    assert may_sign_off_at(_user(Role.JUNIOR_ACTUARY), lvl) is False
    assert may_sign_off_at(_user(Role.SENIOR_ACTUARY, active=False), lvl) is False


# --- config-driven matrix from the seeded env (FR-4-01/04 end-to-end) ---

def test_seeded_users_get_config_permissions(gov_env):
    from src.governance.users import get_user_by_username

    chief = get_user_by_username("c.chief", gov_env["db"])
    analyst = get_user_by_username("a.analyst", gov_env["db"])
    assert is_permitted(chief, Action.SIGN_OFF, config_path=gov_env["config_path"]) is True
    assert is_permitted(analyst, Action.SIGN_OFF, config_path=gov_env["config_path"]) is False
    assert is_permitted(analyst, Action.PROPOSE, config_path=gov_env["config_path"]) is True
