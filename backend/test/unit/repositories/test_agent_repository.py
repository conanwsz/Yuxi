from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEFAULT_AGENT_DESCRIPTION,
    DEFAULT_SHARE_CONFIG,
    GENERAL_PURPOSE_AGENT_DESCRIPTION,
    GENERAL_PURPOSE_AGENT_NAME,
    GENERAL_PURPOSE_AGENT_SLUG,
    SUB_AGENT_BACKEND_ID,
    user_can_access_agent,
    user_can_delete_agent,
    user_can_manage_agent,
)
from yuxi.storage.postgres.models_business import Agent, User


class FakeDb:
    def __init__(self):
        self.added = None
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, item):
        self.added = item


@pytest.mark.asyncio
async def test_ensure_default_agent_creates_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_default_agent()

    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.config_json == {"context": {}}
    assert db.added is agent
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_default_agent_backfills_missing_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        share_config=DEFAULT_SHARE_CONFIG.copy(),
        is_default=True,
        description=None,
        updated_by=None,
        updated_at=None,
    )

    async def get_by_slug(_slug):
        return agent

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    result = await repo.ensure_default_agent(created_by="admin")

    assert result is agent
    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.updated_by == "admin"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_general_purpose_subagent_creates_empty_config_subagent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_general_purpose_subagent(created_by="system")

    assert agent.slug == GENERAL_PURPOSE_AGENT_SLUG
    assert agent.name == GENERAL_PURPOSE_AGENT_NAME
    assert agent.description == GENERAL_PURPOSE_AGENT_DESCRIPTION
    assert agent.backend_id == SUB_AGENT_BACKEND_ID
    assert agent.is_subagent is True
    assert agent.is_default is False
    assert agent.config_json == {"context": {}}
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.created_by == "system"
    assert db.added is agent
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_general_purpose_subagent_is_idempotent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    existing = SimpleNamespace(slug=GENERAL_PURPOSE_AGENT_SLUG, config_json={"context": {"model": "custom:model"}})

    async def get_by_slug(_slug):
        return existing

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_general_purpose_subagent()

    assert agent is existing
    assert db.added is None
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_for_normal_user_forces_private_share(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def fake_unique_slug(_slug, _name):
        return "personal-bot"

    monkeypatch.setattr(repo, "_unique_slug", fake_unique_slug)

    creator = User(username="user", uid="user", password_hash="x", role="user", department_id=1)
    agent = await repo.create(
        name="Personal Bot",
        backend_id="ChatbotAgent",
        slug="personal-bot",
        share_config={"access_level": "global", "department_ids": [], "user_uids": []},
        created_by="user",
        creator=creator,
    )

    assert agent.share_config == {"access_level": "user", "department_ids": [], "user_uids": ["user"]}
    assert db.added is agent


@pytest.mark.asyncio
async def test_manageable_agents_do_not_require_use_access():
    manageable = Agent(
        slug="manageable-only",
        name="Manageable Only",
        backend_id="ChatbotAgent",
        created_by="owner",
        share_config={"access_level": "user", "department_ids": [], "user_uids": []},
        manage_config={"access_level": "user", "department_ids": [], "user_uids": ["manager"]},
    )
    db = FakeDb()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [manageable])))
    repo = AgentRepository(db)
    user = User(username="manager", uid="manager", password_hash="x", role="admin", department_id=1)
    user.permission_keys = {"agents.update"}

    assert await repo.list_visible(user=user) == []
    assert await repo.list_manageable(user=user) == [manageable]


def test_shared_agent_is_accessible_but_not_manageable_for_normal_user():
    user = User(username="user", uid="user", password_hash="x", role="user", department_id=1)
    agent = Agent(
        slug="shared-bot",
        name="Shared Bot",
        backend_id="ChatbotAgent",
        created_by="other",
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["user"]},
    )

    assert user_can_access_agent(user, agent) is True
    assert user_can_manage_agent(user, agent) is False


def test_agent_use_manage_and_delete_scopes_are_independent():
    user = User(username="manager", uid="manager", password_hash="x", role="user", department_id=3)
    user.organization_department_ids = {3}
    user.organization_membership_paths = [{1, 2, 3}]
    agent = Agent(
        slug="managed-bot",
        name="Managed Bot",
        backend_id="ChatbotAgent",
        created_by="owner",
        share_config={"access_level": "user", "department_ids": [], "user_uids": []},
        manage_config={
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [],
            "user_uids": [],
        },
    )

    assert user_can_access_agent(user, agent) is False
    assert user_can_manage_agent(user, agent) is True
    assert user_can_delete_agent(user, agent) is False


@pytest.mark.asyncio
async def test_serialized_agent_exposes_independent_action_flags(monkeypatch):
    from yuxi.agents.buildin import agent_manager

    monkeypatch.setattr(agent_manager, "get_agent", lambda _backend_id: None)
    db = FakeDb()
    repo = AgentRepository(db)
    user = User(username="manager", uid="manager", password_hash="x", role="admin", department_id=1)
    user.permission_keys = {"agents.update", "agents.delete"}
    agent = Agent(
        slug="managed-bot",
        name="Managed Bot",
        backend_id="missing-backend",
        created_by="owner",
        share_config={"access_level": "user", "department_ids": [], "user_uids": []},
        manage_config={"access_level": "user", "department_ids": [], "user_uids": ["manager"]},
    )

    data = await repo.serialize(agent, user=user)

    assert data["can_access"] is False
    assert data["can_manage"] is True
    assert data["can_delete"] is False
