from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from server.utils.auth_middleware import require_permission
from server.utils.knowledge_auth import _permission_for_request, get_knowledge_user


def _request(method: str, path: str, path_params: dict | None = None) -> Request:
    request_path, _, query = path.partition("?")
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": request_path,
            "query_string": query.encode(),
            "headers": [],
        }
    )
    request.scope["path_params"] = path_params or {}
    return request


@pytest.mark.asyncio
async def test_require_permission_rejects_missing_action_permission():
    dependency = require_permission("users.delete")
    user = SimpleNamespace(role="reviewer", permission_keys={"users.read"})

    with pytest.raises(HTTPException) as exc_info:
        await dependency(current_user=user)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_permission_allows_superadmin_bypass():
    dependency = require_permission("users.delete")
    user = SimpleNamespace(role="superadmin", permission_keys=set())

    assert await dependency(current_user=user) is user


@pytest.mark.parametrize(
    ("method", "path", "permission"),
    [
        ("POST", "/api/knowledge/databases", "knowledge.create"),
        ("GET", "/api/knowledge/types", "knowledge.read"),
        ("GET", "/api/knowledge/chunk-presets", "knowledge.read"),
        ("GET", "/api/knowledge/databases", "knowledge.read"),
        ("DELETE", "/api/knowledge/databases/kb-a", "knowledge.delete"),
        ("PUT", "/api/knowledge/databases/kb-a", "knowledge.update"),
        ("POST", "/api/knowledge/databases/kb-a/documents", "knowledge.documents.manage"),
        ("POST", "/api/knowledge/databases/kb-a/graph-build/index", "knowledge.graph.manage"),
        ("POST", "/api/evaluation/kb-a/run", "knowledge.evaluation.manage"),
    ],
)
def test_knowledge_routes_map_to_specific_action_permissions(method, path, permission):
    assert _permission_for_request(_request(method, path)) == permission


@pytest.mark.asyncio
async def test_graph_query_parameter_is_checked_as_knowledge_scope(monkeypatch):
    user = SimpleNamespace(
        role="graph-operator",
        permission_keys={"knowledge.graph.manage"},
        to_dict=lambda: {"role": "graph-operator", "uid": "dept-a", "department_id": 1},
    )

    async def inaccessible(_user, kb_id):
        assert kb_id == "kb-b"
        return False

    monkeypatch.setattr("server.utils.knowledge_auth.knowledge_base.check_accessible", inaccessible)

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_user(
            request=_request("GET", "/api/graph/subgraph?kb_id=kb-b"),
            current_user=user,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_dataset_only_route_resolves_its_knowledge_base_scope(monkeypatch):
    user = SimpleNamespace(
        role="evaluator",
        permission_keys={"knowledge.evaluation.manage"},
        to_dict=lambda: {"role": "evaluator", "uid": "dept-a", "department_id": 1},
    )

    class FakeEvaluationRepository:
        async def get_dataset(self, dataset_id):
            assert dataset_id == "dataset-b"
            return SimpleNamespace(kb_id="kb-b")

    async def inaccessible(_user, kb_id):
        assert kb_id == "kb-b"
        return False

    monkeypatch.setattr("server.utils.knowledge_auth.EvaluationRepository", FakeEvaluationRepository)
    monkeypatch.setattr("server.utils.knowledge_auth.knowledge_base.check_accessible", inaccessible)

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_user(
            request=_request("DELETE", "/api/evaluation/datasets/dataset-b", {"dataset_id": "dataset-b"}),
            current_user=user,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_knowledge_permission_is_combined_with_resource_scope(monkeypatch):
    user = SimpleNamespace(
        role="department-reviewer",
        permission_keys={"knowledge.delete"},
        to_dict=lambda: {"role": "department-reviewer", "uid": "dept-a", "department_id": 1},
    )

    async def inaccessible(_user, _kb_id):
        return False

    monkeypatch.setattr("server.utils.knowledge_auth.knowledge_base.check_accessible", inaccessible)

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_user(
            request=_request("DELETE", "/api/knowledge/databases/kb-b", {"kb_id": "kb-b"}),
            current_user=user,
        )

    assert exc_info.value.status_code == 404
