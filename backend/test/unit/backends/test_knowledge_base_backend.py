from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.agents.backends.knowledge_base_backend as knowledge_base_backend
from yuxi.knowledge.read_models import KnowledgeBaseSummary


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_uses_agent_bound_knowledge(monkeypatch):
    import yuxi.knowledge.runtime as knowledge_runtime

    async def fake_get_databases():
        return [
            KnowledgeBaseSummary(
                kb_id="bound-id",
                name="Agent Bound",
                description=None,
                kb_type="milvus",
                embedding_model_spec=None,
                llm_model_spec=None,
                query_params={},
                additional_params={},
                share_config={"version": 2, "read_scope": None, "manage_scope": None},
                created_by=None,
                created_at=None,
            )
        ]

    async def fail_get_databases_by_uid(_uid):
        raise AssertionError("显式绑定知识库不应依赖用户的知识库读取范围")

    monkeypatch.setattr(knowledge_runtime.knowledge_base, "get_databases", fake_get_databases)
    monkeypatch.setattr(knowledge_runtime.knowledge_base, "get_databases_by_uid", fail_get_databases_by_uid)

    context = SimpleNamespace(uid="u1", knowledges=["bound-id", "missing-id"])

    databases = await knowledge_base_backend.resolve_visible_knowledge_bases_for_context(context)

    assert databases == [
        {
            "kb_id": "bound-id",
            "name": "Agent Bound",
            "description": None,
            "kb_type": "milvus",
        }
    ]


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_without_binding_uses_user_scope(monkeypatch):
    import yuxi.knowledge.runtime as knowledge_runtime

    async def fake_get_databases_by_uid(uid):
        assert uid == "u1"
        return [
            KnowledgeBaseSummary(
                kb_id="shared-id",
                name="Shared",
                description=None,
                kb_type="milvus",
                embedding_model_spec=None,
                llm_model_spec=None,
                query_params={},
                additional_params={},
                share_config={"version": 2, "read_scope": None, "manage_scope": None},
                created_by=None,
                created_at=None,
            )
        ]

    monkeypatch.setattr(knowledge_runtime.knowledge_base, "get_databases_by_uid", fake_get_databases_by_uid)

    context = SimpleNamespace(uid="u1", knowledges=None)

    databases = await knowledge_base_backend.resolve_visible_knowledge_bases_for_context(context)

    assert [database["kb_id"] for database in databases] == ["shared-id"]
