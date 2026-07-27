"""Central permission catalog and role permission resolution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import Role, User

PERMISSION_GROUPS = [
    {
        "key": "users",
        "name": "用户管理",
        "permissions": [
            ("users.read", "查看用户"),
            ("users.create", "创建用户"),
            ("users.update", "更新用户"),
            ("users.quota.manage", "管理用户额度"),
            ("users.disable", "禁用用户"),
            ("users.enable", "激活用户"),
            ("users.delete", "删除用户"),
            ("users.impersonate", "模拟用户"),
        ],
    },
    {
        "key": "departments",
        "name": "部门管理",
        "permissions": [
            ("departments.read", "查看部门"),
            ("departments.create", "创建部门"),
            ("departments.update", "更新部门"),
            ("departments.delete", "删除部门"),
        ],
    },
    {"key": "dashboard", "name": "仪表盘", "permissions": [("dashboard.read", "查看仪表盘")]},
    {
        "key": "system",
        "name": "系统管理",
        "permissions": [
            ("system.config.read", "查看系统配置"),
            ("system.config.update", "修改系统配置"),
            ("system.logs.read", "查看系统日志"),
            ("system.tasks.manage", "管理后台任务"),
            ("system.schedules.manage", "管理定时任务"),
        ],
    },
    {
        "key": "models",
        "name": "模型管理",
        "permissions": [("models.read", "查看模型"), ("models.manage", "管理模型")],
    },
    {
        "key": "tools",
        "name": "工具管理",
        "permissions": [("tools.read", "查看工具"), ("tools.manage", "管理工具")],
    },
    {
        "key": "mcp",
        "name": "MCP 管理",
        "permissions": [("mcp.read", "查看 MCP"), ("mcp.manage", "管理 MCP")],
    },
    {
        "key": "agents",
        "name": "智能体",
        "permissions": [
            ("agents.read", "查看智能体"),
            ("agents.create", "创建智能体"),
            ("agents.update", "更新智能体"),
            ("agents.delete", "删除智能体"),
            ("agents.share", "共享智能体"),
            ("agents.config.admin", "配置管理员字段"),
        ],
    },
    {
        "key": "skills",
        "name": "Skills",
        "permissions": [
            ("skills.read", "查看 Skill"),
            ("skills.create", "创建 Skill"),
            ("skills.update", "更新 Skill"),
            ("skills.delete", "删除 Skill"),
            ("skills.share", "共享 Skill"),
            ("skills.enable", "启停 Skill"),
        ],
    },
    {
        "key": "knowledge",
        "name": "知识库",
        "permissions": [
            ("knowledge.read", "查看知识库"),
            ("knowledge.create", "创建知识库"),
            ("knowledge.update", "更新知识库"),
            ("knowledge.delete", "删除知识库"),
            ("knowledge.share", "共享知识库"),
            ("knowledge.documents.manage", "管理文档"),
            ("knowledge.graph.manage", "管理图谱"),
            ("knowledge.evaluation.manage", "管理评估"),
        ],
    },
    {
        "key": "apikey",
        "name": "API Key",
        "permissions": [
            ("apikey.manage", "管理 API Key"),
            ("apikey.invoke", "通过 API Key 调用"),
        ],
    },
]

ALL_PERMISSION_KEYS = frozenset(permission[0] for group in PERMISSION_GROUPS for permission in group["permissions"])

ADMIN_PERMISSION_KEYS = ALL_PERMISSION_KEYS - {
    "dashboard.read",
    "users.impersonate",
    "users.delete",
    "departments.create",
    "departments.update",
    "departments.delete",
}

DEFAULT_ROLE_PERMISSIONS = {
    "superadmin": set(ALL_PERMISSION_KEYS),
    "admin": set(ADMIN_PERMISSION_KEYS),
    "user": {
        "system.config.read",
        "mcp.read",
        "agents.read",
        "agents.create",
        "agents.update",
        "agents.delete",
        "agents.share",
        "skills.read",
        "skills.create",
        "skills.update",
        "skills.delete",
        "skills.share",
    },
}

DEFAULT_ROLE_DEFINITIONS = {
    "superadmin": ("超级管理员", "拥有全部系统权限"),
    "admin": ("管理员", "管理本部门及平台资源"),
    "user": ("普通用户", "使用个人与已共享资源"),
}


def permission_catalog() -> list[dict[str, Any]]:
    scoped_groups = {"users", "departments", "agents", "skills", "knowledge"}
    return [
        {
            "key": group["key"],
            "name": group["name"],
            "permissions": [
                {
                    "key": key,
                    "name": name,
                    "description": (
                        f"允许当前角色执行“{name}”；实际数据仍受部门、创建者和共享范围限制。"
                        if group["key"] in scoped_groups
                        else f"允许当前角色使用“{name}”功能。"
                    ),
                }
                for key, name in group["permissions"]
            ],
        }
        for group in PERMISSION_GROUPS
    ]


def validate_permission_keys(permission_keys: Iterable[str]) -> list[str]:
    normalized = sorted(set(permission_keys))
    unknown = sorted(set(normalized) - ALL_PERMISSION_KEYS)
    if unknown:
        raise ValueError(f"未知权限: {', '.join(unknown)}")
    return normalized


def has_permission(user: User, permission: str) -> bool:
    if user.role == "superadmin":
        return permission in ALL_PERMISSION_KEYS
    resolved = getattr(user, "permission_keys", None)
    if resolved is None:
        resolved = DEFAULT_ROLE_PERMISSIONS.get(user.role, set())
    return permission in resolved


def authorization_role(user: User) -> str:
    """Bridge legacy role-gated Agent context fields to the permission matrix."""
    if user.role == "superadmin":
        return "superadmin"
    if has_permission(user, "agents.config.admin"):
        return "admin"
    return "user"


async def resolve_user_permissions(db: AsyncSession, user: User) -> User:
    from yuxi.services.resource_access_service import default_resource_access_none, normalize_stored_resource_access

    result = await db.execute(select(Role).where(Role.key == user.role))
    role = result.scalar_one_or_none()
    user.permission_keys = set(role.permissions or []) if role else set()
    user.resource_access = (
        normalize_stored_resource_access(getattr(role, "resource_access", None))
        if role
        else default_resource_access_none()
    )
    user.role_name = role.name if role else user.role
    return user
