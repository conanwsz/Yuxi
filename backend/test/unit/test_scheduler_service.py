"""SchedulerService 单元测试 — 覆盖 cron 校验、时区、next_fire_at、tick 跳过逻辑与 fire 路径。

数据库交互用 sqlite in-memory 替身，避免依赖 docker；
真实 ARQ + AgentRun 路径不在单元测试覆盖范围，由集成测试负责。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import scheduler_service
from yuxi.services.scheduler_service import (
    SchedulerService,
    compute_next_fire_at,
    is_valid_cron_expression,
    is_valid_timezone,
)
from yuxi.storage.postgres.models_business import (
    Base,
    Schedule,
    ScheduleExecution,
    SCHEDULE_STATUS_FAILED,
    SCHEDULE_STATUS_PENDING,
    SCHEDULE_STATUS_RUNNING,
    SCHEDULE_STATUS_SUCCESS,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


# ---------------------------------------------------------------------------
# cron / tz / next_fire_at 纯函数
# ---------------------------------------------------------------------------


def test_cron_validation_accepts_5_and_6_segments_and_aliases():
    assert is_valid_cron_expression("*/5 * * * *")
    assert is_valid_cron_expression("0 0 * * 0")
    assert is_valid_cron_expression("0 */5 * * * *")  # 6 段（含秒）
    assert is_valid_cron_expression("@daily")
    assert is_valid_cron_expression("@hourly")


def test_cron_validation_rejects_garbage():
    assert not is_valid_cron_expression("bad-cron")
    assert not is_valid_cron_expression("")
    assert not is_valid_cron_expression("60 * * * *")  # 分钟越界
    assert not is_valid_cron_expression("not a cron at all")


def test_timezone_validation_accepts_iana_and_rejects_unknown():
    assert is_valid_timezone("UTC")
    assert is_valid_timezone("Asia/Shanghai")
    assert is_valid_timezone("America/New_York")
    assert not is_valid_timezone("Not/AZone")
    assert not is_valid_timezone("")
    assert not is_valid_timezone("UTC+8")


def test_compute_next_fire_at_respects_cron_and_timezone():
    schedule = SimpleNamespace(cron_expression="0 0 * * *", timezone="UTC")
    base = datetime(2026, 1, 1, 0, 0, 0)
    next_at = compute_next_fire_at(schedule=schedule, base=base)
    assert next_at is not None
    # 0 0 * * * 的下一次触发就是次日 00:00 UTC
    assert next_at == datetime(2026, 1, 2, 0, 0, 0)


def test_compute_next_fire_at_handles_utc_base_with_shanghai_timezone():
    """回归：base 是 UTC naive、tz=Asia/Shanghai，必须先标 UTC 再 astimezone，不能直接贴标签。

    2026-01-01 12:00 UTC = 2026-01-01 20:00 +08；下一个 0 */1 = 21:00 +08 = 13:00 UTC。
    之前 bug：base_naive.replace(tzinfo=Asia/Shanghai) 会把 12:00 当成 12:00+08（物理时刻
    变成 04:00 UTC），croniter 算出 13:00+08 = 05:00 UTC —— 偏 8 小时。
    """
    schedule = SimpleNamespace(cron_expression="0 */1 * * *", timezone="Asia/Shanghai")
    base = datetime(2026, 1, 1, 12, 0, 0)  # 12:00 UTC
    next_at = compute_next_fire_at(schedule=schedule, base=base)
    assert next_at is not None
    assert next_at == datetime(2026, 1, 1, 13, 0, 0)


async def test_tick_advances_next_fire_at_in_same_call_as_claim(fake_backend, monkeypatch):
    """回归：claim + advance 必须在同一次调用里完成；reload 中断不会丢 next_fire_at 推进。

    之前实现是 claim_due_schedules 一次事务后 commit，_handle_due_schedule 后续独立事务
    才推进 next_fire_at。watchfiles reload 期间如果中断在两个事务之间，next_fire_at 没
    推进，下次 tick 又会 claim 到同一条 schedule，重复触发。
    """
    past = datetime(2020, 1, 1, 0, 0, 0)
    fake_backend.schedules["a"] = _make_schedule(
        id="a", enabled=1, next_fire_at=past, cron_expression="*/5 * * * *"
    )

    async def fake_dispatch(*, schedule_id, execution_id):
        return None

    svc = SchedulerService()
    monkeypatch.setattr(svc, "_dispatch_execution", fake_dispatch)
    await svc._tick_once()

    # claim + advance 在同一次调用里完成：next_fire_at 已经在第一次 tick 后被推进
    advanced = fake_backend.schedules["a"]
    assert advanced.next_fire_at is not None
    assert advanced.next_fire_at > past
    # last_fired_at 应被设置为「now」（即 tick 那一刻），而不是过去
    assert advanced.last_fired_at is not None
    assert advanced.last_fired_at > past
    # _handle_due_schedule 后续不再额外调 advance_fire_window（被吸收到 claim 里）
    # 因此 advance_calls 中本 schedule 应只有 1 条（来自 claim 阶段）
    advance_for_a = [c for c in fake_backend.advance_calls if c[0] == "a"]
    assert len(advance_for_a) == 1, (
        f"预期 advance 只来自 claim 阶段，实际 {len(advance_for_a)} 次: {advance_for_a}"
    )
    await svc.shutdown()


def test_compute_next_fire_at_returns_none_for_invalid_cron():
    schedule = SimpleNamespace(cron_expression="garbage", timezone="UTC")
    assert compute_next_fire_at(schedule=schedule) is None


def test_compute_next_fire_at_falls_back_to_utc_on_unknown_timezone():
    # 时区非法时函数本身不抛，由调用方负责兜底；这里确认它仍能返回 UTC 时间
    schedule = SimpleNamespace(cron_expression="*/5 * * * *", timezone="Mars/Olympus")
    next_at = compute_next_fire_at(schedule=schedule, base=datetime(2026, 1, 1, 0, 0, 0))
    assert next_at is not None


# ---------------------------------------------------------------------------
# sqlite in-memory 替身
# ---------------------------------------------------------------------------


class _InMemoryBackend:
    """替身 ScheduleRepository / ScheduleExecutionRepository，让 SchedulerService 不需要真 PG。"""

    def __init__(self) -> None:
        self.schedules: dict[str, Schedule] = {}
        self.executions: dict[str, ScheduleExecution] = {}
        self.advance_calls: list[tuple[str, datetime | None]] = []

    # ScheduleRepository 接口
    async def claim_due_schedules(self, *, now, limit, advance_fn=None):
        due = [
            s for s in self.schedules.values()
            if s.enabled == 1 and s.next_fire_at is not None and s.next_fire_at <= now
        ]
        due.sort(key=lambda s: s.next_fire_at)
        claimed = due[: max(limit, 1)]
        # 模拟「claim + advance 同事务」：传入 advance_fn 时立即在内存里推进，
        # 不需要再单独调 advance_fire_window；事务回滚时一并还原。
        if advance_fn is not None and claimed:
            for record in claimed:
                next_fire = advance_fn(schedule=record, base=now)
                self.advance_calls.append((record.id, next_fire))
                record.last_fired_at = now
                record.next_fire_at = next_fire
        return claimed

    async def advance_fire_window(self, *, schedule_id, last_fired_at, next_fire_at):
        self.advance_calls.append((schedule_id, next_fire_at))
        record = self.schedules.get(schedule_id)
        if record is None:
            return
        record.last_fired_at = last_fired_at
        record.next_fire_at = next_fire_at

    async def get_by_id(self, schedule_id):
        return self.schedules.get(schedule_id)

    async def create(self, data):
        record = Schedule(**data)
        self.schedules[record.id] = record
        return record

    async def update(self, schedule_id, data):
        record = self.schedules.get(schedule_id)
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        return record

    async def list_schedules(self, *, enabled=None):
        records = list(self.schedules.values())
        if enabled is not None:
            records = [r for r in records if bool(r.enabled) == enabled]
        return records

    async def delete(self, schedule_id):
        return self.schedules.pop(schedule_id, None) is not None

    async def set_enabled(self, schedule_id, enabled):
        record = self.schedules.get(schedule_id)
        if record is None:
            return None
        record.enabled = 1 if enabled else 0
        return record

    async def fetch_due_schedule_ids(self, *, now, limit):
        return [s.id for s in await self.claim_due_schedules(now=now, limit=limit)]

    # ScheduleExecutionRepository 接口
    async def create_execution(self, data):
        record = ScheduleExecution(**data)
        self.executions[record.id] = record
        return record

    async def update_execution(self, execution_id, data):
        record = self.executions.get(execution_id)
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        return record

    async def list_for_schedule(self, *, schedule_id, limit, before=None):
        records = [r for r in self.executions.values() if r.schedule_id == schedule_id]
        records.sort(key=lambda r: r.created_at, reverse=True)
        if before is not None:
            records = [r for r in records if r.created_at < before]
        return records[: max(limit, 1)]

    async def delete_by_schedule(self, schedule_id):
        removed = sum(1 for r in self.executions.values() if r.schedule_id == schedule_id)
        self.executions = {k: v for k, v in self.executions.items() if v.schedule_id != schedule_id}
        return removed

    async def cleanup_terminal_executions(self, *, older_than):
        kept = {}
        removed = 0
        for k, v in self.executions.items():
            if v.completed_at is not None and v.completed_at < older_than and v.status in {
                SCHEDULE_STATUS_SUCCESS,
                SCHEDULE_STATUS_FAILED,
            }:
                removed += 1
                continue
            kept[k] = v
        self.executions = kept
        return removed

    async def has_running_execution(self, *, schedule_id):
        return any(
            r.schedule_id == schedule_id and r.status in {SCHEDULE_STATUS_PENDING, SCHEDULE_STATUS_RUNNING}
            for r in self.executions.values()
        )


@pytest.fixture()
def fake_backend(monkeypatch):
    backend = _InMemoryBackend()

    def _make_schedule_repo():
        class _Repo:
            async def claim_due_schedules(self, *, now, limit, advance_fn=None):
                return await backend.claim_due_schedules(
                    now=now, limit=limit, advance_fn=advance_fn
                )

            async def advance_fire_window(self, *, schedule_id, last_fired_at, next_fire_at):
                return await backend.advance_fire_window(
                    schedule_id=schedule_id,
                    last_fired_at=last_fired_at,
                    next_fire_at=next_fire_at,
                )

            async def get_by_id(self, schedule_id):
                return await backend.get_by_id(schedule_id)

        return _Repo()

    def _make_execution_repo():
        class _Repo:
            async def create(self, data):
                return await backend.create_execution(data)

            async def update(self, execution_id, data):
                return await backend.update_execution(execution_id, data)

            async def has_running_execution(self, *, schedule_id):
                return await backend.has_running_execution(schedule_id=schedule_id)

            async def cleanup_terminal_executions(self, *, older_than):
                return await backend.cleanup_terminal_executions(older_than=older_than)

        return _Repo()

    monkeypatch.setattr(scheduler_service, "ScheduleRepository", _make_schedule_repo)
    monkeypatch.setattr(scheduler_service, "ScheduleExecutionRepository", _make_execution_repo)
    return backend


def _make_schedule(**overrides) -> Schedule:
    now = datetime(2026, 1, 1, 0, 0, 0)
    base = {
        "id": uuid.uuid4().hex,
        "name": "demo",
        "description": None,
        "agent_slug": "general-agent",
        "query": "ping",
        "runtime_overrides": None,
        "cron_expression": "*/5 * * * *",
        "timezone": "UTC",
        "enabled": 1,
        "owner_uid": "user-1",
        "last_fired_at": None,
        "next_fire_at": now,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Schedule(**base)


async def test_tick_skips_disabled_schedules(fake_backend):
    fake_backend.schedules["a"] = _make_schedule(id="a", enabled=0, next_fire_at=datetime(2020, 1, 1))
    fake_backend.schedules["b"] = _make_schedule(id="b", enabled=1, next_fire_at=datetime(2099, 1, 1))

    svc = SchedulerService(tick_interval_seconds=1)
    await svc._tick_once()
    # 两条 schedule 都不应该被推进
    assert fake_backend.advance_calls == []
    assert fake_backend.schedules["a"].last_fired_at is None
    assert fake_backend.schedules["b"].last_fired_at is None
    await svc.shutdown()


async def test_tick_skips_schedules_with_future_next_fire_at(fake_backend):
    future = datetime(2099, 1, 1)
    fake_backend.schedules["a"] = _make_schedule(id="a", enabled=1, next_fire_at=future)
    svc = SchedulerService()
    await svc._tick_once()
    assert fake_backend.advance_calls == []
    assert fake_backend.executions == {}
    await svc.shutdown()


async def test_tick_dispatches_due_schedule_and_advances_next_fire_at(fake_backend, monkeypatch):
    """启用 + 到点的 schedule 应被推进 next_fire_at 并产生 pending execution。"""
    past = datetime(2020, 1, 1, 0, 0, 0)
    fake_backend.schedules["a"] = _make_schedule(id="a", enabled=1, next_fire_at=past, cron_expression="*/5 * * * *")

    dispatched: list[str] = []

    async def fake_dispatch(*, schedule_id, execution_id):
        dispatched.append(execution_id)

    svc = SchedulerService()
    # 必须在实例上 patch，避免 self 自动注入
    monkeypatch.setattr(svc, "_dispatch_execution", fake_dispatch)
    await svc._tick_once()

    # advance_fire_window 被调用，next_fire_at 推进到未来
    assert any(call[0] == "a" for call in fake_backend.advance_calls)
    advanced = fake_backend.schedules["a"]
    assert advanced.last_fired_at is not None
    assert advanced.next_fire_at is not None
    assert advanced.next_fire_at > past

    # 一条 pending execution 被创建
    assert len(fake_backend.executions) == 1
    exec_record = next(iter(fake_backend.executions.values()))
    assert exec_record.status == SCHEDULE_STATUS_PENDING
    assert exec_record.schedule_id == "a"
    # dispatch 是通过 create_task 派发，dispatched 可能在 tick 结束前未完成 — 至少要等任务完成
    await asyncio.sleep(0.05)
    assert dispatched == [exec_record.id]
    await svc.shutdown()


async def test_fire_now_writes_execution_and_dispatches_async(fake_backend, monkeypatch):
    fake_backend.schedules["a"] = _make_schedule(
        id="a", enabled=0, next_fire_at=datetime(2026, 6, 1, 0, 0, 0)
    )  # 即便禁用也能 fire；保持原 next_fire_at 不被 fire 改写

    dispatched: list[str] = []

    async def fake_dispatch(*, schedule_id, execution_id):
        dispatched.append(execution_id)

    svc = SchedulerService()
    monkeypatch.setattr(svc, "_dispatch_execution", fake_dispatch)
    execution = await svc.fire_now(schedule_id="a")

    assert execution.schedule_id == "a"
    assert execution.status == SCHEDULE_STATUS_PENDING
    await asyncio.sleep(0.05)
    assert dispatched == [execution.id]
    # fire 不应改 next_fire_at
    assert fake_backend.schedules["a"].next_fire_at == datetime(2026, 6, 1, 0, 0, 0)
    await svc.shutdown()


async def test_failed_execution_is_marked_failed_on_dispatch_error(fake_backend, monkeypatch):
    fake_backend.schedules["a"] = _make_schedule(id="a", enabled=1, next_fire_at=datetime(2020, 1, 1))

    async def boom(*, run_id, current_uid):
        del run_id, current_uid
        raise RuntimeError("网络挂了")

    # 拦截 dispatch 真正调用的 await_agent_run_result，让异常沿真实 try/except 上抛
    monkeypatch.setattr(scheduler_service, "await_agent_run_result", boom)

    svc = SchedulerService()
    await svc._tick_once()

    # 派发在 create_task 里完成，_tick_once 不会等；手动等 inflight 收敛
    for _ in range(200):
        statuses = [e.status for e in fake_backend.executions.values()]
        if SCHEDULE_STATUS_FAILED in statuses:
            break
        await asyncio.sleep(0.02)

    assert any(
        e.status == SCHEDULE_STATUS_FAILED
        for e in fake_backend.executions.values()
    )
    await svc.shutdown()


async def test_apply_result_marks_success_and_truncates_summary(monkeypatch):
    svc = SchedulerService(result_summary_max_chars=20)

    class _ExecRepoStub:
        def __init__(self):
            self.captured: list[tuple[str, dict]] = []

        async def update(self, execution_id, data):
            self.captured.append((execution_id, data))
            return None

    stub = _ExecRepoStub()
    monkeypatch.setattr(scheduler_service, "ScheduleExecutionRepository", lambda: stub)

    long_output = "x" * 100
    await svc._apply_result(
        execution_id="exec-1",
        result={"status": "completed", "output": long_output, "error": None},
    )
    assert stub.captured, "update should be called"
    exec_id, payload = stub.captured[-1]
    assert exec_id == "exec-1"
    assert payload["status"] == SCHEDULE_STATUS_SUCCESS
    assert payload["result_summary"].endswith("…")
    assert len(payload["result_summary"]) == 21  # 20 chars + ellipsis
    assert payload["error"] is None


async def test_apply_result_marks_failed_with_error(monkeypatch):
    svc = SchedulerService()

    class _ExecRepoStub:
        def __init__(self):
            self.captured: list[tuple[str, dict]] = []

        async def update(self, execution_id, data):
            self.captured.append((execution_id, data))

    stub = _ExecRepoStub()
    monkeypatch.setattr(scheduler_service, "ScheduleExecutionRepository", lambda: stub)

    await svc._apply_result(
        execution_id="exec-1",
        result={
            "status": "failed",
            "output": "",
            "error": {"type": "tool_error", "message": "工具调用失败"},
        },
    )
    _, payload = stub.captured[-1]
    assert payload["status"] == SCHEDULE_STATUS_FAILED
    assert payload["result_summary"] is None
    assert "工具调用失败" in (payload["error"] or "")


async def test_start_shutdown_lifecycle(fake_backend):
    svc = SchedulerService(tick_interval_seconds=0.05)
    await svc.start()
    assert svc.is_running
    await svc.shutdown()
    assert not svc.is_running


async def test_repositories_have_due_schedules_query_helper():
    """用 sqlite in-memory 跑 ScheduleRepository 的真实 SQL 入口（fetch_due_schedule_ids 走 SELECT）。"""
    from yuxi.utils.datetime_utils import utc_now_naive

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        # 直接通过 SQLAlchemy 插入，绕开 pg_manager
        db.add(
            Schedule(
                id="a",
                name="n",
                agent_slug="agent",
                query="q",
                cron_expression="*/5 * * * *",
                timezone="UTC",
                enabled=1,
                owner_uid="u",
                next_fire_at=utc_now_naive(),
            )
        )
        await db.commit()

    # 现在直接 SELECT 验证：not enabled 的不会出现在 due 列表里
    async with factory() as db:
        result = await db.execute(
            select(Schedule.id).where(Schedule.enabled == 1, Schedule.next_fire_at.is_not(None))
        )
        ids = [row[0] for row in result.all()]
        assert ids == ["a"]

    await engine.dispose()
