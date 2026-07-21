import re
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.repositories.role_repository import RoleRepository
from yuxi.services.operation_log_service import log_operation
from yuxi.services.permission_service import permission_catalog, validate_permission_keys
from yuxi.services.resource_access_service import (
    ResourceAccessValidationError,
    build_resource_catalog,
    default_resource_access_none,
    normalize_resource_access,
)
from yuxi.storage.postgres.models_business import User


roles = APIRouter(prefix="/roles", tags=["roles"])
ROLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class RoleCreate(BaseModel):
    key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)
    resource_access: dict = Field(default_factory=default_resource_access_none)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None
    resource_access: dict | None = None


async def _serialize_role(repo: RoleRepository, role) -> dict:
    data = role.to_dict()
    data["user_count"] = await repo.user_count(role.key)
    data["editable"] = role.key != "superadmin"
    data["deletable"] = not role.is_system and data["user_count"] == 0
    return data


@roles.get("/permissions")
async def list_permissions(_current_user: User = Depends(get_superadmin_user)):
    return {"groups": permission_catalog()}


@roles.get("/resources")
async def list_role_resources(
    _current_user: User = Depends(get_superadmin_user), db: AsyncSession = Depends(get_db)
):
    return await build_resource_catalog(db)


@roles.get("")
async def list_roles(
    _current_user: User = Depends(get_superadmin_user), db: AsyncSession = Depends(get_db)
):
    repo = RoleRepository(db)
    return {"roles": [await _serialize_role(repo, role) for role in await repo.list_all()]}


@roles.post("", status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    key = payload.key.strip().lower()
    if not ROLE_KEY_PATTERN.fullmatch(key):
        raise HTTPException(status_code=422, detail="角色 key 只能包含小写字母、数字、下划线和连字符")
    repo = RoleRepository(db)
    if await repo.get(key):
        raise HTTPException(status_code=409, detail="角色 key 已存在")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="角色名称不能为空")
    if await repo.get_by_name(name):
        raise HTTPException(status_code=409, detail="角色名称已存在")
    try:
        resource_catalog = await build_resource_catalog(db)
        permissions = validate_permission_keys(payload.permissions)
        resource_access = normalize_resource_access(payload.resource_access, catalog=resource_catalog)
        role = await repo.create(
            key=key,
            name=name,
            description=payload.description,
            permissions=permissions,
            resource_access=resource_access,
            is_system=False,
            created_by=current_user.uid,
        )
    except (ResourceAccessValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await log_operation(
        db,
        current_user.id,
        "创建角色",
        "角色: "
        f"{key}; 权限: {permissions}; "
        f"数据权限: {json.dumps(resource_access, ensure_ascii=False, sort_keys=True)}",
        request,
    )
    return {"role": await _serialize_role(repo, role)}


@roles.put("/{role_key}")
async def update_role(
    role_key: str,
    payload: RoleUpdate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    repo = RoleRepository(db)
    role = await repo.get(role_key)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.key == "superadmin":
        raise HTTPException(status_code=403, detail="超级管理员权限不可修改")

    before = sorted(role.permissions or [])
    before_resource_access = role.to_dict()["resource_access"]
    updates = {}
    if payload.name is not None and not role.is_system:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="角色名称不能为空")
        existing = await repo.get_by_name(name)
        if existing and existing.key != role.key:
            raise HTTPException(status_code=409, detail="角色名称已存在")
        updates["name"] = name
    if "description" in payload.model_fields_set:
        updates["description"] = payload.description
    if payload.permissions is not None:
        try:
            updates["permissions"] = validate_permission_keys(payload.permissions)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.resource_access is not None:
        try:
            resource_catalog = await build_resource_catalog(db)
            updates["resource_access"] = normalize_resource_access(
                payload.resource_access,
                catalog=resource_catalog,
                existing=role.resource_access,
            )
        except ResourceAccessValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    role = await repo.update(role, **updates)
    await log_operation(
        db,
        current_user.id,
        "更新角色权限",
        "角色: "
        f"{role.key}; 功能权限修改前: {before}; 功能权限修改后: {sorted(role.permissions or [])}; "
        f"数据权限修改前: {json.dumps(before_resource_access, ensure_ascii=False, sort_keys=True)}; "
        f"数据权限修改后: {json.dumps(role.to_dict()['resource_access'], ensure_ascii=False, sort_keys=True)}",
        request,
    )
    return {"role": await _serialize_role(repo, role)}


@roles.delete("/{role_key}")
async def delete_role(
    role_key: str,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    repo = RoleRepository(db)
    role = await repo.get(role_key)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_system:
        raise HTTPException(status_code=403, detail="系统角色不可删除")
    if await repo.user_count(role.key):
        raise HTTPException(status_code=409, detail="角色仍被用户使用，无法删除")
    try:
        await repo.delete(role)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="角色仍被用户使用，无法删除") from exc
    await log_operation(db, current_user.id, "删除角色", f"角色: {role.key}", request)
    return {"success": True}
