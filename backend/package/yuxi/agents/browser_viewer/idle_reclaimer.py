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
        # 先记录即将被回收的 (user_id, context_id) 配对，再调 evict（避免 mutating dict）
        evicted_snapshot: list[tuple[str, str]] = []
        for user_id in list(self._state._entries.keys()):
            entry = self._state._entries[user_id]
            if entry.dirty:
                continue
            if entry.disconnected_ts is not None:
                cutoff = entry.disconnected_ts + self._config.sse_disconnect_grace_seconds
            else:
                cutoff = entry.last_active_ts + self._state._idle_timeout_seconds
            if now_ts >= cutoff:
                evicted_snapshot.append((user_id, entry.context_id))
        evicted_ids = self._state.evict_idle(
            now_ts=now_ts,
            grace_after_disconnect=self._config.sse_disconnect_grace_seconds,
        )
        for user_id, ctx_id in evicted_snapshot:
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