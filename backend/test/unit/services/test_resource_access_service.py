from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.resource_access_service import (
    KNOWLEDGE_BASE_TOOL_SLUGS,
    ResourceAccessValidationError,
    attach_user_resource_access,
    build_resource_catalog,
    default_resource_access_all,
    get_default_model_for_type,
    normalize_resource_access,
    normalize_stored_resource_access,
    resolve_user_resource_access,
)
from yuxi.storage.postgres.models_business import Base, MCPServer, ModelProvider, Role, User


@pytest.fixture()
async def resource_access_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_resource_catalog_lists_models_tools_and_mcp(resource_access_session, monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.resource_access_service.get_tool_metadata",
        lambda: [
            {"slug": "search_web", "name": "搜索网络", "description": "desc", "category": "buildin", "tags": []},
            {
                "slug": "find_kb_document",
                "name": "find_kb_document",
                "description": "knowledge tool",
                "category": "knowledge",
                "tags": ["知识库"],
            },
        ],
    )
    resource_access_session.add(
        ModelProvider(
            provider_id="openai",
            display_name="OpenAI",
            provider_type="openai",
            base_url="https://example.com",
            capabilities=["chat", "embedding"],
            enabled_models=[
                {"id": "gpt-4.1", "type": "chat", "display_name": "GPT-4.1"},
                {"id": "text-embedding-3-large", "type": "embedding", "display_name": "Embedding"},
            ],
            is_enabled=True,
        )
    )
    resource_access_session.add(
        MCPServer(
            slug="chart",
            name="Chart",
            description="chart server",
            transport="stdio",
            command="npx",
            args=["demo"],
            enabled=1,
            created_by="system",
            updated_by="system",
        )
    )
    await resource_access_session.commit()

    catalog = await build_resource_catalog(resource_access_session)

    assert catalog["models"][0]["key"] == "openai:gpt-4.1"
    assert catalog["models"][1]["key"] == "openai:text-embedding-3-large"
    assert catalog["tools"] == [
        {
            "key": "search_web",
            "name": "搜索网络",
            "description": "desc",
            "category": "buildin",
            "tags": [],
            "enabled": True,
        }
    ]
    assert catalog["mcp_servers"][0]["key"] == "chart"


def test_stored_resource_access_removes_knowledge_base_tools_from_tool_allowlist():
    access = {
        "models": {"mode": "none", "allowed": [], "defaults": {}},
        "tools": {"mode": "selected", "allowed": ["search_web", *KNOWLEDGE_BASE_TOOL_SLUGS]},
        "mcp_servers": {"mode": "none", "allowed": []},
    }

    normalized = normalize_stored_resource_access(access)

    assert normalized["tools"]["allowed"] == ["search_web"]


def test_normalize_resource_access_requires_defaults_for_selected_model_types():
    catalog = {
        "models": [
            {"key": "openai:gpt-4.1", "type": "chat"},
            {"key": "openai:text-embedding-3-large", "type": "embedding"},
        ],
        "tools": [{"key": "search_web"}],
        "mcp_servers": [{"key": "chart"}],
    }

    with pytest.raises(ResourceAccessValidationError, match="默认模型"):
        normalize_resource_access(
            {
                "models": {
                    "mode": "selected",
                    "allowed": ["openai:gpt-4.1", "openai:text-embedding-3-large"],
                    "defaults": {"chat": "openai:gpt-4.1"},
                },
                "tools": {"mode": "selected", "allowed": ["search_web"]},
                "mcp_servers": {"mode": "selected", "allowed": ["chart"]},
            },
            catalog=catalog,
        )


def test_normalize_resource_access_keeps_existing_retired_resources():
    catalog = {
        "models": [{"key": "openai:gpt-4.1", "type": "chat"}],
        "tools": [{"key": "search_web"}],
        "mcp_servers": [{"key": "chart"}],
    }
    existing = {
        "models": {
            "mode": "selected",
            "allowed": ["legacy:offline-chat"],
            "defaults": {"chat": "legacy:offline-chat"},
        },
        "tools": {"mode": "selected", "allowed": ["offline_tool"]},
        "mcp_servers": {"mode": "selected", "allowed": ["offline_mcp"]},
    }

    normalized = normalize_resource_access(
        {
            "models": {
                "mode": "selected",
                "allowed": ["legacy:offline-chat"],
                "defaults": {"chat": "legacy:offline-chat"},
            },
            "tools": {"mode": "selected", "allowed": ["offline_tool"]},
            "mcp_servers": {"mode": "selected", "allowed": ["offline_mcp"]},
        },
        catalog=catalog,
        existing=existing,
    )

    assert normalized == existing

    with pytest.raises(ResourceAccessValidationError, match="未知工具"):
        normalize_resource_access(
            {
                "models": {"mode": "all", "allowed": [], "defaults": {}},
                "tools": {"mode": "selected", "allowed": ["brand_new_tool"]},
                "mcp_servers": {"mode": "none", "allowed": []},
            },
            catalog=catalog,
            existing=existing,
        )


@pytest.mark.asyncio
async def test_resolve_and_attach_user_resource_access(resource_access_session):
    role = Role(
        key="reviewer",
        name="审阅员",
        permissions=[],
        resource_access={
            "models": {
                "mode": "selected",
                "allowed": ["openai:gpt-4.1"],
                "defaults": {"chat": "openai:gpt-4.1"},
            },
            "tools": {"mode": "selected", "allowed": ["search_web"]},
            "mcp_servers": {"mode": "none", "allowed": []},
        },
    )
    user = User(
        username="reviewer",
        uid="reviewer",
        password_hash="hashed",
        role="reviewer",
        department_id=1,
    )
    resource_access_session.add(role)
    resource_access_session.add(user)
    await resource_access_session.commit()

    resolved = await resolve_user_resource_access(resource_access_session, user)
    assert resolved["tools"]["allowed"] == ["search_web"]
    assert get_default_model_for_type(resolved, "chat") == "openai:gpt-4.1"

    attached = await attach_user_resource_access(resource_access_session, user)
    assert attached.resource_access["models"]["allowed"] == ["openai:gpt-4.1"]

    superadmin = SimpleNamespace(role="superadmin")
    assert await resolve_user_resource_access(resource_access_session, superadmin) == default_resource_access_all()
