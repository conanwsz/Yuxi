"""Role resource-access catalog, normalization, and authorization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import MCPServer, ModelProvider, Role, User

RESOURCE_ACCESS_MODES = {"all", "selected", "none"}
MODEL_RESOURCE_TYPES = ("chat", "embedding", "rerank")


def get_tool_metadata(*args, **kwargs):
    """Load the agent tool registry lazily to avoid service import cycles."""
    from yuxi.agents.toolkits.service import get_tool_metadata as load_tool_metadata

    return load_tool_metadata(*args, **kwargs)


class ResourceAccessValidationError(ValueError):
    """Raised when role resource_access payload is structurally invalid."""


class ResourceAccessPermissionError(PermissionError):
    """Raised when a resolved role cannot access the requested resource."""


def default_resource_access_none() -> dict[str, Any]:
    return {
        "models": {"mode": "none", "allowed": [], "defaults": {}},
        "tools": {"mode": "none", "allowed": []},
        "mcp_servers": {"mode": "none", "allowed": []},
    }


def default_resource_access_all() -> dict[str, Any]:
    return {
        "models": {"mode": "all", "allowed": [], "defaults": {}},
        "tools": {"mode": "all", "allowed": []},
        "mcp_servers": {"mode": "all", "allowed": []},
    }


def normalize_stored_resource_access(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    normalized = default_resource_access_none()
    normalized["models"] = _normalize_stored_model_group(data.get("models"))
    normalized["tools"] = _normalize_stored_basic_group(data.get("tools"))
    normalized["mcp_servers"] = _normalize_stored_basic_group(data.get("mcp_servers"))
    return normalized


def _normalize_stored_model_group(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    mode = str(payload.get("mode") or "none")
    if mode not in RESOURCE_ACCESS_MODES:
        mode = "none"
    defaults_payload = payload.get("defaults")
    defaults = {
        model_type: spec
        for model_type, spec in (defaults_payload.items() if isinstance(defaults_payload, Mapping) else [])
        if model_type in MODEL_RESOURCE_TYPES and isinstance(spec, str) and spec.strip()
    }
    return {
        "mode": mode,
        "allowed": _normalize_string_list(payload.get("allowed")),
        "defaults": {model_type: defaults[model_type] for model_type in MODEL_RESOURCE_TYPES if model_type in defaults},
    }


def _normalize_stored_basic_group(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    mode = str(payload.get("mode") or "none")
    if mode not in RESOURCE_ACCESS_MODES:
        mode = "none"
    return {"mode": mode, "allowed": _normalize_string_list(payload.get("allowed"))}


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return sorted(normalized)


async def build_resource_catalog(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    return {
        "models": await _build_model_catalog(db),
        "tools": _build_tool_catalog(),
        "mcp_servers": await _build_mcp_server_catalog(db),
    }


async def _build_model_catalog(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(ModelProvider).order_by(ModelProvider.provider_id.asc()))
    providers = list(result.scalars().all())
    items: list[dict[str, Any]] = []
    for provider in providers:
        for model in provider.enabled_models or []:
            if not isinstance(model, Mapping):
                continue
            model_id = str(model.get("id") or "").strip()
            model_type = str(model.get("type") or "").strip()
            if not model_id or model_type not in MODEL_RESOURCE_TYPES:
                continue
            items.append(
                {
                    "key": f"{provider.provider_id}:{model_id}",
                    "provider_id": provider.provider_id,
                    "provider_name": provider.display_name,
                    "model_id": model_id,
                    "name": str(model.get("display_name") or model_id),
                    "description": model.get("description"),
                    "type": model_type,
                    "enabled": bool(provider.is_enabled),
                    "builtin": bool(provider.is_builtin),
                }
            )
    return sorted(items, key=lambda item: (item["type"], item["provider_id"], item["model_id"]))


def _build_tool_catalog() -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "key": tool["slug"],
                "name": tool.get("name", tool["slug"]),
                "description": tool.get("description"),
                "category": tool.get("category"),
                "tags": tool.get("tags") or [],
                "enabled": True,
            }
            for tool in get_tool_metadata()
        ],
        key=lambda item: (str(item["category"] or ""), item["key"]),
    )


async def _build_mcp_server_catalog(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(MCPServer).order_by(MCPServer.enabled.desc(), MCPServer.slug.asc()))
    servers = list(result.scalars().all())
    return [
        {
            "key": server.slug,
            "name": server.name,
            "description": server.description,
            "transport": server.transport,
            "enabled": bool(server.enabled),
            "builtin": server.created_by == "system",
        }
        for server in servers
    ]


def normalize_resource_access(
    payload: Any,
    *,
    catalog: Mapping[str, list[dict[str, Any]]],
    existing: Any | None = None,
) -> dict[str, Any]:
    if payload is None:
        return default_resource_access_none()
    if not isinstance(payload, Mapping):
        raise ResourceAccessValidationError("resource_access 必须是对象")

    existing_access = normalize_stored_resource_access(existing)
    model_catalog = {item["key"]: item for item in catalog.get("models", [])}
    tool_catalog = {item["key"]: item for item in catalog.get("tools", [])}
    mcp_catalog = {item["key"]: item for item in catalog.get("mcp_servers", [])}

    normalized = default_resource_access_none()
    normalized["models"] = _normalize_model_group(
        payload.get("models"),
        existing_group=existing_access["models"],
        model_catalog=model_catalog,
    )
    normalized["tools"] = _normalize_basic_group(
        payload.get("tools"),
        group_name="工具",
        existing_group=existing_access["tools"],
        known_keys=set(tool_catalog),
    )
    normalized["mcp_servers"] = _normalize_basic_group(
        payload.get("mcp_servers"),
        group_name="MCP 服务",
        existing_group=existing_access["mcp_servers"],
        known_keys=set(mcp_catalog),
    )
    return normalized


def _normalize_model_group(
    payload: Any,
    *,
    existing_group: Mapping[str, Any],
    model_catalog: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResourceAccessValidationError("resource_access.models 必须是对象")

    mode = str(payload.get("mode") or "").strip()
    if mode not in RESOURCE_ACCESS_MODES:
        raise ResourceAccessValidationError("resource_access.models.mode 必须是 all、selected 或 none")

    allowed = _normalize_string_list(payload.get("allowed"))
    defaults_payload = payload.get("defaults")
    if defaults_payload is None:
        defaults_payload = {}
    if not isinstance(defaults_payload, Mapping):
        raise ResourceAccessValidationError("resource_access.models.defaults 必须是对象")

    existing_unknown_allowed = {key for key in existing_group.get("allowed", []) if key not in model_catalog}
    existing_unknown_defaults = {
        spec
        for spec in (existing_group.get("defaults", {}) or {}).values()
        if isinstance(spec, str) and spec not in model_catalog
    }
    _reject_unknown_resources(
        allowed,
        known_keys=set(model_catalog),
        permitted_unknown=existing_unknown_allowed,
        resource_name="模型",
    )

    defaults: dict[str, str] = {}
    for model_type in MODEL_RESOURCE_TYPES:
        raw_value = defaults_payload.get(model_type)
        if raw_value is None or raw_value == "":
            continue
        if not isinstance(raw_value, str):
            raise ResourceAccessValidationError(f"resource_access.models.defaults.{model_type} 必须是字符串")
        spec = raw_value.strip()
        if not spec:
            continue
        if spec not in model_catalog and spec not in existing_unknown_defaults:
            raise ResourceAccessValidationError(f"未知模型: {spec}")
        defaults[model_type] = spec

    if mode == "none":
        return {"mode": "none", "allowed": [], "defaults": {}}
    if mode == "all":
        _validate_model_defaults(defaults, allowed_specs=None, model_catalog=model_catalog)
        return {
            "mode": "all",
            "allowed": [],
            "defaults": {
                model_type: defaults[model_type]
                for model_type in MODEL_RESOURCE_TYPES
                if model_type in defaults
            },
        }

    if not allowed:
        raise ResourceAccessValidationError("resource_access.models.allowed 不能为空")
    _validate_model_defaults(defaults, allowed_specs=allowed, model_catalog=model_catalog)
    return {
        "mode": "selected",
        "allowed": allowed,
        "defaults": {
            model_type: defaults[model_type]
            for model_type in MODEL_RESOURCE_TYPES
            if model_type in defaults
        },
    }


def _validate_model_defaults(
    defaults: Mapping[str, str],
    *,
    allowed_specs: list[str] | None,
    model_catalog: Mapping[str, dict[str, Any]],
) -> None:
    allowed_set = set(allowed_specs or [])
    for model_type, spec in defaults.items():
        if model_type not in MODEL_RESOURCE_TYPES:
            raise ResourceAccessValidationError(f"不支持的默认模型类型: {model_type}")
        model_info = model_catalog.get(spec)
        if model_info and model_info["type"] != model_type:
            raise ResourceAccessValidationError(f"{spec} 不是 {model_type} 类型模型")
        if allowed_specs is not None and spec not in allowed_set:
            raise ResourceAccessValidationError(f"{model_type} 默认模型必须包含在 allowed 列表中: {spec}")

    if allowed_specs is not None:
        required_types = {model_catalog[spec]["type"] for spec in allowed_specs if spec in model_catalog}
        missing = [model_type for model_type in required_types if model_type not in defaults]
        if missing:
            joined = "、".join(missing)
            raise ResourceAccessValidationError(f"selected 模式必须为每种已授权模型类型配置默认模型: {joined}")


def _normalize_basic_group(
    payload: Any,
    *,
    group_name: str,
    existing_group: Mapping[str, Any],
    known_keys: set[str],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResourceAccessValidationError(f"resource_access.{_resource_field_name(group_name)} 必须是对象")
    mode = str(payload.get("mode") or "").strip()
    if mode not in RESOURCE_ACCESS_MODES:
        raise ResourceAccessValidationError(
            f"resource_access.{_resource_field_name(group_name)}.mode 必须是 all、selected 或 none"
        )
    allowed = _normalize_string_list(payload.get("allowed"))
    existing_unknown = {key for key in existing_group.get("allowed", []) if key not in known_keys}
    _reject_unknown_resources(
        allowed,
        known_keys=known_keys,
        permitted_unknown=existing_unknown,
        resource_name=group_name,
    )
    if mode == "none":
        return {"mode": "none", "allowed": []}
    if mode == "all":
        return {"mode": "all", "allowed": []}
    if not allowed:
        raise ResourceAccessValidationError(
            f"resource_access.{_resource_field_name(group_name)}.allowed 不能为空"
        )
    return {"mode": "selected", "allowed": allowed}


def _resource_field_name(group_name: str) -> str:
    return {
        "工具": "tools",
        "MCP 服务": "mcp_servers",
    }.get(group_name, "models")


def _reject_unknown_resources(
    values: list[str],
    *,
    known_keys: set[str],
    permitted_unknown: set[str],
    resource_name: str,
) -> None:
    unknown = sorted({value for value in values if value not in known_keys and value not in permitted_unknown})
    if unknown:
        raise ResourceAccessValidationError(f"未知{resource_name}: {', '.join(unknown)}")


def resolve_role_resource_access(role: Role | None) -> dict[str, Any]:
    if role is None:
        return default_resource_access_none()
    if role.key == "superadmin":
        return default_resource_access_all()
    return normalize_stored_resource_access(role.resource_access)


async def resolve_user_resource_access(db: AsyncSession, user: User) -> dict[str, Any]:
    if user.role == "superadmin":
        return default_resource_access_all()
    result = await db.execute(select(Role).where(Role.key == user.role))
    role = result.scalar_one_or_none()
    return resolve_role_resource_access(role)


async def attach_user_resource_access(db: AsyncSession, user: User) -> User:
    user.resource_access = await resolve_user_resource_access(db, user)
    return user


def get_default_model_for_type(resource_access: Any, model_type: str) -> str | None:
    normalized = normalize_stored_resource_access(resource_access)
    if model_type not in MODEL_RESOURCE_TYPES:
        return None
    spec = normalized["models"]["defaults"].get(model_type)
    return spec or None


def is_model_allowed(resource_access: Any, spec: str) -> bool:
    return _is_resource_allowed(normalize_stored_resource_access(resource_access)["models"], spec)


def is_tool_allowed(resource_access: Any, slug: str) -> bool:
    return _is_resource_allowed(normalize_stored_resource_access(resource_access)["tools"], slug)


def is_mcp_server_allowed(resource_access: Any, slug: str) -> bool:
    return _is_resource_allowed(normalize_stored_resource_access(resource_access)["mcp_servers"], slug)


def assert_model_allowed(resource_access: Any, spec: str) -> str:
    if not is_model_allowed(resource_access, spec):
        raise ResourceAccessPermissionError(f"当前角色无权使用模型: {spec}")
    return spec


def assert_tool_allowed(resource_access: Any, slug: str) -> str:
    if not is_tool_allowed(resource_access, slug):
        raise ResourceAccessPermissionError(f"当前角色无权使用工具: {slug}")
    return slug


def assert_mcp_server_allowed(resource_access: Any, slug: str) -> str:
    if not is_mcp_server_allowed(resource_access, slug):
        raise ResourceAccessPermissionError(f"当前角色无权使用 MCP 服务: {slug}")
    return slug


def _is_resource_allowed(group: Mapping[str, Any], key: str) -> bool:
    mode = group.get("mode")
    if mode == "all":
        return True
    if mode == "none":
        return False
    return key in set(group.get("allowed") or [])
