"""Tests for sandbox provider agent-env merging logic."""

from __future__ import annotations

import pytest

from yuxi.agents.backends.sandbox.provider import merge_user_agent_env


pytestmark = pytest.mark.unit


def test_merge_user_agent_env_non_oidc_returns_user_env_only():
    """非 OIDC 用户不应注入系统级 uid / entity_code / dept_code。"""

    merged = merge_user_agent_env(
        "u-1",
        {"YUXI_USER": "v"},
        is_oidc=False,
        entity_code="YUXI",
        department_code="BM000102",
    )

    assert merged == {"YUXI_USER": "v"}


def test_merge_user_agent_env_oidc_includes_uid_and_org_codes():
    """OIDC 用户必须始终带上 uid，并在部门字段存在时补齐 entity_code / dept_code。"""

    merged = merge_user_agent_env(
        "u-1",
        {"YUXI_USER": "v"},
        is_oidc=True,
        entity_code="YUXI",
        department_code="BM000102",
    )

    assert merged == {
        "YUXI_USER": "v",
        "uid": "u-1",
        "entity_code": "YUXI",
        "dept_code": "BM000102",
    }


def test_merge_user_agent_env_oidc_without_org_codes_exposes_only_uid():
    """OIDC 用户所属部门没有 CNNP 编码时，不输出空字段。"""

    merged = merge_user_agent_env(
        "u-1",
        None,
        is_oidc=True,
        entity_code=None,
        department_code=None,
    )

    assert merged == {"uid": "u-1"}
