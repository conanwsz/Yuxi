from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import resource_access_runtime_service as service


def _user(role="custom", resource_access=None):
    return SimpleNamespace(role=role, resource_access=resource_access)


def test_missing_resource_access_fails_closed_for_non_superadmin(monkeypatch):
    user = _user(resource_access=None)
    monkeypatch.setattr(service, "get_tool_metadata", lambda: [{"slug": "search"}])

    assert service.list_tool_slugs_for_user(user) == []
    assert service.resolve_allowed_mcp_slugs(user, ["docs"]) == set()
    assert service.filter_model_infos_for_user(user, [SimpleNamespace(spec="p:m", model_type="chat")]) == []


def test_superadmin_keeps_hard_coded_all_resource_bypass(monkeypatch):
    user = _user(role="superadmin", resource_access=None)
    monkeypatch.setattr(service, "get_tool_metadata", lambda: [{"slug": "search"}])

    assert service.list_tool_slugs_for_user(user) == ["search"]
    assert service.resolve_allowed_mcp_slugs(user, ["docs"]) == {"docs"}


def test_selected_model_requires_allowed_spec_and_uses_role_default(monkeypatch):
    access = {
        "models": {"mode": "selected", "allowed": ["p:chat"], "defaults": {"chat": "p:chat"}},
        "tools": {"mode": "none", "allowed": []},
        "mcp_servers": {"mode": "none", "allowed": []},
    }
    user = _user(resource_access=access)
    infos = {
        "p:chat": SimpleNamespace(spec="p:chat", model_type="chat"),
        "p:advanced": SimpleNamespace(spec="p:advanced", model_type="chat"),
    }
    monkeypatch.setattr(service.model_cache, "get_model_info", infos.get)

    assert service.resolve_role_default_model_spec(user, model_type="chat") == "p:chat"
    assert service.assert_model_spec_allowed(user, "p:chat", model_type="chat").spec == "p:chat"
    with pytest.raises(HTTPException) as exc:
        service.assert_model_spec_allowed(user, "p:advanced", model_type="chat")
    assert exc.value.status_code == 403


def test_skill_dependencies_cannot_expand_tool_or_mcp_access(monkeypatch):
    access = {
        "models": {"mode": "none", "allowed": [], "defaults": {}},
        "tools": {"mode": "selected", "allowed": ["search"]},
        "mcp_servers": {"mode": "selected", "allowed": ["docs"]},
    }
    user = _user(resource_access=access)
    monkeypatch.setattr(service, "get_tool_metadata", lambda: [{"slug": "search"}, {"slug": "shell"}])
    skills = [
        SimpleNamespace(
            slug="base",
            tool_dependencies=["search"],
            mcp_dependencies=["docs"],
            skill_dependencies=[],
        ),
        SimpleNamespace(
            slug="child",
            tool_dependencies=[],
            mcp_dependencies=[],
            skill_dependencies=["base"],
        ),
        SimpleNamespace(
            slug="forbidden",
            tool_dependencies=["shell"],
            mcp_dependencies=[],
            skill_dependencies=[],
        ),
        SimpleNamespace(
            slug="indirect-forbidden",
            tool_dependencies=[],
            mcp_dependencies=[],
            skill_dependencies=["forbidden"],
        ),
    ]

    visible = service.filter_resource_accessible_skills(user, skills, enabled_mcp_slugs=["docs"])
    assert [skill.slug for skill in visible] == ["base", "child"]
