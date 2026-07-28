from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import (
    Department,
    DepartmentAdminAssignment,
    DepartmentClosure,
    User,
    UserDepartmentMembership,
)
from yuxi.utils.datetime_utils import utc_now_naive


class OrganizationService:
    @staticmethod
    async def list_departments(db: AsyncSession, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        query = select(Department).order_by(Department.sort_order.asc(), Department.name.asc(), Department.id.asc())
        if not include_inactive:
            query = query.where(Department.status == "active")
        departments = list((await db.execute(query)).scalars().all())
        if not departments:
            return []

        direct_count_result = await db.execute(
            select(UserDepartmentMembership.department_id, func.count(func.distinct(UserDepartmentMembership.user_id)))
            .where(UserDepartmentMembership.status == "active")
            .group_by(UserDepartmentMembership.department_id)
        )
        direct_counts = {int(department_id): int(count) for department_id, count in direct_count_result.all()}
        total_count_result = await db.execute(
            select(DepartmentClosure.ancestor_id, func.count(func.distinct(UserDepartmentMembership.user_id)))
            .join(
                UserDepartmentMembership,
                UserDepartmentMembership.department_id == DepartmentClosure.descendant_id,
            )
            .where(UserDepartmentMembership.status == "active")
            .group_by(DepartmentClosure.ancestor_id)
        )
        total_counts = {int(department_id): int(count) for department_id, count in total_count_result.all()}

        by_id = {department.id: department for department in departments}
        path_cache: dict[int, tuple[list[str], int]] = {}

        def resolve_path(department_id: int) -> tuple[list[str], int]:
            if department_id in path_cache:
                return path_cache[department_id]
            seen: set[int] = set()
            names: list[str] = []
            current = by_id.get(department_id)
            root_id = department_id
            while current is not None and current.id not in seen:
                seen.add(current.id)
                names.append(current.name)
                root_id = current.id
                current = by_id.get(current.parent_id) if current.parent_id is not None else None
            names.reverse()
            path_cache[department_id] = (names, root_id)
            return names, root_id

        result: list[dict[str, Any]] = []
        for department in departments:
            path, root_id = resolve_path(department.id)
            item = department.to_dict()
            item.update(
                {
                    "root_id": root_id,
                    "depth": max(0, len(path) - 1),
                    "path": path,
                    "path_label": " / ".join(path),
                    "direct_user_count": direct_counts.get(department.id, 0),
                    "total_user_count": total_counts.get(department.id, 0),
                    "user_count": direct_counts.get(department.id, 0),
                }
            )
            result.append(item)
        return result

    @classmethod
    async def department_tree(cls, db: AsyncSession, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        items = await cls.list_departments(db, include_inactive=include_inactive)
        nodes = {item["id"]: {**item, "children": []} for item in items}
        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent = nodes.get(node["parent_id"])
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)
        return roots

    @staticmethod
    async def get_department(db: AsyncSession, department_id: int) -> Department | None:
        return await db.get(Department, department_id)

    @staticmethod
    async def root_id(db: AsyncSession, department_id: int) -> int | None:
        result = await db.execute(
            select(DepartmentClosure.ancestor_id)
            .where(DepartmentClosure.descendant_id == department_id)
            .order_by(DepartmentClosure.depth.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    @staticmethod
    async def _ensure_sibling_name_available(
        db: AsyncSession, *, name: str, parent_id: int | None, exclude_id: int | None = None
    ) -> None:
        query = select(Department.id).where(func.lower(Department.name) == name.lower())
        query = (
            query.where(Department.parent_id.is_(None))
            if parent_id is None
            else query.where(Department.parent_id == parent_id)
        )
        if exclude_id is not None:
            query = query.where(Department.id != exclude_id)
        if (await db.execute(query)).scalar_one_or_none() is not None:
            raise ValueError("同一上级下已存在同名组织节点")

    @classmethod
    async def create_department(
        cls,
        db: AsyncSession,
        *,
        name: str,
        description: str | None,
        parent_id: int | None,
        sort_order: int = 0,
    ) -> Department:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 50:
            raise ValueError("组织名称长度必须在 1-50 个字符之间")
        if parent_id is not None:
            parent = await db.get(Department, parent_id)
            if parent is None or parent.status != "active":
                raise ValueError("上级组织不存在或已停用")
        await cls._ensure_sibling_name_available(db, name=normalized_name, parent_id=parent_id)

        department = Department(
            name=normalized_name,
            description=description.strip() if description else None,
            parent_id=parent_id,
            sort_order=sort_order,
            status="active",
            is_system=False,
        )
        db.add(department)
        await db.flush()
        db.add(DepartmentClosure(ancestor_id=department.id, descendant_id=department.id, depth=0))
        if parent_id is not None:
            ancestor_result = await db.execute(
                select(DepartmentClosure.ancestor_id, DepartmentClosure.depth).where(
                    DepartmentClosure.descendant_id == parent_id
                )
            )
            for ancestor_id, depth in ancestor_result.all():
                db.add(
                    DepartmentClosure(
                        ancestor_id=int(ancestor_id),
                        descendant_id=department.id,
                        depth=int(depth) + 1,
                    )
                )
        await db.commit()
        await db.refresh(department)
        return department

    @classmethod
    async def update_department(
        cls,
        db: AsyncSession,
        department: Department,
        *,
        name: str | None = None,
        description: str | None = None,
        sort_order: int | None = None,
    ) -> Department:
        if name is not None:
            normalized_name = name.strip()
            if not normalized_name or len(normalized_name) > 50:
                raise ValueError("组织名称长度必须在 1-50 个字符之间")
            await cls._ensure_sibling_name_available(
                db, name=normalized_name, parent_id=department.parent_id, exclude_id=department.id
            )
            department.name = normalized_name
        if description is not None:
            department.description = description.strip() or None
        if sort_order is not None:
            department.sort_order = sort_order
        department.updated_at = utc_now_naive()
        await db.commit()
        await db.refresh(department)
        return department

    @classmethod
    async def move_department(cls, db: AsyncSession, department: Department, new_parent_id: int) -> Department:
        if department.is_system or department.parent_id is None:
            raise ValueError("系统节点和根主体不允许移动")
        if department.id == new_parent_id:
            raise ValueError("组织节点不能移动到自身")
        new_parent = await db.get(Department, new_parent_id)
        if new_parent is None or new_parent.status != "active":
            raise ValueError("目标上级不存在或已停用")
        old_root = await cls.root_id(db, department.id)
        new_root = await cls.root_id(db, new_parent_id)
        if old_root != new_root:
            raise ValueError("组织节点不允许跨主体移动")
        descendant_check = await db.execute(
            select(DepartmentClosure.ancestor_id).where(
                DepartmentClosure.ancestor_id == department.id,
                DepartmentClosure.descendant_id == new_parent_id,
            )
        )
        if descendant_check.scalar_one_or_none() is not None:
            raise ValueError("组织节点不能移动到自身后代")
        await cls._ensure_sibling_name_available(
            db, name=department.name, parent_id=new_parent_id, exclude_id=department.id
        )

        await db.execute(
            text(
                """
                DELETE FROM department_closure
                WHERE descendant_id IN (
                    SELECT descendant_id FROM department_closure WHERE ancestor_id = :node_id
                )
                  AND ancestor_id NOT IN (
                    SELECT descendant_id FROM department_closure WHERE ancestor_id = :node_id
                )
                """
            ),
            {"node_id": department.id},
        )
        await db.execute(
            text(
                """
                INSERT INTO department_closure (ancestor_id, descendant_id, depth)
                SELECT parent_path.ancestor_id,
                       subtree.descendant_id,
                       parent_path.depth + subtree.depth + 1
                FROM department_closure AS parent_path
                CROSS JOIN department_closure AS subtree
                WHERE parent_path.descendant_id = :new_parent_id
                  AND subtree.ancestor_id = :node_id
                ON CONFLICT (ancestor_id, descendant_id)
                DO UPDATE SET depth = EXCLUDED.depth
                """
            ),
            {"new_parent_id": new_parent_id, "node_id": department.id},
        )
        department.parent_id = new_parent_id
        department.updated_at = utc_now_naive()
        await db.commit()
        await db.refresh(department)
        return department

    @staticmethod
    async def archive_department(db: AsyncSession, department: Department) -> Department:
        if department.is_system:
            raise ValueError("系统组织节点不允许停用")
        active_child = await db.execute(
            select(Department.id).where(Department.parent_id == department.id, Department.status == "active").limit(1)
        )
        if active_child.scalar_one_or_none() is not None:
            raise ValueError("请先停用或迁移全部下级组织")
        active_member = await db.execute(
            select(UserDepartmentMembership.id)
            .where(
                UserDepartmentMembership.department_id == department.id,
                UserDepartmentMembership.status == "active",
            )
            .limit(1)
        )
        if active_member.scalar_one_or_none() is not None:
            raise ValueError("请先迁移该组织的全部成员")
        department.status = "inactive"
        department.archived_at = utc_now_naive()
        department.updated_at = utc_now_naive()
        await db.commit()
        await db.refresh(department)
        return department

    @staticmethod
    async def restore_department(db: AsyncSession, department: Department) -> Department:
        if department.status == "active":
            return department
        if department.parent_id is not None:
            parent = await db.get(Department, department.parent_id)
            if parent is None or parent.status != "active":
                raise ValueError("请先恢复上级组织")
        department.status = "active"
        department.archived_at = None
        department.updated_at = utc_now_naive()
        await db.commit()
        await db.refresh(department)
        return department

    @classmethod
    async def set_user_memberships(
        cls,
        db: AsyncSession,
        *,
        user: User,
        primary_department_id: int,
        part_time_department_ids: list[int],
    ) -> None:
        department_ids = [int(primary_department_id), *[int(value) for value in part_time_department_ids]]
        if len(set(department_ids)) != len(department_ids):
            raise ValueError("主部门和兼职部门不能重复")
        department_result = await db.execute(
            select(Department.id, Department.is_system).where(
                Department.id.in_(department_ids), Department.status == "active"
            )
        )
        departments = {int(department_id): bool(is_system) for department_id, is_system in department_result.all()}
        if set(department_ids) != set(departments):
            raise ValueError("组织成员关系包含不存在或已停用的节点")

        roots = {await cls.root_id(db, department_id) for department_id in department_ids}
        if len(roots) != 1:
            raise ValueError("主部门和兼职部门必须属于同一公司主体")
        if departments[primary_department_id] and part_time_department_ids:
            raise ValueError("默认部门用户不能配置兼职部门")

        existing_result = await db.execute(
            select(UserDepartmentMembership).where(UserDepartmentMembership.user_id == user.id)
        )
        existing = {item.department_id: item for item in existing_result.scalars().all()}
        desired = {primary_department_id: "primary"} | {
            department_id: "part_time" for department_id in part_time_department_ids
        }
        for item in existing.values():
            item.status = "inactive"
        await db.flush()
        for department_id, membership_type in desired.items():
            item = existing.get(department_id)
            if item is None:
                db.add(
                    UserDepartmentMembership(
                        user_id=user.id,
                        department_id=department_id,
                        membership_type=membership_type,
                        status="active",
                    )
                )
            else:
                item.membership_type = membership_type
                item.status = "active"
                item.updated_at = utc_now_naive()
        user.department_id = primary_department_id
        await db.commit()

    @staticmethod
    async def user_memberships(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
        result = await db.execute(
            select(UserDepartmentMembership, Department)
            .join(Department, Department.id == UserDepartmentMembership.department_id)
            .where(UserDepartmentMembership.user_id == user_id, UserDepartmentMembership.status == "active")
            .order_by(UserDepartmentMembership.membership_type.asc(), Department.name.asc())
        )
        departments = await OrganizationService.list_departments(db)
        by_id = {item["id"]: item for item in departments}
        return [
            {
                "department_id": department.id,
                "name": department.name,
                "path": by_id.get(department.id, {}).get("path", [department.name]),
                "path_label": by_id.get(department.id, {}).get("path_label", department.name),
                "membership_type": membership.membership_type,
            }
            for membership, department in result.all()
        ]

    @staticmethod
    async def set_admin_assignments(
        db: AsyncSession, *, user: User, department_ids: list[int], granted_by: int | None
    ) -> None:
        normalized_ids = sorted(set(int(value) for value in department_ids))
        if normalized_ids:
            result = await db.execute(
                select(Department.id).where(Department.id.in_(normalized_ids), Department.status == "active")
            )
            if {int(value) for value in result.scalars().all()} != set(normalized_ids):
                raise ValueError("管理范围包含不存在或已停用的组织节点")

        existing_result = await db.execute(
            select(DepartmentAdminAssignment).where(DepartmentAdminAssignment.user_id == user.id)
        )
        existing = {item.department_id: item for item in existing_result.scalars().all()}
        for item in existing.values():
            if item.department_id not in normalized_ids:
                item.status = "inactive"
        for department_id in normalized_ids:
            item = existing.get(department_id)
            if item is None:
                db.add(
                    DepartmentAdminAssignment(
                        user_id=user.id,
                        department_id=department_id,
                        status="active",
                        granted_by=granted_by,
                    )
                )
            else:
                item.status = "active"
                item.granted_by = granted_by
                item.updated_at = utc_now_naive()
        await db.commit()

    @staticmethod
    async def admin_assignments(db: AsyncSession, user_id: int) -> list[int]:
        result = await db.execute(
            select(DepartmentAdminAssignment.department_id).where(
                DepartmentAdminAssignment.user_id == user_id,
                DepartmentAdminAssignment.status == "active",
            )
        )
        return [int(value) for value in result.scalars().all()]
