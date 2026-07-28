from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.auth_router import activate_user, delete_user, disable_user
from server.routers.user_router import APIKeyCreate, create_api_key
from server.utils.auth_middleware import _verify_api_key, get_current_user
from yuxi.repositories import user_repository as user_repository_module
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.permission_service import resolve_user_permissions
from yuxi.storage.postgres.models_business import (
    APIKey,
    Base,
    CLIAuthSession,
    Department,
    ExternalIdentity,
    User,
    UserDepartmentMembership,
)
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeApiKeySession:
    def __init__(self, api_key: APIKey):
        self.api_key = api_key
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _ScalarResult(self.api_key)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept_a = Department(name="Dept A")
        dept_b = Department(name="Dept B")
        superadmin = User(
            username="Super Admin",
            uid="superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept_a,
        )
        dept_b_admin = User(
            username="Dept B Admin",
            uid="dept_b_admin",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_b,
        )
        regular_user = User(
            username="Regular",
            uid="regular",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
        )
        deleted_user = User(
            username="Deleted",
            uid="deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
            is_deleted=1,
        )
        db.add_all([dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user])
        await db.commit()
        for item in [dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user]:
            await db.refresh(item)
        yield {
            "db": db,
            "dept_a": dept_a,
            "dept_b": dept_b,
            "superadmin": superadmin,
            "dept_b_admin": dept_b_admin,
            "regular_user": regular_user,
            "deleted_user": deleted_user,
        }
    await engine.dispose()


async def test_api_key_rejects_deleted_bound_user_without_department_or_superadmin_fallback(session):
    db = session["db"]
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="deleted user key",
        user_id=session["deleted_user"].id,
        department_id=session["dept_b"].id,
        created_by=str(session["deleted_user"].id),
    )
    db.add(api_key)
    await db.commit()

    user, verified_key = await _verify_api_key(secret, db)

    assert user is None
    assert verified_key is None


async def test_api_key_auth_blocks_user_without_apikey_invoke_permission(session, monkeypatch):
    """未获得 apikey.invoke 权限的用户，其 API Key 在 auth_middleware 阶段被 403 拒绝。

    这里通过 stub has_permission 让"非 superadmin 也没有 apikey.invoke"的场景可被触发：
    superadmin 在 has_permission 内置分支里始终返回 True，所以我们用 monkeypatch 覆盖。
    """
    db = session["db"]
    user = session["regular_user"]
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="regular user key",
        user_id=user.id,
        department_id=session["dept_a"].id,
        created_by=str(user.id),
    )
    db.add(api_key)
    await db.commit()

    import yuxi.services.permission_service as permission_service_module
    from yuxi.services.permission_service import has_permission as real_has_permission

    def stub_has_permission(target_user, permission):
        # 模拟"普通用户没有 apikey.invoke"：对所有非 superadmin 返回 False
        if target_user.role == "superadmin":
            return real_has_permission(target_user, permission)
        if permission == "apikey.invoke":
            return False
        return real_has_permission(target_user, permission)

    monkeypatch.setattr(permission_service_module, "has_permission", stub_has_permission)
    # auth_middleware 通过 from ... import 拿到了原始符号，需要同时 stub
    from server.utils import auth_middleware as auth_middleware_module

    monkeypatch.setattr(auth_middleware_module, "has_permission", stub_has_permission)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization=f"Bearer {secret}", db=db)

    assert exc.value.status_code == 403
    assert "apikey.invoke" in exc.value.detail


async def test_api_key_without_user_binding_is_rejected_before_department_mapping(session):
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="department key",
        user_id=None,
        department_id=session["dept_b"].id,
        created_by=str(session["superadmin"].id),
    )
    fake_db = _FakeApiKeySession(api_key)

    user, verified_key = await _verify_api_key(secret, fake_db)

    assert user is None
    assert verified_key is None
    assert fake_db.execute_calls == 1


async def test_create_api_key_rejects_mismatched_department(session):
    db = session["db"]

    with pytest.raises(HTTPException) as exc:
        await create_api_key(
            APIKeyCreate(name="wrong department", department_id=session["dept_b"].id),
            current_user=session["regular_user"],
            db=db,
        )

    assert exc.value.status_code == 403


async def test_create_api_key_allows_current_user_department(session):
    db = session["db"]

    response = await create_api_key(
        APIKeyCreate(name="own department", department_id=session["dept_a"].id),
        current_user=session["regular_user"],
        db=db,
    )

    assert response.api_key.user_id == session["regular_user"].id
    assert response.api_key.department_id == session["dept_a"].id
    assert response.secret.startswith(response.api_key.key_prefix)


async def test_disable_user_disables_owned_api_keys(session):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    membership = UserDepartmentMembership(
        user_id=session["regular_user"].id,
        department_id=session["dept_a"].id,
        membership_type="primary",
        status="active",
    )
    db.add_all([api_key, membership])
    await db.commit()
    await db.refresh(api_key)
    original_username = session["regular_user"].username
    original_password_hash = session["regular_user"].password_hash

    result = await disable_user(session["regular_user"].id, None, session["superadmin"], db)
    await db.refresh(api_key)
    await db.refresh(session["regular_user"])
    await db.refresh(membership)

    assert result["success"] is True
    assert result["message"] == "用户已禁用"
    assert api_key.is_enabled is False
    assert session["regular_user"].username == original_username
    assert session["regular_user"].password_hash == original_password_hash
    assert membership.status == "active"


async def test_activate_user_restores_login_state_but_keeps_api_keys_disabled(session):
    db = session["db"]
    user = session["regular_user"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=user.id,
        created_by=str(user.id),
    )
    membership = UserDepartmentMembership(
        user_id=user.id,
        department_id=session["dept_a"].id,
        membership_type="primary",
        status="inactive",
    )
    db.add_all([api_key, membership])
    await db.commit()

    await disable_user(user.id, None, session["superadmin"], db)
    result = await activate_user(user.id, None, session["superadmin"], db)
    await db.refresh(user)
    await db.refresh(api_key)
    await db.refresh(membership)

    assert result == {"success": True, "message": "用户已激活"}
    assert user.is_deleted == 0
    assert user.deleted_at is None
    assert api_key.is_enabled is False
    assert membership.status == "active"


async def test_delete_user_physically_removes_account_and_auth_bindings(session):
    db = session["db"]
    user = session["regular_user"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=user.id,
        created_by=str(user.id),
    )
    identity = ExternalIdentity(
        issuer="https://issuer.example",
        subject="subject-123",
        user_id=user.id,
        email="790100005580@example.com",
    )
    db.add_all([api_key, identity])
    await db.flush()
    cli_session = CLIAuthSession(
        device_code_hash="device-code",
        user_code="USER-CODE",
        status="approved",
        key_name="test",
        approved_user_id=user.id,
        api_key_id=api_key.id,
        expires_at=utc_now_naive() + timedelta(minutes=5),
    )
    db.add(cli_session)
    await db.commit()

    result = await delete_user(user.id, None, session["superadmin"], db)

    assert result == {"success": True, "message": "用户已删除"}
    assert await db.get(User, user.id) is None
    assert (await db.execute(select(APIKey).where(APIKey.user_id == user.id))).scalar_one_or_none() is None
    assert (
        await db.execute(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id))
    ).scalar_one_or_none() is None
    await db.refresh(cli_session)
    assert cli_session.approved_user_id is None
    assert cli_session.api_key_id is None


async def test_user_repository_disable_disables_owned_api_keys(session, monkeypatch):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="repository owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    @asynccontextmanager
    async def fake_session_context():
        yield db
        await db.commit()

    monkeypatch.setattr(user_repository_module.pg_manager, "get_async_session_context", fake_session_context)

    assert await UserRepository().disable(session["regular_user"].id) is True
    await db.refresh(api_key)

    assert api_key.is_enabled is False
