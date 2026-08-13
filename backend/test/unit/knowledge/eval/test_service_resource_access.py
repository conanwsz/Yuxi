from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.knowledge.eval import service as evaluation_service
from yuxi.knowledge.read_models import KnowledgeBaseDetail


def _database_detail(
    *,
    embedding_model_spec: str | None = None,
    llm_model_spec: str | None = None,
) -> KnowledgeBaseDetail:
    """构造评估任务权限复验所需的知识库详情。"""

    return KnowledgeBaseDetail(
        kb_id="kb-1",
        name="测试知识库",
        description=None,
        kb_type="milvus",
        embedding_model_spec=embedding_model_spec,
        llm_model_spec=llm_model_spec,
        query_params={},
        additional_params={},
        share_config={},
        created_by="user-1",
        created_at=None,
    )


@pytest.mark.asyncio
async def test_queued_evaluation_reloads_current_role_and_rechecks_all_models(monkeypatch):
    user = SimpleNamespace(uid="user-1", role="restricted", is_deleted=0)

    class FakeResult:
        def scalar_one_or_none(self):
            return user

    class FakeDb:
        async def execute(self, statement):
            del statement
            return FakeResult()

    @asynccontextmanager
    async def fake_session_context():
        yield FakeDb()

    hydrated = []
    checked = []

    async def fake_hydrate(db, current_user):
        del db
        hydrated.append(current_user.uid)
        current_user.resource_access = {"models": {"mode": "selected", "allowed": []}}
        return current_user

    def fake_assert(current_user, model_spec, model_type=None):
        checked.append((current_user.uid, model_spec, model_type))
        return SimpleNamespace(spec=model_spec, model_type=model_type)

    monkeypatch.setattr(evaluation_service.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(evaluation_service, "hydrate_user_resource_access", fake_hydrate)
    monkeypatch.setattr(
        evaluation_service.knowledge_base,
        "get_database_info",
        lambda kb_id: _async_value(
            _database_detail(
                embedding_model_spec="provider:embedding",
                llm_model_spec="provider:kb-chat",
            )
        ),
    )
    monkeypatch.setattr(evaluation_service, "assert_model_spec_allowed", fake_assert)

    await evaluation_service._assert_task_model_resource_access(
        uid="user-1",
        kb_id="kb-1",
        explicit_models=(("provider:answer", "chat"), ("provider:rerank", "rerank")),
    )

    assert hydrated == ["user-1"]
    assert checked == [
        ("user-1", "provider:embedding", "embedding"),
        ("user-1", "provider:kb-chat", "chat"),
        ("user-1", "provider:answer", "chat"),
        ("user-1", "provider:rerank", "rerank"),
    ]


@pytest.mark.asyncio
async def test_queued_evaluation_stops_when_role_was_downgraded(monkeypatch):
    user = SimpleNamespace(uid="user-1", role="restricted", is_deleted=0)

    class FakeResult:
        def scalar_one_or_none(self):
            return user

    class FakeDb:
        async def execute(self, statement):
            del statement
            return FakeResult()

    @asynccontextmanager
    async def fake_session_context():
        yield FakeDb()

    async def fake_hydrate(db, current_user):
        del db
        return current_user

    def deny_model(current_user, model_spec, model_type=None):
        del current_user, model_type
        raise HTTPException(status_code=403, detail=f"当前角色无权使用模型: '{model_spec}'")

    monkeypatch.setattr(evaluation_service.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(evaluation_service, "hydrate_user_resource_access", fake_hydrate)
    monkeypatch.setattr(
        evaluation_service.knowledge_base,
        "get_database_info",
        lambda kb_id: _async_value(_database_detail(embedding_model_spec="provider:embedding")),
    )
    monkeypatch.setattr(evaluation_service, "assert_model_spec_allowed", deny_model)

    with pytest.raises(HTTPException) as exc:
        await evaluation_service._assert_task_model_resource_access(
            uid="user-1",
            kb_id="kb-1",
            explicit_models=(),
        )
    assert exc.value.status_code == 403


async def _async_value(value):
    return value
