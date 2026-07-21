from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers.chat_router import chat
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.storage.postgres.models_business import User


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat, prefix="/api")

    async def fake_required_user():
        return User(
            username="user",
            uid="user-1",
            password_hash="x",
            role="user",
            department_id=1,
        )

    async def fake_db():
        return None

    app.dependency_overrides[get_required_user] = fake_required_user
    app.dependency_overrides[get_db] = fake_db
    return app


def test_chat_call_uses_allowed_explicit_model(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hydrate(db, user):
        del db
        user.resource_access = {
            "models": {"mode": "selected", "allowed": ["provider:allowed"], "defaults": {"chat": "provider:default"}},
            "tools": {"mode": "all", "allowed": []},
            "mcp_servers": {"mode": "all", "allowed": []},
        }
        return user

    def fake_assert_model_spec_allowed(user, model_spec: str, model_type: str | None = None):
        captured["asserted"] = (str(user.uid), model_spec, model_type)
        return SimpleNamespace(spec=model_spec, model_type=model_type or "chat")

    class FakeModel:
        async def call(self, query: str):
            captured["query"] = query
            return SimpleNamespace(content="ok")

    monkeypatch.setattr("server.routers.chat_router.hydrate_user_resource_access", fake_hydrate)
    monkeypatch.setattr("server.routers.chat_router.assert_model_spec_allowed", fake_assert_model_spec_allowed)
    monkeypatch.setattr("server.routers.chat_router.select_model", lambda model_spec: FakeModel())

    client = TestClient(_build_app())
    response = client.post("/api/chat/call", json={"query": "hello", "meta": {"model_spec": "provider:allowed"}})

    assert response.status_code == 200, response.text
    assert response.json()["response"] == "ok"
    assert captured["asserted"] == ("user-1", "provider:allowed", "chat")
    assert captured["query"] == "hello"


def test_chat_call_rejects_forbidden_model(monkeypatch):
    async def fake_hydrate(db, user):
        del db, user
        return None

    def fake_assert_model_spec_allowed(user, model_spec: str, model_type: str | None = None):
        del user, model_type
        raise HTTPException(status_code=403, detail=f"当前角色无权使用模型: '{model_spec}'")

    monkeypatch.setattr("server.routers.chat_router.hydrate_user_resource_access", fake_hydrate)
    monkeypatch.setattr("server.routers.chat_router.assert_model_spec_allowed", fake_assert_model_spec_allowed)

    client = TestClient(_build_app())
    response = client.post("/api/chat/call", json={"query": "hello", "meta": {"model_spec": "provider:forbidden"}})

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "当前角色无权使用模型: 'provider:forbidden'"
