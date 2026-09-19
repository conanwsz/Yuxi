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
        # state 已被清理
        assert state.active_count() == 0
        # mcp.close_context 已被调用一次（即使 context_id 是新的）
        assert mcp.close_context.await_count == 1

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