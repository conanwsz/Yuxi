"""Integration coverage for the global role and permission matrix APIs."""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _require_superadmin(test_client, headers):
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    if response.json()["role"] != "superadmin":
        pytest.skip("Role administration requires a superadmin integration account.")


async def test_role_crud_assignment_and_in_use_delete_conflict(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)
    suffix = uuid.uuid4().hex[:8]
    role_key = f"reviewer_{suffix}"
    user_id = None

    catalog_response = await test_client.get("/api/roles/permissions", headers=admin_headers)
    assert catalog_response.status_code == 200, catalog_response.text
    catalog_keys = {
        permission["key"]
        for group in catalog_response.json()["groups"]
        for permission in group["permissions"]
    }
    assert {"users.read", "knowledge.read", "knowledge.documents.manage"} <= catalog_keys

    create_response = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={
            "key": role_key,
            "name": f"审阅员 {suffix}",
            "description": "pytest role",
            "permissions": ["knowledge.read"],
        },
    )
    assert create_response.status_code == 201, create_response.text

    try:
        update_response = await test_client.put(
            f"/api/roles/{role_key}",
            headers=admin_headers,
            json={"permissions": ["knowledge.read", "knowledge.documents.manage"]},
        )
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["role"]["permissions"] == [
            "knowledge.documents.manage",
            "knowledge.read",
        ]

        user_response = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"role_{suffix}", "password": f"Pw!{suffix}", "role": role_key},
        )
        assert user_response.status_code == 200, user_response.text
        user_id = user_response.json()["id"]
        assert user_response.json()["role_name"] == f"审阅员 {suffix}"

        conflict_response = await test_client.delete(f"/api/roles/{role_key}", headers=admin_headers)
        assert conflict_response.status_code == 409, conflict_response.text
    finally:
        if user_id is not None:
            await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        delete_response = await test_client.delete(f"/api/roles/{role_key}", headers=admin_headers)
        assert delete_response.status_code in {200, 404}, delete_response.text


async def test_role_api_rejects_unknown_permission_and_protects_system_roles(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)

    invalid_response = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={"key": f"invalid_{uuid.uuid4().hex[:8]}", "name": "非法权限角色", "permissions": ["nope"]},
    )
    assert invalid_response.status_code == 422, invalid_response.text

    update_response = await test_client.put(
        "/api/roles/superadmin", headers=admin_headers, json={"permissions": []}
    )
    assert update_response.status_code == 403, update_response.text

    delete_response = await test_client.delete("/api/roles/admin", headers=admin_headers)
    assert delete_response.status_code == 403, delete_response.text


async def test_role_resource_access_catalog_and_crud(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)
    suffix = uuid.uuid4().hex[:8]
    role_key = f"resource_{suffix}"

    resources_response = await test_client.get("/api/roles/resources", headers=admin_headers)
    assert resources_response.status_code == 200, resources_response.text
    resources = resources_response.json()
    assert {"models", "tools", "mcp_servers"} <= set(resources)
    assert resources["tools"], "expected at least one built-in tool in resource catalog"

    selected_tool = resources["tools"][0]["key"]
    selected_mcp = resources["mcp_servers"][0]["key"] if resources["mcp_servers"] else None
    selected_chat_model = next((item for item in resources["models"] if item["type"] == "chat"), None)
    if selected_chat_model is None:
        pytest.skip("This test requires at least one chat model in /api/roles/resources.")

    create_response = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={
            "key": role_key,
            "name": f"资源角色 {suffix}",
            "permissions": [],
            "resource_access": {
                "models": {
                    "mode": "selected",
                    "allowed": [selected_chat_model["key"]],
                    "defaults": {"chat": selected_chat_model["key"]},
                },
                "tools": {"mode": "selected", "allowed": [selected_tool]},
                "mcp_servers": {
                    "mode": "selected" if selected_mcp else "none",
                    "allowed": [selected_mcp] if selected_mcp else [],
                },
            },
        },
    )
    assert create_response.status_code == 201, create_response.text
    assert create_response.json()["role"]["resource_access"]["models"]["defaults"]["chat"] == selected_chat_model["key"]

    try:
        invalid_update = await test_client.put(
            f"/api/roles/{role_key}",
            headers=admin_headers,
            json={
                "resource_access": {
                    "models": {
                        "mode": "selected",
                        "allowed": [selected_chat_model["key"]],
                        "defaults": {},
                    },
                    "tools": {"mode": "selected", "allowed": [selected_tool]},
                    "mcp_servers": {"mode": "none", "allowed": []},
                }
            },
        )
        assert invalid_update.status_code == 422, invalid_update.text

        unknown_tool_update = await test_client.put(
            f"/api/roles/{role_key}",
            headers=admin_headers,
            json={
                "resource_access": {
                    "models": {
                        "mode": "selected",
                        "allowed": [selected_chat_model["key"]],
                        "defaults": {"chat": selected_chat_model["key"]},
                    },
                    "tools": {"mode": "selected", "allowed": ["missing_tool"]},
                    "mcp_servers": {"mode": "none", "allowed": []},
                }
            },
        )
        assert unknown_tool_update.status_code == 422, unknown_tool_update.text
    finally:
        delete_response = await test_client.delete(f"/api/roles/{role_key}", headers=admin_headers)
        assert delete_response.status_code in {200, 404}, delete_response.text


async def test_jwt_and_api_key_resolve_updated_role_permissions_on_every_request(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)
    suffix = uuid.uuid4().hex[:8]
    role_key = f"live_{suffix}"
    user_id = None
    api_key_id = None

    create_role = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={"key": role_key, "name": f"即时权限 {suffix}", "permissions": []},
    )
    assert create_role.status_code == 201, create_role.text

    try:
        password = f"Pw!{suffix}"
        create_user = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"live_{suffix}", "password": password, "role": role_key},
        )
        assert create_user.status_code == 200, create_user.text
        user = create_user.json()
        user_id = user["id"]

        login = await test_client.post(
            "/api/auth/token", data={"username": user["uid"], "password": password}
        )
        assert login.status_code == 200, login.text
        jwt_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        create_key = await test_client.post(
            "/api/user/apikey/", headers=jwt_headers, json={"name": "role-live-test"}
        )
        assert create_key.status_code == 200, create_key.text
        api_key_id = create_key.json()["api_key"]["id"]
        api_key_headers = {"Authorization": f"Bearer {create_key.json()['secret']}"}

        for headers in (jwt_headers, api_key_headers):
            denied = await test_client.get("/api/dashboard/conversations", headers=headers)
            assert denied.status_code == 403, denied.text

        update_role = await test_client.put(
            f"/api/roles/{role_key}",
            headers=admin_headers,
            json={"permissions": ["dashboard.read"]},
        )
        assert update_role.status_code == 200, update_role.text

        for headers in (jwt_headers, api_key_headers):
            allowed = await test_client.get("/api/dashboard/conversations", headers=headers)
            assert allowed.status_code == 200, allowed.text
    finally:
        if api_key_id is not None:
            await test_client.delete(f"/api/user/apikey/{api_key_id}", headers=admin_headers)
        if user_id is not None:
            await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        await test_client.delete(f"/api/roles/{role_key}", headers=admin_headers)
