import pytest

from server.routers import knowledge_eval_router, knowledge_router
from yuxi.knowledge.read_models import KnowledgeBaseDetail


def _database_detail() -> KnowledgeBaseDetail:
    """构造包含模型配置的知识库详情读取模型。"""

    return KnowledgeBaseDetail(
        kb_id="kb-1",
        name="测试知识库",
        description=None,
        kb_type="milvus",
        embedding_model_spec="provider:embedding",
        llm_model_spec="provider:chat",
        query_params={},
        additional_params={},
        share_config={},
        created_by="user-1",
        created_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("router", [knowledge_router, knowledge_eval_router])
async def test_database_model_access_reads_typed_detail_fields(monkeypatch, router):
    """知识库模型权限校验应遵循详情读取模型的属性契约。"""

    async def fake_get_database_info(_kb_id):
        return _database_detail()

    checked = []

    def fake_assert(_current_user, model_spec, model_type=None):
        checked.append((model_spec, model_type))

    monkeypatch.setattr(router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(router, "assert_model_spec_allowed", fake_assert)

    database = await router._assert_database_models_allowed("kb-1", object())

    if router is knowledge_router:
        assert database == _database_detail()
    else:
        assert database is None
    assert checked == [
        ("provider:embedding", "embedding"),
        ("provider:chat", "chat"),
    ]
