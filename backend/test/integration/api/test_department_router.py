"""组织架构 API 集成测试。"""

from __future__ import annotations

import uuid

import pytest

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
