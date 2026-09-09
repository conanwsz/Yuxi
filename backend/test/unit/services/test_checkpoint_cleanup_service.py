"""CheckpointCleanupService 单元测试 — 覆盖候选收集、删除行为、后端守卫与 scheduler 节流。

数据库交互用 sqlite in-memory 替身（真实 SQLAlchemy 查询），删除动作 monkeypatch，
不依赖 docker；真实 AsyncPostgresSaver 路径由集成测试负责。
"""

from __future__ import annotations

import os
import zoneinfo
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import checkpoint_cleanup_service, scheduler_service
from yuxi.services.checkpoint_cleanup_service import cleanup_inactive_checkpoints
from yuxi.services.scheduler_service import SchedulerService
from yuxi.storage.postgres import manager as manager_module
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

FRESH = utc_now_naive()
STALE = FRESH - timedelta(days=40)


# ---------------------------------------------------------------------------
# fixture：sqlite in-memory 骨架库 + pg_manager.get_async_session_context 替身
# ---------------------------------------------------------------------------
@pytest.fixture()
async def db_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # checkpoints 表由 langgraph setup() 在生产建表，业务 Base.metadata 里没有，测试建骨架
        await conn.execute(
            text(
                "CREATE TABLE checkpoints ("
                "thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', "
                "checkpoint_id TEXT NOT NULL, checkpoint TEXT NOT NULL, metadata TEXT NOT NULL, "
                "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _session_context():
        async with factory() as session:
            yield session

    monkeypatch.setattr(manager_module.pg_manager, "get_async_session_context", _session_context)
    monkeypatch.setattr(checkpoint_cleanup_service.pg_manager, "get_async_session_context", _session_context)
    monkeypatch.setattr(checkpoint_cleanup_service, "_check_backend", lambda: None)

    yield SimpleNamespace(engine=engine, factory=factory)
    await engine.dispose()


def _add_conversation(db, thread_id: str, *, updated_at, status: str = "active") -> None:
    db.add(
        Conversation(
            thread_id=thread_id,
            uid="u1",
            agent_id="chatbot",
            title=thread_id,
            status=status,
            created_at=updated_at,
            updated_at=updated_at,
        )
    )


def _add_run(db, thread_id: str, status: str) -> None:
    db.add(
        AgentRun(
            id=f"run-{thread_id}-{status}",
            conversation_thread_id=thread_id,
            agent_slug="chatbot",
            uid="u",
            status=status,
            request_id=f"req-{thread_id}-{status}",
            created_at=FRESH,
            updated_at=FRESH,
        )
    )


async def _seed(env, rows: list[Conversation | AgentRun]) -> None:
    async with env.factory() as db:
        for row in rows:
            db.add(row)
        await db.commit()


# ---------------------------------------------------------------------------
# 1. 候选收集（真实 SQL）
# ---------------------------------------------------------------------------
async def test_collect_stale_includes_only_expired_and_unprotected(db_env):
    async with db_env.factory() as db:
        _add_conversation(db, "stale-1", updated_at=STALE)
        _add_conversation(db, "fresh-1", updated_at=FRESH)
        _add_conversation(db, "deleted-stale", updated_at=STALE, status="deleted")
        _add_conversation(db, "protected", updated_at=STALE)
        _add_run(db, "protected", "running")
        # 有终态 run 的不保护
        _add_conversation(db, "stale-done", updated_at=STALE)
        _add_run(db, "stale-done", "completed")
        await db.commit()

    threshold = FRESH - timedelta(days=30)
    ids = await checkpoint_cleanup_service._collect_stale_thread_ids(threshold=threshold, batch_limit=100)
    assert sorted(ids) == ["deleted-stale", "stale-1", "stale-done"]


async def test_collect_stale_excludes_pending_and_cancel_requested(db_env):
    async with db_env.factory() as db:
        for status in ("pending", "running", "cancel_requested"):
            thread = f"guard-{status}"
            _add_conversation(db, thread, updated_at=STALE)
            _add_run(db, thread, status)
        await db.commit()

    threshold = FRESH - timedelta(days=30)
    ids = await checkpoint_cleanup_service._collect_stale_thread_ids(threshold=threshold, batch_limit=100)
    assert ids == []


async def test_collect_stale_respects_batch_limit_orders_oldest_first(db_env):
    async with db_env.factory() as db:
        for i in range(5):
            _add_conversation(db, f"t-{i}", updated_at=FRESH - timedelta(days=40 + i))
        await db.commit()

    ids = await checkpoint_cleanup_service._collect_stale_thread_ids(
        threshold=FRESH - timedelta(days=30), batch_limit=3
    )
    # 最老优先（40+4 天前最老）
    assert ids == ["t-4", "t-3", "t-2"]


async def test_collect_orphan_threads(db_env):
    async with db_env.factory() as db:
        _add_conversation(db, "main", updated_at=STALE)
        await db.commit()
        await db.execute(
            text(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES ('orphan-1', '', 'ck1', '{}', '{}'), "
                "('main', '', 'ck2', '{}', '{}')"
            )
        )
        await db.commit()

    ids = await checkpoint_cleanup_service._collect_orphan_thread_ids(batch_limit=100)
    assert ids == ["orphan-1"]


# ---------------------------------------------------------------------------
# 2. 删除行为（monkeypatch 删除动作）
# ---------------------------------------------------------------------------
async def test_cleanup_deletes_each_candidate_and_counts(db_env, monkeypatch):
    async with db_env.factory() as db:
        _add_conversation(db, "a", updated_at=STALE)
        _add_conversation(db, "b", updated_at=STALE)
        await db.commit()

    deleted_ids: list[str] = []

    async def _fake_delete(thread_id):
        deleted_ids.append(thread_id)

    monkeypatch.setattr(checkpoint_cleanup_service, "_delete_thread_checkpoints", _fake_delete)

    result = await cleanup_inactive_checkpoints(now=FRESH)
    assert result == {"skipped": None, "candidates": 2, "deleted_threads": 2}
    assert sorted(deleted_ids) == ["a", "b"]


async def test_cleanup_continues_after_single_failure(db_env, monkeypatch):
    async with db_env.factory() as db:
        _add_conversation(db, "bad", updated_at=STALE)
        _add_conversation(db, "good", updated_at=STALE)
        await db.commit()

    async def _fake_delete(thread_id):
        if thread_id == "bad":
            raise RuntimeError("boom")

    monkeypatch.setattr(checkpoint_cleanup_service, "_delete_thread_checkpoints", _fake_delete)

    result = await cleanup_inactive_checkpoints(now=FRESH)
    assert result["candidates"] == 2
    assert result["deleted_threads"] == 1


async def test_cleanup_enforces_batch_limit(db_env, monkeypatch):
    async with db_env.factory() as db:
        for i in range(7):
            _add_conversation(db, f"t-{i}", updated_at=STALE)
        await db.commit()

    deleted_ids: list[str] = []

    async def _fake_delete(thread_id):
        deleted_ids.append(thread_id)

    monkeypatch.setattr(checkpoint_cleanup_service, "_delete_thread_checkpoints", _fake_delete)

    result = await cleanup_inactive_checkpoints(now=FRESH, batch_limit=2)
    assert result["candidates"] == 2
    assert len(deleted_ids) == 2


# ---------------------------------------------------------------------------
# 3. 后端守卫（不触 DB）
# ---------------------------------------------------------------------------
async def test_check_backend_rejects_non_postgres(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER_BACKEND", "sqlite")
    reason = checkpoint_cleanup_service._check_backend()
    assert reason is not None and "sqlite" in reason


async def test_check_backend_rejects_uninitialized_pool(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER_BACKEND", "postgres")
    monkeypatch.setattr(manager_module.pg_manager, "langgraph_pool", None)
    reason = checkpoint_cleanup_service._check_backend()
    assert reason is not None and "langgraph_pool" in reason


async def test_cleanup_returns_skipped_without_db_touch(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER_BACKEND", "sqlite")

    touched = False

    def _fail_session(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("should not touch db")

    monkeypatch.setattr(checkpoint_cleanup_service.pg_manager, "get_async_session_context", _fail_session)

    result = await cleanup_inactive_checkpoints()
    assert result["skipped"] is not None
    assert result["candidates"] == 0
    assert not touched


# ---------------------------------------------------------------------------
# 4. Scheduler 节流
# ---------------------------------------------------------------------------
async def test_scheduler_throttles_checkpoint_cleanup(monkeypatch):
    calls: list[int] = []

    async def _fake_cleanup():
        calls.append(1)

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _fake_cleanup)

    svc = SchedulerService(checkpoint_cleanup_interval_hours=24)
    base = utc_now_naive()

    await svc._maybe_cleanup_stale_checkpoints(now=base)
    assert len(calls) == 1

    # interval 内不执行
    await svc._maybe_cleanup_stale_checkpoints(now=base + timedelta(hours=1))
    assert len(calls) == 1

    # interval 后再次执行
    await svc._maybe_cleanup_stale_checkpoints(now=base + timedelta(hours=25))
    assert len(calls) == 2


async def test_scheduler_cleanup_exception_does_not_raise(monkeypatch):
    async def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _boom)

    svc = SchedulerService()
    await svc._maybe_cleanup_stale_checkpoints(now=utc_now_naive())  # 不应抛异常


async def test_tick_once_invokes_checkpoint_cleanup(db_env, monkeypatch):
    calls: list[bool] = []

    async def _fake_cleanup(*, now=None):
        calls.append(True)

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _fake_cleanup)

    async def _no_due(*_args, **_kwargs):
        return []

    monkeypatch.setattr(scheduler_service.ScheduleRepository, "claim_due_schedules", _no_due)

    svc = SchedulerService()
    await svc._tick_once()
    assert calls == [True]


# ---------------------------------------------------------------------------
# 5. CHECKPOINT_CLEANUP_DAILY_AT：解析 / 时区命中 / scheduler 固定时刻模式
# ---------------------------------------------------------------------------
SHANGHAI_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")


def test_parse_daily_at_accepts_valid_and_rejects_invalid(monkeypatch):
    assert checkpoint_cleanup_service.parse_daily_at("03:30") == (3, 30)
    assert checkpoint_cleanup_service.parse_daily_at(" 23:05 ") == (23, 5)
    assert checkpoint_cleanup_service.parse_daily_at("0:00") == (0, 0)
    # 未配置 / 空串 → None（默认模式）
    assert checkpoint_cleanup_service.parse_daily_at(None) is None
    assert checkpoint_cleanup_service.parse_daily_at("   ") is None
    # 非法格式 → 告警并回退 None
    for bad in ("25:00", "12:60", "abc", "12", "12:30:45", ""):
        assert checkpoint_cleanup_service.parse_daily_at(bad) is None, bad


def test_parse_daily_at_reads_env(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_CLEANUP_DAILY_AT", "04:15")
    assert checkpoint_cleanup_service.parse_daily_at(os.getenv(checkpoint_cleanup_service.DAILY_AT_ENV_KEY)) == (4, 15)


def test_is_daily_at_due_matches_minute_window_and_dedupes_by_local_date():
    # 2026-06-01 03:29 上海 = 前一天 19:29 UTC
    before = datetime(2026, 6, 1, 19, 29)
    at_window = datetime(2026, 6, 1, 19, 30)  # 上海 03:30 整
    after = datetime(2026, 6, 1, 19, 31)

    assert not checkpoint_cleanup_service.is_daily_at_due(
        now_utc=before, daily_at=(3, 30), tz=SHANGHAI_TZ, last_run_at=None
    )
    assert checkpoint_cleanup_service.is_daily_at_due(
        now_utc=at_window, daily_at=(3, 30), tz=SHANGHAI_TZ, last_run_at=None
    )
    # 当天已跑 → 不再重复
    assert not checkpoint_cleanup_service.is_daily_at_due(
        now_utc=at_window,
        daily_at=(3, 30),
        tz=SHANGHAI_TZ,
        last_run_at=datetime(2026, 6, 1, 19, 30),
    )
    # 窗口已过但当天没跑 → 不补跑
    assert not checkpoint_cleanup_service.is_daily_at_due(
        now_utc=after, daily_at=(3, 30), tz=SHANGHAI_TZ, last_run_at=None
    )


def test_is_daily_at_due_handles_dst_gap_safely():
    # 用 UTC↔local 换算，DST 时区下窗口判断依然成立（此处验证不抛异常且日期去重正确）
    tz = zoneinfo.ZoneInfo("America/New_York")
    assert checkpoint_cleanup_service.is_daily_at_due(
        now_utc=datetime(2026, 3, 8, 7, 30),  # 纽约 03:30 EDT（跳跃日）
        daily_at=(3, 30),
        tz=tz,
        last_run_at=None,
    )


async def test_scheduler_daily_at_mode_fires_once_per_day(monkeypatch):
    calls: list[int] = []

    async def _fake_cleanup():
        calls.append(1)

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _fake_cleanup)
    monkeypatch.setenv("CHECKPOINT_CLEANUP_DAILY_AT", "03:30")

    svc = SchedulerService()
    day1 = datetime(2026, 6, 1, 19, 30)  # 上海 6-2 03:30
    day2_window = datetime(2026, 6, 2, 19, 30)  # 上海 6-3 03:30
    day2_after = datetime(2026, 6, 2, 19, 35)

    await svc._maybe_cleanup_stale_checkpoints(now=day1)
    assert len(calls) == 1

    # 同一天后续 tick（即使隔了 25h 的墙钟时间）不再执行
    await svc._maybe_cleanup_stale_checkpoints(now=day2_after)
    assert len(calls) == 1

    # 次日窗口再次执行
    await svc._maybe_cleanup_stale_checkpoints(now=day2_window)
    assert len(calls) == 2


async def test_scheduler_daily_at_outside_window_skips(monkeypatch):
    calls: list[int] = []

    async def _fake_cleanup():
        calls.append(1)

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _fake_cleanup)
    monkeypatch.setenv("CHECKPOINT_CLEANUP_DAILY_AT", "03:30")

    svc = SchedulerService()
    # 启动锚点行为不适用：固定时刻模式下窗口外不执行
    await svc._maybe_cleanup_stale_checkpoints(now=datetime(2026, 6, 1, 19, 0))
    assert calls == []


async def test_scheduler_invalid_daily_at_falls_back_to_interval_mode(monkeypatch):
    calls: list[int] = []

    async def _fake_cleanup():
        calls.append(1)

    monkeypatch.setattr(checkpoint_cleanup_service, "cleanup_inactive_checkpoints", _fake_cleanup)
    monkeypatch.setenv("CHECKPOINT_CLEANUP_DAILY_AT", "not-a-time")

    svc = SchedulerService(checkpoint_cleanup_interval_hours=24)
    base = utc_now_naive()
    await svc._maybe_cleanup_stale_checkpoints(now=base)
    assert len(calls) == 1
    await svc._maybe_cleanup_stale_checkpoints(now=base + timedelta(hours=1))
    assert len(calls) == 1
    await svc._maybe_cleanup_stale_checkpoints(now=base + timedelta(hours=25))
    assert len(calls) == 2
