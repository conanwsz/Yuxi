"""OIDC 组织层级与成员同步的 PostgreSQL 集成测试。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select

from yuxi.services.oidc_organization_service import OIDCOrganizationService
from yuxi.services.organization_service import OrganizationService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, DepartmentClosure, User, UserDepartmentMembership

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


async def _ensure_database() -> None:
    # 集成 schema fixture 使用独立 anyio loop，这里在当前测试 loop 重建连接池。
    pg_manager._initialized = False
    pg_manager.async_engine = None
    pg_manager.AsyncSession = None
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()


async def _close_database() -> None:
    await pg_manager.async_engine.dispose()
    pg_manager._initialized = False
    pg_manager.async_engine = None
    pg_manager.AsyncSession = None


async def _cleanup_entity(entity_code: str, user_id: int | None = None) -> None:
    async with pg_manager.get_async_session_context() as db:
        if user_id is not None:
            await db.execute(delete(UserDepartmentMembership).where(UserDepartmentMembership.user_id == user_id))
            user = await db.get(User, user_id)
            if user is not None:
                await db.delete(user)
                await db.flush()

        departments = list(
            (await db.execute(select(Department).where(Department.entity_code == entity_code))).scalars().all()
        )
        for department in sorted(departments, key=lambda item: len(item.department_code or ""), reverse=True):
            await db.execute(
                delete(DepartmentClosure).where(
                    (DepartmentClosure.ancestor_id == department.id)
                    | (DepartmentClosure.descendant_id == department.id)
                )
            )
            await db.delete(department)


async def test_resolve_cnnp_department_builds_placeholders_and_preserves_local_name():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"T{suffix}"

    try:
        async with pg_manager.get_async_session_context() as db:
            leaf = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="测试公司",
                department_code="JXI-BM460503",
                department_name="三级部门",
                default_department_name="默认部门",
            )
            departments = list(
                (
                    await db.execute(
                        select(Department).where(Department.entity_code == entity_code).order_by(Department.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(departments) == 4
            by_code = {item.department_code: item for item in departments}
            assert by_code[None].oidc_name == "测试公司"
            assert by_code["JXI-BM46"].oidc_name is None
            assert by_code["JXI-BM4605"].oidc_name is None
            assert leaf.department_code == "JXI-BM460503"
            assert leaf.oidc_name == "三级部门"

            leaf.name = "本地三级部门"
            await db.commit()
            await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="测试公司新名称",
                department_code="JXI-BM460503",
                department_name="OIDC 三级部门新名称",
                default_department_name="默认部门",
            )
            await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="测试公司新名称",
                department_code="JXI-BM4605",
                department_name="二级部门",
                default_department_name="默认部门",
            )

            refreshed_leaf = await db.get(Department, leaf.id)
            assert refreshed_leaf is not None
            assert refreshed_leaf.name == "本地三级部门"
            assert refreshed_leaf.oidc_name == "OIDC 三级部门新名称"
            assert refreshed_leaf.display_name == "本地三级部门"
            parent = (
                await db.execute(
                    select(Department).where(
                        Department.entity_code == entity_code,
                        Department.department_code == "JXI-BM4605",
                    )
                )
            ).scalar_one()
            assert parent.oidc_name == "二级部门"

            items = await OrganizationService.list_departments(db)
            leaf_item = next(item for item in items if item["id"] == leaf.id)
            assert leaf_item["path"][-3:] == ["JXI-BM46", "二级部门", "本地三级部门"]
    finally:
        await _cleanup_entity(entity_code)
        await _close_database()


async def test_resolve_prefixless_bm_department_code_builds_hierarchy():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"B{suffix}"

    try:
        async with pg_manager.get_async_session_context() as db:
            leaf = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="武汉楚能",
                department_code="BM2203",
                department_name="电极车间",
                default_department_name="默认部门",
            )
            departments = list(
                (await db.execute(select(Department).where(Department.entity_code == entity_code))).scalars().all()
            )

            assert {item.department_code for item in departments} == {None, "BM22", "BM2203"}
            assert leaf.department_code == "BM2203"
            assert leaf.oidc_name == "电极车间"
            parent = next(item for item in departments if item.department_code == "BM22")
            assert leaf.parent_id == parent.id
    finally:
        await _cleanup_entity(entity_code)
        await _close_database()


async def test_sync_user_primary_department_preserves_only_same_root_part_time_memberships():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_a = f"A{suffix}"
    entity_b = f"B{suffix}"
    user_id = None

    try:
        async with pg_manager.get_async_session_context() as db:
            target = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_a,
                entity_short_name="主体 A",
                department_code="JXI-BM46",
                department_name="主部门 A",
                default_department_name="默认部门",
            )
            same_root_part_time = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_a,
                entity_short_name="主体 A",
                department_code="JXI-BM47",
                department_name="兼职部门 A",
                default_department_name="默认部门",
            )
            old_primary = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_b,
                entity_short_name="主体 B",
                department_code="JXI-BM48",
                department_name="主部门 B",
                default_department_name="默认部门",
            )
            cross_root_part_time = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_b,
                entity_short_name="主体 B",
                department_code="JXI-BM49",
                department_name="兼职部门 B",
                default_department_name="默认部门",
            )

            user = User(
                username=f"oidc-org-{suffix}",
                uid=f"oidc_org_{suffix}",
                password_hash="test-only",
                role="user",
                department_id=old_primary.id,
            )
            db.add(user)
            await db.flush()
            user_id = user.id
            db.add_all(
                [
                    UserDepartmentMembership(
                        user_id=user.id,
                        department_id=old_primary.id,
                        membership_type="primary",
                        status="active",
                    ),
                    UserDepartmentMembership(
                        user_id=user.id,
                        department_id=same_root_part_time.id,
                        membership_type="part_time",
                        status="active",
                    ),
                    UserDepartmentMembership(
                        user_id=user.id,
                        department_id=target.id,
                        membership_type="part_time",
                        status="active",
                    ),
                    UserDepartmentMembership(
                        user_id=user.id,
                        department_id=cross_root_part_time.id,
                        membership_type="part_time",
                        status="active",
                    ),
                ]
            )
            await db.commit()

            await OIDCOrganizationService.sync_user_primary_department(db, user=user, department=target)
            memberships = list(
                (
                    await db.execute(
                        select(UserDepartmentMembership).where(
                            UserDepartmentMembership.user_id == user.id,
                            UserDepartmentMembership.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            active = {(item.department_id, item.membership_type) for item in memberships}
            assert active == {
                (target.id, "primary"),
                (same_root_part_time.id, "part_time"),
            }
            assert user.department_id == target.id
    finally:
        await _cleanup_entity(entity_a, user_id)
        await _cleanup_entity(entity_b)
        await _close_database()


async def test_concurrent_cnnp_department_resolution_does_not_create_duplicates():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"C{suffix}"

    async def resolve_once() -> int:
        async with pg_manager.get_async_session_context() as db:
            department = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="并发测试公司",
                department_code="JXI-BM4605",
                department_name="并发测试部门",
                default_department_name="默认部门",
            )
            return department.id

    try:
        department_ids = await asyncio.gather(resolve_once(), resolve_once())
        assert department_ids[0] == department_ids[1]
        async with pg_manager.get_async_session_context() as db:
            departments = list(
                (await db.execute(select(Department).where(Department.entity_code == entity_code))).scalars().all()
            )
            assert len(departments) == 3
    finally:
        await _cleanup_entity(entity_code)
        await _close_database()


async def test_cnnp_resolution_adopts_unique_unencoded_nodes_under_expected_parent():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"L{suffix}"
    root_name = f"存量主体-{suffix}"
    department_name = f"存量部门-{suffix}"

    try:
        async with pg_manager.get_async_session_context() as db:
            root = await OrganizationService.create_department(
                db,
                name=root_name,
                description=None,
                parent_id=None,
            )
            department = await OrganizationService.create_department(
                db,
                name=department_name,
                description=None,
                parent_id=root.id,
            )
            resolved = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name=root_name,
                department_code="JXI-BM46",
                department_name=department_name,
                default_department_name="默认部门",
            )

            assert resolved.id == department.id
            assert resolved.name == department_name
            assert resolved.oidc_name == department_name
            refreshed_root = await db.get(Department, root.id)
            assert refreshed_root is not None
            assert refreshed_root.entity_code == entity_code
            assert refreshed_root.name == root_name
            assert refreshed_root.oidc_name == root_name
    finally:
        await _cleanup_entity(entity_code)
        await _close_database()


async def test_cnnp_resolution_does_not_adopt_archived_local_node():
    await _ensure_database()
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"R{suffix}"
    root_name = f"归档主体-{suffix}"
    archived_root_id = None

    try:
        async with pg_manager.get_async_session_context() as db:
            archived_root = await OrganizationService.create_department(
                db,
                name=root_name,
                description=None,
                parent_id=None,
            )
            archived_root_id = archived_root.id
            archived_root.status = "inactive"
            await db.commit()

            resolved = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name=root_name,
                department_code="JXI-BM46",
                department_name="新部门",
                default_department_name="默认部门",
            )

            assert resolved.id != archived_root_id
            refreshed_archived = await db.get(Department, archived_root_id)
            assert refreshed_archived is not None
            assert refreshed_archived.status == "inactive"
            assert refreshed_archived.entity_code is None
    finally:
        await _cleanup_entity(entity_code)
        if archived_root_id is not None:
            async with pg_manager.get_async_session_context() as db:
                await db.execute(
                    delete(DepartmentClosure).where(
                        (DepartmentClosure.ancestor_id == archived_root_id)
                        | (DepartmentClosure.descendant_id == archived_root_id)
                    )
                )
                archived_root = await db.get(Department, archived_root_id)
                if archived_root is not None:
                    await db.delete(archived_root)
        await _close_database()


async def test_invalid_cnnp_codes_resolve_to_configured_default_department():
    await _ensure_database()
    try:
        async with pg_manager.get_async_session_context() as db:
            department = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=None,
                entity_short_name="无效公司",
                department_code="JXI-BM460",
                department_name="无效部门",
                default_department_name="默认部门",
            )
            assert department.name == "默认部门"
            assert department.entity_code is None
            assert department.department_code is None
    finally:
        await _close_database()
