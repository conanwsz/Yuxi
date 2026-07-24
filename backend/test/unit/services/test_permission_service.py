from types import SimpleNamespace

import pytest

from yuxi.services.permission_service import (
    ALL_PERMISSION_KEYS,
    DEFAULT_ROLE_PERMISSIONS,
    has_permission,
    validate_permission_keys,
)


def _user(role: str, permissions: set[str] | None = None):
    return SimpleNamespace(role=role, permission_keys=permissions or set())


def test_superadmin_always_has_every_registered_permission():
    user = _user("superadmin")

    assert ALL_PERMISSION_KEYS
    assert all(has_permission(user, permission) for permission in ALL_PERMISSION_KEYS)


def test_custom_role_uses_resolved_permission_keys():
    user = _user("reviewer", {"knowledge.read", "dashboard.read"})

    assert has_permission(user, "knowledge.read")
    assert not has_permission(user, "knowledge.delete")


def test_default_roles_keep_existing_management_boundary():
    assert "dashboard.read" in DEFAULT_ROLE_PERMISSIONS["superadmin"]
    assert "dashboard.read" not in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "knowledge.update" in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "knowledge.update" not in DEFAULT_ROLE_PERMISSIONS["user"]
    assert "users.disable" in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "users.enable" in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "users.delete" not in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "users.delete" in DEFAULT_ROLE_PERMISSIONS["superadmin"]


def test_unknown_permission_is_rejected():
    with pytest.raises(ValueError, match="未知权限"):
        validate_permission_keys(["knowledge.read", "unknown.permission"])
