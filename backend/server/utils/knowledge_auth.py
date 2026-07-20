"""Permission and data-scope dependency shared by knowledge routers."""

from fastapi import Depends, HTTPException, Request, status

from server.utils.auth_middleware import get_required_user
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.services.permission_service import has_permission
from yuxi.storage.postgres.models_business import User


def _permission_for_request(request: Request) -> str:
    path = request.url.path
    method = request.method.upper()
    if path.startswith("/api/evaluation") or "/evaluation" in path:
        return "knowledge.evaluation.manage"
    if path.startswith("/api/graph") or any(marker in path for marker in ("/graph", "/mindmap")):
        return "knowledge.graph.manage"
    if any(marker in path for marker in ("/documents", "/files", "/upload", "/parse", "/chunks")):
        return "knowledge.documents.manage"
    if method == "POST" and path.rstrip("/") == "/api/knowledge/databases":
        return "knowledge.create"
    if method == "DELETE" and "/databases/" in path:
        return "knowledge.delete"
    if method in {"PUT", "PATCH"} and "/databases/" in path:
        return "knowledge.update"
    return "knowledge.read"


async def get_knowledge_user(
    request: Request,
    current_user: User = Depends(get_required_user),
) -> User:
    permission = _permission_for_request(request)
    if not has_permission(current_user, permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {permission}")

    kb_id = request.path_params.get("kb_id") or request.query_params.get("kb_id")
    dataset_id = request.path_params.get("dataset_id")
    if not kb_id and dataset_id:
        dataset = await EvaluationRepository().get_dataset(str(dataset_id))
        if dataset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="评估数据集不存在")
        kb_id = dataset.kb_id
    if kb_id and not await knowledge_base.check_accessible(current_user.to_dict(), str(kb_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在或无权访问")
    return current_user
