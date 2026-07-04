"""Role-based access control (Tech Spec v3.0 §H.4; FR-4-04/06; NFR-G-02).

The permission matrix is config-defined (``config/governance_config.yaml``).
Authorisation is enforced **server-side** — ``require()`` is called inside every
governed operation, not only in the UI, so a direct function call cannot bypass
it; every denial is logged.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from src.governance.users import DEFAULT_CONFIG_PATH
from src.utils.types import ChainLevel, Role, User

_log = logging.getLogger("governance.rbac")


class Action(str, Enum):
    PROPOSE  = "propose"
    SIGN_OFF = "sign_off"
    VIEW     = "view"
    EXPORT   = "export"


class PermissionDenied(Exception):
    """Raised when a user attempts an action their role does not permit (FR-4-04)."""


def load_permission_matrix(cfg: dict) -> dict[Role, set[Action]]:
    """Build ``{Role: {Action, ...}}`` from a parsed config's ``permissions`` block."""
    matrix: dict[Role, set[Action]] = {}
    for role_name, actions in (cfg.get("permissions") or {}).items():
        matrix[Role(role_name)] = {Action(a) for a in (actions or [])}
    return matrix


def _matrix_from_path(config_path: str) -> dict[Role, set[Action]]:
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return load_permission_matrix(cfg)


def is_permitted(
    user: User,
    action: Action,
    *,
    matrix: Optional[dict[Role, set[Action]]] = None,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> bool:
    """Whether ``user``'s role permits ``action``.

    Uses ``matrix`` when supplied (test injection); otherwise loads the matrix
    from ``config_path``. An inactive user is permitted nothing.
    """
    if not user.active:
        return False
    if matrix is None:
        matrix = _matrix_from_path(config_path)
    return action in matrix.get(user.role, set())


def require(
    user: User,
    action: Action,
    *,
    matrix: Optional[dict[Role, set[Action]]] = None,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> None:
    """Raise ``PermissionDenied`` if ``user`` may not perform ``action``.

    Called inside governed operations (server-side), so a direct call cannot
    bypass authorisation. Every denial is logged (FR-4-04 / NFR-G-02).
    """
    if is_permitted(user, action, matrix=matrix, config_path=config_path):
        return
    _log.warning(
        "RBAC denied: user=%s role=%s action=%s",
        user.username, user.role.value, action.value,
    )
    raise PermissionDenied(
        f"User '{user.username}' (role {user.role.value}) may not perform "
        f"action '{action.value}'."
    )


def may_sign_off_at(user: User, level: ChainLevel) -> bool:
    """True only if the user's role matches the chain level's required role (FR-4-06)."""
    return user.active and user.role == level.required_role
