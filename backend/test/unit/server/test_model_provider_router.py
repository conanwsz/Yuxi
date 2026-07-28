from types import SimpleNamespace

import pytest

from server.routers import model_provider_router
from server.routers.model_provider_router import ModelProviderPayload


def test_model_provider_payload_accepts_embedding_and_rerank_urls():
    payload = ModelProviderPayload(
        provider_id="mixed-provider",
        display_name="Mixed Provider",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/v1/embeddings",
        rerank_base_url="https://api.example.com/v1/rerank",
        capabilities=["chat", "embedding", "rerank"],
    )

    data = payload.model_dump(exclude_none=True)

    assert data["embedding_base_url"] == "https://api.example.com/v1/embeddings"
    assert data["rerank_base_url"] == "https://api.example.com/v1/rerank"


@pytest.mark.asyncio
async def test_update_provider_commits_before_refreshing_cache(monkeypatch):
    calls = []

    class Db:
        async def commit(self):
            calls.append("commit")

    class User:
        username = "admin"

    class Provider:
        def to_dict(self):
            return {"provider_id": "alibaba"}

    async def fake_update_provider_config(db, provider_id, data, username):
        calls.append("update")
        return Provider()

    async def fake_refresh_model_cache():
        calls.append("refresh")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update_provider_config)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fake_refresh_model_cache)

    result = await model_provider_router.update_provider(
        "alibaba",
        ModelProviderPayload(enabled_models=[]),
        current_user=User(),
        db=Db(),
    )

    assert result == {"success": True, "data": {"provider_id": "alibaba"}}
    assert calls == ["update", "commit", "refresh"]


@pytest.mark.asyncio
async def test_v2_models_respects_resource_scope_for_model_managers(monkeypatch):
    from yuxi.models.providers.cache import model_cache

    allowed_model = SimpleNamespace(
        spec="provider:allowed",
        model_id="allowed",
        model_type="chat",
        display_name="Allowed",
        dimension=None,
        batch_size=40,
    )
    forbidden_model = SimpleNamespace(
        spec="provider:forbidden",
        model_id="forbidden",
        model_type="chat",
        display_name="Forbidden",
        dimension=None,
        batch_size=40,
    )
    user = SimpleNamespace(
        role="admin",
        permission_keys={"models.manage"},
        resource_access={
            "models": {
                "mode": "selected",
                "allowed": [allowed_model.spec],
                "defaults": {"chat": allowed_model.spec},
            },
            "tools": {"mode": "all", "allowed": []},
            "mcp_servers": {"mode": "all", "allowed": []},
        },
    )

    monkeypatch.setattr(
        model_cache,
        "get_specs_grouped_by_provider",
        lambda model_type: {"provider": [allowed_model, forbidden_model]},
    )

    async def fake_get_all_model_providers(db):
        return [SimpleNamespace(provider_id="provider", display_name="Provider")]

    monkeypatch.setattr(model_provider_router, "get_all_model_providers", fake_get_all_model_providers)

    result = await model_provider_router.get_v2_models(current_user=user, db=SimpleNamespace())

    assert [model["spec"] for model in result["data"]["provider"]["models"]] == [allowed_model.spec]
    assert result["data"]["provider"]["models"][0]["is_default"] is True
