"""SSE 端点：周期性截图 + snapshot 推流。

调用 mcp-playwright 的 browser_evaluate 拿 base64 截图、browser_snapshot 拿无障碍文本。
session 失效时把 state 标 dirty，下次 ensure 会重建。
"""

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

SCREENSHOT_EVAL_CODE = (
    'async () => { return await page.screenshot({encoding: "base64", type: "png"}); }'
)


def _format_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_first_text(result: dict) -> str:
    """从 MCP tools/call result.content 里取第一个 text 类型片段。"""
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")
    return ""


async def stream_events(
    user_id: str,
    state: BrowserViewerState,
    mcp: BrowserMCPClient,
    config: BrowserViewerConfig,
) -> AsyncIterator[str]:
    """周期性产出 SSE data 行。"""
    state.ensure(user_id)
    state.mark_active(user_id)
    poll_sec = config.sse_poll_interval_ms / 1000.0
    while True:
        try:
            screenshot_bytes = await mcp.take_screenshot_bytes()
            screenshot_b64 = (
                base64.b64encode(screenshot_bytes).decode("ascii")
                if screenshot_bytes is not None else ""
            )
            snapshot_result = await mcp.call_tool("browser_snapshot", {})
            snapshot_text = _extract_first_text(snapshot_result).strip()
            yield _format_sse({
                "ts": asyncio.get_event_loop().time(),
                "screenshot_b64": screenshot_b64,
                "url": "",
                "title": "",
                "snapshot": snapshot_text,
                "action_summary": "",
                "status": "active",
            })
        except MCPClientError as exc:
            logger.warning("stream_events MCP error for %s: %s", user_id, exc)
            state.mark_all_dirty()
            state.ensure(user_id)
            yield _format_sse({"status": "unavailable"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream_events unexpected error for %s: %s", user_id, exc)
            yield _format_sse({"status": "unavailable"})
        await asyncio.sleep(poll_sec)
