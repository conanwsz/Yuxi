from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.models.providers.cache import ModelInfo, model_cache
from yuxi.services.resource_access_service import (
    KNOWLEDGE_BASE_TOOL_SLUGS,
    default_resource_access_all,
    default_resource_access_none,
    get_tool_metadata,
    normalize_stored_resource_access,
)
from yuxi.storage.postgres.models_business import MCPServer, Role, Skill, User


def _all_access_payload() -> dict[str, Any]:
    return default_resource_access_all()


def _normalize_strings(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _resource_payload(user: User) -> dict[str, Any]:
    if user.role == "superadmin":
        return _all_access_payload()

    raw = getattr(user, "resource_access", None)
    if not isinstance(raw, dict):
        return default_resource_access_none()
    return normalize_stored_resource_access(raw)


async def hydrate_user_resource_access(db: AsyncSession | None, user: User) -> User:
    if user.role == "superadmin":
        user.resource_access = _all_access_payload()
        return user

    current_access = getattr(user, "resource_access", None)
    if isinstance(current_access, dict):
        user.resource_access = normalize_stored_resource_access(current_access)
        return user
    if db is None:
        user.resource_access = default_resource_access_none()
        return user

    result = await db.execute(select(Role).where(Role.key == user.role))
    role = result.scalar_one_or_none()
    if role:
        raw_access = getattr(role, "resource_access", None)
        user.resource_access = (
            normalize_stored_resource_access(raw_access)
            if isinstance(raw_access, dict)
            else default_resource_access_all()
        )
    else:
        user.resource_access = default_resource_access_none()
    if role and getattr(user, "permission_keys", None) is None:
        user.permission_keys = set(getattr(role, "permissions", None) or [])
    return user


def _group_mode(user: User, group: str) -> str:
    return str(_resource_payload(user).get(group, {}).get("mode") or "all")


def _group_allowed(user: User, group: str) -> set[str]:
    return set(_resource_payload(user).get(group, {}).get("allowed") or [])


def filter_model_infos_for_user(
    user: User,
    infos: Iterable[ModelInfo],
    *,
    model_type: str | None = None,
) -> list[ModelInfo]:
    items = [info for info in infos if model_type is None or info.model_type == model_type]
    if user.role == "superadmin":
        return items

    mode = _group_mode(user, "models")
    if mode == "none":
        return []
    if mode == "all":
        return items

    allowed = _group_allowed(user, "models")
    return [info for info in items if info.spec in allowed]


def list_model_infos_for_user(user: User, *, model_type: str | None = None) -> list[ModelInfo]:
    return filter_model_infos_for_user(user, model_cache.get_all_specs(model_type), model_type=model_type)


def assert_model_spec_allowed(user: User, model_spec: str, model_type: str | None = None) -> ModelInfo:
    normalized = str(model_spec or "").strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="model_spec 不能为空")

    info = model_cache.get_model_info(normalized)
    if info is None:
        raise HTTPException(status_code=422, detail=f"未找到可用模型: '{normalized}'")

    expected_type = model_type or info.model_type
    if info.model_type != expected_type:
        raise HTTPException(status_code=422, detail=f"模型 {normalized} 不是 {expected_type} 类型")

    if user.role == "superadmin":
        return info

    mode = _group_mode(user, "models")
    if mode == "none":
        raise HTTPException(status_code=403, detail=f"当前角色无权使用 {expected_type} 模型")
    if mode == "selected" and normalized not in _group_allowed(user, "models"):
        raise HTTPException(status_code=403, detail=f"当前角色无权使用模型: '{normalized}'")
    return info


def resolve_role_default_model_spec(
    user: User,
    *,
    model_type: str,
    fallback: str | None = None,
) -> str | None:
    if user.role == "superadmin":
        return fallback

    mode = _group_mode(user, "models")
    if mode == "none":
        raise HTTPException(status_code=403, detail=f"当前角色无权使用 {model_type} 模型")
    if mode == "all":
        return fallback

    defaults = _resource_payload(user).get("models", {}).get("defaults") or {}
    default_spec = defaults.get(model_type)
    if not isinstance(default_spec, str) or not default_spec.strip():
        raise HTTPException(status_code=422, detail=f"当前角色未配置默认 {model_type} 模型")
    assert_model_spec_allowed(user, default_spec, model_type=model_type)
    return default_spec


def filter_tool_metadata_for_user(user: User, tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [tool for tool in tools if isinstance(tool, dict) and tool.get("slug")]
    if user.role == "superadmin":
        return items

    knowledge_tools = [tool for tool in items if tool["slug"] in KNOWLEDGE_BASE_TOOL_SLUGS]
    mode = _group_mode(user, "tools")
    if mode == "none":
        return knowledge_tools
    if mode == "all":
        return items

    allowed = _group_allowed(user, "tools")
    return [tool for tool in items if tool["slug"] in KNOWLEDGE_BASE_TOOL_SLUGS or str(tool.get("slug")) in allowed]


def list_tool_slugs_for_user(user: User) -> list[str]:
    return [item["slug"] for item in filter_tool_metadata_for_user(user, get_tool_metadata())]


def assert_tool_slugs_allowed(user: User, slugs: Iterable[Any]) -> list[str]:
    normalized = _normalize_strings(slugs)
    known = {tool["slug"] for tool in get_tool_metadata() if tool.get("slug")}
    unknown = [slug for slug in normalized if slug not in known]
    if unknown:
        raise HTTPException(status_code=422, detail=f"存在未知工具: {', '.join(unknown)}")

    allowed = set(list_tool_slugs_for_user(user))
    forbidden = [slug for slug in normalized if slug not in allowed]
    if forbidden:
        raise HTTPException(status_code=403, detail=f"当前角色无权使用工具: {', '.join(forbidden)}")
    return normalized


def filter_mcp_servers_for_user(user: User, servers: Iterable[MCPServer]) -> list[MCPServer]:
    items = [server for server in servers if getattr(server, "slug", None)]
    if user.role == "superadmin":
        return items

    mode = _group_mode(user, "mcp_servers")
    if mode == "none":
        return []
    if mode == "all":
        return items

    allowed = _group_allowed(user, "mcp_servers")
    return [server for server in items if str(server.slug) in allowed]


def list_mcp_slugs_for_user(user: User, servers: Iterable[MCPServer]) -> list[str]:
    return [str(server.slug) for server in filter_mcp_servers_for_user(user, servers)]


def resolve_allowed_mcp_slugs(user: User, enabled_slugs: Iterable[str]) -> set[str]:
    enabled = set(_normalize_strings(enabled_slugs))
    if user.role == "superadmin":
        return enabled

    mode = _group_mode(user, "mcp_servers")
    if mode == "none":
        return set()
    if mode == "all":
        return enabled
    return enabled & _group_allowed(user, "mcp_servers")


def assert_mcp_slugs_allowed(
    user: User,
    slugs: Iterable[Any],
    *,
    existing_slugs: Iterable[str],
    enabled_slugs: Iterable[str],
) -> list[str]:
    normalized = _normalize_strings(slugs)
    existing = set(_normalize_strings(existing_slugs))
    enabled = set(_normalize_strings(enabled_slugs))

    unknown = [slug for slug in normalized if slug not in existing]
    if unknown:
        raise HTTPException(status_code=422, detail=f"存在未知 MCP 服务: {', '.join(unknown)}")

    unavailable = [slug for slug in normalized if slug not in enabled]
    if unavailable:
        raise HTTPException(status_code=422, detail=f"MCP 服务不可用: {', '.join(unavailable)}")

    allowed = resolve_allowed_mcp_slugs(user, enabled)
    forbidden = [slug for slug in normalized if slug not in allowed]
    if forbidden:
        raise HTTPException(status_code=403, detail=f"当前角色无权使用 MCP 服务: {', '.join(forbidden)}")
    return normalized


def filter_resource_accessible_skills(
    user: User,
    skills: Iterable[Skill],
    *,
    enabled_mcp_slugs: Iterable[str],
) -> list[Skill]:
    items = [item for item in skills if isinstance(getattr(item, "slug", None), str) and item.slug]
    if not items:
        return []

    allowed_tool_slugs = set(list_tool_slugs_for_user(user))
    known_tool_slugs = {tool["slug"] for tool in get_tool_metadata() if tool.get("slug")}
    enabled_mcps = set(_normalize_strings(enabled_mcp_slugs))
    allowed_mcp_slugs = resolve_allowed_mcp_slugs(user, enabled_mcps)

    skill_map = {item.slug: item for item in items}
    memo: dict[str, bool] = {}
    visiting: set[str] = set()

    def is_allowed(slug: str) -> bool:
        cached = memo.get(slug)
        if cached is not None:
            return cached
        if slug in visiting:
            memo[slug] = False
            return False

        item = skill_map.get(slug)
        if item is None:
            memo[slug] = False
            return False

        visiting.add(slug)
        try:
            tool_deps = _normalize_strings(item.tool_dependencies or [])
            if any(dep not in known_tool_slugs or dep not in allowed_tool_slugs for dep in tool_deps):
                memo[slug] = False
                return False

            mcp_deps = _normalize_strings(item.mcp_dependencies or [])
            if any(dep not in enabled_mcps or dep not in allowed_mcp_slugs for dep in mcp_deps):
                memo[slug] = False
                return False

            for dep_slug in _normalize_strings(item.skill_dependencies or []):
                if dep_slug not in skill_map or not is_allowed(dep_slug):
                    memo[slug] = False
                    return False

            memo[slug] = True
            return True
        finally:
            visiting.discard(slug)

    return [item for item in items if is_allowed(item.slug)]
