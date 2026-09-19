"""SSE 端点：周期性截图 + snapshot 推流。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient, MCPClientError
from yuxi.agents.browser_viewer.state import BrowserViewerState

logger = logging.getLogger(__name__)


def _format_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
                "url": "",
                "title": "",
                "snapshot": snapshot_text,
                "action_summary": "",
                "status": "active",
            })
        except MCPClientError as exc:
            logger.warning("stream_events MCP error for %s: %s", user_id, exc)
            yield _format_sse({"status": "unavailable"})
        await asyncio.sleep(poll_sec)