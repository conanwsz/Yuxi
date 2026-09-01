"""OIDC 组织编码解析、部门树同步与用户归属同步。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.organization_service import OrganizationService
from yuxi.storage.postgres.models_business import Department, DepartmentClosure, User, UserDepartmentMembership
from yuxi.utils.datetime_utils import utc_now_naive

DEPARTMENT_CODE_PATTERN = re.compile(r"^(?:(?P<prefix>[A-Z0-9]+(?:-[A-Z0-9]+)*)-)?BM(?P<digits>\d+)$")
ENTITY_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")


def department_code_chain(value: Any) -> list[str] | None:
    """把部门编码解析为从一级到当前节点的完整编码链。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    match = DEPARTMENT_CODE_PATTERN.fullmatch(normalized)
    if match is None:
        return None

    digits = match.group("digits")
    if len(digits) < 2 or len(digits) % 2:
        return None
    prefix = match.group("prefix")
    code_prefix = f"{prefix}-BM" if prefix else "BM"
    return [f"{code_prefix}{digits[:length]}" for length in range(2, len(digits) + 1, 2)]


def normalize_entity_code(value: Any) -> str | None:
    """返回可作为公司主体稳定键的规范化编码。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 64 or ENTITY_CODE_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def _normalize_oidc_name(value: Any) -> str | None:
    """清理 OIDC 名称，空值保留为占位状态。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:100] or None


class OIDCOrganizationService:
    """按 OIDC 稳定编码维护公司主体、部门树和用户主部门。"""

    @classmethod
    async def resolve_cnnp_department(
        cls,
        db: AsyncSession,
        *,
        entity_code: Any,
        entity_short_name: Any,
        department_code: Any,
        department_name: Any,
        default_department_name: str,
    ) -> Department:
        """解析 CNNP 组织字段；编码无效时返回配置的默认部门。"""
        normalized_entity_code = normalize_entity_code(entity_code)
        code_chain = department_code_chain(department_code)
        if normalized_entity_code is None or code_chain is None:
            return await cls._resolve_default_department(db, default_department_name)

        for attempt in range(2):
            try:
                department = await cls._resolve_coded_hierarchy(
                    db,
                    entity_code=normalized_entity_code,
                    entity_short_name=_normalize_oidc_name(entity_short_name),
                    code_chain=code_chain,
                    department_name=_normalize_oidc_name(department_name),
                )
                await db.commit()
                await db.refresh(department)
                return department
            except IntegrityError:
                await db.rollback()
                if attempt == 1:
                    raise

        raise RuntimeError("OIDC 组织同步未返回部门")

    @classmethod
    async def _resolve_coded_hierarchy(
        cls,
        db: AsyncSession,
        *,
        entity_code: str,
        entity_short_name: str | None,
        code_chain: list[str],
        department_name: str | None,
    ) -> Department:
        """在同一事务中幂等补齐主体、部门节点和 closure 路径。"""
        parent = await cls._upsert_root(db, entity_code, entity_short_name)
        for index, code in enumerate(code_chain):
            oidc_name = department_name if index == len(code_chain) - 1 else None
            parent = await cls._upsert_department(db, parent, entity_code, code, oidc_name)
        return parent

    @classmethod
    async def _upsert_root(
        cls,
        db: AsyncSession,
        entity_code: str,
        oidc_name: str | None,
    ) -> Department:
        result = await db.execute(
            select(Department).where(
                func.upper(Department.entity_code) == entity_code,
                Department.department_code.is_(None),
            )
        )
        matches = list(result.scalars().all())
        if len(matches) > 1:
            raise ValueError(f"公司编码 {entity_code} 对应多个组织节点")
        department = matches[0] if matches else None

        if department is None and oidc_name:
            department = await cls._find_adoptable_local_department(db, parent_id=None, oidc_name=oidc_name)
        if department is None:
            department = Department(
                name=None,
                oidc_name=oidc_name,
                entity_code=entity_code,
                department_code=None,
                parent_id=None,
                status="active",
            )
            db.add(department)
            await db.flush()
            db.add(DepartmentClosure(ancestor_id=department.id, descendant_id=department.id, depth=0))
        else:
            department.entity_code = entity_code
            await cls._ensure_expected_parent(db, department, None)

        cls._refresh_oidc_fields(department, oidc_name)
        return department

    @classmethod
    async def _upsert_department(
        cls,
        db: AsyncSession,
        parent: Department,
        entity_code: str,
        department_code: str,
        oidc_name: str | None,
    ) -> Department:
        result = await db.execute(
            select(Department).where(
                func.upper(Department.entity_code) == entity_code,
                func.upper(Department.department_code) == department_code,
            )
        )
        matches = list(result.scalars().all())
        if len(matches) > 1:
            raise ValueError(f"部门编码 {department_code} 对应多个组织节点")
        department = matches[0] if matches else None

        if department is None and oidc_name:
            department = await cls._find_adoptable_local_department(db, parent_id=parent.id, oidc_name=oidc_name)
        if department is None:
            department = Department(
                name=None,
                oidc_name=oidc_name,
                entity_code=entity_code,
                department_code=department_code,
                parent_id=parent.id,
                status="active",
            )
            db.add(department)
            await db.flush()
            await cls._insert_closure_paths(db, department.id, parent.id)
        else:
            department.entity_code = entity_code
            department.department_code = department_code
            await cls._ensure_expected_parent(db, department, parent.id)

        cls._refresh_oidc_fields(department, oidc_name)
        return department

    @staticmethod
    async def _find_adoptable_local_department(
        db: AsyncSession,
        *,
        parent_id: int | None,
        oidc_name: str,
    ) -> Department | None:
        query = select(Department).where(
            Department.entity_code.is_(None),
            Department.department_code.is_(None),
            Department.status == "active",
            func.lower(Department.name) == oidc_name.lower(),
        )
        query = (
            query.where(Department.parent_id.is_(None))
            if parent_id is None
            else query.where(Department.parent_id == parent_id)
        )
        matches = list((await db.execute(query.limit(2).with_for_update())).scalars().all())
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _refresh_oidc_fields(department: Department, oidc_name: str | None) -> None:
        if oidc_name is not None:
            department.oidc_name = oidc_name
        department.status = "active"
        department.archived_at = None
        department.updated_at = utc_now_naive()

    @classmethod
    async def _ensure_expected_parent(
        cls,
        db: AsyncSession,
        department: Department,
        parent_id: int | None,
    ) -> None:
        if department.parent_id == parent_id:
            return

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
        department.parent_id = parent_id
        if parent_id is not None:
            await cls._insert_parent_closure_paths(db, department.id, parent_id)

    @classmethod
    async def _insert_closure_paths(cls, db: AsyncSession, department_id: int, parent_id: int) -> None:
        db.add(DepartmentClosure(ancestor_id=department_id, descendant_id=department_id, depth=0))
        await db.flush()
        await cls._insert_parent_closure_paths(db, department_id, parent_id)

    @staticmethod
    async def _insert_parent_closure_paths(db: AsyncSession, department_id: int, parent_id: int) -> None:
        await db.execute(
            text(
                """
                INSERT INTO department_closure (ancestor_id, descendant_id, depth)
                SELECT parent_path.ancestor_id,
                       subtree.descendant_id,
                       parent_path.depth + subtree.depth + 1
                FROM department_closure AS parent_path
                CROSS JOIN department_closure AS subtree
                WHERE parent_path.descendant_id = :parent_id
                  AND subtree.ancestor_id = :node_id
                ON CONFLICT (ancestor_id, descendant_id)
                DO UPDATE SET depth = EXCLUDED.depth
                """
            ),
            {"parent_id": parent_id, "node_id": department_id},
        )

    @classmethod
    async def _resolve_default_department(cls, db: AsyncSession, configured_name: str) -> Department:
        """按既有配置语义查找或创建默认根部门。"""
        name = (configured_name or "默认部门").strip()[:50] or "默认部门"
        result = await db.execute(
            select(Department).where(Department.parent_id.is_(None), func.lower(Department.name) == name.lower())
        )
        department = result.scalar_one_or_none()
        if department is None:
            department = Department(name=name, description=f"{name}部门", status="active")
            db.add(department)
            try:
                await db.flush()
                db.add(DepartmentClosure(ancestor_id=department.id, descendant_id=department.id, depth=0))
                await db.commit()
                await db.refresh(department)
                return department
            except IntegrityError:
                await db.rollback()
                result = await db.execute(
                    select(Department).where(
                        Department.parent_id.is_(None), func.lower(Department.name) == name.lower()
                    )
                )
                department = result.scalar_one()
        if department.status != "active":
            department.status = "active"
            department.archived_at = None
            await db.commit()
            await db.refresh(department)
        return department

    @staticmethod
    async def sync_user_primary_department(
        db: AsyncSession,
        *,
        user: User,
        department: Department,
    ) -> None:
        """同步主部门，并仅保留新主体内的兼职部门。"""
        target_root_id = await OrganizationService.root_id(db, department.id)
        part_time_result = await db.execute(
            select(UserDepartmentMembership.department_id).where(
                UserDepartmentMembership.user_id == user.id,
                UserDepartmentMembership.membership_type == "part_time",
                UserDepartmentMembership.status == "active",
            )
        )
        preserved_part_time_ids: list[int] = []
        for department_id in part_time_result.scalars().all():
            normalized_department_id = int(department_id)
            if normalized_department_id == department.id:
                continue
            if await OrganizationService.root_id(db, normalized_department_id) == target_root_id:
                preserved_part_time_ids.append(normalized_department_id)

        await OrganizationService.set_user_memberships(
            db,
            user=user,
            primary_department_id=department.id,
            part_time_department_ids=preserved_part_time_ids,
        )
