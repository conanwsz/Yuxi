from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services.oidc_provider import (
    OIDCCallbackData,
    OIDCHTTPRequest,
    OIDCProfile,
    OIDCProviderAdapter,
    OIDCTokenSet,
    StandardOIDCProvider,
    get_oidc_provider,
    normalize_provider_type,
)


pytestmark = [pytest.mark.unit]


@pytest.fixture
def standard_config() -> SimpleNamespace:
    return SimpleNamespace(
        force_prompt_login=True,
        username_claim="custom_username",
        email_claim="work_email",
        name_claim="display_name",
        fetch_department_info=True,
        department_claim="org_unit",
    )


def test_get_oidc_provider_defaults_to_standard_and_normalizes_type(standard_config):
    provider = get_oidc_provider(None, standard_config)
    normalized_provider = get_oidc_provider(" Standard ", standard_config)

    assert isinstance(provider, StandardOIDCProvider)
    assert isinstance(normalized_provider, StandardOIDCProvider)
    assert normalize_provider_type("") == "standard"
    assert normalize_provider_type(" STANDARD ") == "standard"


def test_get_oidc_provider_rejects_unknown_type(standard_config):
    with pytest.raises(ValueError, match="支持的类型: standard"):
        get_oidc_provider("custom-provider", standard_config)


def test_standard_provider_parses_query_callback(standard_config):
    provider = StandardOIDCProvider(standard_config)

    callback = provider.parse_callback(
        query={
            "code": "query-code",
            "state": "query-state",
            "error": "access_denied",
            "error_description": "query description",
        },
        form=None,
    )

    assert callback == OIDCCallbackData(
        code="query-code",
        state="query-state",
        error="access_denied",
        error_description="query description",
    )


def test_standard_provider_parses_form_callback(standard_config):
    provider = StandardOIDCProvider(standard_config)

    callback = provider.parse_callback(
        query=None,
        form={
            "code": ["form-code"],
            "state": ["form-state"],
            "error": None,
            "error_description": None,
        },
    )

    assert callback == OIDCCallbackData(code="form-code", state="form-state")


def test_standard_provider_rejects_conflicting_callback_sources(standard_config):
    provider = StandardOIDCProvider(standard_config)

    with pytest.raises(ValueError, match="query 和 form"):
        provider.parse_callback(
            query={"state": "query-state"},
            form={"state": "form-state"},
        )


def test_standard_provider_rejects_multiple_callback_values(standard_config):
    provider = StandardOIDCProvider(standard_config)

    with pytest.raises(ValueError, match="多个值"):
        provider.parse_callback(
            query={"state": ["first", "second"]},
            form=None,
        )


def test_standard_provider_rejects_oversized_callback_value(standard_config):
    provider = StandardOIDCProvider(standard_config)

    with pytest.raises(ValueError, match="过长"):
        provider.parse_callback(
            query={"code": "x" * 4097},
            form=None,
        )


def test_standard_provider_builds_authorization_params(standard_config):
    provider = StandardOIDCProvider(standard_config)

    params = provider.authorization_params(
        {
            "client_id": "client-id",
            "response_type": "code",
            "state": "state-value",
        }
    )

    assert params == {
        "client_id": "client-id",
        "response_type": "code",
        "state": "state-value",
        "prompt": "login",
    }


def test_standard_provider_builds_token_request(standard_config):
    provider = StandardOIDCProvider(standard_config)
    metadata = SimpleNamespace(token_endpoint="https://issuer.example/token")

    request = provider.token_request(
        {
            "grant_type": "authorization_code",
            "code": "auth-code",
            "client_secret": "client-secret",
        },
        metadata,
    )

    assert request == OIDCHTTPRequest(
        method="POST",
        url="https://issuer.example/token",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        form={
            "grant_type": "authorization_code",
            "code": "auth-code",
            "client_secret": "client-secret",
        },
    )
    assert "client-secret" not in repr(request)


def test_standard_provider_parses_token_response_and_hides_sensitive_repr(standard_config):
    provider = StandardOIDCProvider(standard_config)

    token_set = provider.parse_token_response(
        {
            "access_token": "secret-access-token",
            "id_token": "secret-id-token",
            "refresh_token": "secret-refresh-token",
            "token_type": "Bearer",
            "scope": "openid profile",
            "expires_in": 300,
        }
    )

    assert token_set == OIDCTokenSet(
        access_token="secret-access-token",
        id_token="secret-id-token",
        refresh_token="secret-refresh-token",
        token_type="Bearer",
        scope="openid profile",
        expires_in=300,
        raw={
            "access_token": "secret-access-token",
            "id_token": "secret-id-token",
            "refresh_token": "secret-refresh-token",
            "token_type": "Bearer",
            "scope": "openid profile",
            "expires_in": 300,
        },
    )
    token_repr = repr(token_set)
    assert "secret-access-token" not in token_repr
    assert "secret-id-token" not in token_repr
    assert "secret-refresh-token" not in token_repr


def test_standard_provider_builds_userinfo_request(standard_config):
    provider = StandardOIDCProvider(standard_config)
    metadata = SimpleNamespace(userinfo_endpoint="https://issuer.example/userinfo")

    request = provider.userinfo_request("secret-access-token", metadata)

    assert request == OIDCHTTPRequest(
        method="GET",
        url="https://issuer.example/userinfo",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer secret-access-token",
        },
    )
    assert "secret-access-token" not in repr(request)


def test_standard_provider_maps_profile_with_existing_claim_semantics(standard_config):
    provider = StandardOIDCProvider(standard_config)

    profile = provider.map_profile(
        {
            "sub": "subject-1234567890",
            "custom_username": "",
            "preferred_username": "alice",
            "work_email": "",
            "email": "alice@example.com",
            "display_name": "",
            "name": "Alice Example",
            "picture": " https://issuer.example/avatar.png ",
            "org_unit": "",
            "department": "研发部",
            "department_desc": "负责平台研发",
        },
        {"sub": "subject-1234567890"},
    )

    assert profile == OIDCProfile(
        subject="subject-1234567890",
        username="alice",
        email="alice@example.com",
        name="Alice Example",
        avatar="https://issuer.example/avatar.png",
        department_name="研发部",
        department_description="负责平台研发",
        raw={
            "sub": "subject-1234567890",
            "custom_username": "",
            "preferred_username": "alice",
            "work_email": "",
            "email": "alice@example.com",
            "display_name": "",
            "name": "Alice Example",
            "picture": " https://issuer.example/avatar.png ",
            "org_unit": "",
            "department": "研发部",
            "department_desc": "负责平台研发",
        },
    )


def test_standard_provider_falls_back_to_email_prefix_and_subject(standard_config):
    provider = StandardOIDCProvider(
        SimpleNamespace(
            force_prompt_login=False,
            username_claim="username",
            email_claim="email",
            name_claim="name",
            fetch_department_info=False,
            department_claim="department",
        )
    )

    profile = provider.map_profile(
        {
            "sub": "subject-1234567890-abcdef",
            "email": "fallback@example.com",
        },
        {"sub": "subject-1234567890-abcdef"},
    )

    assert profile.username == "fallback"
    assert profile.name == "fallback"

    profile_without_email = provider.map_profile(
        {"sub": "subject-1234567890-abcdef"},
        {"sub": "subject-1234567890-abcdef"},
    )
    assert profile_without_email.username == "subject-1234567890-a"
    assert profile_without_email.name == "subject-1234567890-a"


class FixtureOIDCProvider(OIDCProviderAdapter):
    """测试专用适配器，用于证明差异都能封装在 Provider 内。"""

    provider_type = "fixture"

    def parse_callback(self, query, form) -> OIDCCallbackData:
        source = query or form or {}
        return OIDCCallbackData(
            code=self._single(source, "auth_code"),
            state=self._single(source, "session_state"),
            error=self._single(source, "provider_error"),
            error_description=self._single(source, "provider_error_detail"),
        )

    def authorization_params(self, common_params):
        return dict(common_params) | {"resource": "knowledge-center"}

    def token_request(self, common_form, metadata) -> OIDCHTTPRequest:
        return OIDCHTTPRequest(
            method="POST",
            url=metadata.token_endpoint,
            headers={"Accept": "application/json", "X-Provider": "fixture"},
            form=dict(common_form) | {"client_auth_mode": "body"},
        )

    def parse_token_response(self, payload) -> OIDCTokenSet:
        tokens = payload["data"]["tokens"]
        return OIDCTokenSet(
            access_token=tokens["access"],
            id_token=tokens["id"],
            refresh_token=tokens.get("refresh"),
            raw=dict(payload),
        )

    def userinfo_request(self, access_token, metadata) -> OIDCHTTPRequest:
        return OIDCHTTPRequest(
            method="GET",
            url=metadata.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}", "X-Profile-Mode": "nested"},
        )

    def map_profile(self, userinfo_payload, verified_id_token_claims) -> OIDCProfile:
        profile = userinfo_payload["payload"]["profile"]
        org = userinfo_payload["payload"]["organization"]
        return OIDCProfile(
            subject=verified_id_token_claims["sub"],
            username=profile["account"]["alias"],
            email=profile["contacts"]["primary_email"],
            name=profile["display"]["full_name"],
            avatar=profile["display"].get("avatar_url"),
            department_name=org["department"]["name"],
            department_description=org["department"].get("description"),
            raw=dict(userinfo_payload),
        )

    @staticmethod
    def _single(source, field_name: str) -> str | None:
        value = source.get(field_name)
        if isinstance(value, list):
            return value[0] if value else None
        return value


def test_fixture_provider_demonstrates_alias_wrapper_and_nested_claim_compatibility():
    provider = FixtureOIDCProvider(SimpleNamespace())
    metadata = SimpleNamespace(
        token_endpoint="https://issuer.example/token",
        userinfo_endpoint="https://issuer.example/userinfo",
    )

    callback = provider.parse_callback(
        query={"auth_code": "alias-code", "session_state": "alias-state"},
        form=None,
    )
    authorization_params = provider.authorization_params({"client_id": "client-id"})
    token_request = provider.token_request({"code": "alias-code"}, metadata)
    token_set = provider.parse_token_response(
        {
            "data": {
                "tokens": {
                    "access": "wrapped-access-token",
                    "id": "wrapped-id-token",
                    "refresh": "wrapped-refresh-token",
                }
            }
        }
    )
    userinfo_request = provider.userinfo_request("wrapped-access-token", metadata)
    profile = provider.map_profile(
        {
            "payload": {
                "profile": {
                    "account": {"alias": "alice"},
                    "contacts": {"primary_email": "alice@example.com"},
                    "display": {
                        "full_name": "Alice Example",
                        "avatar_url": "https://issuer.example/avatar.png",
                    },
                },
                "organization": {
                    "department": {
                        "name": "研发平台部",
                        "description": "负责 OIDC 接入",
                    }
                },
            }
        },
        {"sub": "verified-subject"},
    )

    assert callback == OIDCCallbackData(code="alias-code", state="alias-state")
    assert authorization_params == {"client_id": "client-id", "resource": "knowledge-center"}
    assert token_request == OIDCHTTPRequest(
        method="POST",
        url="https://issuer.example/token",
        headers={"Accept": "application/json", "X-Provider": "fixture"},
        form={"code": "alias-code", "client_auth_mode": "body"},
    )
    assert token_set.access_token == "wrapped-access-token"
    assert token_set.id_token == "wrapped-id-token"
    assert token_set.refresh_token == "wrapped-refresh-token"
    assert userinfo_request == OIDCHTTPRequest(
        method="GET",
        url="https://issuer.example/userinfo",
        headers={
            "Authorization": "Bearer wrapped-access-token",
            "X-Profile-Mode": "nested",
        },
    )
    assert profile == OIDCProfile(
        subject="verified-subject",
        username="alice",
        email="alice@example.com",
        name="Alice Example",
        avatar="https://issuer.example/avatar.png",
        department_name="研发平台部",
        department_description="负责 OIDC 接入",
        raw={
            "payload": {
                "profile": {
                    "account": {"alias": "alice"},
                    "contacts": {"primary_email": "alice@example.com"},
                    "display": {
                        "full_name": "Alice Example",
                        "avatar_url": "https://issuer.example/avatar.png",
                    },
                },
                "organization": {
                    "department": {
                        "name": "研发平台部",
                        "description": "负责 OIDC 接入",
                    }
                },
            }
        },
    )
