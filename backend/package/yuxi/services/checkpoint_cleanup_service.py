"""LangGraph checkpoint 定期清理服务。

删除连续 N 天不活跃（按 conversations.updated_at 判定）的对话线程在
checkpoint 三张表（checkpoints / checkpoint_blobs / checkpoint_writes）中的
全部数据，并附带清扫没有 conversation 行的孤儿 thread。
仅支持 postgres checkpointer 后端，其他后端跳过。

执行时机：默认跟随 api 进程启动时刻（首个 tick 即执行，之后每 24h 一轮）；
配置 CHECKPOINT_CLEANUP_DAILY_AT=HH:MM（服务器时区，与 SCHEDULER_TIMEZONE 一致）
后改为每天固定时刻执行，错过的当天不再补跑。

活跃度信号：conversations.updated_at 在每次 add_message 时刷新；软删对话
（status='deleted'）的 updated_at 停在删除时刻，到期后同样进入候选。
在途保护：有 pending/running/cancel_requested 运行的 thread 永不删除。
"""

from __future__ import annotations

import os
import zoneinfo
from datetime import datetime, timedelta

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, Conversation
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

# ---- 常量 ----------------------------------------------------------------
CHECKPOINT_RETENTION_DAYS = 30
CHECKPOINT_CLEANUP_BATCH_LIMIT = 500
CHECKPOINT_CLEANUP_INTERVAL_HOURS = 24
PROTECTED_RUN_STATUSES = ("pending", "running", "cancel_requested")

_BACKEND_ENV_KEY = "LANGGRAPH_CHECKPOINTER_BACKEND"
DAILY_AT_ENV_KEY = "CHECKPOINT_CLEANUP_DAILY_AT"

_UTC = zoneinfo.ZoneInfo("UTC")


# ---- 公开入口 --------------------------------------------------------------
def parse_daily_at(raw: str | None) -> tuple[int, int] | None:
    """解析 CHECKPOINT_CLEANUP_DAILY_AT 的 HH:MM 值。

    未配置返回 None（走默认启动锚点模式）；配置了但格式非法时告警并返回 None，
    回退到默认模式，不让错误的配置阻塞调度。
    """
    if raw is None or not raw.strip():
        return None
    parts = raw.strip().split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        hour, minute = -1, -1
    if not (len(parts) == 2 and 0 <= hour <= 23 and 0 <= minute <= 59):
        logger.warning(
            "Invalid {}={!r}, expected HH:MM; falling back to startup-anchored schedule",
            DAILY_AT_ENV_KEY,
            raw,
        )
        return None
    return (hour, minute)


def resolve_scheduler_timezone() -> zoneinfo.ZoneInfo:
    """返回调度时区，语义与 scheduler_service.compute_next_fire_at 一致。"""
    tz_name = os.environ.get("SCHEDULER_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("Asia/Shanghai")


def is_daily_at_due(
    *,
    now_utc: datetime,
    daily_at: tuple[int, int],
    tz: zoneinfo.ZoneInfo,
    last_run_at: datetime | None,
) -> bool:
    """判断 now_utc（UTC naive）是否命中当天固定清理时刻。

    只认 HH:MM 分钟窗口（tick 间隔 10s，必然命中）；当天已跑过不再重复。
    时刻已过但当天没跑（进程重启/停机）不补跑，等下一天。
    """
    local_now = now_utc.replace(tzinfo=_UTC).astimezone(tz)
    if (local_now.hour, local_now.minute) != daily_at:
        return False
    if last_run_at is not None:
        local_last = last_run_at.replace(tzinfo=_UTC).astimezone(tz)
        if local_last.date() == local_now.date():
            return False
    return True


async def cleanup_inactive_checkpoints(
    *,
    retention_days: int = CHECKPOINT_RETENTION_DAYS,
    batch_limit: int = CHECKPOINT_CLEANUP_BATCH_LIMIT,
    now: datetime | None = None,
) -> dict:
    """清理不活跃 thread 的全部 checkpoint 数据。

    返回统计：{"skipped": 跳过原因或 None, "candidates": 候选数, "deleted_threads": 成功删除数}。
    单个 thread 删除失败仅记录日志并继续，不中断整批。
    """
    skipped = _check_backend()
    if skipped is not None:
        logger.debug("Checkpoint cleanup skipped: {}", skipped)
        return {"skipped": skipped, "candidates": 0, "deleted_threads": 0}

    current = now or utc_now_naive()
    threshold = current - timedelta(days=retention_days)
    stale_ids = await _collect_stale_thread_ids(threshold=threshold, batch_limit=batch_limit)
    orphan_ids = await _collect_orphan_thread_ids(batch_limit=batch_limit)
    candidates = list(dict.fromkeys([*stale_ids, *orphan_ids]))[:batch_limit]

    deleted = 0
    for thread_id in candidates:
        try:
            await _delete_thread_checkpoints(thread_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - 单个失败不应中断整批
            logger.warning("Checkpoint cleanup: failed to delete thread {}: {}", thread_id, exc)

    if candidates:
        logger.info(
            "Checkpoint cleanup: removed checkpoints for {} thread(s) (candidates={})",
            deleted,
            len(candidates),
        )
    return {"skipped": None, "candidates": len(candidates), "deleted_threads": deleted}


# ---- 下沉细节 --------------------------------------------------------------
def _check_backend() -> str | None:
    """返回跳过原因，后端可用时返回 None。"""
    backend = os.getenv(_BACKEND_ENV_KEY, "sqlite").strip().lower()
    if backend != "postgres":
        return f"LANGGRAPH_CHECKPOINTER_BACKEND={backend!r} is not postgres"
    if pg_manager.langgraph_pool is None:
        return "pg_manager.langgraph_pool is not initialized"
    return None


async def _collect_stale_thread_ids(*, threshold: datetime, batch_limit: int) -> list[str]:
    """收集超期不活跃、且无在途 run 的 thread（active 与 deleted 同等对待）。"""
    protected = tuple(PROTECTED_RUN_STATUSES)
    statement = (
        select(Conversation.thread_id)
        .where(
            Conversation.updated_at < threshold,
            ~select(AgentRun.id)
            .where(
                AgentRun.conversation_thread_id == Conversation.thread_id,
                AgentRun.status.in_(protected),
            )
            .exists(),
        )
        .order_by(Conversation.updated_at.asc())
        .limit(batch_limit)
    )
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(statement)
        return [row[0] for row in result.all()]


async def _collect_orphan_thread_ids(*, batch_limit: int) -> list[str]:
    """收集有 checkpoint 数据但没有 conversation 行的孤儿 thread。"""
    from sqlalchemy import text

    statement = text(
        "SELECT DISTINCT k.thread_id FROM checkpoints k "
        "LEFT JOIN conversations c ON c.thread_id = k.thread_id "
        "WHERE c.thread_id IS NULL LIMIT :limit"
    )
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(statement, {"limit": batch_limit})
        return [row[0] for row in result.all()]


async def _delete_thread_checkpoints(thread_id: str) -> None:
    """删除单个 thread 的全部 checkpoint 数据（三张表一次清）。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    saver = AsyncPostgresSaver(pg_manager.langgraph_pool)
    await saver.adelete_thread(thread_id)
