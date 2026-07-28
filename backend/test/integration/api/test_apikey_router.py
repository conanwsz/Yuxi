"""
Integration tests for API Key router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

API_KEYS_PATH = "/api/user/apikey/"
# 受登录保护的轻量端点，用于验证 Bearer 鉴权（API Key / JWT）是否生效，无需执行智能体
PROTECTED_PATH = "/api/agent"


async def test_list_api_keys_requires_auth(test_client):
    """List API keys should require authentication."""
    response = await test_client.get(API_KEYS_PATH)
    assert response.status_code == 401


async def test_list_api_keys_requires_admin(test_client, admin_headers):
    """List API keys should require admin privileges."""
    response = await test_client.get(API_KEYS_PATH, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_keys" in data
    assert "total" in data


async def test_create_api_key(test_client, admin_headers):
    """Admin should be able to create a new API key."""
    payload = {
        "name": "Test API Key",
    }
    response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_key" in data
    assert "secret" in data
    assert data["api_key"]["name"] == "Test API Key"
    assert data["api_key"]["key_prefix"].startswith("yxkey_")
    assert data["secret"].startswith(data["api_key"]["key_prefix"])


async def test_get_api_key(test_client, admin_headers):
    """Admin should be able to get a single API key."""
    # First create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Get Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Then retrieve it
    response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["id"] == created["id"]
    assert data["api_key"]["name"] == "Get Test"


async def test_update_api_key(test_client, admin_headers):
    """Admin should be able to update an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Update Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Update it
    response = await test_client.put(
        f"{API_KEYS_PATH}{created['id']}",
        json={"name": "Updated Name", "is_enabled": False},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["name"] == "Updated Name"
    assert data["api_key"]["is_enabled"] is False


async def test_delete_api_key(test_client, admin_headers):
    """Admin should be able to delete an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Delete Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Delete it
    response = await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    # Verify it's gone
    get_response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert get_response.status_code == 404


async def test_regenerate_api_key(test_client, admin_headers):
    """Admin should be able to regenerate an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Regenerate Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    original_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    # Regenerate it
    response = await test_client.post(f"{API_KEYS_PATH}{created['id']}/regenerate", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "secret" in data
    assert data["secret"] != original_secret
    assert data["api_key"]["key_prefix"] != original_secret[:12]


async def test_api_key_auth_protected_endpoint(test_client, admin_headers):
    """Test that API Key can be used to authenticate to a protected endpoint via Bearer token."""
    # Create an API key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Auth Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    api_key_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    try:
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert response.status_code == 200, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_api_key_auth_requires_valid_key(test_client):
    """Test that invalid API Key is rejected."""
    # Call protected endpoint with invalid API Key
    response = await test_client.get(
        PROTECTED_PATH,
        headers={"Authorization": "Bearer yxkey_invalid_key_that_does_not_exist"},
    )
    assert response.status_code == 401, response.text


async def test_api_key_auth_requires_bearer_prefix(test_client, admin_headers):
    """Test that API Key must be prefixed with 'Bearer '."""
    # Create an API key
    admin_response = await test_client.post(API_KEYS_PATH, json={"name": "Prefix Test"}, headers=admin_headers)
    assert admin_response.status_code == 200
    api_key_secret = admin_response.json()["secret"]
    created = admin_response.json()["api_key"]

    try:
        # Call without Bearer prefix should fail
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": api_key_secret},  # Missing "Bearer " prefix
        )
        assert response.status_code == 401, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_jwt_still_works_after_apikey_auth(test_client, admin_headers):
    """Test that JWT Bearer tokens still work after API Key changes."""
    # Call protected endpoint with JWT Bearer token (admin_headers)
    response = await test_client.get(PROTECTED_PATH, headers=admin_headers)
    assert response.status_code == 200, response.text


async def test_api_key_auto_binds_to_current_user(test_client, admin_headers):
    """Test that API Key created without user_id is auto-bound to creator."""
    # Create API key as admin
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Auto Bind Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    try:
        # Verify user_id is set (auto-bound to admin)
        assert created["user_id"] is not None, "API Key should be auto-bound to creator"

        # Verify the key can be used for auth
        api_key_secret = create_response.json()["secret"]
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert response.status_code == 200, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_list_api_keys_requires_apikey_manage_for_regular_user(test_client, standard_user):
    """普通用户（无 apikey.manage）不能列出 API Key。"""
    response = await test_client.get(API_KEYS_PATH, headers=standard_user["headers"])
    assert response.status_code == 403, response.text
    assert "apikey.manage" in response.text or "缺少权限" in response.text


async def test_create_api_key_requires_apikey_manage_for_regular_user(test_client, standard_user):
    """普通用户（无 apikey.manage）不能创建 API Key。"""
    response = await test_client.post(API_KEYS_PATH, json={"name": "Forbidden"}, headers=standard_user["headers"])
    assert response.status_code == 403, response.text


async def test_api_key_auth_blocked_when_user_lacks_apikey_invoke(test_client, standard_user, admin_headers):
    """用户的 API Key 在关联用户没有 apikey.invoke 时被 403 拒绝。

    测试流程：
    1. admin 给 standard_user 角色加上 apikey.manage + apikey.invoke（让他能创建 key 并调 API）
    2. standard_user 创建一个 API Key
    3. 该 Key 调用 /api/agent 应当成功（因为 standard_user 此时已有 apikey.invoke）
    4. admin 收回 standard_user 角色的 apikey.invoke
    5. 再次用该 Key 调用 /api/agent 应当返回 403
    """
    import time

    role_key = "user"
    # 0. 读取 user 角色原始权限（通过列表接口拿）
    list_resp = await test_client.get("/api/roles", headers=admin_headers)
    assert list_resp.status_code == 200, list_resp.text
    user_role = next((r for r in list_resp.json().get("roles", []) if r.get("key") == role_key), None)
    assert user_role is not None, "user role not found"
    original_perms = list(user_role.get("permissions") or [])

    # 1. 加权限：apikey.manage + apikey.invoke
    augmented_perms = list(set(original_perms) | {"apikey.manage", "apikey.invoke"})
    update_resp = await test_client.put(
        f"/api/roles/{role_key}",
        json={"permissions": augmented_perms},
        headers=admin_headers,
    )
    assert update_resp.status_code == 200, update_resp.text

    api_key_id = None
    try:
        # 2. 创建 Key
        create_resp = await test_client.post(API_KEYS_PATH, json={"name": "Perm Test"}, headers=standard_user["headers"])
        assert create_resp.status_code == 200, create_resp.text
        api_key_secret = create_resp.json()["secret"]
        api_key_id = create_resp.json()["api_key"]["id"]

        # 3. 此时 Key 可用
        ok_resp = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert ok_resp.status_code == 200, ok_resp.text

        # 4. 收回 apikey.invoke
        revoke_resp = await test_client.put(
            f"/api/roles/{role_key}",
            json={
                "permissions": [p for p in augmented_perms if p not in {"apikey.manage", "apikey.invoke"}],
            },
            headers=admin_headers,
        )
        assert revoke_resp.status_code == 200, revoke_resp.text

        # 5. 再次调用应 403
        # 给缓存留点时间，避免 role 缓存陈旧
        time.sleep(0.1)
        denied_resp = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert denied_resp.status_code == 403, denied_resp.text
        assert "apikey.invoke" in denied_resp.text

    finally:
        # 清理：删除测试 key + 还原 user 角色权限到测试前状态
        if api_key_id is not None:
            try:
                await test_client.delete(f"{API_KEYS_PATH}{api_key_id}", headers=admin_headers)
            except Exception:
                pass
        await test_client.put(
            f"/api/roles/{role_key}",
            json={"permissions": original_perms},
            headers=admin_headers,
        )
