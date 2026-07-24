"""定时任务 HTTP 入口：CRUD + 立即触发 + 执行历史。"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from server.utils.auth_middleware import require_permission
from yuxi.repositories.schedule_repository import (
    ScheduleExecutionRepository,
    ScheduleRepository,
)
from yuxi.services.scheduler_service import (
    compute_next_fire_at,
    is_valid_cron_expression,
    is_valid_timezone,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime


schedule_router = APIRouter(prefix="/schedules", tags=["schedules"])


class _SchedulePatchModel(BaseModel):
    """PATCH /schedules/{id} 的可选字段集合——只有显式传入的字段会被更新。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    agent_slug: str | None = Field(default=None, min_length=1, max_length=128)
    query: str | None = Field(default=None, min_length=1)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    runtime_overrides: dict[str, Any] | None = None


class _ScheduleCreateModel(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    agent_slug: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1)
    cron_expression: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    enabled: bool = True
    runtime_overrides: dict[str, Any] | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not is_valid_cron_expression(normalized):
            raise ValueError("cron_expression 不合法")
        return normalized

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            return "UTC"
        if not is_valid_timezone(normalized):
            raise ValueError("timezone 不合法")
        return normalized


def _parse_before(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return coerce_any_to_utc_datetime(value).replace(tzinfo=None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"before 参数不合法: {exc}") from exc


@schedule_router.get("")
async def list_schedules(
    enabled: bool | None = Query(default=None),
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    """列出所有 schedule，可选按启用状态过滤。"""
    records = await ScheduleRepository().list_schedules(enabled=enabled)
    return {"schedules": [record.to_dict() for record in records]}


@schedule_router.post("")
async def create_schedule(
    payload: _ScheduleCreateModel,
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    """新建一个 schedule。"""
    repository = ScheduleRepository()
    schedule_id = uuid.uuid4().hex
    # 初始 next_fire_at 用 cron 算一次，让 enabled=true 的 schedule 在下一个 tick 就能被触发
    initial_view = SimpleNamespace(
        cron_expression=payload.cron_expression,
        timezone=payload.timezone,
    )
    initial_next_fire = compute_next_fire_at(schedule=initial_view)
    await repository.create(
        {
            "id": schedule_id,
            "name": payload.name.strip(),
            "description": payload.description,
            "agent_slug": payload.agent_slug.strip(),
            "query": payload.query,
            "runtime_overrides": payload.runtime_overrides or {},
            "cron_expression": payload.cron_expression,
            "timezone": payload.timezone,
            "enabled": 1 if payload.enabled else 0,
            "owner_uid": str(current_user.uid),
            "next_fire_at": initial_next_fire,
        }
    )
    # 重新读取以拿到 created_at / updated_at
    record = await repository.get_by_id(schedule_id)
    assert record is not None
    return {"schedule": record.to_dict()}


@schedule_router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    record = await ScheduleRepository().get_by_id(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="schedule 不存在")
    return {"schedule": record.to_dict()}


@schedule_router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    payload: _SchedulePatchModel,
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    record = await ScheduleRepository().get_by_id(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="schedule 不存在")

    update_data: dict[str, Any] = {}
    cron_or_tz_changed = False
    if payload.name is not None:
        update_data["name"] = payload.name.strip()
    if payload.description is not None:
        update_data["description"] = payload.description
    if payload.agent_slug is not None:
        update_data["agent_slug"] = payload.agent_slug.strip()
    if payload.query is not None:
        update_data["query"] = payload.query
    if payload.runtime_overrides is not None:
        update_data["runtime_overrides"] = payload.runtime_overrides
    if payload.cron_expression is not None:
        normalized = payload.cron_expression.strip()
        if not is_valid_cron_expression(normalized):
            raise HTTPException(status_code=422, detail="cron_expression 不合法")
        update_data["cron_expression"] = normalized
        cron_or_tz_changed = True
    if payload.timezone is not None:
        normalized_tz = payload.timezone.strip() or "UTC"
        if not is_valid_timezone(normalized_tz):
            raise HTTPException(status_code=422, detail="timezone 不合法")
        update_data["timezone"] = normalized_tz
        cron_or_tz_changed = True
    if payload.enabled is not None:
        update_data["enabled"] = 1 if payload.enabled else 0

    # cron / timezone 变更：按新表达式重新算 next_fire_at（漏过去的多次不补）
    if cron_or_tz_changed:
        merged_view = SimpleNamespace(
            cron_expression=update_data.get("cron_expression", record.cron_expression),
            timezone=update_data.get("timezone", record.timezone),
        )
        update_data["next_fire_at"] = compute_next_fire_at(schedule=merged_view)

    if not update_data:
        return {"schedule": record.to_dict()}

    updated = await ScheduleRepository().update(schedule_id, update_data)
    if updated is None:
        raise HTTPException(status_code=404, detail="schedule 不存在")
    return {"schedule": updated.to_dict()}


@schedule_router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    """删除 schedule，并级联删除其 execution 记录。"""
    repository = ScheduleRepository()
    record = await repository.get_by_id(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="schedule 不存在")
    removed_executions = await ScheduleExecutionRepository().delete_by_schedule(schedule_id)
    deleted = await repository.delete(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="schedule 不存在")
    return {
        "schedule_id": schedule_id,
        "deleted": True,
        "executions_removed": removed_executions,
    }


@schedule_router.post("/{schedule_id}/fire")
async def fire_schedule(
    schedule_id: str,
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    """立即触发一条 schedule：写一条 execution 并异步派发，不等待结果。"""
    from yuxi.services.scheduler_service import scheduler

    execution = await scheduler.fire_now(schedule_id=schedule_id)
    return {
        "schedule_id": schedule_id,
        "execution_id": execution.id,
        "status": execution.status,
    }


@schedule_router.get("/{schedule_id}/executions")
async def list_executions(
    schedule_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, description="按 created_at 倒序游标分页"),
    current_user: User = Depends(require_permission("system.schedules.manage")),
):
    record = await ScheduleRepository().get_by_id(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="schedule 不存在")
    before_dt = _parse_before(before)
    executions = await ScheduleExecutionRepository().list_for_schedule(
        schedule_id=schedule_id, limit=limit, before=before_dt
    )
    return {
        "schedule_id": schedule_id,
        "executions": [item.to_dict() for item in executions],
        "next_before": (
            executions[-1].created_at.isoformat() if executions else None
        ),
    }


__all__ = ["schedule_router"]
