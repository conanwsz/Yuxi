"""组织架构 API 集成测试。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from yuxi.services.oidc_organization_service import OIDCOrganizationService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_department(test_client, headers, *, name, parent_id=None):
    response = await test_client.post(
        "/api/departments",
        json={"name": name, "parent_id": parent_id},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _archive_and_delete(test_client, headers, department_id):
    archive = await test_client.post(f"/api/departments/{department_id}/archive", headers=headers)
    if archive.status_code not in {200, 409, 404}:
        pytest.fail(archive.text)
    if archive.status_code != 404:
        deleted = await test_client.delete(f"/api/departments/{department_id}", headers=headers)
        assert deleted.status_code in {200, 404, 409}, deleted.text


async def test_organization_hierarchy_memberships_and_archive_rules(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    root_a = root_b = child_a = child_b = same_a = same_b = None
    user_id = None
    try:
        root_a = await _create_department(test_client, admin_headers, name=f"公司A-{suffix}")
        root_b = await _create_department(test_client, admin_headers, name=f"公司B-{suffix}")

        clear_local_root = await test_client.put(
            f"/api/departments/{root_a['id']}",
            json={"local_name": None},
            headers=admin_headers,
        )
        assert clear_local_root.status_code == 422, clear_local_root.text

        child_a = await _create_department(test_client, admin_headers, name=f"研发-{suffix}", parent_id=root_a["id"])
        child_b = await _create_department(test_client, admin_headers, name=f"产品-{suffix}", parent_id=root_a["id"])
        same_a = await _create_department(test_client, admin_headers, name="同名部门", parent_id=child_a["id"])
        same_b = await _create_department(test_client, admin_headers, name="同名部门", parent_id=root_b["id"])

        tree_response = await test_client.get("/api/departments/tree", headers=admin_headers)
        assert tree_response.status_code == 200, tree_response.text
        roots = tree_response.json()
        root_a_tree = next(item for item in roots if item["id"] == root_a["id"])
        assert {item["id"] for item in root_a_tree["children"]} >= {child_a["id"], child_b["id"]}
        assert same_a["path_label"].endswith(f"研发-{suffix} / 同名部门")
        assert same_b["path_label"].endswith(f"公司B-{suffix} / 同名部门")

        user_response = await test_client.post(
            "/api/auth/users",
            json={
                "username": f"组织用户_{suffix}",
                "password": "RouterUser123!",
                "role": "user",
                "primary_department_id": child_a["id"],
                "part_time_department_ids": [child_b["id"]],
            },
            headers=admin_headers,
        )
        assert user_response.status_code == 200, user_response.text
        user = user_response.json()
        user_id = user["id"]
        assert user["primary_department"]["department_id"] == child_a["id"]
        assert [item["department_id"] for item in user["part_time_departments"]] == [child_b["id"]]

        cross_root_membership = await test_client.put(
            f"/api/auth/users/{user_id}",
            json={"part_time_department_ids": [same_b["id"]]},
            headers=admin_headers,
        )
        assert cross_root_membership.status_code == 422, cross_root_membership.text

        cross_root_move = await test_client.post(
            f"/api/departments/{child_a['id']}/move",
            json={"parent_id": root_b["id"]},
            headers=admin_headers,
        )
        assert cross_root_move.status_code == 422, cross_root_move.text

        archive_with_member = await test_client.post(f"/api/departments/{child_a['id']}/archive", headers=admin_headers)
        assert archive_with_member.status_code == 409, archive_with_member.text

        move_user = await test_client.put(
            f"/api/auth/users/{user_id}",
            json={
                "primary_department_id": child_b["id"],
                "part_time_department_ids": [],
            },
            headers=admin_headers,
        )
        assert move_user.status_code == 200, move_user.text

        # 仍有启用中的子节点时不能停用父节点。
        archive_with_child = await test_client.post(f"/api/departments/{child_a['id']}/archive", headers=admin_headers)
        assert archive_with_child.status_code == 409, archive_with_child.text

        await _archive_and_delete(test_client, admin_headers, same_a["id"])
        same_a = None
        archive = await test_client.post(f"/api/departments/{child_a['id']}/archive", headers=admin_headers)
        assert archive.status_code == 200, archive.text
        assert archive.json()["status"] == "inactive"
        restore = await test_client.post(f"/api/departments/{child_a['id']}/restore", headers=admin_headers)
        assert restore.status_code == 200, restore.text
        assert restore.json()["status"] == "active"
    finally:
        if user_id is not None:
            await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        for item in [same_a, same_b, child_a, child_b, root_a, root_b]:
            if item is not None:
                await _archive_and_delete(test_client, admin_headers, item["id"])


async def test_default_department_is_protected(test_client, admin_headers):
    departments_response = await test_client.get("/api/departments", headers=admin_headers)
    assert departments_response.status_code == 200, departments_response.text
    default_department = next(item for item in departments_response.json() if item["is_system"])

    archive = await test_client.post(f"/api/departments/{default_department['id']}/archive", headers=admin_headers)
    assert archive.status_code == 409, archive.text

    deleted = await test_client.delete(f"/api/departments/{default_department['id']}", headers=admin_headers)
    assert deleted.status_code == 400, deleted.text


async def test_oidc_department_api_exposes_names_and_rejects_local_move(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8].upper()
    entity_code = f"API{suffix}"
    created_ids: list[int] = []
    try:
        pg_manager._initialized = False
        pg_manager.async_engine = None
        pg_manager.AsyncSession = None
        pg_manager.initialize()
        await pg_manager.ensure_business_schema()
        async with pg_manager.get_async_session_context() as db:
            leaf = await OIDCOrganizationService.resolve_cnnp_department(
                db,
                entity_code=entity_code,
                entity_short_name="接口测试公司",
                department_code="JXI-BM4605",
                department_name="OIDC 接口部门",
                default_department_name="默认部门",
            )
            leaf_id = leaf.id
            oidc_departments = list(
                (await db.execute(select(Department).where(Department.entity_code == entity_code))).scalars().all()
            )
            created_ids = [
                item.id
                for item in sorted(oidc_departments, key=lambda value: len(value.department_code or ""), reverse=True)
            ]
        await pg_manager.async_engine.dispose()
        pg_manager._initialized = False
        pg_manager.async_engine = None
        pg_manager.AsyncSession = None

        departments_response = await test_client.get("/api/departments", headers=admin_headers)
        assert departments_response.status_code == 200, departments_response.text
        oidc_items = [item for item in departments_response.json() if item["entity_code"] == entity_code]
        leaf_item = next(item for item in oidc_items if item["id"] == leaf_id)
        assert leaf_item["name"] == "OIDC 接口部门"
        assert leaf_item["local_name"] is None
        assert leaf_item["oidc_name"] == "OIDC 接口部门"
        assert leaf_item["department_code"] == "JXI-BM4605"

        local_override = await test_client.put(
            f"/api/departments/{leaf_id}",
            json={"local_name": "本地接口部门"},
            headers=admin_headers,
        )
        assert local_override.status_code == 200, local_override.text
        assert local_override.json()["name"] == "本地接口部门"
        assert local_override.json()["oidc_name"] == "OIDC 接口部门"

        clear_override = await test_client.put(
            f"/api/departments/{leaf_id}",
            json={"local_name": None},
            headers=admin_headers,
        )
        assert clear_override.status_code == 200, clear_override.text
        assert clear_override.json()["name"] == "OIDC 接口部门"
        assert clear_override.json()["local_name"] is None

        root = next(item for item in oidc_items if item["parent_id"] is None)
        move = await test_client.post(
            f"/api/departments/{leaf_id}/move",
            json={"parent_id": root["id"]},
            headers=admin_headers,
        )
        assert move.status_code == 422, move.text

        conflicting_names = await test_client.put(
            f"/api/departments/{leaf_id}",
            json={"name": "名称 A", "local_name": "名称 B"},
            headers=admin_headers,
        )
        assert conflicting_names.status_code == 422, conflicting_names.text
    finally:
        for department_id in created_ids:
            await _archive_and_delete(test_client, admin_headers, department_id)
        if pg_manager.async_engine is not None:
            await pg_manager.async_engine.dispose()
        pg_manager._initialized = False
        pg_manager.async_engine = None
        pg_manager.AsyncSession = None
