"""
Schedule HTTP API 集成测试 —— 覆盖 CRUD、cron 校验、立即触发、权限门禁与执行历史查询。

依赖：
- docker compose up -d 后 `api-dev` 健康（``/api/system/health`` 通）；
- 集成测试凭据 ``TEST_USERNAME`` / ``TEST_PASSWORD`` 已注入 ``backend/test/.env.test``。

若凭据缺失，conftest 已用 ``pytest.skip`` 处理；测试函数里直接复用 ``admin_headers`` / ``standard_user`` 即可。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


_SCHEDULE_BASE_PAYLOAD = {
    "name": "pytest schedule",
    "description": "integration test",
    "agent_slug": "default-chatbot",
    "query": "ping",
    "cron_expression": "0 0 * * *",
    "timezone": "UTC",
    "enabled": True,
}


async def test_schedule_routes_require_admin(test_client, standard_user):
    """非 admin 访问应被 403 拒绝。"""
    headers = standard_user["headers"]

    list_resp = await test_client.get("/api/schedules", headers=headers)
    assert list_resp.status_code == 403

    detail_resp = await test_client.get(f"/api/schedules/{uuid.uuid4().hex}", headers=headers)
    assert detail_resp.status_code == 403

    fire_resp = await test_client.post(
        f"/api/schedules/{uuid.uuid4().hex}/fire", headers=headers
    )
    assert fire_resp.status_code == 403


async def test_admin_can_create_get_update_delete_schedule(test_client, admin_headers):
    """完整的 CRUD 链路。"""
    # 1) create
    unique_name = f"pytest_schedule_{uuid.uuid4().hex[:8]}"
    payload = {**_SCHEDULE_BASE_PAYLOAD, "name": unique_name}
    create_resp = await test_client.post("/api/schedules", json=payload, headers=admin_headers)
    assert create_resp.status_code == 200, create_resp.text
    schedule = create_resp.json()["schedule"]
    schedule_id = schedule["id"]
    assert schedule["name"] == unique_name
    assert schedule["enabled"] is True
    assert schedule["agent_slug"] == "default-chatbot"
    assert schedule["cron_expression"] == "0 0 * * *"
    assert schedule["timezone"] == "UTC"
    assert schedule["owner_uid"], "owner_uid 应被默认填充为当前用户"

    try:
        # 2) get detail
        detail_resp = await test_client.get(
            f"/api/schedules/{schedule_id}", headers=admin_headers
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["schedule"]["id"] == schedule_id

        # 3) patch
        patch_resp = await test_client.patch(
            f"/api/schedules/{schedule_id}",
            json={"name": unique_name + "_v2", "enabled": False, "timezone": "Asia/Shanghai"},
            headers=admin_headers,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        patched = patch_resp.json()["schedule"]
        assert patched["name"] == unique_name + "_v2"
        assert patched["enabled"] is False
        assert patched["timezone"] == "Asia/Shanghai"

        # 4) list
        list_resp = await test_client.get("/api/schedules", headers=admin_headers)
        assert list_resp.status_code == 200
        ids = [item["id"] for item in list_resp.json()["schedules"]]
        assert schedule_id in ids

        # 5) executions 端点目前为空
        exec_resp = await test_client.get(
            f"/api/schedules/{schedule_id}/executions", headers=admin_headers
        )
        assert exec_resp.status_code == 200
        assert exec_resp.json()["executions"] == []
    finally:
        # 6) delete
        delete_resp = await test_client.delete(
            f"/api/schedules/{schedule_id}", headers=admin_headers
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True

        # 7) 再 get 应 404
        after_resp = await test_client.get(
            f"/api/schedules/{schedule_id}", headers=admin_headers
        )
        assert after_resp.status_code == 404


async def test_create_with_invalid_cron_returns_422(test_client, admin_headers):
    """非法 cron 表达式在创建时直接 422。"""
    payload = {**_SCHEDULE_BASE_PAYLOAD, "cron_expression": "not-a-cron"}
    resp = await test_client.post("/api/schedules", json=payload, headers=admin_headers)
    assert resp.status_code == 422, resp.text


async def test_create_with_invalid_timezone_returns_422(test_client, admin_headers):
    payload = {**_SCHEDULE_BASE_PAYLOAD, "timezone": "Mars/Olympus"}
    resp = await test_client.post("/api/schedules", json=payload, headers=admin_headers)
    assert resp.status_code == 422, resp.text


async def test_fire_endpoint_writes_execution(test_client, admin_headers):
    """POST /fire 写一条 execution（status=pending 或 running），不阻塞等待结果。"""
    unique_name = f"pytest_schedule_fire_{uuid.uuid4().hex[:8]}"
    payload = {**_SCHEDULE_BASE_PAYLOAD, "name": unique_name, "enabled": False}
    create_resp = await test_client.post("/api/schedules", json=payload, headers=admin_headers)
    assert create_resp.status_code == 200, create_resp.text
    schedule_id = create_resp.json()["schedule"]["id"]

    try:
        fire_resp = await test_client.post(
            f"/api/schedules/{schedule_id}/fire", headers=admin_headers
        )
        assert fire_resp.status_code == 200, fire_resp.text
        body = fire_resp.json()
        assert body["schedule_id"] == schedule_id
        assert body["execution_id"], "fire 应返回 execution_id"
        # status 此时应该还在 pending 或已经切到 running/failed，三者皆可
        assert body["status"] in {"pending", "running", "failed", "success"}

        # executions 列表应至少有一条
        exec_resp = await test_client.get(
            f"/api/schedules/{schedule_id}/executions", headers=admin_headers
        )
        assert exec_resp.status_code == 200
        executions = exec_resp.json()["executions"]
        assert len(executions) >= 1
        assert executions[0]["id"] == body["execution_id"]
    finally:
        await test_client.delete(f"/api/schedules/{schedule_id}", headers=admin_headers)


async def test_fire_unknown_schedule_returns_404(test_client, admin_headers):
    resp = await test_client.post(
        f"/api/schedules/{uuid.uuid4().hex}/fire", headers=admin_headers
    )
    assert resp.status_code == 404


async def test_get_unknown_schedule_returns_404(test_client, admin_headers):
    resp = await test_client.get(f"/api/schedules/{uuid.uuid4().hex}", headers=admin_headers)
    assert resp.status_code == 404


async def test_list_with_enabled_filter(test_client, admin_headers):
    """?enabled=true 仅返回启用的。"""
    name_a = f"pytest_schedule_e1_{uuid.uuid4().hex[:8]}"
    name_b = f"pytest_schedule_e0_{uuid.uuid4().hex[:8]}"
    a_resp = await test_client.post(
        "/api/schedules",
        json={**_SCHEDULE_BASE_PAYLOAD, "name": name_a, "enabled": True},
        headers=admin_headers,
    )
    b_resp = await test_client.post(
        "/api/schedules",
        json={**_SCHEDULE_BASE_PAYLOAD, "name": name_b, "enabled": False},
        headers=admin_headers,
    )
    a_id = a_resp.json()["schedule"]["id"]
    b_id = b_resp.json()["schedule"]["id"]

    try:
        resp = await test_client.get("/api/schedules?enabled=true", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["schedules"]]
        assert a_id in ids
        assert b_id not in ids

        resp_off = await test_client.get("/api/schedules?enabled=false", headers=admin_headers)
        assert resp_off.status_code == 200
        ids_off = [item["id"] for item in resp_off.json()["schedules"]]
        assert b_id in ids_off
        assert a_id not in ids_off
    finally:
        await test_client.delete(f"/api/schedules/{a_id}", headers=admin_headers)
        await test_client.delete(f"/api/schedules/{b_id}", headers=admin_headers)


async def test_executions_pagination(test_client, admin_headers):
    """分页参数 limit / before 能正常收敛。"""
    import asyncio

    unique_name = f"pytest_schedule_page_{uuid.uuid4().hex[:8]}"
    create_resp = await test_client.post(
        "/api/schedules",
        json={**_SCHEDULE_BASE_PAYLOAD, "name": unique_name, "enabled": False},
        headers=admin_headers,
    )
    schedule_id = create_resp.json()["schedule"]["id"]

    try:
        # fire 一次确认基线；后续单条 GET 验证分页参数仍能正常返回与 before 解析
        fire_resp = await test_client.post(
            f"/api/schedules/{schedule_id}/fire", headers=admin_headers
        )
        assert fire_resp.status_code == 200
        await asyncio.sleep(0.1)

        first_page = await test_client.get(
            f"/api/schedules/{schedule_id}/executions?limit=10",
            headers=admin_headers,
        )
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert len(first_body["executions"]) >= 1, "fire 之后至少应有一条 execution"
        assert first_body["next_before"], "分页应返回 next_before 游标"

        # 用 first page 最后一条的 created_at 作 cursor 拉下一页：应返回 0 条
        cursor = first_body["next_before"]
        second_page = await test_client.get(
            f"/api/schedules/{schedule_id}/executions?limit=10&before={cursor}",
            headers=admin_headers,
        )
        assert second_page.status_code == 200
        assert second_page.json()["executions"] == []

        # before 参数非法值应返回 422（明确校验）
        bad_page = await test_client.get(
            f"/api/schedules/{schedule_id}/executions?before=not-a-date",
            headers=admin_headers,
        )
        assert bad_page.status_code == 422
    finally:
        await test_client.delete(f"/api/schedules/{schedule_id}", headers=admin_headers)
