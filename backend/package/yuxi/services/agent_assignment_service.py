"""Agent 分配范围查询、校验与受限合并。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.permissions import normalize_permission_config
from yuxi.services.permission_service import has_permission
from yuxi.storage.postgres.models_business import Department, User, UserDepartmentMembership


def _int_set(values) -> set[int]:
    return {int(value) for value in values or []}


def _uid_set(values) -> set[str]:
    return {str(value).strip() for value in values or [] if str(value).strip()}


async def get_agent_assignment_options(db: AsyncSession, user: User) -> dict[str, Any]:
    """返回当前用户可用于 Agent 分配的部门和人员选项。"""

    if user.role == "superadmin":
        allowed_department_ids: set[int] | None = None
        allowed_levels = ["global", "department", "user"]
    else:
        managed_ids = _int_set(getattr(user, "managed_department_ids", set()))
        member_ids = _int_set(getattr(user, "organization_department_ids", set()))
        allowed_department_ids = managed_ids or member_ids
        allowed_levels = ["department", "user"] if managed_ids else ["user"]

    department_stmt = select(Department).where(Department.status == "active")
    if allowed_department_ids is not None:
        department_stmt = department_stmt.where(Department.id.in_(allowed_department_ids))
    department_result = await db.execute(department_stmt.order_by(Department.id.asc()))
    all_departments = list(department_result.scalars().all())
    departments = all_departments if "department" in allowed_levels else []

    membership_user_ids = select(UserDepartmentMembership.user_id).where(UserDepartmentMembership.status == "active")
    if allowed_department_ids is not None:
        membership_user_ids = membership_user_ids.where(
            UserDepartmentMembership.department_id.in_(allowed_department_ids)
        )
    user_result = await db.execute(
        select(User)
        .where(User.id.in_(membership_user_ids), User.is_deleted == 0)
        .order_by(User.username.asc(), User.id.asc())
    )
    users = list(user_result.scalars().all())
    department_names = {department.id: department.name for department in all_departments}

    return {
        "allowed_access_levels": allowed_levels,
        "departments": [{"id": item.id, "name": item.name} for item in departments],
        "users": [
            {
                "uid": item.uid,
                "username": item.username,
                "department_id": (
                    item.department_id
                    if allowed_department_ids is None or item.department_id in allowed_department_ids
                    else None
                ),
                "department_name": (
                    department_names.get(item.department_id)
                    if allowed_department_ids is None or item.department_id in allowed_department_ids
                    else None
                ),
            }
            for item in users
        ],
    }


def assignment_from_share_config(share_config: dict | None) -> dict[str, Any]:
    """将 Agent 共享配置投影为统一分配结构。"""

    config = normalize_permission_config(share_config)
    scope = config.get("read_scope") or {}
    return {
        "global_access": scope.get("access_level") == "global",
        "department_ids": sorted(_int_set(scope.get("department_ids"))),
        "user_uids": sorted(_uid_set(scope.get("user_uids"))),
    }


def share_config_from_assignment(
    *,
    global_access: bool,
    department_ids: set[int],
    user_uids: set[str],
    owner_uid: str,
) -> dict:
    """将统一分配结构转换为 Agent v2 读取范围。"""

    if global_access:
        read_scope = {"access_level": "global", "department_ids": [], "user_uids": []}
    elif department_ids:
        read_scope = {
            "access_level": "department",
            "department_ids": sorted(department_ids),
            "user_uids": sorted(user_uids),
        }
    else:
        resolved_users = user_uids or {owner_uid}
        read_scope = {
            "access_level": "user",
            "department_ids": [],
            "user_uids": sorted(resolved_users),
        }
    return {"version": 2, "read_scope": read_scope, "manage_scope": None}


async def validate_new_agent_assignment(
    db: AsyncSession,
    *,
    user: User,
    share_config: dict | None,
) -> dict:
    """校验新 Agent 的初始分配范围，并返回规范化配置。"""

    owner_uid = str(user.uid or "")
    assignment = (
        assignment_from_share_config(share_config)
        if share_config
        else {
            "global_access": False,
            "department_ids": [],
            "user_uids": [owner_uid],
        }
    )
    if not has_permission(user, "agents.share"):
        if assignment["global_access"] or assignment["department_ids"] or set(assignment["user_uids"]) != {owner_uid}:
            raise ValueError("当前用户无权分配智能体")
        return share_config_from_assignment(
            global_access=False,
            department_ids=set(),
            user_uids={owner_uid},
            owner_uid=owner_uid,
        )

    options = await get_agent_assignment_options(db, user)
    allowed_departments = {int(item["id"]) for item in options["departments"]}
    allowed_users = {str(item["uid"]) for item in options["users"]}
    if assignment["global_access"] and "global" not in options["allowed_access_levels"]:
        raise ValueError("当前用户无权全局分配智能体")
    if not set(assignment["department_ids"]).issubset(allowed_departments):
        raise ValueError("智能体包含超出当前管理范围的部门")
    if not set(assignment["user_uids"]).issubset(allowed_users):
        raise ValueError("智能体包含超出当前可分配范围的用户")
    return share_config_from_assignment(
        global_access=assignment["global_access"],
        department_ids=set(assignment["department_ids"]),
        user_uids=set(assignment["user_uids"]),
        owner_uid=owner_uid,
    )


async def get_editable_agent_assignment(
    db: AsyncSession,
    *,
    user: User,
    share_config: dict,
) -> dict[str, Any]:
    """返回操作者可编辑的分配切片和范围外锁定数量。"""

    options = await get_agent_assignment_options(db, user)
    assignment = assignment_from_share_config(share_config)
    if user.role == "superadmin":
        editable_departments = set(assignment["department_ids"])
        editable_users = set(assignment["user_uids"])
    else:
        allowed_departments = {int(item["id"]) for item in options["departments"]}
        allowed_users = {str(item["uid"]) for item in options["users"]}
        editable_departments = set(assignment["department_ids"]) & allowed_departments
        editable_users = set(assignment["user_uids"]) & allowed_users

    return {
        "global_access": assignment["global_access"],
        "department_ids": sorted(editable_departments),
        "user_uids": sorted(editable_users),
        "locked_department_count": len(set(assignment["department_ids"]) - editable_departments),
        "locked_user_count": len(set(assignment["user_uids"]) - editable_users),
        "options": options,
    }


async def merge_agent_assignment(
    db: AsyncSession,
    *,
    user: User,
    current_share_config: dict,
    global_access: bool,
    department_ids: list[int],
    user_uids: list[str],
    owner_uid: str,
) -> dict:
    """替换操作者可编辑的分配切片，同时保留范围外既有分配。"""

    options = await get_agent_assignment_options(db, user)
    allowed_departments = {int(item["id"]) for item in options["departments"]}
    allowed_users = {str(item["uid"]) for item in options["users"]}
    requested_departments = _int_set(department_ids)
    requested_users = _uid_set(user_uids)
    if global_access and "global" not in options["allowed_access_levels"]:
        raise ValueError("当前用户无权全局分配智能体")
    if not requested_departments.issubset(allowed_departments):
        raise ValueError("智能体包含超出当前管理范围的部门")
    if not requested_users.issubset(allowed_users):
        raise ValueError("智能体包含超出当前可分配范围的用户")

    current = assignment_from_share_config(current_share_config)
    if user.role == "superadmin" or global_access:
        merged_departments = requested_departments
        merged_users = requested_users
    else:
        merged_departments = (set(current["department_ids"]) - allowed_departments) | requested_departments
        merged_users = (set(current["user_uids"]) - allowed_users) | requested_users
    return share_config_from_assignment(
        global_access=global_access,
        department_ids=merged_departments,
        user_uids=merged_users,
        owner_uid=owner_uid,
    )
