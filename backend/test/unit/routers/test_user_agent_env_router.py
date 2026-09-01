"""Unit tests for user_agent_env response builder.

These tests focus on the pure ``build_agent_env_response`` helper so they do
not depend on the live API container, mirror the integration contract for the
OIDC ``uid`` / ``entity_code`` / ``dept_code`` injection.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.routers.user_router import (
    AgentEnvResponse,
    OIDC_READONLY_ENV_KEYS,
    build_agent_env_response,
)


pytestmark = pytest.mark.unit


def _user(uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(uid=uid)


def test_non_oidc_response_keeps_user_env_untouched():
    response = build_agent_env_response(
        {"YUXI_USER": "kept"},
        user=_user(),
        oidc_user=False,
    )

    assert response.env == {"YUXI_USER": "kept"}
    assert response.readonly_keys == []


def test_oidc_response_injects_only_uid_when_no_readonly_source():
    response = build_agent_env_response(
        {"YUXI_USER": "kept"},
        user=_user(uid="emp-001"),
        oidc_user=True,
    )

    assert response.env == {"YUXI_USER": "kept", "uid": "emp-001"}
    assert response.readonly_keys == ["uid"]


def test_oidc_response_injects_full_readonly_set():
    readonly = {"uid": "emp-001", "entity_code": "YUXI", "dept_code": "BM000102"}

    response = build_agent_env_response(
        {"YUXI_USER": "kept"},
        user=_user(),
        oidc_user=True,
        oidc_readonly_env=readonly,
    )

    assert response.env == {
        "YUXI_USER": "kept",
        "uid": "emp-001",
        "entity_code": "YUXI",
        "dept_code": "BM000102",
    }
    assert set(response.readonly_keys) == set(readonly.keys())


def test_oidc_response_overrides_stale_user_values_with_authoritative_source():
    """前端缓存里残留的同名键应当被系统覆盖，保证权威来源唯一。"""

    response = build_agent_env_response(
        {"uid": "spoofed", "entity_code": "spoofed", "YUXI_USER": "kept"},
        user=_user(uid="emp-001"),
        oidc_user=True,
        oidc_readonly_env={
            "uid": "emp-001",
            "entity_code": "YUXI",
            "dept_code": "BM000102",
        },
    )

    assert response.env == {
        "YUXI_USER": "kept",
        "uid": "emp-001",
        "entity_code": "YUXI",
        "dept_code": "BM000102",
    }
    assert set(response.readonly_keys) == {"uid", "entity_code", "dept_code"}


def test_oidc_readonly_keys_match_documented_set():
    """``uid``、``entity_code``、``dept_code`` 是 OIDC 用户不可改的三个系统字段。"""

    assert OIDC_READONLY_ENV_KEYS == ("uid", "entity_code", "dept_code")
