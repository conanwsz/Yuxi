"""Skills 管理路由"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user, require_permission
from yuxi.agents.skills.recommended_suites import (
    RecommendedSuiteConflictError,
    RecommendedSuiteNotFoundError,
    RecommendedSuiteValidationError,
    create_recommended_suite,
    delete_recommended_suite,
    get_recommended_suite,
    list_recommended_suites,
    list_recommended_suites_admin,
    set_recommended_suite_enabled,
    update_recommended_suite,
)
from yuxi.agents.skills.service import (
    clone_recommended_workspace_skill_to_personal,
    confirm_personal_skill_install_draft,
    confirm_recommended_workspace_install,
    confirm_skill_install_draft,
    create_skill_node,
    delete_recommended_workspace_skill,
    delete_skill,
    delete_skill_node,
    delete_skills_batch,
    delete_personal_skill,
    discard_skill_install_draft,
    export_skill_zip,
    get_allowed_skill_access_levels,
    get_manageable_skill_or_raise,
    get_management_readable_skill_or_raise,
    get_skill_dependency_options,
    get_skill_tree,
    init_builtin_skills,
    is_builtin_skill,
    list_accessible_skills,
    list_recommended_workspace_skills,
    list_recommended_workspace_skills_admin,
    list_skill_cards_for_user,
    list_skills,
    list_visible_skills_for_management,
    prepare_remote_skill_install,
    prepare_skill_upload,
    prepare_suite_upload,
    read_personal_skill_file,
    read_skill_file,
    set_recommended_workspace_skill_enabled,
    update_skill_dependencies,
    update_skill_enabled,
    update_skill_file,
    update_skill_share_config,
    user_can_manage_skill,
)
from yuxi.permissions import resolve_skill_permission
from yuxi.agents.skills.remote_install import list_remote_skills, search_remote_skills
from yuxi.storage.postgres.models_business import User
from yuxi.services.organization_scope_service import validate_v2_share_config
from yuxi.utils.logging_config import logger

skills = APIRouter(prefix="/system/skills", tags=["skills"])
user_skills = APIRouter(prefix="/skills", tags=["skills"])


class ShareConfigPayload(BaseModel):
    share_config: dict | None = Field(None, description="共享权限配置")


class SkillEnabledUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


class SkillNodeCreateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    is_dir: bool = Field(False, description="是否创建目录")
    content: str | None = Field("", description="文件内容（仅文件创建时生效）")


class SkillFileUpdateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    content: str = Field(..., description="文件内容")


class SkillDependenciesUpdateRequest(BaseModel):
    tool_dependencies: list[str] = Field(default_factory=list, description="依赖的内置工具列表")
    mcp_dependencies: list[str] = Field(default_factory=list, description="依赖的 MCP 服务列表")
    skill_dependencies: list[str] = Field(default_factory=list, description="依赖的其他 skill slug 列表")


class RemoteSkillSourceRequest(BaseModel):
    source: str = Field(..., description="skills 仓库来源，如 owner/repo 或 GitHub URL")


class RemoteSkillPrepareRequest(RemoteSkillSourceRequest):
    skills: list[str] = Field(..., description="需要安装的 skill 名称列表")


class RemoteSkillSearchRequest(BaseModel):
    query: str = Field(..., description="搜索关键字")


class RecommendedSuiteMemberPayload(BaseModel):
    slug: str = Field(..., description="skill 目录名（与远程仓库子目录一致）")
    name: str = Field(..., description="展示名")
    description: str = Field("", description="描述")
    sort_order: int = Field(0, description="成员显示顺序")


class RecommendedSuiteUpsertRequest(BaseModel):
    slug: str = Field(..., description="稳定 ID（管理端手动起名）")
    name: str = Field(..., description="展示名")
    provider: str = Field(..., description="提供方")
    description: str = Field("", description="卡片描述")
    source: str = Field(
        ..., description="用户安装时使用的 source URL（owner/repo、GitHub URL 或 ModelScope 单 skill URL）"
    )
    sort_order: int = Field(0, description="列表顺序")
    enabled: bool = Field(True, description="是否启用")
    members: list[RecommendedSuiteMemberPayload] = Field(..., description="套件成员列表，至少 1 条")


class RecommendedSuiteEnabledRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


class RecommendedWorkspaceInstallRequest(BaseModel):
    draft_id: str = Field(..., description="已解析的 install draft_id")
    slugs: list[str] | None = Field(None, description="需要安装的 skill slug 列表，None 表示全选")


class SkillBatchDeleteRequest(BaseModel):
    slugs: list[str] = Field(..., max_length=50, description="需要批量删除的 skill slug 列表，最多支持 50 个")


class _DraftConfirmRequestBase(BaseModel):
    slugs: list[str] | None = Field(None, description="本次确认安装的 Skill slug")


class SkillDraftConfirmRequest(_DraftConfirmRequestBase):
    share_config: dict | None = Field(None, description="共享权限配置")


class PersonalSkillDraftConfirmRequest(_DraftConfirmRequestBase):
    pass


def _raise_from_value_error(e: ValueError) -> None:
    message = str(e)
    status_code = 404 if "不存在" in message or "无权" in message else 400
    raise HTTPException(status_code=status_code, detail=message)


def _cleanup_export_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to cleanup exported skill archive '{path}': {e}")


def _summarize_results(results: list[dict]) -> dict[str, int]:
    return {
        "total": len(results),
        "success": sum(1 for item in results if item.get("success")),
        "failed": sum(1 for item in results if not item.get("success")),
    }


def _serialize_skill_for_user(item, user: User) -> dict:
    data = item.to_dict()
    data["can_manage"] = user_can_manage_skill(user, item)
    data["effective_permission"] = resolve_skill_permission(user, item).value
    data["is_builtin"] = is_builtin_skill(item)
    return data


@user_skills.get("")
async def list_skill_cards_route(
    refresh_personal: bool = Query(False, description="是否强制重新扫描个人 Skill"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items, snapshot = await list_skill_cards_for_user(
            db,
            current_user,
            refresh_personal=refresh_personal,
        )
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "personal_cache": {
                "scanned_at": snapshot.scanned_at,
                "from_cache": snapshot.from_cache,
            },
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except Exception as e:
        logger.error(f"Failed to list Skill cards: {e}")
        raise HTTPException(status_code=500, detail="获取 Skill 列表失败")


@user_skills.get("/accessible")
async def list_accessible_skills_route(
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_accessible_skills(db, current_user)
        return {"success": True, "data": [_serialize_skill_for_user(item, current_user) for item in items]}
    except Exception as e:
        logger.error(f"Failed to list accessible skills: {e}")
        raise HTTPException(status_code=500, detail="获取可访问 Skills 失败")


@user_skills.post("/import/prepare")
async def prepare_skill_upload_route(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("skills.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await prepare_skill_upload(
            db,
            filename=file.filename or "",
            file_bytes=await file.read(),
            operator=current_user,
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to prepare skill upload: {e}")
        raise HTTPException(status_code=500, detail="解析上传 Skill 失败")


@user_skills.post("/remote/list")
async def list_remote_skills_route(
    payload: RemoteSkillSourceRequest, _current_user: User = Depends(require_permission("skills.create"))
):
    try:
        return {"success": True, "data": await list_remote_skills(payload.source)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to list remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="获取远程 skills 列表失败")


@user_skills.post("/remote/search")
async def search_remote_skills_route(
    payload: RemoteSkillSearchRequest, _current_user: User = Depends(require_permission("skills.create"))
):
    try:
        return {"success": True, "data": await search_remote_skills(payload.query)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to search remote skills with query '{payload.query}': {e}")
        raise HTTPException(status_code=500, detail="搜索远程 skills 失败")


@user_skills.post("/remote/prepare")
async def prepare_remote_skills_route(
    payload: RemoteSkillPrepareRequest,
    current_user: User = Depends(require_permission("skills.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await prepare_remote_skill_install(
            db,
            source=payload.source,
            skills=payload.skills,
            operator=current_user,
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to prepare remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="解析远程 Skills 失败")


@user_skills.post("/install-drafts/{draft_id}/confirm")
async def confirm_skill_install_draft_route(
    draft_id: str,
    payload: SkillDraftConfirmRequest,
    current_user: User = Depends(require_permission("skills.share")),
    db: AsyncSession = Depends(get_db),
):
    try:
        if payload.share_config is not None:
            await validate_v2_share_config(db, payload.share_config)
        results = await confirm_skill_install_draft(
            db,
            draft_id=draft_id,
            share_config=payload.share_config,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to confirm skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装 Skill 失败")


@user_skills.post("/personal/install-drafts/{draft_id}/confirm")
async def confirm_personal_skill_install_draft_route(
    draft_id: str,
    payload: PersonalSkillDraftConfirmRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        results = await confirm_personal_skill_install_draft(
            draft_id=draft_id,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to confirm personal Skill draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装个人 Skill 失败")


@user_skills.get("/personal/{slug}/file")
async def read_personal_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 Skill 根目录的文件路径"),
    current_user: User = Depends(get_required_user),
):
    try:
        return {
            "success": True,
            "data": await read_personal_skill_file(str(current_user.uid), slug, path),
        }
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to read personal Skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取个人 Skill 文件失败")


@user_skills.delete("/personal/{slug}")
async def delete_personal_skill_route(
    slug: str,
    current_user: User = Depends(get_required_user),
):
    try:
        snapshot = await delete_personal_skill(str(current_user.uid), slug)
        return {
            "success": True,
            "personal_cache": {
                "scanned_at": snapshot.scanned_at,
                "from_cache": snapshot.from_cache,
            },
        }
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to delete personal Skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除个人 Skill 失败")


@user_skills.delete("/install-drafts/{draft_id}")
async def discard_skill_install_draft_route(
    draft_id: str, current_user: User = Depends(require_permission("skills.create"))
):
    try:
        await discard_skill_install_draft(draft_id=draft_id, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to discard skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="取消安装 Skill 失败")


@skills.get("")
async def list_skills_route(
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_visible_skills_for_management(db, current_user)
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except Exception as e:
        logger.error(f"Failed to list manageable skills: {e}")
        raise HTTPException(status_code=500, detail="获取技能列表失败")


@skills.get("/dependency-options")
async def get_skill_dependency_options_route(
    slug: str | None = Query(None, description="当前 Skill slug"),
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        if slug:
            await get_manageable_skill_or_raise(db, current_user, slug)
        return {"success": True, "data": await get_skill_dependency_options(db, current_user, slug)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to get skill dependency options: {e}")
        raise HTTPException(status_code=500, detail="获取 skill 依赖选项失败")


@skills.get("/builtin")
async def list_builtin_skills_route(
    _current_user: User = Depends(require_permission("skills.enable")),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = [item for item in await list_skills(db) if item.source_type == "builtin"]
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to list builtin skills: {e}")
        raise HTTPException(status_code=500, detail="获取内置 skill 列表失败")


@skills.post("/builtin/sync")
async def sync_builtin_skills_route(
    current_user: User = Depends(require_permission("skills.enable")),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await init_builtin_skills(db, created_by=current_user.uid)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to sync builtin skills: {e}")
        raise HTTPException(status_code=500, detail="同步内置 skill 失败")


@skills.put("/{slug}/share-config")
async def update_skill_share_config_route(
    slug: str,
    payload: ShareConfigPayload,
    current_user: User = Depends(require_permission("skills.share")),
    db: AsyncSession = Depends(get_db),
):
    try:
        if payload.share_config is not None:
            await validate_v2_share_config(db, payload.share_config)
        item = await update_skill_share_config(db, slug=slug, share_config=payload.share_config, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to update skill share config '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 共享范围失败")


@skills.put("/{slug}/enabled")
async def update_skill_enabled_route(
    slug: str,
    payload: SkillEnabledUpdateRequest,
    current_user: User = Depends(require_permission("skills.enable")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_enabled(db, slug=slug, enabled=payload.enabled, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to update skill enabled '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 启用状态失败")


@skills.get("/{slug}/tree")
async def get_skill_tree_route(
    slug: str,
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_management_readable_skill_or_raise(db, current_user, slug)
        return {"success": True, "data": await get_skill_tree(db, slug)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to get skill tree '{slug}': {e}")
        raise HTTPException(status_code=500, detail="获取技能目录树失败")


@skills.get("/{slug}/file")
async def get_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_management_readable_skill_or_raise(db, current_user, slug)
        return {"success": True, "data": await read_skill_file(db, slug, path)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to read skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取技能文件失败")


@skills.post("/{slug}/file")
async def create_skill_file_route(
    slug: str,
    payload: SkillNodeCreateRequest,
    current_user: User = Depends(require_permission("skills.update")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_manageable_skill_or_raise(db, current_user, slug)
        await create_skill_node(
            db,
            slug=slug,
            relative_path=payload.path,
            is_dir=payload.is_dir,
            content=payload.content,
            updated_by=current_user.uid,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to create skill node '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="创建技能文件失败")


@skills.put("/{slug}/file")
async def update_skill_file_route(
    slug: str,
    payload: SkillFileUpdateRequest,
    current_user: User = Depends(require_permission("skills.update")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_manageable_skill_or_raise(db, current_user, slug)
        await update_skill_file(
            db,
            slug=slug,
            relative_path=payload.path,
            content=payload.content,
            updated_by=current_user.uid,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to update skill file '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="更新技能文件失败")


@skills.put("/{slug}/dependencies")
async def update_skill_dependencies_route(
    slug: str,
    payload: SkillDependenciesUpdateRequest,
    current_user: User = Depends(require_permission("skills.update")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_dependencies(
            db,
            slug=slug,
            tool_dependencies=payload.tool_dependencies,
            mcp_dependencies=payload.mcp_dependencies,
            skill_dependencies=payload.skill_dependencies,
            operator=current_user,
        )
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to update skill dependencies '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 skill 依赖失败")


@skills.delete("/{slug}/file")
async def delete_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(require_permission("skills.update")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_manageable_skill_or_raise(db, current_user, slug)
        await delete_skill_node(db, slug=slug, relative_path=path)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to delete skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="删除技能文件失败")


@skills.get("/{slug}/export")
async def export_skill_route(
    slug: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_manageable_skill_or_raise(db, current_user, slug)
        export_path, download_name = await export_skill_zip(db, slug)
        background_tasks.add_task(_cleanup_export_file, export_path)
        return FileResponse(path=export_path, media_type="application/zip", filename=download_name)
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to export skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="导出技能失败")


# ---------- 推荐位治理（推荐工作区技能的下架/重新上架/删除） ----------
#
# 门控口径：路由级 `skills.recommend` + service 层对象级 `is_recommended_workspace` 校验。
# 与 `delete_skill_route`（限 skills.delete + MANAGE，即创建者/超管）有意区分：
# 推荐位是平台级治理资源，管理员需要能下架/删除**他人发布**到推荐工作区的技能。


@skills.get("/recommended-workspace/admin")
async def list_recommended_workspace_admin_route(
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """管理视角：列出全部推荐工作区技能（含已下架）。"""
    try:
        items = await list_recommended_workspace_skills_admin(db)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list recommended workspace skills (admin): {e}")
        raise HTTPException(status_code=500, detail="获取推荐工作区技能失败")


@skills.patch("/recommended-workspace/{slug}/enabled")
async def patch_recommended_workspace_skill_enabled_route(
    slug: str,
    payload: SkillEnabledUpdateRequest,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """下架（enabled=False）/ 重新上架（enabled=True）推荐工作区技能。"""
    try:
        data = await set_recommended_workspace_skill_enabled(
            db, slug=slug, enabled=payload.enabled, operator=current_user
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to set recommended workspace skill enabled '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新推荐技能上下架状态失败")


@skills.delete("/recommended-workspace/{slug}")
async def delete_recommended_workspace_skill_route(
    slug: str,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """物理删除推荐工作区技能（不可恢复）。"""
    try:
        await delete_recommended_workspace_skill(db, slug=slug)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to delete recommended workspace skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除推荐工作区技能失败")


@skills.delete("/{slug}")
async def delete_skill_route(
    slug: str,
    current_user: User = Depends(require_permission("skills.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_manageable_skill_or_raise(db, current_user, slug)
        await delete_skill(db, slug=slug)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to delete skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除技能失败")


@skills.post("/delete-batch")
async def delete_skills_batch_route(
    payload: SkillBatchDeleteRequest,
    current_user: User = Depends(require_permission("skills.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        for slug in payload.slugs:
            await get_manageable_skill_or_raise(db, current_user, slug)
        results = await delete_skills_batch(db, slugs=payload.slugs)
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to delete skills batch: {e}")
        raise HTTPException(status_code=500, detail="批量删除技能失败")


# ---------- 推荐技能套件 ----------


@skills.get("/recommended-suites")
async def list_recommended_suites_route(
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    """用户视角：仅返回启用的套件。"""
    try:
        items = await list_recommended_suites(db)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list recommended suites: {e}")
        raise HTTPException(status_code=500, detail="获取推荐套件失败")


@skills.get("/recommended-suites/admin")
async def list_recommended_suites_admin_route(
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """管理视角：含 disabled。"""
    try:
        items = await list_recommended_suites_admin(db)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list recommended suites (admin): {e}")
        raise HTTPException(status_code=500, detail="获取推荐套件失败")


@skills.get("/recommended-suites/{suite_id}")
async def get_recommended_suite_route(
    suite_id: int,
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    """用户视角单套件详情。Disabled 套件用户不可见（返回 404）。"""
    try:
        item = await get_recommended_suite(db, suite_id)
        if item is None or not item.get("enabled", True):
            raise HTTPException(status_code=404, detail="推荐套件不存在")
        return {"success": True, "data": item}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get recommended suite '{suite_id}': {e}")
        raise HTTPException(status_code=500, detail="获取推荐套件失败")


@skills.post("/recommended-suites")
async def create_recommended_suite_route(
    payload: RecommendedSuiteUpsertRequest,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await create_recommended_suite(
            db,
            payload=payload.model_dump(),
            operator=current_user,
        )
        return {"success": True, "data": data}
    except RecommendedSuiteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RecommendedSuiteValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create recommended suite: {e}")
        raise HTTPException(status_code=500, detail="创建推荐套件失败")


@skills.put("/recommended-suites/{suite_id}")
async def update_recommended_suite_route(
    suite_id: int,
    payload: RecommendedSuiteUpsertRequest,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await update_recommended_suite(
            db,
            suite_id=suite_id,
            payload=payload.model_dump(),
            operator=current_user,
        )
        return {"success": True, "data": data}
    except RecommendedSuiteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RecommendedSuiteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RecommendedSuiteValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update recommended suite '{suite_id}': {e}")
        raise HTTPException(status_code=500, detail="更新推荐套件失败")


@skills.patch("/recommended-suites/{suite_id}/enabled")
async def patch_recommended_suite_enabled_route(
    suite_id: int,
    payload: RecommendedSuiteEnabledRequest,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await set_recommended_suite_enabled(
            db,
            suite_id=suite_id,
            enabled=payload.enabled,
            operator=current_user,
        )
        return {"success": True, "data": data}
    except RecommendedSuiteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RecommendedSuiteValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to set recommended suite enabled '{suite_id}': {e}")
        raise HTTPException(status_code=500, detail="更新推荐套件失败")


@skills.delete("/recommended-suites/{suite_id}")
async def delete_recommended_suite_route(
    suite_id: int,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_recommended_suite(db, suite_id=suite_id)
        return {"success": True}
    except RecommendedSuiteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete recommended suite '{suite_id}': {e}")
        raise HTTPException(status_code=500, detail="删除推荐套件失败")


@user_skills.post("/import/suite-prepare")
async def prepare_suite_upload_route(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """解析多 skill 的 zip 压缩包，返回每个 skill 的元数据（不持久化草稿）。"""
    try:
        data = await prepare_suite_upload(
            db,
            filename=file.filename or "",
            file_bytes=await file.read(),
            operator=current_user,
        )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to prepare suite upload: {e}")
        raise HTTPException(status_code=500, detail="解析套件上传失败")


# ---------- 推荐工作区（用户共建池） ----------


@user_skills.get("/recommended-workspace")
async def list_recommended_workspace_route(
    current_user: User = Depends(require_permission("skills.read")),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可访问的「推荐工作区」skill。"""
    try:
        items = await list_recommended_workspace_skills(db, current_user)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list recommended workspace: {e}")
        raise HTTPException(status_code=500, detail="获取推荐工作区失败")


@user_skills.post("/import/install-to-recommended-workspace")
async def install_to_recommended_workspace_route(
    payload: RecommendedWorkspaceInstallRequest,
    current_user: User = Depends(require_permission("skills.recommend")),
    db: AsyncSession = Depends(get_db),
):
    """把 draft 里的 skill 发布到「推荐」用户共建池（不开新草稿，1 步完成）。

    注意：仅在 ``skills`` 表写入 ``is_recommended_workspace=True`` 记录，**不会**装到
    publisher 的工作区。如要使用，publisher 需到「推荐」列表选装。
    """
    try:
        results = await confirm_recommended_workspace_install(
            db,
            draft_id=payload.draft_id,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to install to recommended workspace: {e}")
        raise HTTPException(status_code=500, detail="装入推荐工作区失败")


@user_skills.post("/recommended-workspace/{slug}/install-to-personal")
async def install_recommended_workspace_to_personal_route(
    slug: str,
    current_user: User = Depends(require_permission("skills.create")),
    db: AsyncSession = Depends(get_db),
):
    """从推荐工作区克隆一个 skill 到当前用户的个人工作区。"""
    try:
        data = await clone_recommended_workspace_skill_to_personal(db, slug=slug, operator=current_user)
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        logger.error(f"Failed to install recommended workspace skill '{slug}' to personal: {e}")
        raise HTTPException(status_code=500, detail="安装到个人工作区失败")
