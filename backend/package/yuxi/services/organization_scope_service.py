from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import (
    Department,
    DepartmentAdminAssignment,
    DepartmentClosure,
    User,
    UserDepartmentMembership,
)


def _int_set(values: Iterable | None) -> set[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


async def hydrate_user_organization_scope(db: AsyncSession, user: User) -> User:
    """加载当前用户的组织成员路径和显式管理子树，供同步判权函数复用。"""
    membership_result = await db.execute(
        select(UserDepartmentMembership.department_id, DepartmentClosure.ancestor_id)
        .join(Department, Department.id == UserDepartmentMembership.department_id)
        .join(DepartmentClosure, DepartmentClosure.descendant_id == UserDepartmentMembership.department_id)
        .where(
            UserDepartmentMembership.user_id == user.id,
            UserDepartmentMembership.status == "active",
            Department.status == "active",
        )
    )
    paths: dict[int, set[int]] = {}
    for department_id, ancestor_id in membership_result.all():
        paths.setdefault(int(department_id), set()).add(int(ancestor_id))

    if not paths and user.department_id is not None:
        fallback_result = await db.execute(
            select(DepartmentClosure.ancestor_id).where(DepartmentClosure.descendant_id == user.department_id)
        )
        ancestors = {int(value) for value in fallback_result.scalars().all()}
        paths[int(user.department_id)] = ancestors or {int(user.department_id)}

    managed_result = await db.execute(
        select(DepartmentClosure.descendant_id)
        .join(
            DepartmentAdminAssignment,
            DepartmentAdminAssignment.department_id == DepartmentClosure.ancestor_id,
        )
        .join(Department, Department.id == DepartmentClosure.descendant_id)
        .where(
            DepartmentAdminAssignment.user_id == user.id,
            DepartmentAdminAssignment.status == "active",
            Department.status == "active",
        )
    )

    user.organization_membership_paths = list(paths.values())
    user.organization_department_ids = set(paths)
    user.managed_department_ids = {int(value) for value in managed_result.scalars().all()}
    return user


def share_config_allows_user(user: User | dict, share_config: dict | None) -> bool:
    """按 v1 精确部门或 v2 子树继承规则判断资源访问。"""
    config = share_config or {}
    role = user.get("role") if isinstance(user, dict) else user.role
    if role == "superadmin":
        return True

    uid = str(user.get("uid") if isinstance(user, dict) else user.uid or "")
    if uid and uid in {str(value) for value in config.get("user_uids") or []}:
        return True

    access_level = config.get("access_level", "global")
    if access_level == "global":
        return True
    if access_level == "user":
        return False
    if access_level != "department":
        return False

    if isinstance(user, dict):
        department_ids = _int_set(user.get("organization_department_ids"))
        ancestor_paths = [_int_set(path) for path in user.get("organization_membership_paths") or []]
        fallback_department_id = user.get("department_id")
    else:
        department_ids = _int_set(getattr(user, "organization_department_ids", None))
        ancestor_paths = [_int_set(path) for path in getattr(user, "organization_membership_paths", [])]
        fallback_department_id = user.department_id

    if not department_ids and fallback_department_id is not None:
        department_ids = {int(fallback_department_id)}
    if not ancestor_paths:
        ancestor_paths = [{department_id} for department_id in department_ids]

    include_ids = _int_set(config.get("department_ids"))
    if int(config.get("org_scope_version") or 1) < 2:
        return bool(department_ids & include_ids)

    exclude_ids = _int_set(config.get("excluded_department_ids"))
    return any(bool(path & include_ids) and not bool(path & exclude_ids) for path in ancestor_paths)


def user_can_manage_department(user: User, department_id: int) -> bool:
    if user.role == "superadmin":
        return True
    return int(department_id) in _int_set(getattr(user, "managed_department_ids", None))


def user_can_manage_user(user: User, target_user: User) -> bool:
    if user.role == "superadmin":
        return True
    target_department_ids = _int_set(getattr(target_user, "organization_department_ids", None))
    if not target_department_ids and target_user.department_id is not None:
        target_department_ids = {int(target_user.department_id)}
    return bool(target_department_ids & _int_set(getattr(user, "managed_department_ids", None)))


async def validate_v2_share_config(db: AsyncSession, share_config: dict) -> None:
    if int(share_config.get("org_scope_version") or 1) < 2:
        return

    include_ids = _int_set(share_config.get("department_ids"))
    exclude_ids = _int_set(share_config.get("excluded_department_ids"))
    all_ids = include_ids | exclude_ids
    if not include_ids:
        raise ValueError("部门共享至少需要选择一个组织节点")

    department_result = await db.execute(
        select(Department.id).where(Department.id.in_(all_ids), Department.status == "active")
    )
    if {int(value) for value in department_result.scalars().all()} != all_ids:
        raise ValueError("组织授权包含不存在或已停用的节点")

    if not exclude_ids:
        return

    relation_result = await db.execute(
        select(DepartmentClosure.ancestor_id, DepartmentClosure.descendant_id).where(
            DepartmentClosure.ancestor_id.in_(include_ids | exclude_ids),
            DepartmentClosure.descendant_id.in_(include_ids | exclude_ids),
        )
    )
    relations = {(int(ancestor), int(descendant)) for ancestor, descendant in relation_result.all()}
    if any(not any((included, excluded) in relations for included in include_ids) for excluded in exclude_ids):
        raise ValueError("排除节点必须位于已授权组织的子树中")
    if any((excluded, included) in relations for excluded in exclude_ids for included in include_ids):
        raise ValueError("不能在已排除的组织子树内重新授权下级节点")
