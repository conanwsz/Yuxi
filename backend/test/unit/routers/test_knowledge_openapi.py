"""知识库三方同步主链路的 OpenAPI 契约测试。"""

import pytest
from fastapi import FastAPI

from server.routers.knowledge_router import knowledge
from server.routers.system_task_router import tasks


pytestmark = pytest.mark.unit


def test_knowledge_sync_openapi_explains_sequence_authentication_and_examples():
    """Swagger 应能告诉三方开发者如何认证，并按顺序组合核心接口。"""
    app = FastAPI()
    app.include_router(knowledge, prefix="/api")
    app.include_router(tasks, prefix="/api")

    schema = app.openapi()
    upload = schema["paths"]["/api/knowledge/files/upload"]["post"]
    ingest = schema["paths"]["/api/knowledge/databases/{kb_id}/documents"]["post"]
    task = schema["paths"]["/api/tasks/{task_id}"]["get"]

    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert upload["security"] == [{"BearerAuth": []}]
    assert upload["summary"] == "上传知识原始文件"
    assert "不会创建知识文档" in upload["description"]
    assert ingest["summary"] == "提交文档解析和可选自动入库任务"
    assert "auto_index=true" in ingest["description"]
    assert task["summary"] == "查询后台任务进度和结果"

    request_schema = ingest["requestBody"]["content"]["application/json"]["schema"]
    request_component = request_schema["$ref"].rsplit("/", 1)[-1]
    params_schema = schema["components"]["schemas"][request_component]["properties"]["params"]
    assert params_schema["examples"][0]["auto_index"] is True
