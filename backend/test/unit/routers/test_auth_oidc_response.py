from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers import auth_router


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _oidc_test_app() -> FastAPI:
    """创建仅用于 OIDC 路由契约测试的应用。"""
    app = FastAPI()
    app.include_router(auth_router.auth, prefix="/api")

    async def fake_db():
        yield object()

    app.dependency_overrides[auth_router.get_db] = fake_db
    return app


async def test_oidc_exchange_preserves_role_permissions(monkeypatch):
    async def fake_exchange_code_handler(code: str) -> dict:
        assert code == "login-code"
        return {
            "access_token": "token",
            "token_type": "bearer",
            "user_id": 7,
            "username": "Test User",
            "uid": "test-user",
            "phone_number": None,
            "avatar": None,
            "role": "user",
            "role_name": "普通用户",
            "permissions": ["agents.read", "knowledge.read"],
            "department_id": 1,
            "department_name": "测试部门",
            "redirect_path": "/agent",
        }

    monkeypatch.setattr(auth_router, "oidc_exchange_code_handler", fake_exchange_code_handler)
    app = FastAPI()
    app.include_router(auth_router.auth, prefix="/api")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/auth/oidc/exchange-code", json={"code": "login-code"})

    assert response.status_code == 200
    assert response.json()["role_name"] == "普通用户"
    assert response.json()["permissions"] == ["agents.read", "knowledge.read"]


@pytest.mark.parametrize(
    ("method", "request_kwargs", "expected_query", "expected_form"),
    [
        (
            "GET",
            {"params": {"code": "query-code", "state": "query-state"}},
            {"code": ["query-code"], "state": ["query-state"]},
            None,
        ),
        (
            "POST",
            {"data": {"code": "form-code", "state": "form-state"}},
            {},
            {"code": ["form-code"], "state": ["form-state"]},
        ),
    ],
)
async def test_oidc_callback_accepts_query_and_form_post(
    monkeypatch,
    method,
    request_kwargs,
    expected_query,
    expected_form,
):
    received = {}

    async def fake_callback_handler(query, form, db, request):
        received.update(query=query, form=form, db=db, method=request.method)
        return auth_router.RedirectResponse("/auth/oidc/callback?code=exchange-code", status_code=302)

    monkeypatch.setattr(auth_router, "oidc_callback_request_handler", fake_callback_handler)
    app = _oidc_test_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/api/auth/oidc/callback", **request_kwargs)

    assert response.status_code == 302
    assert received["query"] == expected_query
    assert received["form"] == expected_form
    assert received["method"] == method


async def test_oidc_callback_rejects_unsupported_post_content_type():
    app = _oidc_test_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/oidc/callback",
            json={"code": "code", "state": "state"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC 回调请求格式不支持"


async def test_oidc_callback_rejects_duplicate_state():
    app = _oidc_test_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/auth/oidc/callback",
            params=[("code", "code"), ("state", "first"), ("state", "second")],
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "oidc_error=" in response.headers["location"]


async def test_oidc_callback_rejects_oversized_form_body():
    app = _oidc_test_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/oidc/callback",
            content=b"code=" + b"x" * 16_384,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC 回调请求过大"
