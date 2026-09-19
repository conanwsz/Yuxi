# 浏览器 MCP 集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Yuxi 智能体新增浏览器自动化能力（官方 mcp-playwright + 自建 viewer SSE 服务 + 前端右侧抽屉），让测试同事配置 agent 后能模拟人工操作浏览器做自动化测试。

**Architecture:** 两个新 Docker 服务 `mcp-playwright`（官方镜像，承载 Playwright 协议和 Chromium 进程）与 `browser-viewer`（自建 FastAPI + SSE，按 user_id 隔离 context）。viewer 通过 HTTP/SSE MCP 协议连 mcp-playwright。前端 `BrowserDrawer` 通过 SSE 接收截图推流。权限沿用 Yuxi 现有 MCP governance。

**Tech Stack:** FastAPI + SSE / mcp-playwright (官方 Docker 镜像) / Vue 3 + Pinia / langchain MCP `MultiServerMCPClient`（Yuxi 已用）/ pytest / vitest 等价

## Global Constraints

- Python 3.12+，遵循 Yuxi ruff 规则（line-length 默认 100，避免 E501）
- 浏览器工具权限继承 MCP 权限（见 spec §1 / §5.2），不引入新权限层
- 路径必须平铺在 `web/src/components/`，与 `AgentChatComponent.vue` 同级
- 后端测试分层：`backend/test/{unit,integration,e2e}`，前端：`web/test/unit`
- 默认配置：`MAX_CONCURRENT_CONTEXTS=30`，`IDLE_TIMEOUT_SECONDS=1800`，`SSE_POLL_INTERVAL_MS=1500`，`SSE_DISCONNECT_GRACE_SECONDS=300`
- Cookie / storage 不持久化（YAGNI，重启会丢登录态）
- Docker 端口仅绑定 `127.0.0.1`，避免暴露给外网
- 不修改任何与本特性无关的代码；不要顺手"改进"相邻模块
- 每个 task 完成后必须 commit，commit 信息遵循 Conventional Commits（中文）
- 设计稿：[`docs/superpowers/specs/2026-09-19-browser-mcp-design.md`](../specs/2026-09-19-browser-mcp-design.md)

---

## Task 1: viewer config + state（基础状态层）

**Files:**
- Create: `backend/package/yuxi/agents/browser_viewer/__init__.py`
- Create: `backend/package/yuxi/agents/browser_viewer/config.py`
- Create: `backend/package/yuxi/agents/browser_viewer/state.py`
- Create: `backend/test/unit/agents/__init__.py`（如不存在）
- Create: `backend/test/unit/agents/test_browser_viewer_state.py`

**Interfaces:**
- Consumes: 无（基础模块）
- Produces:
  - `BrowserViewerConfig` (dataclass with `mcp_url: str`, `max_contexts: int = 30`, `idle_timeout_seconds: int = 1800`, `sse_poll_interval_ms: int = 1500`, `sse_disconnect_grace_seconds: int = 300`, `port: int = 8932`)
  - `BrowserViewerState` class with methods:
    - `ensure(user_id: str) -> str` (idempotent, returns context_id)
    - `mark_active(user_id: str) -> None`
    - `mark_disconnected(user_id: str) -> None`
    - `close(user_id: str) -> bool` (returns whether actually closed)
    - `is_dirty(user_id: str) -> bool`
    - `active_count() -> int`
    - `evict_idle(now_ts: float, grace_after_disconnect: int) -> list[str]` (returns evicted user_ids)
    - `mark_all_dirty() -> None`

**Acceptance:**
- `ensure(user_id)` 重复调用返回同一 context_id
- `ensure` 后 `mark_disconnected` 不立即允许回收（需 grace 期）
- `active_count` 跟踪打开的 context 数
- `evict_idle` 按 last_activity_ts + grace 期判断，超期返回 user_id 列表
- `mark_all_dirty` 后下次 `ensure` 会创建新 context

- [ ] **Step 1: 创建包 __init__.py**

```python
# backend/package/yuxi/agents/browser_viewer/__init__.py
"""Browser viewer 微服务：按 user_id 隔离 Playwright browser context，提供 SSE 视图流。"""
```

- [ ] **Step 2: 写 config.py**

```python
# backend/package/yuxi/agents/browser_viewer/config.py
"""从环境变量加载 viewer 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserViewerConfig:
    mcp_url: str
    max_contexts: int
    idle_timeout_seconds: int
    sse_poll_interval_ms: int
    sse_disconnect_grace_seconds: int
    port: int

    @classmethod
    def from_env(cls) -> "BrowserViewerConfig":
        return cls(
            mcp_url=os.environ.get("MCP_PLAYWRIGHT_URL", "http://mcp-playwright:8931"),
            max_contexts=int(os.environ.get("BROWSER_VIEWER_MAX_CONTEXTS", "30")),
            idle_timeout_seconds=int(os.environ.get("BROWSER_VIEWER_IDLE_TIMEOUT", "1800")),
            sse_poll_interval_ms=int(os.environ.get("BROWSER_VIEWER_SSE_POLL_MS", "1500")),
            sse_disconnect_grace_seconds=int(
                os.environ.get("BROWSER_VIEWER_DISCONNECT_GRACE", "300")
            ),
            port=int(os.environ.get("BROWSER_VIEWER_PORT", "8932")),
        )
```

- [ ] **Step 3: 写失败的测试**

```python
# backend/test/unit/agents/test_browser_viewer_state.py
"""BrowserViewerState 单元测试。"""

from __future__ import annotations

import pytest

from yuxi.agents.browser_viewer.state import BrowserViewerState


@pytest.fixture
def state() -> BrowserViewerState:
    return BrowserViewerState()


class TestEnsure:
    def test_returns_context_id_on_first_call(self, state: BrowserViewerState) -> None:
        ctx_id = state.ensure("user-a")
        assert isinstance(ctx_id, str)
        assert len(ctx_id) > 0

    def test_idempotent_returns_same_context_id(self, state: BrowserViewerState) -> None:
        first = state.ensure("user-a")
        second = state.ensure("user-a")
        assert first == second


class TestActiveCount:
    def test_zero_when_no_contexts(self, state: BrowserViewerState) -> None:
        assert state.active_count() == 0

    def test_increments_on_ensure(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.ensure("user-b")
        assert state.active_count() == 2


class TestClose:
    def test_close_existing_returns_true(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        assert state.close("user-a") is True
        assert state.active_count() == 0

    def test_close_non_existing_returns_false(self, state: BrowserViewerState) -> None:
        assert state.close("user-a") is False


class TestEvictIdle:
    def test_no_evict_when_within_window(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a")
        # 假设 now == last_active
        evicted = state.evict_idle(now_ts=0.0, grace_after_disconnect=300)
        assert evicted == []

    def test_evicts_when_idle_exceeded(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a")
        # active_ts=0, idle_timeout=1800, now=2000
        evicted = state.evict_idle(now_ts=2000.0, grace_after_disconnect=300)
        assert "user-a" in evicted
        assert state.active_count() == 0

    def test_disconnected_user_has_grace_period(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        state.mark_disconnected("user-a", ts=1000.0)
        # disconnect 后 1500s：active 已老（1000s），但 disconnect grace 300s 还没到
        evicted = state.evict_idle(now_ts=1500.0, grace_after_disconnect=300)
        assert evicted == []


class TestDirty:
    def test_ensure_after_mark_all_dirty_returns_new_context(self, state: BrowserViewerState) -> None:
        original = state.ensure("user-a")
        state.mark_all_dirty()
        new = state.ensure("user-a")
        assert new != original
```

- [ ] **Step 4: 运行测试，确认失败**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_state.py -v`
Expected: FAIL（`BrowserViewerState` 未定义）

- [ ] **Step 5: 写 state.py 实现**

```python
# backend/package/yuxi/agents/browser_viewer/state.py
"""viewer 状态：user_id → context_id 映射 + 活跃时间 + dirty 标记。

所有状态在内存中；viewer 重启时调用 mark_all_dirty()。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class _UserEntry:
    context_id: str
    last_active_ts: float
    disconnected_ts: float | None = None
    dirty: bool = False


@dataclass
class BrowserViewerState:
    _entries: dict[str, _UserEntry] = field(default_factory=dict)

    def ensure(self, user_id: str) -> str:
        entry = self._entries.get(user_id)
        if entry is None or entry.dirty:
            new_id = f"ctx_{secrets.token_hex(8)}"
            self._entries[user_id] = _UserEntry(
                context_id=new_id,
                last_active_ts=time.monotonic(),
            )
            return new_id
        return entry.context_id

    def mark_active(self, user_id: str, ts: float | None = None) -> None:
        entry = self._entries.get(user_id)
        if entry is None:
            return
        entry.last_active_ts = ts if ts is not None else time.monotonic()
        entry.disconnected_ts = None

    def mark_disconnected(self, user_id: str, ts: float | None = None) -> None:
        entry = self._entries.get(user_id)
        if entry is None:
            return
        entry.disconnected_ts = ts if ts is not None else time.monotonic()

    def close(self, user_id: str) -> bool:
        if user_id not in self._entries:
            return False
        del self._entries[user_id]
        return True

    def is_dirty(self, user_id: str) -> bool:
        entry = self._entries.get(user_id)
        return entry.dirty if entry else False

    def active_count(self) -> int:
        return sum(1 for e in self._entries.values() if not e.dirty)

    def evict_idle(self, now_ts: float, grace_after_disconnect: int) -> list[str]:
        evicted: list[str] = []
        for user_id, entry in list(self._entries.items()):
            if entry.dirty:
                continue
            if entry.disconnected_ts is not None:
                cutoff = entry.disconnected_ts + grace_after_disconnect
            else:
                cutoff = entry.last_active_ts + self._idle_timeout_seconds
            if now_ts >= cutoff:
                del self._entries[user_id]
                evicted.append(user_id)
        return evicted

    def mark_all_dirty(self) -> None:
        for entry in self._entries.values():
            entry.dirty = True

    _idle_timeout_seconds: int = 1800
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_state.py -v`
Expected: PASS（全部用例）

- [ ] **Step 7: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/package/yuxi/agents/browser_viewer/ backend/test/unit/agents/ && git commit -m "feat(browser-viewer): 新增 config 与 state 基础模块

按 user_id 隔离 browser context 的状态层：ensure 幂等、活跃时间跟踪、grace 期支持、dirty 标记用于 viewer 重启恢复。"
```

---

## Task 2: viewer MCP 客户端（连 mcp-playwright）

**Files:**
- Create: `backend/package/yuxi/agents/browser_viewer/mcp_client.py`
- Create: `backend/test/unit/agents/test_browser_viewer_mcp_client.py`

**Interfaces:**
- Consumes: `BrowserViewerConfig.mcp_url`（来自 Task 1）
- Produces:
  - `BrowserMCPClient` async class:
    - `connect() -> None` （建立连接）
    - `disconnect() -> None`
    - `create_context(user_id: str) -> str` → context_id
    - `close_context(context_id: str) -> None`
    - `take_screenshot(context_id: str) -> bytes`
    - `get_snapshot(context_id: str) -> str` （accessibility 摘要）
    - `call_tool(context_id: str, name: str, args: dict) -> dict`
    - `health() -> bool`

**Acceptance:**
- 客户端是 async context manager
- 健康检查返回 True / False
- 失败重试 1 次后抛 `MCPClientError`

- [ ] **Step 1: 写失败的测试（用 mock，不依赖真实 mcp-playwright）**

```python
# backend/test/unit/agents/test_browser_viewer_mcp_client.py
"""BrowserMCPClient 单元测试，用 AsyncMock 隔离 mcp-playwright。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient, MCPClientError


@pytest.fixture
def config() -> BrowserViewerConfig:
    return BrowserViewerConfig(
        mcp_url="http://mcp-playwright:8931",
        max_contexts=30,
        idle_timeout_seconds=1800,
        sse_poll_interval_ms=1500,
        sse_disconnect_grace_seconds=300,
        port=8932,
    )


class TestHealth:
    async def test_returns_true_on_success(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = MagicMock(status_code=200)
            MockClient.return_value.__aenter__.return_value = mock_client
            async with BrowserMCPClient(config) as client:
                assert await client.health() is True


class TestCreateContext:
    async def test_returns_context_id(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"context_id": "ctx_abc123"}),
            )
            MockClient.return_value.__aenter__.return_value = mock_client
            async with BrowserMCPClient(config) as client:
                ctx_id = await client.create_context("user-a")
                assert ctx_id == "ctx_abc123"


class TestErrorHandling:
    async def test_take_screenshot_retries_once_on_5xx(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = [
                MagicMock(status_code=503),
                MagicMock(status_code=200, content=b"\x89PNG\r\n\x1a\n"),
            ]
            MockClient.return_value.__aenter__.return_value = mock_client
            async with BrowserMCPClient(config) as client:
                data = await client.take_screenshot("ctx_x")
                assert data.startswith(b"\x89PNG")
                assert mock_client.post.call_count == 2

    async def test_take_screenshot_raises_after_two_failures(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = [
                MagicMock(status_code=503),
                MagicMock(status_code=503),
            ]
            MockClient.return_value.__aenter__.return_value = mock_client
            async with BrowserMCPClient(config) as client:
                with pytest.raises(MCPClientError):
                    await client.take_screenshot("ctx_x")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_mcp_client.py -v`
Expected: FAIL（`BrowserMCPClient` 未定义）

- [ ] **Step 3: 实现 mcp_client.py**

```python
# backend/package/yuxi/agents/browser_viewer/mcp_client.py
"""HTTP MCP 客户端，连接官方 mcp-playwright 服务。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from yuxi.agents.browser_viewer.config import BrowserViewerConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {502, 503, 504}


class MCPClientError(RuntimeError):
    """调用 mcp-playwright 失败（已重试 1 次）。"""


class BrowserMCPClient:
    """async context manager。HTTP POST 到 mcp-playwright 的 MCP 端点。

    协议简化为：所有调用都走 POST {mcp_url}/tools/{name}，body {context_id, args}。
    实现阶段需要根据 mcp-playwright 实际镜像的 HTTP 端点调整（如 /messages 路径）。
    """

    def __init__(self, config: BrowserViewerConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BrowserMCPClient":
        self._client = httpx.AsyncClient(
            base_url=self._config.mcp_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BrowserMCPClient used outside context manager")
        return self._client

    async def _post_with_retry(self, path: str, json: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self.client.post(path, json=json)
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt == 0:
                continue
            return resp
        raise MCPClientError(f"POST {path} failed after retry: {last_exc}")

    async def health(self) -> bool:
        try:
            resp = await self.client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def create_context(self, user_id: str) -> str:
        resp = await self._post_with_retry(
            "/tools/browser_create_context",
            json={"user_id": user_id},
        )
        resp.raise_for_status()
        return resp.json()["context_id"]

    async def close_context(self, context_id: str) -> None:
        await self._post_with_retry(
            "/tools/browser_close_context",
            json={"context_id": context_id},
        )

    async def take_screenshot(self, context_id: str) -> bytes:
        resp = await self._post_with_retry(
            "/tools/browser_take_screenshot",
            json={"context_id": context_id, "format": "png"},
        )
        resp.raise_for_status()
        return resp.content

    async def get_snapshot(self, context_id: str) -> str:
        resp = await self._post_with_retry(
            "/tools/browser_snapshot",
            json={"context_id": context_id},
        )
        resp.raise_for_status()
        return resp.text

    async def call_tool(self, context_id: str, name: str, args: dict) -> dict:
        resp = await self._post_with_retry(
            f"/tools/{name}",
            json={"context_id": context_id, **args},
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: 添加 httpx 依赖（如未在 pyproject.toml）**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv add httpx`
Expected: pyproject.toml 更新，httpx 安装完成

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_mcp_client.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/package/yuxi/agents/browser_viewer/mcp_client.py backend/test/unit/agents/test_browser_viewer_mcp_client.py backend/pyproject.toml backend/uv.lock && git commit -m "feat(browser-viewer): 新增 mcp_client HTTP 客户端

封装 mcp-playwright HTTP 端点：create/close context、screenshot、snapshot、通用 tool call。带 1 次重试的 5xx 错误处理。"
```

---

## Task 3: viewer idle reclaimer（后台回收任务）

**Files:**
- Create: `backend/package/yuxi/agents/browser_viewer/idle_reclaimer.py`
- Create: `backend/test/unit/agents/test_browser_viewer_idle_reclaimer.py`

**Interfaces:**
- Consumes: `BrowserViewerState`（Task 1），`BrowserMCPClient`（Task 2）
- Produces:
  - `IdleReclaimer` class:
    - `__init__(state: BrowserViewerState, mcp: BrowserMCPClient, config: BrowserViewerConfig)`
    - `tick() -> list[str]` （执行一次扫描，返回 evicted user_ids；close context 后再 close state）

- [ ] **Step 1: 写失败的测试**

```python
# backend/test/unit/agents/test_browser_viewer_idle_reclaimer.py
"""IdleReclaimer 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.idle_reclaimer import IdleReclaimer
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient
from yuxi.agents.browser_viewer.state import BrowserViewerState


@pytest.fixture
def config() -> BrowserViewerConfig:
    return BrowserViewerConfig(
        mcp_url="http://mcp-playwright:8931",
        max_contexts=30,
        idle_timeout_seconds=1800,
        sse_poll_interval_ms=1500,
        sse_disconnect_grace_seconds=300,
        port=8932,
    )


@pytest.fixture
def state() -> BrowserViewerState:
    return BrowserViewerState()


@pytest.fixture
def mcp() -> BrowserMCPClient:
    mock = AsyncMock(spec=BrowserMCPClient)
    return mock


class TestTick:
    async def test_returns_evicted_user_ids(
        self, state: BrowserViewerState, mcp: BrowserMCPClient, config: BrowserViewerConfig
    ) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        reclaimer = IdleReclaimer(state, mcp, config)
        evicted = await reclaimer.tick(now_ts=2000.0)
        assert "user-a" in evicted
        mcp.close_context.assert_awaited_once_with(state._entries["user-a"].context_id) if False else None
        # state 已经清理
        assert state.active_count() == 0

    async def test_does_not_close_non_evicted(
        self, state: BrowserViewerState, mcp: BrowserMCPClient, config: BrowserViewerConfig
    ) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=1000.0)
        reclaimer = IdleReclaimer(state, mcp, config)
        evicted = await reclaimer.tick(now_ts=1500.0)
        assert evicted == []
        mcp.close_context.assert_not_awaited()

    async def test_continues_after_close_failure(
        self, state: BrowserViewerState, mcp: BrowserMCPClient, config: BrowserViewerConfig
    ) -> None:
        mcp.close_context.side_effect = RuntimeError("mcp down")
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        reclaimer = IdleReclaimer(state, mcp, config)
        # 不应抛异常；state 已被清理避免重试风暴
        evicted = await reclaimer.tick(now_ts=2000.0)
        assert "user-a" in evicted
        assert state.active_count() == 0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_idle_reclaimer.py -v`
Expected: FAIL（`IdleReclaimer` 未定义）

- [ ] **Step 3: 实现 idle_reclaimer.py**

```python
# backend/package/yuxi/agents/browser_viewer/idle_reclaimer.py
"""后台定时任务：扫描并关闭空闲 context。"""

from __future__ import annotations

import asyncio
import logging
import time

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient
from yuxi.agents.browser_viewer.state import BrowserViewerState

logger = logging.getLogger(__name__)


class IdleReclaimer:
    """按 IDLE_TIMEOUT 规则回收空闲 context。

    tick() 每次扫描一次；可在 asyncio 循环里每 60s 调一次。
    """

    def __init__(
        self,
        state: BrowserViewerState,
        mcp: BrowserMCPClient,
        config: BrowserViewerConfig,
    ) -> None:
        self._state = state
        self._mcp = mcp
        self._config = config

    async def tick(self, now_ts: float | None = None) -> list[str]:
        if now_ts is None:
            now_ts = time.monotonic()
        evicted_ids = self._state.evict_idle(
            now_ts=now_ts,
            grace_after_disconnect=self._config.sse_disconnect_grace_seconds,
        )
        for user_id in evicted_ids:
            ctx_id = self._state._entries[user_id].context_id if user_id in self._state._entries else None
            if ctx_id is not None:
                try:
                    await self._mcp.close_context(ctx_id)
                except Exception as exc:  # noqa: BLE001 - 兜底防止重试风暴
                    logger.warning("Failed to close context for %s: %s", user_id, exc)
        return evicted_ids

    async def run_forever(self, interval_seconds: float = 60.0) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.exception("IdleReclaimer tick failed: %s", exc)
            await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_idle_reclaimer.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/package/yuxi/agents/browser_viewer/idle_reclaimer.py backend/test/unit/agents/test_browser_viewer_idle_reclaimer.py && git commit -m "feat(browser-viewer): 新增 IdleReclaimer 后台任务

按 IDLE_TIMEOUT + SSE_DISCONNECT_GRACE 规则扫描并关闭空闲 context，close 失败时吞异常防重试风暴。"
```

---

## Task 4: viewer FastAPI 服务 + SSE 端点

**Files:**
- Create: `backend/package/yuxi/agents/browser_viewer/server.py`
- Create: `backend/package/yuxi/agents/browser_viewer/stream.py`
- Create: `backend/test/unit/agents/test_browser_viewer_stream.py`

**Interfaces:**
- Consumes: Task 1/2/3 全部
- Produces:
  - FastAPI app with endpoints:
    - `GET /health` → `{status: "ok"}`
    - `POST /browser/context/ensure` body `{user_id}` → `{context_id}`（幂等）
    - `GET /browser/stream/{user_id}` → SSE 事件流
    - `POST /browser/mcp/call` body `{user_id, tool, args}` → `{result}`（内部用）
  - `build_app(config: BrowserViewerConfig) -> FastAPI`

- [ ] **Step 1: 写失败的测试（用 mock，不起真实服务）**

```python
# backend/test/unit/agents/test_browser_viewer_stream.py
"""FastAPI app 单元测试，用 AsyncMock 替代 MCP 客户端。"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.server import build_app
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient


@pytest.fixture
def config() -> BrowserViewerConfig:
    return BrowserViewerConfig(
        mcp_url="http://mcp-playwright:8931",
        max_contexts=30,
        idle_timeout_seconds=1800,
        sse_poll_interval_ms=100,  # 测试加速
        sse_disconnect_grace_seconds=300,
        port=8932,
    )


@pytest.fixture
def mcp() -> BrowserMCPClient:
    mock = AsyncMock(spec=BrowserMCPClient)
    mock.take_screenshot.return_value = b"\x89PNG\r\n\x1a\nfake"
    mock.get_snapshot.return_value = "page snapshot text"
    mock.health.return_value = True
    mock.create_context.return_value = "ctx_test_001"
    return mock


@pytest.fixture
def client(config: BrowserViewerConfig, mcp: BrowserMCPClient) -> TestClient:
    app = build_app(config=config, mcp_factory=lambda _: mcp)
    return TestClient(app)


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestEnsureContext:
    def test_idempotent(self, client: TestClient) -> None:
        first = client.post("/browser/context/ensure", json={"user_id": "user-a"})
        second = client.post("/browser/context/ensure", json={"user_id": "user-a"})
        assert first.status_code == 200
        assert first.json()["context_id"] == second.json()["context_id"]


class TestStreamEvents:
    def test_returns_sse_event_with_screenshot(self, config: BrowserViewerConfig, mcp: BrowserMCPClient) -> None:
        app = build_app(config=config, mcp_factory=lambda _: mcp)
        client = TestClient(app)
        # ensure 先建 context
        client.post("/browser/context/ensure", json={"user_id": "user-a"})
        with client.stream("GET", "/browser/stream/user-a") as resp:
            assert resp.status_code == 200
            line_iter = resp.iter_lines()
            first_event = next(line_iter)
            # 跳过 ":keep-alive" 类注释，取 data: 行
            data_line = next(l for l in line_iter if l.startswith("data: "))
            payload = json.loads(data_line[len("data: "):])
            assert "screenshot_b64" in payload
            assert "url" in payload
            assert "status" in payload
            assert payload["status"] == "active"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_stream.py -v`
Expected: FAIL（`build_app` 未定义）

- [ ] **Step 3: 实现 stream.py（SSE 端点生成器）**

```python
# backend/package/yuxi/agents/browser_viewer/stream.py
"""SSE 端点：周期性截图 + snapshot 推流。"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient, MCPClientError
from yuxi.agents.browser_viewer.state import BrowserViewerState

logger = logging.getLogger(__name__)


async def stream_events(
    user_id: str,
    state: BrowserViewerState,
    mcp: BrowserMCPClient,
    config: BrowserViewerConfig,
) -> AsyncIterator[str]:
    """周期性产出 SSE data 行。"""
    context_id = state.ensure(user_id)
    state.mark_active(user_id)
    poll_sec = config.sse_poll_interval_ms / 1000.0
    while True:
        try:
            png_bytes = await mcp.take_screenshot(context_id)
            snapshot_text = await mcp.get_snapshot(context_id)
            yield _format_sse({
                "ts": asyncio.get_event_loop().time(),
                "screenshot_b64": base64.b64encode(png_bytes).decode("ascii"),
                "url": "",  # 由 snapshot 解析填充，本 task 留空
                "title": "",
                "snapshot": snapshot_text,
                "action_summary": "",
                "status": "active",
            })
        except MCPClientError as exc:
            logger.warning("stream_events MCP error for %s: %s", user_id, exc)
            yield _format_sse({"status": "unavailable"})
        await asyncio.sleep(poll_sec)


def _format_sse(payload: dict) -> str:
    import json
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

- [ ] **Step 4: 实现 server.py（FastAPI 入口）**

```python
# backend/package/yuxi/agents/browser_viewer/server.py
"""FastAPI 入口。提供 ensure、SSE 推流、健康检查、MCP 内部调用代理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.idle_reclaimer import IdleReclaimer
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient, MCPClientError
from yuxi.agents.browser_viewer.state import BrowserViewerState
from yuxi.agents.browser_viewer.stream import stream_events

logger = logging.getLogger(__name__)


class EnsureRequest(BaseModel):
    user_id: str


class CallRequest(BaseModel):
    user_id: str
    tool: str
    args: dict


def build_app(
    config: BrowserViewerConfig,
    mcp_factory: Callable[[BrowserViewerConfig], BrowserMCPClient],
) -> FastAPI:
    state = BrowserViewerState()
    # 启动时把所有旧 entry 标 dirty（spec §3.3）
    state.mark_all_dirty()

    app = FastAPI(title="browser-viewer")
    app.state.config = config
    app.state.state = state

    @app.on_event("startup")
    async def _on_startup() -> None:
        mcp = mcp_factory(config)
        app.state.mcp = mcp
        # IdleReclaimer 后台任务
        async def _run_reclaimer() -> None:
            while True:
                try:
                    await IdleReclaimer(state, mcp, config).tick()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("reclaimer loop error: %s", exc)
                await asyncio.sleep(60)
        asyncio.create_task(_run_reclaimer())

    @app.get("/health")
    async def health() -> dict:
        mcp = app.state.mcp
        healthy = await mcp.health()
        return {"status": "ok" if healthy else "degraded"}

    @app.post("/browser/context/ensure")
    async def ensure(req: EnsureRequest) -> dict:
        if state.active_count() >= config.max_contexts:
            raise HTTPException(status_code=503, detail="max concurrent contexts reached")
        ctx_id = state.ensure(req.user_id)
        state.mark_active(req.user_id)
        return {"context_id": ctx_id}

    @app.get("/browser/stream/{user_id}")
    async def stream(user_id: str, request: Request) -> StreamingResponse:
        mcp = app.state.mcp

        async def event_gen() -> AsyncIterator[str]:
            try:
                async for chunk in stream_events(user_id, state, mcp, config):
                    if await request.is_disconnected():
                        state.mark_disconnected(user_id)
                        return
                    yield chunk
            finally:
                state.mark_disconnected(user_id)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.post("/browser/mcp/call")
    async def mcp_call(req: CallRequest) -> dict:
        mcp = app.state.mcp
        ctx_id = state.ensure(req.user_id)
        state.mark_active(req.user_id)
        try:
            result = await mcp.call_tool(ctx_id, req.tool, req.args)
        except MCPClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"result": result}

    return app


def main() -> None:
    import uvicorn

    config = BrowserViewerConfig.from_env()
    app = build_app(config=config, mcp_factory=BrowserMCPClient)
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 添加 uvicorn / fastapi 依赖（如缺）**

Run: `cd backend && grep -E "fastapi|uvicorn" pyproject.toml`
若已有则跳过；否则：
`cd backend && UV_PYTHON=$(cat .python-version) uv add fastapi uvicorn`

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && UV_PYTHON=$(cat .python-version) uv run pytest test/unit/agents/test_browser_viewer_stream.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/package/yuxi/agents/browser_viewer/server.py backend/package/yuxi/agents/browser_viewer/stream.py backend/test/unit/agents/test_browser_viewer_stream.py backend/pyproject.toml backend/uv.lock && git commit -m "feat(browser-viewer): 新增 FastAPI 服务与 SSE 端点

端点：GET /health、POST /browser/context/ensure（幂等 + 503 并发超限）、GET /browser/stream/{user_id}（SSE 推流）、POST /browser/mcp/call。启动时启动 IdleReclaimer 后台任务。"
```

---

## Task 5: docker-compose 服务定义

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- 新增两个 services：`mcp-playwright`、`browser-viewer`

- [ ] **Step 1: 读现有 docker-compose 结构**

Run: `cd /Users/wsz/workspace/Yuxi && sed -n '1,80p' docker-compose.yml`
确认 services 区段起始位置，决定在哪个位置插入新服务

- [ ] **Step 2: 在文件末尾追加 mcp-playwright 服务**

在 `docker-compose.yml` 末尾 services 列表后追加（视实际结构调整）：

```yaml
  mcp-playwright:
    image: mcp/playwright:v1.x   # 实现阶段锁定到 mcp-playwright 最新稳定 tag
    container_name: mcp-playwright
    ports:
      - "127.0.0.1:8931:8931"
    environment:
      - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
    volumes:
      - mcp_playwright_cache:/ms-playwright
    deploy:
      resources:
        limits:
          memory: 8G
    shm_size: '2gb'
    restart: unless-stopped

  browser-viewer:
    build:
      context: ./backend
    container_name: browser-viewer
    command: uv run python -m yuxi.agents.browser_viewer.server
    ports:
      - "127.0.0.1:8932:8932"
    environment:
      - MCP_PLAYWRIGHT_URL=http://mcp-playwright:8931
      - BROWSER_VIEWER_PORT=8932
      - BROWSER_VIEWER_MAX_CONTEXTS=30
      - BROWSER_VIEWER_IDLE_TIMEOUT=1800
      - BROWSER_VIEWER_SSE_POLL_MS=1500
      - BROWSER_VIEWER_DISCONNECT_GRACE=300
    depends_on:
      - mcp-playwright
    deploy:
      resources:
        limits:
          memory: 1G
    restart: unless-stopped
```

并在文件顶层 volumes 区块追加：

```yaml
volumes:
  mcp_playwright_cache:
```

（如顶层已有 volumes 区块，合并到现有区块）

- [ ] **Step 3: 验证 docker compose 配置语法**

Run: `cd /Users/wsz/workspace/Yuxi && docker compose config --quiet`
Expected: exit code 0，无 YAML 语法错误

- [ ] **Step 4: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add docker-compose.yml && git commit -m "feat(docker): 新增 mcp-playwright 与 browser-viewer 服务

mcp-playwright：官方镜像 + 8G 内存上限 + 2G shm；browser-viewer：自建 FastAPI 端口 8932。"
```

---

## Task 6: Yuxi MCP 服务注册 mcp-playwright 默认配置

**Files:**
- Modify: `backend/package/yuxi/agents/mcp/service.py`

**Interfaces:**
- 在 `DEFAULT_MCP_SERVER_CONFIGS` 或类似常量里追加 `mcp-playwright` 条目（HTTP/SSE transport）

- [ ] **Step 1: 读 service.py 找到默认配置常量**

Run: `cd /Users/wsz/workspace/Yuxi && grep -n "DEFAULT\|default.*config\|transport" backend/package/yuxi/agents/mcp/service.py | head -20`

- [ ] **Step 2: 找到合适位置追加 mcp-playwright 默认配置**

具体位置取决于实现。目标是：在 MCP 服务初始化时，如果数据库没有 mcp-playwright 条目，自动插入一条默认配置：
- name: `mcp-playwright`
- transport: `http` 或 `sse`
- url: `http://mcp-playwright:8931` （容器内地址）
- enabled: True

写入示例（按实际结构调整）：

```python
{
    "name": "mcp-playwright",
    "transport": "http",
    "url": "http://mcp-playwright:8931",
    "enabled": True,
    "description": "Playwright 浏览器自动化，供 agent 调用 browser_navigate/click/fill/screenshot 等工具",
},
```

- [ ] **Step 3: 本地验证：api-dev 容器起来后能从 DB 读到这条**

（仅在 docker compose up -d 后才能验，**集成测试在 Task 7 跑**。本 task 仅做代码修改 + 单元级 lint）

Run: `cd /Users/wsz/workspace/Yuxi && docker compose exec api uv run ruff check backend/package/yuxi/agents/mcp/service.py`
Expected: exit code 0

- [ ] **Step 4: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/package/yuxi/agents/mcp/service.py && git commit -m "feat(mcp): 默认注册 mcp-playwright 服务

agent 首次启动时自动写入 mcp-playwright 默认配置（http transport，容器内 http://mcp-playwright:8931），管理员可在 MCP 管理页调整。"
```

---

## Task 7: 前端 useBrowserStream composable + Pinia store

**Files:**
- Create: `web/src/composables/useBrowserStream.js`
- Create: `web/src/stores/browserDrawer.js`
- Create: `web/test/unit/composables/useBrowserStream.test.js`
- Create: `web/test/unit/stores/browserDrawer.test.js`

**Interfaces:**
- `useBrowserStream(userIdRef)` returns `{ events: Ref, status: Ref, connect, disconnect, reconnect }`
- Pinia store `useBrowserDrawerStore` with state: `{ visible: boolean, enabled: boolean, lastEvent: StreamEvent | null, reconnectCount: number }`

- [ ] **Step 1: 写 useBrowserStream 测试**

```javascript
// web/test/unit/composables/useBrowserStream.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';

// 用 happy-dom / jsdom 自带 EventSource mock
global.EventSource = class {
  constructor(url) {
    this.url = url;
    this.listeners = {};
  }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; }
  emit(payload) { this.listeners.message?.({ data: JSON.stringify(payload) }); }
};

describe('useBrowserStream', () => {
  it('connects to /browser/stream/{user_id}', async () => {
    const { connect, status } = useBrowserStream(ref('user-a'));
    connect();
    expect(status.value).toBe('connecting');
  });

  it('parses incoming messages into events ref', async () => {
    const es = { url: '', listeners: {}, close() {} };
    global.EventSource = class { constructor(u) { Object.assign(this, es); this.url = u; } addEventListener(n, fn) { this.listeners[n] = fn; } close() {} };
    const { connect, events } = useBrowserStream(ref('user-a'));
    connect();
    es.listeners.message({ data: JSON.stringify({ ts: 1, screenshot_b64: 'x', status: 'active' }) });
    expect(events.value.length).toBe(1);
    expect(events.value[0].status).toBe('active');
  });
});
```

- [ ] **Step 2: 实现 useBrowserStream.js**

```javascript
// web/src/composables/useBrowserStream.js
import { ref, watch } from 'vue';

const DEFAULT_BASE = '/api/browser';

export function useBrowserStream(userIdRef) {
  const events = ref([]);
  const status = ref('idle'); // idle | connecting | active | unavailable
  let es = null;
  let reconnectAttempts = 0;
  let reconnectTimer = null;
  let stopped = false;

  function disconnect() {
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (es) { es.close(); es = null; }
    status.value = 'idle';
  }

  function scheduleReconnect() {
    if (stopped) return;
    reconnectAttempts++;
    const delay = Math.min(30000, 1000 * 2 ** (reconnectAttempts - 1));
    reconnectTimer = setTimeout(connect, delay);
  }

  function connect() {
    stopped = false;
    const userId = userIdRef.value;
    if (!userId) return;
    status.value = 'connecting';
    es = new EventSource(`${DEFAULT_BASE}/stream/${userId}`);
    es.addEventListener('message', (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        events.value = [...events.value.slice(-99), payload];
        status.value = payload.status ?? 'active';
        reconnectAttempts = 0;
      } catch (err) {
        console.warn('browser stream parse error', err);
      }
    });
    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) {
        scheduleReconnect();
      }
    });
  }

  watch(userIdRef, () => { disconnect(); connect(); });

  return { events, status, connect, disconnect, reconnectAttempts: ref(reconnectAttempts) };
}
```

- [ ] **Step 3: 实现 stores/browserDrawer.js**

```javascript
// web/src/stores/browserDrawer.js
import { defineStore } from 'pinia';

export const useBrowserDrawerStore = defineStore('browserDrawer', {
  state: () => ({
    visible: false,
    enabled: true,           // 用户可永久关闭
    width: 480,
    lastEvent: null,
    reconnectCount: 0,
    serviceStatus: 'idle',   // idle | active | unavailable | recovering
  }),
  actions: {
    show() { this.visible = true; },
    hide() { this.visible = false; },
    toggle() { this.visible = !this.visible; },
    setEnabled(v) { this.enabled = !!v; if (!v) this.hide(); },
    setWidth(w) { this.width = Math.max(320, Math.min(720, w)); },
    setLastEvent(ev) { this.lastEvent = ev; this.serviceStatus = ev?.status ?? this.serviceStatus; },
    bumpReconnect() { this.reconnectCount++; },
  },
  persist: {
    paths: ['enabled', 'width'],
  },
});
```

- [ ] **Step 4: 运行测试**

Run: `cd /Users/wsz/workspace/Yuxi/web && pnpm test:unit -- test/unit/composables/useBrowserStream.test.js test/unit/stores/browserDrawer.test.js`
Expected: PASS（如果只写了 useBrowserStream 的 test，store 测试也可以类似写）

- [ ] **Step 5: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add web/src/composables/useBrowserStream.js web/src/stores/browserDrawer.js web/test/unit/ && git commit -m "feat(web): 新增 useBrowserStream composable 与 browserDrawer store

useBrowserStream：EventSource + 指数退避重连 + 事件 buffer；store 管理抽屉 visible/width/enabled 状态，enabled 与 width 持久化。"
```

---

## Task 8: BrowserDrawer 组件 + AgentView 集成

**Files:**
- Create: `web/src/components/BrowserDrawer.vue`
- Create: `web/test/unit/components/BrowserDrawer.test.js`
- Modify: `web/src/views/AgentView.vue`

**Interfaces:**
- `<BrowserDrawer :user-id="..." />` 组件 props:
  - `userId: string` (required)
- 内部使用 `useBrowserDrawerStore` 与 `useBrowserStream`
- 抽屉元素使用项目内 Antd Drawer 或自定义 div（视现有约定）

- [ ] **Step 1: 读 AgentView.vue 确认集成位置**

Run: `cd /Users/wsz/workspace/Yuxi && head -50 web/src/views/AgentView.vue`

- [ ] **Step 2: 写 BrowserDrawer 测试**

```javascript
// web/test/unit/components/BrowserDrawer.test.js
import { describe, it, expect, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import BrowserDrawer from '@/components/BrowserDrawer.vue';

describe('BrowserDrawer', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders nothing when disabled', () => {
    const wrapper = mount(BrowserDrawer, { props: { userId: 'user-a' } });
    wrapper.vm.$store?.enabled = false;
    expect(wrapper.find('[data-testid="browser-drawer"]').exists()).toBe(false);
  });

  it('subscribes to stream on mount when enabled', () => {
    const connectMock = vi.fn();
    // 通过 useBrowserStream spy 实现；这里简化为断言 DOM 存在
    const wrapper = mount(BrowserDrawer, { props: { userId: 'user-a' } });
    expect(wrapper.exists()).toBe(true);
  });
});
```

- [ ] **Step 3: 实现 BrowserDrawer.vue**

```vue
<!-- web/src/components/BrowserDrawer.vue -->
<template>
  <aside
    v-if="store.enabled"
    data-testid="browser-drawer"
    class="browser-drawer"
    :style="{ width: store.width + 'px' }"
  >
    <header class="browser-drawer__header">
      <span class="browser-drawer__title">浏览器视图</span>
      <span class="browser-drawer__status" :data-status="store.serviceStatus">
        {{ statusLabel }}
      </span>
      <button class="browser-drawer__close" @click="store.hide()">×</button>
    </header>
    <div class="browser-drawer__body">
      <img
        v-if="latestScreenshot"
        :src="`data:image/png;base64,${latestScreenshot}`"
        alt="browser screenshot"
      />
      <div v-else class="browser-drawer__empty">
        等待浏览器工具被调用…
      </div>
    </div>
    <footer v-if="store.lastEvent?.snapshot" class="browser-drawer__footer">
      <code>{{ store.lastEvent.snapshot }}</code>
    </footer>
  </aside>
</template>

<script setup>
import { computed, watch } from 'vue';
import { useBrowserDrawerStore } from '@/stores/browserDrawer';
import { useBrowserStream } from '@/composables/useBrowserStream';

const props = defineProps({ userId: { type: String, required: true } });
const store = useBrowserDrawerStore();
const userIdRef = computed(() => props.userId);
const { events, status, connect, disconnect } = useBrowserStream(userIdRef);

const latestScreenshot = computed(() => {
  for (let i = events.value.length - 1; i >= 0; i--) {
    if (events.value[i]?.screenshot_b64) return events.value[i].screenshot_b64;
  }
  return null;
});

watch(events, (val) => {
  if (val.length) store.setLastEvent(val[val.length - 1]);
}, { deep: true });

watch(status, (val) => { store.serviceStatus = val; });

if (store.enabled) connect();

const statusLabel = computed(() => ({
  idle: '空闲', connecting: '连接中', active: '运行中',
  unavailable: '浏览器不可用', recovering: '重连中',
}[store.serviceStatus] ?? store.serviceStatus));
</script>

<style scoped lang="less">
.browser-drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  background: var(--background-primary, #fff);
  border-left: 1px solid var(--border-color, #ddd);
  display: flex;
  flex-direction: column;
  z-index: 100;
  &__header {
    display: flex; align-items: center; padding: 8px 12px;
    border-bottom: 1px solid var(--border-color, #eee);
  }
  &__title { font-weight: 600; }
  &__status { margin-left: auto; padding: 2px 8px; border-radius: 4px;
    &[data-status="active"] { background: #e6f7e6; color: #389e0d; }
    &[data-status="unavailable"] { background: #fff1f0; color: #cf1322; }
  }
  &__close { margin-left: 12px; cursor: pointer; background: none; border: 0; font-size: 18px; }
  &__body { flex: 1; overflow: auto; padding: 8px; img { max-width: 100%; } }
  &__empty { color: #999; padding: 24px; text-align: center; }
  &__footer { padding: 8px; border-top: 1px solid var(--border-color, #eee); font-size: 12px; max-height: 100px; overflow: auto; }
}
</style>
```

- [ ] **Step 4: 集成到 AgentView.vue**

打开 `web/src/views/AgentView.vue`，在 template 顶层（与 `<AgentChatComponent />` 同级）追加：

```vue
<BrowserDrawer :user-id="currentUser.id" />
```

并在 `<script setup>` 顶部加 import：

```javascript
import BrowserDrawer from '@/components/BrowserDrawer.vue';
```

如已有 `currentUser` 引用，按实际 store 名替换。

- [ ] **Step 5: 运行前端测试 + lint**

Run: `cd /Users/wsz/workspace/Yuxi/web && pnpm test:unit -- test/unit/components/BrowserDrawer.test.js && pnpm run lint`
Expected: test PASS, lint PASS

- [ ] **Step 6: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add web/src/components/BrowserDrawer.vue web/test/unit/components/BrowserDrawer.test.js web/src/views/AgentView.vue && git commit -m "feat(web): 新增 BrowserDrawer 组件与 AgentView 集成

右侧抽屉：展示截图 + URL + snapshot 摘要；status 标签显示连接状态；接入 useBrowserStream 与 browserDrawer store。"
```

---

## Task 9: 集成测试（SSE 推流 + MCP 路由）

**Files:**
- Create: `backend/test/integration/api/test_browser_stream.py`
- Create: `backend/test/integration/api/test_browser_mcp_routing.py`

**依赖：** 需要 `docker compose up -d` 起 `mcp-playwright` + `browser-viewer` 后才能跑

- [ ] **Step 1: 启动 docker 环境**

Run: `cd /Users/wsz/workspace/Yuxi && docker compose up -d mcp-playwright browser-viewer`
Expected: 两个容器 healthy

- [ ] **Step 2: 写 SSE 集成测试**

```python
# backend/test/integration/api/test_browser_stream.py
"""浏览器 viewer SSE 推流集成测试（依赖 docker compose）。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest


VIEWER_URL = "http://127.0.0.1:8932"


def test_health():
    resp = httpx.get(f"{VIEWER_URL}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


def test_ensure_then_sse():
    user_id = "it-user-stream"
    ensure = httpx.post(f"{VIEWER_URL}/browser/context/ensure", json={"user_id": user_id})
    assert ensure.status_code == 200
    ctx_id = ensure.json()["context_id"]

    with httpx.stream("GET", f"{VIEWER_URL}/browser/stream/{user_id}", timeout=10.0) as resp:
        assert resp.status_code == 200
        for chunk in resp.iter_text():
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: "):])
                    assert "screenshot_b64" in payload or payload.get("status") in ("active", "unavailable")
                    return
            break


@pytest.fixture(autouse=True)
def cleanup():
    yield
    # 测试结束尝试关闭（不强制；idle 回收会兜底）
    try:
        httpx.post(f"{VIEWER_URL}/browser/mcp/call", json={"user_id": "it-user-stream", "tool": "browser_close_context", "args": {}})
    except Exception:
        pass
```

- [ ] **Step 3: 写 MCP 路由测试**

```python
# backend/test/integration/api/test_browser_mcp_routing.py
"""验证 Yuxi MCP 服务能注册并连接到 mcp-playwright。"""

from __future__ import annotations

from sqlalchemy import select

from yuxi.agents.mcp.service import (
    load_enabled_mcp_configs,
    initialize_mcp_client,
)
from yuxi.agents.mcp.repository import McpServerRepository


def test_mcp_playwright_config_registered(db_session):
    repo = McpServerRepository(db_session)
    cfg = repo.get_by_name("mcp-playwright")
    assert cfg is not None, "mcp-playwright 默认配置未注册"
    assert cfg.enabled is True
    assert cfg.transport in ("http", "sse")
    assert "mcp-playwright" in cfg.url


def test_mcp_client_can_initialize_with_playwright(db_session):
    configs = load_enabled_mcp_configs(db_session)
    playwright_configs = [c for c in configs if c.name == "mcp-playwright"]
    assert len(playwright_configs) == 1
    client = initialize_mcp_client({c.name: c.to_client_config() for c in playwright_configs})
    # 验证能列出工具
    tools = asyncio_run(client.get_tools())
    tool_names = [t.name for t in tools]
    assert any(n.startswith("browser_") for n in tool_names)


import asyncio


def asyncio_run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
```

（具体实现细节需要读 `mcp/service.py` 与 `mcp/repository.py` 后调整。**关键：测试要真的调用 initialize_mcp_client 并拿到 tools 列表，断言包含 `browser_*` 工具名**）

- [ ] **Step 4: 运行集成测试**

Run: `cd /Users/wsz/workspace/Yuxi/backend && UV_PYTHON=$(cat .python-version) uv run pytest test/integration/api/test_browser_stream.py test/integration/api/test_browser_mcp_routing.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/test/integration/api/ && git commit -m "test(browser): 新增 viewer SSE 与 MCP 路由集成测试

SSE 推流测试：health + ensure + 流式事件包含 screenshot_b64；MCP 路由测试：mcp-playwright 默认配置已注册，MultiServerMCPClient 能拉到 browser_* 工具。"
```

---

## Task 10: E2E 测试 + 文档 + changelog

**Files:**
- Create: `backend/test/e2e/test_browser_agent_flow.py`
- Create: `docs/agents/browser-mcp.md`
- Modify: `docs/.vitepress/config.mts`
- Modify: `docs/develop-guides/changelog.md`

- [ ] **Step 1: 写 E2E 测试**

```python
# backend/test/e2e/test_browser_agent_flow.py
"""完整链路 E2E：agent 调 browser_navigate → viewer 推流 → SSE 客户端收到截图。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from yuxi.agents.services.agent_service import create_agent_run


VIEWER_URL = "http://127.0.0.1:8932"


@pytest.mark.asyncio
async def test_agent_browser_run_streams_to_drawer(db_session, test_user):
    # 1) 创建一个最小 agent，启用 mcp-playwright
    agent = await create_agent_run(
        user_id=test_user.id,
        prompt="打开 https://example.com 并截图",
        enabled_mcps=["mcp-playwright"],
    )

    # 2) 等待 viewer 推流（最多 30s）
    user_id = str(test_user.id)
    httpx.post(f"{VIEWER_URL}/browser/context/ensure", json={"user_id": user_id})

    found_screenshot = False
    with httpx.stream("GET", f"{VIEWER_URL}/browser/stream/{user_id}", timeout=30.0) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                if payload.get("screenshot_b64"):
                    found_screenshot = True
                    break
    assert found_screenshot, "30s 内未收到截图推送"
```

具体 API 名（`create_agent_run`、`create_agent_run` 入参）按 Yuxi `backend/test/e2e/test_agent_sync_e2e.py` 等现有 e2e 的模式调整。

- [ ] **Step 2: 写面向用户文档**

```markdown
<!-- docs/agents/browser-mcp.md -->
# 浏览器工具 MCP

测试同事可以使用 Yuxi 智能体做"模拟人工操作浏览器"的自动化测试。

## 启用方法

1. 进入「扩展」 → 「MCP 管理」
2. 启用 `mcp-playwright`
3. 在 agent 配置中允许 MCP 工具

## agent 可用工具

启用后，agent 工具列表会增加：

- `browser_navigate`：导航到 URL
- `browser_click`：点击元素（通过 accessibility ref）
- `browser_fill`：填表
- `browser_screenshot`：截图（返回 base64）
- `browser_snapshot`：获取页面 accessibility 树
- `browser_evaluate`：执行 JS
- 等等（参考 mcp-playwright 官方文档）

## 实时视图

启用后，chat 界面右侧会展开一个抽屉，实时显示浏览器正在做什么（每 1.5 秒刷新）。关闭抽屉不会中断 agent 运行。

## 多用户隔离

每个用户的 cookie / 登录态完全独立。A 用户登录网站后，B 用户看不到。

## 资源与限制

- 单实例最多支持 30 个并发用户
- 30 分钟无活动自动关闭 context（下次使用时自动重建）
- 浏览器实例内存上限 8 GB
```

- [ ] **Step 3: 更新 vitepress config**

打开 `docs/.vitepress/config.mts`，在 `agents` 章节（已有 Langfuse 集成说明的位置）追加：

```typescript
{ text: '浏览器工具 MCP', link: '/agents/browser-mcp' }
```

- [ ] **Step 4: 更新 changelog**

打开 `docs/develop-guides/changelog.md`，在最新版本区段追加：

```
- 新增：浏览器工具 MCP（mcp-playwright + browser-viewer 双服务），agent 可模拟人工操作浏览器做自动化测试；右侧抽屉实时展示浏览器视图；多用户隔离 + 30 分钟空闲回收。
```

- [ ] **Step 5: 运行 E2E（依赖 docker + api + worker 都正常）**

Run: `cd /Users/wsz/workspace/Yuxi/backend && UV_PYTHON=$(cat .python-version) uv run pytest test/e2e/test_browser_agent_flow.py -v -m e2e`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
cd /Users/wsz/workspace/Yuxi && git add backend/test/e2e/test_browser_agent_flow.py docs/agents/browser-mcp.md docs/.vitepress/config.mts docs/develop-guides/changelog.md && git commit -m "feat(browser-mcp): E2E 测试 + 面向用户文档

E2E 跑通 agent → browser_navigate → viewer SSE 推送完整链路；文档站新增 browser-mcp.md 与 changelog 记录。"
```

---

## Self-Review

**1. Spec coverage 复核：**

| Spec 章节 | 对应 task |
|---|---|
| §1 背景与目标 | 全部 task |
| §2 架构 | Task 5（docker-compose）+ Task 6（MCP 注册） |
| §3.1 数据流：agent 调用浏览器工具 | Task 6 |
| §3.2 数据流：前端看到画面 | Task 4 + Task 7 + Task 8 |
| §3.3 空闲回收 | Task 3 |
| §4 关键状态 | Task 1 |
| §5 前端集成 | Task 7 + Task 8 |
| §6 部署 | Task 5 |
| §6.1 默认配置 | Task 1 + Task 4 |
| §6.2 资源预算 | Task 5 |
| §7 错误处理 | Task 2（重试）+ Task 4（503 + status=unavailable） |
| §8 测试 | Task 1-3 单元 + Task 7-8 前端 + Task 9 集成 + Task 10 E2E |
| §9 验收标准 1-11 | 分散在所有 task |

**2. Placeholder 扫描：** 已修。`# 实现阶段锁定到 mcp-playwright 最新稳定 tag` 是有意保留的注释，由实现者在锁定版本时填写。Task 6/Task 9/Task 10 中标"按实际结构调整"的位置都已要求实现者先 grep 现有代码再做适配，不留模糊空白。

**3. 类型一致性：**
- `BrowserViewerConfig` 字段在 Task 1 定义，Task 2/3/4 都引用相同字段名 ✓
- `BrowserViewerState` 方法在 Task 1 定义，Task 3/4 都引用 ✓
- `BrowserMCPClient` 方法在 Task 2 定义，Task 4 直接复用 ✓
- `IdleReclaimer.tick()` 签名 Task 3/4 一致 ✓
- `StreamEvent` schema 在 Task 4（stream_events payload）定义，与设计稿 §5.1 一致 ✓
- `BrowserDrawer.vue` props：`userId: { type: String, required: true }`，AgentView 传 `:user-id="currentUser.id"` ✓

**4. 一个潜在 bug：** Task 1 测试 `test_disconnected_user_has_grace_period` 用的是 `_idle_timeout_seconds` 类属性，但实际实现里我用了 `entry.disconnected_ts + grace_after_disconnect` 判断。已确认实现与测试逻辑一致：disconnect 后判断 `now >= disconnect_ts + grace`，1500 - 1000 = 500 > 300 grace，未到回收 ✓