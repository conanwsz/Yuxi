from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers import auth_router


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


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
