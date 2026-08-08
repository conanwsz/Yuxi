"""组织架构管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, require_permission
from yuxi.services.operation_log_service import log_operation
from yuxi.services.organization_scope_service import user_can_manage_department
from yuxi.services.organization_service import OrganizationService
from yuxi.storage.postgres.models_business import (
    APIKey,
    Department,
    DepartmentAdminAssignment,
    DepartmentClosure,
    User,
    UserDepartmentMembership,
)

department = APIRouter(prefix="/departments", tags=["department"])


class DepartmentCreate(BaseModel):
    name: str
    description: str | None = None
    parent_id: int | None = None
    sort_order: int = 0


class DepartmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


class DepartmentMove(BaseModel):
    parent_id: int = Field(..., gt=0)


class DepartmentResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    parent_id: int | None = None
    root_id: int
    depth: int
    path: list[str]
    path_label: str
    status: str
    sort_order: int
    is_system: bool
    created_at: str | None
    updated_at: str | None
    archived_at: str | None
    direct_user_count: int = 0
    total_user_count: int = 0
    user_count: int = 0


def _visible_departments(current_user: User, items: list[dict]) -> list[dict]:
    if current_user.role == "superadmin":
        return items
    managed_ids = set(getattr(current_user, "managed_department_ids", set()))
    return [item for item in items if item["id"] in managed_ids]


async def _get_department_or_404(db: AsyncSession, department_id: int) -> Department:
    item = await db.get(Department, department_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组织节点不存在")
    return item


def _ensure_manage_scope(current_user: User, department_id: int) -> None:
    if not user_can_manage_department(current_user, department_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理该组织节点")


@department.get("", response_model=list[DepartmentResponse])
async def get_departments(
    current_user: User = Depends(require_permission("departments.read")),
    db: AsyncSession = Depends(get_db),
):
    return _visible_departments(current_user, await OrganizationService.list_departments(db))


@department.get("/tree")
async def get_department_tree(
    current_user: User = Depends(require_permission("departments.read")),
    db: AsyncSession = Depends(get_db),
):
    visible = _visible_departments(current_user, await OrganizationService.list_departments(db))
    nodes = {item["id"]: {**item, "children": []} for item in visible}
    roots: list[dict] = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


@department.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: int,
    current_user: User = Depends(require_permission("departments.read")),
    db: AsyncSession = Depends(get_db),
):
    _ensure_manage_scope(current_user, department_id)
    items = await OrganizationService.list_departments(db)
    item = next((value for value in items if value["id"] == department_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组织节点不存在")
    return item


@department.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    payload: DepartmentCreate,
    request: Request,
    current_user: User = Depends(require_permission("departments.create")),
    db: AsyncSession = Depends(get_db),
):
    if payload.parent_id is None and current_user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以创建根主体")
    if payload.parent_id is not None:
        _ensure_manage_scope(current_user, payload.parent_id)
    try:
        item = await OrganizationService.create_department(
            db,
            name=payload.name,
            description=payload.description,
            parent_id=payload.parent_id,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await log_operation(db, current_user.id, "创建组织节点", f"创建组织节点: {item.name}", request)
    return next(value for value in await OrganizationService.list_departments(db) if value["id"] == item.id)


@department.put("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    request: Request,
    current_user: User = Depends(require_permission("departments.update")),
    db: AsyncSession = Depends(get_db),
):
    _ensure_manage_scope(current_user, department_id)
    item = await _get_department_or_404(db, department_id)
    try:
        await OrganizationService.update_department(
            db,
            item,
            name=payload.name,
            description=payload.description,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await log_operation(db, current_user.id, "更新组织节点", f"更新组织节点: {item.name}", request)
    return next(value for value in await OrganizationService.list_departments(db) if value["id"] == item.id)


@department.post("/{department_id}/move", response_model=DepartmentResponse)
async def move_department(
    department_id: int,
    payload: DepartmentMove,
    request: Request,
    current_user: User = Depends(require_permission("departments.update")),
    db: AsyncSession = Depends(get_db),
):
    _ensure_manage_scope(current_user, department_id)
    _ensure_manage_scope(current_user, payload.parent_id)
    item = await _get_department_or_404(db, department_id)
    try:
        await OrganizationService.move_department(db, item, payload.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await log_operation(db, current_user.id, "移动组织节点", f"移动组织节点: {item.name}", request)
    return next(value for value in await OrganizationService.list_departments(db) if value["id"] == item.id)


@department.post("/{department_id}/archive", response_model=DepartmentResponse)
async def archive_department(
    department_id: int,
    request: Request,
    current_user: User = Depends(require_permission("departments.delete")),
    db: AsyncSession = Depends(get_db),
):
    _ensure_manage_scope(current_user, department_id)
    item = await _get_department_or_404(db, department_id)
    try:
        await OrganizationService.archive_department(db, item)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await log_operation(db, current_user.id, "停用组织节点", f"停用组织节点: {item.name}", request)
    return next(value for value in await OrganizationService.list_departments(db) if value["id"] == item.id)


@department.post("/{department_id}/restore", response_model=DepartmentResponse)
async def restore_department(
    department_id: int,
    request: Request,
    current_user: User = Depends(require_permission("departments.update")),
    db: AsyncSession = Depends(get_db),
):
    _ensure_manage_scope(current_user, department_id)
    item = await _get_department_or_404(db, department_id)
    try:
        await OrganizationService.restore_department(db, item)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await log_operation(db, current_user.id, "恢复组织节点", f"恢复组织节点: {item.name}", request)
    return next(value for value in await OrganizationService.list_departments(db) if value["id"] == item.id)


@department.delete("/{department_id}")
async def delete_department(
    department_id: int,
    request: Request,
    current_user: User = Depends(require_permission("departments.delete")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以物理删除组织节点")
    item = await _get_department_or_404(db, department_id)
    if item.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="默认部门不允许删除")
    reference_checks = [
        select(Department.id).where(Department.parent_id == department_id).limit(1),
        select(UserDepartmentMembership.id).where(UserDepartmentMembership.department_id == department_id).limit(1),
        select(DepartmentAdminAssignment.id).where(DepartmentAdminAssignment.department_id == department_id).limit(1),
        select(APIKey.id).where(APIKey.department_id == department_id).limit(1),
    ]
    for query in reference_checks:
        if (await db.execute(query)).scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="组织节点仍有下级或业务引用，不能物理删除")

    resource_reference = await db.execute(
        text(
            """
            SELECT 1 FROM (
                SELECT share_config::jsonb AS share_config FROM agents
                UNION ALL SELECT manage_config::jsonb FROM agents
                UNION ALL SELECT share_config::jsonb FROM skills
                UNION ALL SELECT share_config::jsonb FROM knowledge_bases
            ) resources
            WHERE COALESCE(resources.share_config, '{}'::jsonb) @>
                  jsonb_build_object('department_ids', jsonb_build_array(CAST(:department_id AS INTEGER)))
               OR COALESCE(resources.share_config, '{}'::jsonb) @>
                  jsonb_build_object(
                      'excluded_department_ids',
                      jsonb_build_array(CAST(:department_id AS INTEGER))
                  )
            LIMIT 1
            """
        ),
        {"department_id": department_id},
    )
    if resource_reference.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="组织节点仍被资源授权引用，不能物理删除")

    name = item.name
    await db.execute(delete(DepartmentClosure).where(DepartmentClosure.descendant_id == department_id))
    await db.delete(item)
    await db.commit()
    await log_operation(db, current_user.id, "删除组织节点", f"物理删除组织节点: {name}", request)
    return {"success": True, "message": "组织节点已删除"}
