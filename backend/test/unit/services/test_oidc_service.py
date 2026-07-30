from __future__ import annotations

import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import pytest_asyncio
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "dummy")

from yuxi.services import oidc_service
from yuxi.storage.postgres.models_business import ExternalIdentity, Role, User


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def oidc_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Role.__table__.create)
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(ExternalIdentity.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Role(key="user", name="普通用户", permissions=[]))
        await session.commit()
        yield session

    await engine.dispose()


async def _create_user(session, uid: str = "alice") -> User:
    user = User(username="alice", uid=uid, password_hash="x", role="user", is_deleted=0)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_find_user_by_oidc_sub_resolves_placeholder_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_user_by_oidc_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 0


async def test_find_deleted_oidc_user_by_sub_resolves_deleted_target_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)
    user.is_deleted = 1
    await oidc_session.commit()

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_deleted_oidc_user_by_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 1


async def test_oidc_callback_allows_existing_binding_when_sub_contains_colon(oidc_session, monkeypatch):
    user = await _create_user(oidc_session)
    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "legacy_issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")
    monkeypatch.setattr(oidc_service.oidc_config, "token_endpoint", "https://example/token")
    monkeypatch.setattr(oidc_service.oidc_config, "authorization_endpoint", "https://example/auth")
    monkeypatch.setattr(oidc_service.oidc_config, "userinfo_endpoint", "https://example/userinfo")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", True)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", False)

    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )

    async def fake_exchange(cls, code, code_verifier):
        assert code_verifier == "verifier"
        return {"access_token": "token", "id_token": "id-token"}

    async def fake_verify_id_token(cls, id_token, expected_nonce):
        assert expected_nonce == "nonce"
        return {"iss": "https://issuer.example", "sub": "tenant:user"}

    async def fake_userinfo(cls, access_token):
        return {
            "sub": "tenant:user",
            "preferred_username": "alice",
            "picture": "https://issuer.example/avatars/alice.png",
        }

    async def fake_log_operation(db, user_id, operation, request=None):
        return None

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_id_token", classmethod(fake_verify_id_token))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))
    monkeypatch.setattr(oidc_service, "log_operation", fake_log_operation)

    response = await oidc_service.oidc_callback_handler("dummy-code", "dummy-state", oidc_session)

    assert response.status_code == 302
    assert unquote(response.headers["location"]).startswith("/auth/oidc/callback?code=")

    identity = await oidc_session.get(ExternalIdentity, 1)
    assert identity is not None
    assert identity.issuer == "https://issuer.example"
    assert identity.subject == "tenant:user"
    assert identity.user_id == user.id
    await oidc_session.refresh(user)
    assert user.avatar == "https://issuer.example/avatars/alice.png"


async def test_oidc_callback_rejects_disabled_user_instead_of_restoring(oidc_session, monkeypatch):
    user = await _create_user(oidc_session, "oidc:legacy-user")
    await oidc_service.bind_external_identity(
        oidc_session,
        user,
        "https://issuer.example",
        "disabled-subject",
        "790100005580@example.com",
    )
    user.is_deleted = 1
    await oidc_session.commit()

    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", False)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", True)
    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )

    async def fake_exchange(cls, code, code_verifier):
        return {"access_token": "token", "id_token": "id-token"}

    async def fake_verify_id_token(cls, id_token, expected_nonce):
        return {"iss": "https://issuer.example", "sub": "disabled-subject"}

    async def fake_userinfo(cls, access_token):
        return {
            "sub": "disabled-subject",
            "preferred_username": "disabled-user",
            "name": "Disabled User",
            "email": "790100005580@example.com",
        }

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_id_token", classmethod(fake_verify_id_token))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))

    response = await oidc_service.oidc_callback_handler("code", "state", oidc_session)

    assert response.status_code == 302
    assert "error=" in response.headers["location"]
    await oidc_session.refresh(user)
    assert user.is_deleted == 1
    assert user.uid == "oidc:legacy-user"


async def test_authorization_url_uses_pkce_nonce_and_safe_redirect(monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "client-id")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")

    async def fake_metadata(cls):
        return SimpleNamespace(authorization_endpoint="https://issuer.example/authorize")

    monkeypatch.setattr(oidc_service.OIDCUtils, "get_metadata", classmethod(fake_metadata))
    oidc_service.OIDCUtils._state_store.clear()

    url = await oidc_service.OIDCUtils.build_authorization_url("https://attacker.example")

    query = parse_qs(urlparse(url).query)
    state = query["state"][0]
    transaction = oidc_service.OIDCUtils.verify_state(state)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [oidc_service.OIDCUtils.code_challenge(transaction["code_verifier"])]
    assert query["nonce"] == [transaction["nonce"]]
    assert transaction["redirect_path"] == "/"
    assert oidc_service.OIDCUtils.verify_state(state) is None


async def test_metadata_discovery_retries_after_transient_failure(monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "client-id")
    monkeypatch.setattr(
        oidc_service.oidc_config,
        "redirect_uri",
        "https://yuxi.example/api/auth/oidc/callback",
    )

    attempts = 0

    class FakeMetadata:
        def __init__(self):
            self._loaded = False
            self.authorization_endpoint = None
            self.last_error = None

        async def load(self, issuer_url):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.last_error = "temporary failure"
                return False
            self._loaded = True
            self.authorization_endpoint = f"{issuer_url}/authorize"
            return True

    monkeypatch.setattr(oidc_service, "OIDCProviderMetadata", FakeMetadata)
    oidc_service.OIDCUtils._metadata = None

    assert await oidc_service.OIDCUtils.get_metadata() is None
    metadata = await oidc_service.OIDCUtils.get_metadata()

    assert metadata is not None
    assert metadata.authorization_endpoint == "https://issuer.example/authorize"
    assert attempts == 2
    oidc_service.OIDCUtils._metadata = None


async def test_callback_rejects_userinfo_subject_different_from_verified_id_token(oidc_session, monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")
    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )

    async def fake_exchange(cls, code, code_verifier):
        return {"access_token": "token", "id_token": "id-token"}

    async def fake_verify_id_token(cls, id_token, expected_nonce):
        return {"iss": "https://issuer.example", "sub": "verified-sub"}

    async def fake_userinfo(cls, access_token):
        return {"sub": "other-sub", "preferred_username": "alice"}

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_id_token", classmethod(fake_verify_id_token))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))

    response = await oidc_service.oidc_callback_handler("code", "state", oidc_session)

    assert response.status_code == 302
    assert "oidc_error=" in response.headers["location"]


async def test_callback_rejects_new_user_without_normalized_email(oidc_session, monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", False)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", True)
    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )

    async def fake_exchange(cls, code, code_verifier):
        return {"access_token": "token", "id_token": "id-token"}

    async def fake_verify_id_token(cls, id_token, expected_nonce):
        return {"iss": "https://issuer.example", "sub": "new-sub"}

    async def fake_userinfo(cls, access_token):
        return {"sub": "new-sub", "preferred_username": "new-user"}

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_id_token", classmethod(fake_verify_id_token))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))

    response = await oidc_service.oidc_callback_handler("code", "state", oidc_session)

    assert response.status_code == 302
    assert "无法获取有效邮箱" in unquote(response.headers["location"])


async def test_callback_redirects_when_email_is_bound_to_another_identity(oidc_session, monkeypatch):
    existing = await _create_user(oidc_session, "existing")
    await oidc_service.bind_external_identity(
        oidc_session, existing, "https://other-issuer.example", "other-sub", "alice@example.com"
    )
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "issuer_url", "https://issuer.example")
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "redirect_uri", "https://yuxi.example/api/auth/oidc/callback")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", False)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", True)
    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )

    async def fake_exchange(cls, code, code_verifier):
        return {"access_token": "token", "id_token": "id-token"}

    async def fake_verify_id_token(cls, id_token, expected_nonce):
        return {"iss": "https://issuer.example", "sub": "new-sub"}

    async def fake_userinfo(cls, access_token):
        return {"sub": "new-sub", "preferred_username": "new-user", "email": " Alice@Example.com "}

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_id_token", classmethod(fake_verify_id_token))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))

    response = await oidc_service.oidc_callback_handler("code", "state", oidc_session)

    assert response.status_code == 302
    assert "该邮箱已绑定其他第三方身份" in unquote(response.headers["location"])
    assert await oidc_service.find_user_by_external_identity(oidc_session, "https://issuer.example", "new-sub") is None


async def test_callback_provider_error_is_redirected_without_echoing_description(oidc_session, monkeypatch):
    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/", "nonce": "nonce", "code_verifier": "verifier"}),
    )
    response = await oidc_service.oidc_callback_handler(
        None,
        "state",
        oidc_session,
        error="access_denied",
        error_description="internal provider detail",
    )

    assert response.status_code == 302
    assert "internal+provider+detail" not in response.headers["location"]
    assert "oidc_error=" in response.headers["location"]


async def test_callback_provider_error_with_invalid_state_reports_expired_session(oidc_session, monkeypatch):
    monkeypatch.setattr(oidc_service.OIDCUtils, "verify_state", classmethod(lambda cls, state: None))

    response = await oidc_service.oidc_callback_handler(None, "invalid-state", oidc_session, error="access_denied")

    assert response.status_code == 302
    assert "登录会话已过期" in unquote(response.headers["location"])


async def test_verify_id_token_requires_verified_claims_and_nonce(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    metadata = SimpleNamespace(issuer="https://issuer.example", id_token_signing_alg_values_supported=("RS256",))
    now = int(time.time())
    id_token = jwt.encode(
        {
            "iss": "https://issuer.example",
            "sub": "subject",
            "aud": "client-id",
            "iat": now,
            "exp": now + 300,
            "nonce": "expected-nonce",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "client-id")

    async def fake_metadata(cls):
        return metadata

    async def fake_signing_jwk(cls, received_metadata, received_token):
        assert received_metadata is metadata
        assert received_token == id_token
        return SimpleNamespace(algorithm_name="RS256", key=private_key.public_key())

    monkeypatch.setattr(oidc_service.OIDCUtils, "get_metadata", classmethod(fake_metadata))
    monkeypatch.setattr(oidc_service.OIDCUtils, "_get_signing_jwk", classmethod(fake_signing_jwk))

    assert (await oidc_service.OIDCUtils.verify_id_token(id_token, "expected-nonce"))["sub"] == "subject"
    assert await oidc_service.OIDCUtils.verify_id_token(id_token, "wrong-nonce") is None

    monkeypatch.setattr(oidc_service.oidc_config, "id_token_algorithms", ("ES256",))
    assert await oidc_service.OIDCUtils.verify_id_token(id_token, "expected-nonce") is None


async def test_external_identity_is_scoped_by_issuer(oidc_session):
    first = await _create_user(oidc_session, "first")
    second = User(username="bob", uid="second", password_hash="x", role="user", is_deleted=0)
    oidc_session.add(second)
    await oidc_session.commit()
    await oidc_session.refresh(second)

    await oidc_service.bind_external_identity(
        oidc_session, first, "https://issuer-a.example", "same-sub", "a@example.com"
    )
    await oidc_service.bind_external_identity(
        oidc_session, second, "https://issuer-b.example", "same-sub", "b@example.com"
    )

    first_identity = await oidc_service.find_user_by_external_identity(
        oidc_session, "https://issuer-a.example", "same-sub"
    )
    second_identity = await oidc_service.find_user_by_external_identity(
        oidc_session, "https://issuer-b.example", "same-sub"
    )
    assert first_identity.id == first.id
    assert second_identity.id == second.id


async def test_external_identity_rejects_email_bound_to_another_identity(oidc_session):
    first = await _create_user(oidc_session, "first")
    second = User(username="bob", uid="second", password_hash="x", role="user", is_deleted=0)
    oidc_session.add(second)
    await oidc_session.commit()
    await oidc_session.refresh(second)
    await oidc_service.bind_external_identity(
        oidc_session, first, "https://issuer-a.example", "subject-a", "alice@example.com"
    )

    with pytest.raises(oidc_service.OIDCIdentityConflict):
        await oidc_service.bind_external_identity(
            oidc_session, second, "https://issuer-b.example", "subject-b", "alice@example.com"
        )


async def test_external_identity_translates_concurrent_email_conflict(oidc_session, monkeypatch):
    first = await _create_user(oidc_session, "first")
    second = User(username="bob", uid="second", password_hash="x", role="user", is_deleted=0)
    oidc_session.add(second)
    await oidc_session.commit()
    await oidc_service.bind_external_identity(
        oidc_session, first, "https://issuer-a.example", "subject-a", "alice@example.com"
    )

    real_find_by_email = oidc_service.find_external_identity_by_email
    calls = 0

    async def hide_first_email_lookup(db, email):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return await real_find_by_email(db, email)

    monkeypatch.setattr(oidc_service, "find_external_identity_by_email", hide_first_email_lookup)

    with pytest.raises(oidc_service.OIDCIdentityConflict):
        await oidc_service.bind_external_identity(
            oidc_session, second, "https://issuer-b.example", "subject-b", "alice@example.com"
        )


async def test_legacy_sub_binding_requires_explicit_matching_issuer(oidc_session, monkeypatch):
    user = await _create_user(oidc_session)
    await oidc_service._create_oidc_binding_placeholder(oidc_session, "same-sub", user)
    monkeypatch.setattr(oidc_service.oidc_config, "legacy_issuer_url", "https://old-issuer.example")

    assert (
        await oidc_service.find_legacy_user_by_oidc_sub(oidc_session, "https://new-issuer.example", "same-sub") is None
    )
    resolved = await oidc_service.find_legacy_user_by_oidc_sub(oidc_session, "https://old-issuer.example", "same-sub")
    assert resolved.id == user.id


async def test_created_oidc_user_uses_employee_number_from_email_as_uid(oidc_session, monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", False)
    monkeypatch.setattr(oidc_service.oidc_config, "default_role", "user")
    subject = "subject-123"

    user = await oidc_service.create_oidc_user(
        oidc_session,
        {
            "sub": subject,
            "name": "张三疯",
            "username": "zhang-sanfeng",
            "avatar": "https://issuer.example/avatars/zhang.png",
        },
        "https://issuer.example",
        "790100005580@cn-ne.cn",
    )

    assert user.uid == "790100005580"
    assert user.avatar == "https://issuer.example/avatars/zhang.png"
    identity = await oidc_service.find_user_by_external_identity(oidc_session, "https://issuer.example", subject)
    assert identity.id == user.id


async def test_oidc_login_without_avatar_keeps_existing_avatar(oidc_session):
    user = await _create_user(oidc_session)
    user.avatar = "/api/files/avatar/alice.png"
    await oidc_session.commit()

    await oidc_service.update_oidc_user_login(oidc_session, user, None)

    await oidc_session.refresh(user)
    assert user.avatar == "/api/files/avatar/alice.png"


async def test_create_oidc_user_rejects_employee_uid_owned_by_another_account(oidc_session, monkeypatch):
    await _create_user(oidc_session, "790100005580")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", False)
    monkeypatch.setattr(oidc_service.oidc_config, "default_role", "user")

    with pytest.raises(oidc_service.HTTPException) as exc_info:
        await oidc_service.create_oidc_user(
            oidc_session,
            {"sub": "new-subject", "name": "张三疯", "username": "zhang-sanfeng"},
            "https://issuer.example",
            "790100005580@cn-ne.cn",
        )

    assert exc_info.value.status_code == 409
    assert "员工编号 790100005580 已被其他账号使用" in exc_info.value.detail


async def test_oidc_issuer_base_url_takes_precedence(monkeypatch):
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_BASE_URL", "https://base.example")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://legacy.example")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")

    config = oidc_service.OIDCConfig.from_env()

    assert config.issuer_url == "https://base.example"


async def test_oidc_default_role_rejects_superadmin():
    with pytest.raises(ValueError, match="OIDC_DEFAULT_ROLE"):
        oidc_service.OIDCConfig(
            enabled=True,
            issuer_url="https://issuer.example",
            client_id="client",
            default_role="superadmin",
        )


async def test_oidc_config_requires_absolute_https_redirect_uri():
    config = oidc_service.OIDCConfig(
        enabled=True,
        issuer_url="https://issuer.example",
        client_id="client",
        client_secret="secret",
        redirect_uri="/api/auth/oidc/callback",
    )
    assert config.is_configured() is False
    assert config.is_token_exchange_configured() is False

    config.redirect_uri = "http://localhost:5173/api/auth/oidc/callback"
    assert config.is_configured() is True

    config.redirect_uri = "http://example.com/api/auth/oidc/callback"
    assert config.is_configured() is False

    config.redirect_uri = "https://yuxi.example/api/auth/oidc/callback"
    assert config.is_token_exchange_configured() is True


async def test_oidc_auto_create_rejects_missing_default_role(oidc_session, monkeypatch):
    monkeypatch.setattr(oidc_service.oidc_config, "default_role", "missing-role")

    with pytest.raises(oidc_service.HTTPException) as exc_info:
        await oidc_service.create_oidc_user(
            oidc_session,
            {"sub": "new-user", "name": "New User", "username": "new-user"},
            "https://issuer.example",
            "new_user@example.com",
            department_id=1,
        )

    assert exc_info.value.status_code == 500
    assert "默认角色不存在" in exc_info.value.detail
