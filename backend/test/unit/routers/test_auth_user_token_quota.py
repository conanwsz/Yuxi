from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.routers.auth_router import auth
from server.routers.user_router import user_router
from server.utils.auth_middleware import get_current_user, get_db, get_required_user
from yuxi.services.permission_service import ADMIN_PERMISSION_KEYS, permission_catalog
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    DepartmentClosure,
    Role,
    User,
    UserDepartmentMembership,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def fake_token_quota_service(monkeypatch):
    user_router_module = importlib.import_module("server.routers.user_router")
    auth_router_module = importlib.import_module("server.routers.auth_router")

    module = types.ModuleType("yuxi.services.token_quota_service")

    async def get_user_token_quota_status(db, user, **_):
        del db
        return {
            "mode": user.token_quota_mode,
            "effective_weekly_token_quota": user.weekly_token_quota,
            "used_tokens": 120,
            "remaining_tokens": None if user.weekly_token_quota is None else user.weekly_token_quota - 120,
            "reset_at": "2026-07-27 00:00",
            "is_unlimited": user.token_quota_mode == "unlimited",
        }

    async def batch_get_user_token_quota_statuses(db, users, **_):
        statuses: dict[int, dict] = {}
        for user in users:
            statuses[user.id] = await get_user_token_quota_status(db, user)
        return statuses

    async def reset_weekly_usage(db, user, **_):
        return await get_user_token_quota_status(db, user)

    async def reset_weekly_usage_for_users(db, user_ids, **_):
        del db
        normalized = sorted({int(user_id) for user_id in user_ids})
        return {
            "target_count": len(normalized),
            "reset_count": len(normalized),
            "reset_at": "2026-07-22T02:00:00Z",
        }

    module.get_user_token_quota_status = get_user_token_quota_status
    module.batch_get_user_token_quota_statuses = batch_get_user_token_quota_statuses
    module.reset_weekly_usage = reset_weekly_usage
    module.reset_weekly_usage_for_users = reset_weekly_usage_for_users
    monkeypatch.setitem(sys.modules, "yuxi.services.token_quota_service", module)

    async def get_user_token_quota_payload_with_breakdown(db, user):
        return {
            "token_quota_mode": user.token_quota_mode,
            "weekly_token_quota": user.weekly_token_quota,
            "token_quota": await get_user_token_quota_status(db, user),
        }

    monkeypatch.setattr(
        user_router_module,
        "get_user_token_quota_payload_with_breakdown",
        get_user_token_quota_payload_with_breakdown,
    )
    monkeypatch.setattr(auth_router_module, "_reset_user_weekly_token_quota", reset_weekly_usage)
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", reset_weekly_usage_for_users)


async def _create_user_with_membership(
    db: AsyncSession,
    *,
    department_id: int,
    username: str,
    uid: str,
    role: str = "user",
    token_quota_mode: str = "inherit",
    weekly_token_quota: int | None = None,
) -> User:
    user = User(
        username=username,
        uid=uid,
        password_hash="$argon2id$placeholder",
        role=role,
        department_id=department_id,
        token_quota_mode=token_quota_mode,
        weekly_token_quota=weekly_token_quota,
    )
    db.add(user)
    await db.flush()
    db.add(
        UserDepartmentMembership(
            user_id=user.id,
            department_id=department_id,
            membership_type="primary",
            status="active",
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture()
async def quota_app_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        department = Department(name="默认部门", status="active", is_system=False)
        db.add(department)
        await db.flush()
        db.add(DepartmentClosure(ancestor_id=department.id, descendant_id=department.id, depth=0))
        db.add_all(
            [
                Role(key="admin", name="管理员", permissions=[]),
                Role(key="user", name="普通用户", permissions=[]),
            ]
        )
        await db.commit()

        current_user = await _create_user_with_membership(
            db,
            department_id=department.id,
            username="Admin",
            uid="admin",
            role="admin",
        )
        current_user.permission_keys = {"users.read"}
        current_user.managed_department_ids = {department.id}

        app = FastAPI()
        app.include_router(auth, prefix="/api")
        app.include_router(user_router, prefix="/api")

        async def override_db():
            yield db

        async def override_required_user():
            return current_user

        async def override_current_user():
            return current_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_required_user
        app.dependency_overrides[get_current_user] = override_current_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(
                client=client,
                db=db,
                department=department,
                current_user=current_user,
            )

    await engine.dispose()


async def test_self_profile_and_token_quota_endpoint_include_current_user_status(quota_app_client):
    quota_app_client.current_user.token_quota_mode = "custom"
    quota_app_client.current_user.weekly_token_quota = 1234
    await quota_app_client.db.commit()

    me_response = await quota_app_client.client.get("/api/auth/me")
    assert me_response.status_code == 200, me_response.text
    me_payload = me_response.json()
    assert me_payload["token_quota_mode"] == "custom"
    assert me_payload["weekly_token_quota"] == 1234
    assert me_payload["token_quota"]["used_tokens"] == 120
    assert me_payload["token_quota"]["remaining_tokens"] == 1114

    quota_response = await quota_app_client.client.get("/api/user/token-quota")
    assert quota_response.status_code == 200, quota_response.text
    quota_payload = quota_response.json()
    assert quota_payload["token_quota_mode"] == "custom"
    assert quota_payload["weekly_token_quota"] == 1234
    assert quota_payload["token_quota"]["reset_at"] == "2026-07-27 00:00"


async def test_create_user_rejects_quota_settings_without_manage_permission(quota_app_client):
    quota_app_client.current_user.permission_keys = {"users.create"}

    response = await quota_app_client.client.post(
        "/api/auth/users",
        json={
            "username": "quota_target",
            "password": "Pw!quota_target",
            "role": "user",
            "token_quota_mode": "custom",
            "weekly_token_quota": 100,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "缺少权限: users.quota.manage"


async def test_create_user_applies_custom_quota_when_actor_has_permission(quota_app_client):
    quota_app_client.current_user.permission_keys = {"users.create", "users.quota.manage"}

    response = await quota_app_client.client.post(
        "/api/auth/users",
        json={
            "username": "quota_enabled_user",
            "password": "Pw!quota_enabled_user",
            "role": "user",
            "token_quota_mode": "custom",
            "weekly_token_quota": 256,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token_quota_mode"] == "custom"
    assert payload["weekly_token_quota"] == 256
    assert payload["token_quota"]["effective_weekly_token_quota"] == 256
    assert payload["token_quota"]["mode"] == "custom"


async def test_update_user_validates_and_updates_quota_fields(quota_app_client):
    quota_app_client.current_user.permission_keys = {"users.update", "users.quota.manage"}
    target_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_update_user",
        uid="quota_update_user",
        token_quota_mode="custom",
        weekly_token_quota=64,
    )

    invalid_response = await quota_app_client.client.put(
        f"/api/auth/users/{target_user.id}",
        json={"token_quota_mode": "inherit", "weekly_token_quota": 128},
    )
    assert invalid_response.status_code == 422, invalid_response.text
    assert invalid_response.json()["detail"] == "仅自定义额度模式允许设置 weekly_token_quota"

    valid_response = await quota_app_client.client.put(
        f"/api/auth/users/{target_user.id}",
        json={"weekly_token_quota": 128},
    )
    assert valid_response.status_code == 200, valid_response.text
    payload = valid_response.json()
    assert payload["token_quota_mode"] == "custom"
    assert payload["weekly_token_quota"] == 128
    assert payload["token_quota"]["effective_weekly_token_quota"] == 128


async def test_reset_user_weekly_quota_requires_permission_and_resets_target(quota_app_client, monkeypatch):
    target_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_reset_user",
        uid="quota_reset_user",
        token_quota_mode="custom",
        weekly_token_quota=512,
    )
    reset_calls: list[int] = []

    async def fake_reset(db, user):
        del db
        reset_calls.append(user.id)
        return {"used_tokens": 0}

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_user_weekly_token_quota", fake_reset)

    quota_app_client.current_user.permission_keys = {"users.read"}
    forbidden_response = await quota_app_client.client.post(f"/api/auth/users/{target_user.id}/token-quota/reset")
    assert forbidden_response.status_code == 403, forbidden_response.text

    quota_app_client.current_user.permission_keys = {"users.read", "users.quota.manage"}
    response = await quota_app_client.client.post(f"/api/auth/users/{target_user.id}/token-quota/reset")

    assert response.status_code == 200, response.text
    assert response.json()["token_quota"]["used_tokens"] == 0
    assert reset_calls == [target_user.id]


async def test_reset_batch_quota_deduplicates_and_requires_scope(quota_app_client, monkeypatch):
    target_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_batch_user",
        uid="quota_batch_user",
        token_quota_mode="custom",
        weekly_token_quota=256,
    )
    reset_calls: list[list[int]] = []

    async def fake_batch_reset(db, users):
        del db
        reset_calls.append([user.id for user in users])
        return {
            "target_count": len(users),
            "reset_count": len(users),
            "reset_at": "2026-07-22T02:00:00Z",
        }

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_batch_reset)

    quota_app_client.current_user.permission_keys = {"users.read"}
    forbidden = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch",
        json={"user_ids": [target_user.id]},
    )
    assert forbidden.status_code == 403, forbidden.text

    quota_app_client.current_user.permission_keys = {"users.read", "users.quota.manage"}
    response = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch",
        json={"user_ids": [target_user.id, target_user.id]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "scope": "selected",
        "target_count": 1,
        "reset_count": 1,
        "reset_at": "2026-07-22T02:00:00Z",
    }
    assert reset_calls == [[target_user.id]]


async def test_reset_batch_quota_rejects_users_outside_manage_scope_atomically(quota_app_client, monkeypatch):
    other_department = Department(name="其他部门", status="active", is_system=False)
    quota_app_client.db.add(other_department)
    await quota_app_client.db.flush()
    quota_app_client.db.add(
        DepartmentClosure(ancestor_id=other_department.id, descendant_id=other_department.id, depth=0)
    )
    await quota_app_client.db.commit()

    in_scope_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_in_scope_user",
        uid="quota_in_scope_user",
    )
    out_scope_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=other_department.id,
        username="quota_out_scope_user",
        uid="quota_out_scope_user",
    )
    reset_calls: list[list[int]] = []

    async def fake_batch_reset(db, users):
        del db
        reset_calls.append([user.id for user in users])
        return {
            "target_count": len(users),
            "reset_count": len(users),
            "reset_at": "2026-07-22T02:00:00Z",
        }

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_batch_reset)

    quota_app_client.current_user.permission_keys = {"users.read", "users.quota.manage"}
    response = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch",
        json={"user_ids": [in_scope_user.id, out_scope_user.id]},
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "批量重置包含管理范围外的用户"
    assert reset_calls == []


async def test_reset_global_quota_requires_permission_and_includes_part_time_membership(quota_app_client, monkeypatch):
    other_department = Department(name="兼职部门", status="active", is_system=False)
    quota_app_client.db.add(other_department)
    await quota_app_client.db.flush()
    quota_app_client.db.add(
        DepartmentClosure(ancestor_id=other_department.id, descendant_id=other_department.id, depth=0)
    )
    await quota_app_client.db.commit()

    primary_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_global_primary",
        uid="quota_global_primary",
    )
    part_time_user = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="quota_global_part_time",
        uid="quota_global_part_time",
    )
    part_time_membership = (
        await quota_app_client.db.execute(
            select(UserDepartmentMembership).where(UserDepartmentMembership.user_id == part_time_user.id)
        )
    ).scalar_one()
    part_time_membership.membership_type = "part_time"
    part_time_user.department_id = other_department.id
    await quota_app_client.db.commit()
    outsider = await _create_user_with_membership(
        quota_app_client.db,
        department_id=other_department.id,
        username="quota_global_outsider",
        uid="quota_global_outsider",
    )
    await quota_app_client.db.commit()

    reset_calls: list[list[int]] = []

    async def fake_batch_reset(db, users):
        del db
        reset_calls.append([user.id for user in users])
        return {
            "target_count": len(users),
            "reset_count": len(users),
            "reset_at": "2026-07-22T02:00:00Z",
        }

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_batch_reset)

    quota_app_client.current_user.permission_keys = {"users.read"}
    forbidden = await quota_app_client.client.post("/api/auth/users/token-quota/reset-global")
    assert forbidden.status_code == 403, forbidden.text

    quota_app_client.current_user.permission_keys = {"users.read", "users.quota.reset.global"}
    response = await quota_app_client.client.post("/api/auth/users/token-quota/reset-global")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "scope": "managed",
        "target_count": 3,
        "reset_count": 3,
        "reset_at": "2026-07-22T02:00:00Z",
    }
    assert reset_calls == [[quota_app_client.current_user.id, primary_user.id, part_time_user.id]]
    assert outsider.id not in reset_calls[0]


async def test_batch_quota_reset_deduplicates_targets_and_requires_manage_permission(quota_app_client, monkeypatch):
    targets = [
        await _create_user_with_membership(
            quota_app_client.db,
            department_id=quota_app_client.department.id,
            username=f"batch_user_{index}",
            uid=f"batch_user_{index}",
        )
        for index in range(2)
    ]
    captured_ids: list[int] = []

    async def fake_batch_reset(db, users):
        del db
        captured_ids.extend(user.id for user in users)
        return {"target_count": len(users), "reset_count": len(users), "reset_at": "2026-07-22T02:00:00Z"}

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_batch_reset)
    user_ids = [targets[1].id, targets[0].id, targets[0].id]

    quota_app_client.current_user.permission_keys = {"users.read"}
    forbidden = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch", json={"user_ids": user_ids}
    )
    assert forbidden.status_code == 403, forbidden.text

    quota_app_client.current_user.permission_keys = {"users.read", "users.quota.manage"}
    response = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch", json={"user_ids": user_ids}
    )

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "selected"
    assert response.json()["target_count"] == 2
    assert captured_ids == sorted(user.id for user in targets)


async def test_batch_quota_reset_rejects_invalid_payload_and_out_of_scope_target(quota_app_client, monkeypatch):
    other_department = Department(name="其他部门", status="active", is_system=False)
    quota_app_client.db.add(other_department)
    await quota_app_client.db.flush()
    quota_app_client.db.add(
        DepartmentClosure(ancestor_id=other_department.id, descendant_id=other_department.id, depth=0)
    )
    await quota_app_client.db.commit()
    target = await _create_user_with_membership(
        quota_app_client.db,
        department_id=other_department.id,
        username="out_of_scope",
        uid="out_of_scope",
    )
    reset_called = False

    async def fake_batch_reset(db, users):
        del db, users
        nonlocal reset_called
        reset_called = True
        return {"target_count": 0, "reset_count": 0, "reset_at": "2026-07-22T02:00:00Z"}

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_batch_reset)
    quota_app_client.current_user.permission_keys = {"users.quota.manage"}

    empty = await quota_app_client.client.post("/api/auth/users/token-quota/reset-batch", json={"user_ids": []})
    too_many = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch", json={"user_ids": list(range(1, 1002))}
    )
    out_of_scope = await quota_app_client.client.post(
        "/api/auth/users/token-quota/reset-batch", json={"user_ids": [target.id]}
    )

    assert empty.status_code == 422, empty.text
    assert too_many.status_code == 422, too_many.text
    assert out_of_scope.status_code == 403, out_of_scope.text
    assert reset_called is False


async def test_global_quota_reset_uses_active_memberships_in_management_scope(quota_app_client, monkeypatch):
    other_department = Department(name="其他主体", status="active", is_system=False)
    quota_app_client.db.add(other_department)
    await quota_app_client.db.flush()
    quota_app_client.db.add(
        DepartmentClosure(ancestor_id=other_department.id, descendant_id=other_department.id, depth=0)
    )
    await quota_app_client.db.commit()
    primary_in_scope = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="primary_in_scope",
        uid="primary_in_scope",
    )
    part_time_in_scope = await _create_user_with_membership(
        quota_app_client.db,
        department_id=quota_app_client.department.id,
        username="part_time_in_scope",
        uid="part_time_in_scope",
    )
    part_time_membership = (
        await quota_app_client.db.execute(
            select(UserDepartmentMembership).where(UserDepartmentMembership.user_id == part_time_in_scope.id)
        )
    ).scalar_one()
    part_time_membership.membership_type = "part_time"
    part_time_in_scope.department_id = other_department.id
    await quota_app_client.db.commit()
    outside = await _create_user_with_membership(
        quota_app_client.db,
        department_id=other_department.id,
        username="outside",
        uid="outside",
    )
    await quota_app_client.db.commit()
    captured_ids: list[int] = []

    async def fake_global_reset(db, users):
        del db
        captured_ids.extend(user.id for user in users)
        return {"target_count": len(users), "reset_count": 0, "reset_at": "2026-07-22T02:00:00Z"}

    auth_router_module = importlib.import_module("server.routers.auth_router")
    monkeypatch.setattr(auth_router_module, "_reset_users_weekly_token_quota", fake_global_reset)

    quota_app_client.current_user.permission_keys = {"users.read"}
    forbidden = await quota_app_client.client.post("/api/auth/users/token-quota/reset-global")
    assert forbidden.status_code == 403, forbidden.text

    quota_app_client.current_user.permission_keys = {"users.quota.reset.global"}
    response = await quota_app_client.client.post("/api/auth/users/token-quota/reset-global")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "managed"
    assert set(captured_ids) == {
        quota_app_client.current_user.id,
        primary_in_scope.id,
        part_time_in_scope.id,
    }
    assert outside.id not in captured_ids


async def test_global_quota_reset_permission_is_not_granted_to_default_admin():
    permission_keys = {item["key"] for group in permission_catalog() for item in group["permissions"]}

    assert "users.quota.reset.global" in permission_keys
    assert "users.quota.reset.global" not in ADMIN_PERMISSION_KEYS
