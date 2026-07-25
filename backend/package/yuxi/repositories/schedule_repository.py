"""定时任务(Schedule)与执行历史(ScheduleExecution)的持久化层。

只负责 SQL 与 ORM 映射；调度循环、HTTP 入口与执行跟踪由 service 层负责。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, or_, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Agent,
    Schedule,
    ScheduleExecution,
    SCHEDULE_EXECUTION_TERMINAL_STATUSES,
)
from yuxi.utils.datetime_utils import utc_now_naive


class ScheduleRepository:
    """schedules 表的仓库方法集合，依赖 pg_manager 的会话上下文。"""

    async def get_by_id(self, schedule_id: str) -> Schedule | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Schedule).where(Schedule.id == schedule_id))
            return result.scalar_one_or_none()

    async def get_agent_name_map(self, slugs: set[str]) -> dict[str, str]:
        """按 slug 批量查 agent 显示名，调用方把 slug -> name 注入到 schedule dict。

        找不到或空集时返回空 dict，调用方 fallback 到 slug 自身。
        """
        if not slugs:
            return {}
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(Agent.slug, Agent.name).where(Agent.slug.in_(slugs))
            )
            return {slug: name for slug, name in result.all()}

    async def list_schedules(self, *, enabled: bool | None = None) -> list[Schedule]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(Schedule).order_by(Schedule.created_at.desc())
            if enabled is not None:
                stmt = stmt.where(Schedule.enabled == (1 if enabled else 0))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> Schedule:
        async with pg_manager.get_async_session_context() as session:
            record = Schedule(**data)
            session.add(record)
            await session.flush()
            await session.refresh(record)
            return record

    async def update(self, schedule_id: str, data: dict[str, Any]) -> Schedule | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Schedule).where(Schedule.id == schedule_id))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in data.items():
                setattr(record, key, value)
            record.updated_at = utc_now_naive()
            await session.flush()
            await session.refresh(record)
            return record

    async def delete(self, schedule_id: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(Schedule).where(Schedule.id == schedule_id))
            return result.rowcount > 0

    async def set_enabled(self, schedule_id: str, enabled: bool) -> Schedule | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Schedule).where(Schedule.id == schedule_id))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            record.enabled = 1 if enabled else 0
            record.updated_at = utc_now_naive()
            await session.flush()
            await session.refresh(record)
            return record

    async def fetch_due_schedule_ids(
        self, *, now: datetime, limit: int = 20
    ) -> list[str]:
        """拉取到点可触发的 schedule id 列表（不加锁，由调用方在事务内使用 SKIP LOCKED）。"""
        async with pg_manager.get_async_session_context() as session:
            stmt = (
                select(Schedule.id)
                .where(Schedule.enabled == 1)
                .where(Schedule.next_fire_at.is_not(None))
                .where(Schedule.next_fire_at <= now)
                .order_by(Schedule.next_fire_at.asc())
                .limit(max(limit, 1))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def claim_due_schedules(
        self,
        *,
        now: datetime,
        limit: int = 20,
        advance_fn: Any | None = None,
    ) -> list[Schedule]:
        """在单事务内对到点的 schedule 加行锁并返回。

        ``advance_fn`` 可选：传入 ``(schedule, base) -> next_fire_at | None``，
        会在同一事务里把 ``last_fired_at`` / ``next_fire_at`` 推进并 flush，
        事务 commit 时一起落库。这样 claim + advance 原子化，避免跨事务
        推进在 reload 中断时丢更新导致重复触发。
        """
        async with pg_manager.get_async_session_context() as session:
            async with session.begin():
                stmt = (
                    select(Schedule)
                    .where(Schedule.enabled == 1)
                    .where(Schedule.next_fire_at.is_not(None))
                    .where(Schedule.next_fire_at <= now)
                    .order_by(Schedule.next_fire_at.asc())
                    .limit(max(limit, 1))
                    .with_for_update(skip_locked=True)
                )
                result = await session.execute(stmt)
                schedules = list(result.scalars().all())
                if advance_fn is not None and schedules:
                    for record in schedules:
                        record.last_fired_at = now
                        record.next_fire_at = advance_fn(schedule=record, base=now)
                        record.updated_at = utc_now_naive()
                    await session.flush()
                return schedules

    async def advance_fire_window(
        self, *, schedule_id: str, last_fired_at: datetime, next_fire_at: datetime | None
    ) -> None:
        """更新 last_fired_at / next_fire_at，next_fire_at 允许为 None（暂时不调度）。"""
        async with pg_manager.get_async_session_context() as session:
            record = await session.get(Schedule, schedule_id)
            if record is None:
                return
            record.last_fired_at = last_fired_at
            record.next_fire_at = next_fire_at
            record.updated_at = utc_now_naive()
            await session.flush()


class ScheduleExecutionRepository:
    """schedule_executions 表的仓库方法。"""

    async def get_by_id(self, execution_id: str) -> ScheduleExecution | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ScheduleExecution).where(ScheduleExecution.id == execution_id)
            )
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> ScheduleExecution:
        async with pg_manager.get_async_session_context() as session:
            record = ScheduleExecution(**data)
            session.add(record)
            await session.flush()
            await session.refresh(record)
            return record

    async def update(self, execution_id: str, data: dict[str, Any]) -> ScheduleExecution | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ScheduleExecution).where(ScheduleExecution.id == execution_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in data.items():
                setattr(record, key, value)
            await session.flush()
            await session.refresh(record)
            return record

    async def list_for_schedule(
        self,
        *,
        schedule_id: str,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[ScheduleExecution]:
        """按 schedule_id 拉取执行历史，支持 ``before`` 游标分页。"""
        async with pg_manager.get_async_session_context() as session:
            stmt = (
                select(ScheduleExecution)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .order_by(ScheduleExecution.created_at.desc())
                .limit(max(limit, 1))
            )
            if before is not None:
                stmt = stmt.where(ScheduleExecution.created_at < before)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_by_schedule(self, schedule_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(ScheduleExecution).where(ScheduleExecution.schedule_id == schedule_id)
            )
            return int(result.rowcount or 0)

    async def cleanup_terminal_executions(self, *, older_than: datetime) -> int:
        """清理 90 天前的终态执行历史，避免表无限膨胀。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(ScheduleExecution).where(
                    and_(
                        ScheduleExecution.completed_at.is_not(None),
                        ScheduleExecution.completed_at < older_than,
                        ScheduleExecution.status.in_(tuple(SCHEDULE_EXECUTION_TERMINAL_STATUSES)),
                    )
                )
            )
            return int(result.rowcount or 0)

    async def has_running_execution(self, *, schedule_id: str) -> bool:
        """检查同一 schedule 是否仍存在 running/pending 的执行，避免重复触发。"""
        async with pg_manager.get_async_session_context() as session:
            stmt = (
                select(ScheduleExecution.id)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .where(
                    or_(
                        ScheduleExecution.status == "pending",
                        ScheduleExecution.status == "running",
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None


__all__ = [
    "ScheduleRepository",
    "ScheduleExecutionRepository",
]
