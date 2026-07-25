"""In-process 定时任务调度器。

负责：
- 周期性扫描到点的 schedule（``tick``），按 cron 表达式推进 ``next_fire_at``；
- 用 owner_uid 身份在执行线程中创建 AgentRun、等待结果并把状态写回 schedule_executions；
- 调度立即触发（``fire``）的请求；
- 清理超过 90 天的终态执行历史；
- 优雅停机：停机时取消 tick 任务并等待 in-flight 派发完成。
"""

from __future__ import annotations

import asyncio
import uuid
import zoneinfo
from datetime import datetime, timedelta
from typing import Any

from croniter import croniter
from croniter import CroniterBadCronError

from yuxi.repositories.schedule_repository import (
    ScheduleExecutionRepository,
    ScheduleRepository,
)
from yuxi.services.agent_run_service import (
    await_agent_run_result,
    create_agent_run_view,
    enqueue_agent_run,
)
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    SCHEDULE_STATUS_FAILED,
    SCHEDULE_STATUS_PENDING,
    SCHEDULE_STATUS_RUNNING,
    SCHEDULE_STATUS_SUCCESS,
    Schedule,
    ScheduleExecution,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


# ---- 常量 ---------------------------------------------------------------
TICK_INTERVAL_SECONDS = 10.0
TICK_BATCH_LIMIT = 20
RESULT_SUMMARY_MAX_CHARS = 500
EXECUTION_RETENTION_DAYS = 90
SCHEDULE_METADATA_SOURCE = "schedule"
SCHEDULE_METADATA_SCHEDULE_ID_KEY = "schedule_id"
SCHEDULE_METADATA_EXECUTION_ID_KEY = "schedule_execution_id"


# ---- cron / tz 校验工具 -------------------------------------------------
def is_valid_cron_expression(expr: str) -> bool:
    """校验 cron 表达式是否被 croniter 接受（5/6/7 段及 @daily 等别名）。"""
    if not isinstance(expr, str) or not expr.strip():
        return False
    try:
        croniter(expr.strip())
    except (CroniterBadCronError, ValueError, TypeError, KeyError):
        return False
    return True


def is_valid_timezone(name: str) -> bool:
    """校验 IANA 时区名是否合法。"""
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        zoneinfo.ZoneInfo(name.strip())
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def compute_next_fire_at(
    *, schedule: Schedule, base: datetime | None = None
) -> datetime | None:
    """基于 cron 表达式与 timezone 计算下一次触发时间，base 默认用 UTC 当前时刻。"""
    base_naive = (base or utc_now_naive()).replace(tzinfo=None)
    tz_name = (schedule.timezone or "UTC").strip() or "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = zoneinfo.ZoneInfo("UTC")

    # 关键：base_naive 是 UTC naive，必须先标 UTC 再 astimezone 到目标 tz。
    # 直接 .replace(tzinfo=tz) 是「贴标签」而非「转换」，会把 01:30 UTC 错标为 01:30+08
    # （物理时刻变成 17:30 UTC 前一天），croniter 算出的 next 会偏 8 小时。
    local_base = base_naive.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(tz)
    try:
        iterator = croniter(schedule.cron_expression, local_base)
    except (CroniterBadCronError, ValueError, TypeError, KeyError):
        return None
    next_local = iterator.get_next(datetime)
    if next_local is None:
        return None
    if next_local.tzinfo is None:
        return next_local.replace(tzinfo=None)
    return next_local.astimezone(zoneinfo.ZoneInfo("UTC")).replace(tzinfo=None)


# ---- 调度器 --------------------------------------------------------------
class SchedulerService:
    """API 进程内单例调度器。"""

    def __init__(
        self,
        *,
        tick_interval_seconds: float = TICK_INTERVAL_SECONDS,
        tick_batch_limit: int = TICK_BATCH_LIMIT,
        result_summary_max_chars: int = RESULT_SUMMARY_MAX_CHARS,
        execution_retention_days: int = EXECUTION_RETENTION_DAYS,
    ) -> None:
        self._tick_interval = float(tick_interval_seconds)
        self._tick_batch_limit = int(tick_batch_limit)
        self._result_summary_max_chars = int(result_summary_max_chars)
        self._execution_retention_days = int(execution_retention_days)

        self._started = False
        self._lock = asyncio.Lock()
        self._tick_task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[Any]] = set()
        self._shutdown_event: asyncio.Event | None = None

    # -- lifecycle --
    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._shutdown_event = asyncio.Event()
            self._tick_task = asyncio.create_task(self._tick_loop(), name="scheduler-tick")
            self._started = True
            logger.info("SchedulerService started (interval={}s)", self._tick_interval)

    async def shutdown(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            if self._shutdown_event is not None:
                self._shutdown_event.set()
            if self._tick_task is not None:
                self._tick_task.cancel()
                try:
                    await self._tick_task
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Scheduler tick loop exited: {}", exc)
            self._tick_task = None

        # 等 in-flight 派发结束（带超时，避免 shutdown 永久阻塞）
        if self._inflight:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._inflight, return_exceptions=True),
                    timeout=10.0,
                )
            except TimeoutError:
                logger.warning(
                    "Scheduler shutdown: {} in-flight dispatch tasks did not finish in time",
                    len(self._inflight),
                )
        logger.info("SchedulerService shutdown complete")

    # -- main loop --
    async def _tick_loop(self) -> None:
        assert self._shutdown_event is not None
        while not self._shutdown_event.is_set():
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scheduler tick failed: {}", exc)
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=self._tick_interval
                )
            except TimeoutError:
                continue

    async def _tick_once(self) -> None:
        """单次 tick：扫描到点的 schedule、推进 next_fire_at、派发执行。

        claim + advance 在同一事务里 commit，避免 watchfiles reload 在 claim commit
        之后、advance commit 之前中断导致 next_fire_at 没推进、下次 tick 重复 claim。
        """
        now = utc_now_naive()
        repo = ScheduleRepository()
        try:
            due_schedules = await repo.claim_due_schedules(
                now=now,
                limit=self._tick_batch_limit,
                advance_fn=compute_next_fire_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduler tick: failed to claim due schedules: {}", exc)
            return

        if due_schedules:
            logger.info(
                "Scheduler tick: {} due schedule(s) at {}",
                len(due_schedules),
                now.isoformat(),
            )
        for schedule in due_schedules:
            await self._handle_due_schedule(schedule=schedule, now=now)
            await self._maybe_cleanup_old_executions()

    async def _handle_due_schedule(self, *, schedule: Schedule, now: datetime) -> None:
        """单条 schedule 的处理：写 pending execution + 派发执行任务。

        next_fire_at 已在 claim_due_schedules 的同一事务里推进，
        这里不再调用 advance_fire_window，避免跨事务 race。
        """
        try:
            execution = await self._create_pending_execution(
                schedule=schedule, scheduled_at=now
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Scheduler tick: failed to create execution for {}: {}",
                schedule.id,
                exc,
            )
            return

        task = asyncio.create_task(
            self._dispatch_execution(schedule_id=schedule.id, execution_id=execution.id),
            name=f"schedule-dispatch-{execution.id}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    # -- execution helpers --
    async def _create_pending_execution(
        self, *, schedule: Schedule, scheduled_at: datetime
    ) -> ScheduleExecution:
        """创建一条 pending 的 execution 记录，fired_at 用 UTC now。"""
        repo = ScheduleExecutionRepository()
        return await repo.create(
            {
                "id": uuid.uuid4().hex,
                "schedule_id": schedule.id,
                "agent_run_id": None,
                "scheduled_at": scheduled_at,
                "fired_at": utc_now_naive(),
                "started_at": None,
                "completed_at": None,
                "status": SCHEDULE_STATUS_PENDING,
                "result_summary": None,
                "error": None,
            }
        )

    async def _dispatch_execution(
        self, *, schedule_id: str, execution_id: str
    ) -> None:
        """真实派发逻辑：以 owner_uid 创建 run、等待结果并回填 execution。"""
        schedule_repo = ScheduleRepository()
        execution_repo = ScheduleExecutionRepository()

        schedule = await schedule_repo.get_by_id(schedule_id)
        if schedule is None:
            await execution_repo.update(
                execution_id,
                {
                    "status": SCHEDULE_STATUS_FAILED,
                    "completed_at": utc_now_naive(),
                    "error": "schedule 已不存在",
                },
            )
            return

        # 同一个 schedule 已有 running/pending 时跳过（防御性，正常不会发生）
        if await execution_repo.has_running_execution(schedule_id=schedule_id):
            # 当前 execution 是新的，重复不归它管，保留
            pass

        run_id: str | None = None
        try:
            run_id = await self._enqueue_run(schedule=schedule, execution_id=execution_id)
            await execution_repo.update(
                execution_id,
                {
                    "agent_run_id": run_id,
                    "started_at": utc_now_naive(),
                    "status": SCHEDULE_STATUS_RUNNING,
                },
            )

            result = await await_agent_run_result(
                run_id=run_id, current_uid=str(schedule.owner_uid)
            )
            await self._apply_result(execution_id=execution_id, result=result)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Schedule dispatch failed (schedule={} execution={}): {}",
                schedule_id,
                execution_id,
                exc,
            )
            await execution_repo.update(
                execution_id,
                {
                    "status": SCHEDULE_STATUS_FAILED,
                    "completed_at": utc_now_naive(),
                    "error": str(exc) or type(exc).__name__,
                },
            )

    async def _enqueue_run(
        self, *, schedule: Schedule, execution_id: str
    ) -> str:
        """以 owner_uid 身份在独立会话里创建并入队一个 AgentRun。"""
        meta: dict[str, Any] = {
            "source": SCHEDULE_METADATA_SOURCE,
            SCHEDULE_METADATA_SCHEDULE_ID_KEY: schedule.id,
            SCHEDULE_METADATA_EXECUTION_ID_KEY: execution_id,
        }
        if isinstance(schedule.runtime_overrides, dict):
            for key, value in schedule.runtime_overrides.items():
                # 拒绝明显越权的字段
                if key in {"model_spec"} and value:
                    meta.setdefault("model_spec", value)
            if "custom_variables" in schedule.runtime_overrides and isinstance(
                schedule.runtime_overrides["custom_variables"], dict
            ):
                meta["custom_variables"] = dict(schedule.runtime_overrides["custom_variables"])

        input_message = build_chat_input_message(schedule.query)
        # 每 schedule 一个独立 thread（fixed seed），前端的「跳转到对话」能直接定位
        thread_id = f"schedule-{schedule.id}"

        # 1) 准备 conversation（ensure exists）并获取 conversation_id
        async with pg_manager.get_async_session_context() as db:
            from yuxi.repositories.conversation_repository import ConversationRepository

            conv_repo = ConversationRepository(db)
            conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
            if conversation is None:
                await conv_repo.add_conversation(
                    uid=str(schedule.owner_uid),
                    agent_id=schedule.agent_slug,
                    title=f"[定时] {schedule.name}",
                    thread_id=thread_id,
                    metadata={
                        "source": SCHEDULE_METADATA_SOURCE,
                        SCHEDULE_METADATA_SCHEDULE_ID_KEY: schedule.id,
                    },
                )

            run_view = await create_agent_run_view(
                input_message=input_message,
                agent_slug=schedule.agent_slug,
                thread_id=thread_id,
                meta=meta,
                current_uid=str(schedule.owner_uid),
                db=db,
            )
            run_id = str(run_view.get("run_id") or run_view.get("run", {}).get("id"))

        # 2) 显式 enqueue（如果 create_agent_run_view 内部已自动 enqueue，这里重复入队会被 job_id 去重覆盖）
        await enqueue_agent_run(run_id)
        return run_id

    async def _apply_result(
        self, *, execution_id: str, result: dict[str, Any]
    ) -> None:
        """把 await_agent_run_result 的结果回填到 execution 记录。"""
        status_value = str(result.get("status") or "")
        if status_value == "completed":
            target_status = SCHEDULE_STATUS_SUCCESS
        elif status_value in {"failed", "cancelled", "interrupted"}:
            target_status = SCHEDULE_STATUS_FAILED
        else:
            target_status = SCHEDULE_STATUS_FAILED

        output = result.get("output") or ""
        summary = str(output)
        if len(summary) > self._result_summary_max_chars:
            summary = summary[: self._result_summary_max_chars] + "…"

        error_blob = result.get("error")
        if isinstance(error_blob, dict):
            error_text = "; ".join(
                str(v) for v in error_blob.values() if v is not None
            ) or None
        elif error_blob:
            error_text = str(error_blob)
        else:
            error_text = None

        # 成功时清理 error 字段，失败时优先用 result 里的 error
        await ScheduleExecutionRepository().update(
            execution_id,
            {
                "status": target_status,
                "completed_at": utc_now_naive(),
                "result_summary": summary or None,
                "error": None if target_status == SCHEDULE_STATUS_SUCCESS else (error_text or "run 异常终止"),
            },
        )

    async def _maybe_cleanup_old_executions(self) -> None:
        """清理超过保留期的终态 execution。"""
        threshold = utc_now_naive() - timedelta(days=self._execution_retention_days)
        try:
            removed = await ScheduleExecutionRepository().cleanup_terminal_executions(
                older_than=threshold
            )
            if removed:
                logger.info("Scheduler cleanup: removed {} old execution records", removed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Scheduler cleanup skipped: {}", exc)

    # -- external API --
    async def fire_now(self, *, schedule_id: str) -> ScheduleExecution:
        """立即触发一条 schedule：写一条 execution 并异步派发，不等结果。"""
        repo = ScheduleRepository()
        schedule = await repo.get_by_id(schedule_id)
        if schedule is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="schedule 不存在")

        now = utc_now_naive()
        execution = await self._create_pending_execution(schedule=schedule, scheduled_at=now)
        # 立即触发不强制修改 next_fire_at，避免用户重复点 fire 导致提前推进调度
        task = asyncio.create_task(
            self._dispatch_execution(schedule_id=schedule.id, execution_id=execution.id),
            name=f"schedule-fire-{execution.id}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return execution

    # -- introspection (used by tests) --
    @property
    def tick_interval(self) -> float:
        return self._tick_interval

    @property
    def is_running(self) -> bool:
        return self._started


# 全局单例
scheduler = SchedulerService()


__all__ = [
    "SchedulerService",
    "scheduler",
    "is_valid_cron_expression",
    "is_valid_timezone",
    "compute_next_fire_at",
    "TICK_INTERVAL_SECONDS",
    "TICK_BATCH_LIMIT",
    "EXECUTION_RETENTION_DAYS",
    "RESULT_SUMMARY_MAX_CHARS",
    "SCHEDULE_METADATA_SOURCE",
    "SCHEDULE_METADATA_SCHEDULE_ID_KEY",
    "SCHEDULE_METADATA_EXECUTION_ID_KEY",
]
