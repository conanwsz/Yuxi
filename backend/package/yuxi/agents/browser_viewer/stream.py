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

SCREENSHOT_EVAL_CODE = 'async () => { return await page.screenshot({encoding: "base64", type: "png"}); }'


def _format_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_first_text(result: dict) -> str:
    """从 MCP tools/call result.content 里取第一个 text 类型片段。"""
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")
    return ""


def _extract_page_info(text: str) -> tuple[str, str]:
    """从 browser_snapshot 的文本中提取 Page URL 和 Page Title。"""
    url = ""
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- Page URL:"):
            url = line[len("- Page URL:") :].strip()
        elif line.startswith("- Page Title:"):
            title = line[len("- Page Title:") :].strip()
    return url, title


async def stream_events(
    user_id: str,
    state: BrowserViewerState,
    mcp: BrowserMCPClient,
    config: BrowserViewerConfig,
) -> AsyncIterator[str]:
    """周期性产出 SSE data 行。"""
    poll_sec = config.sse_poll_interval_ms / 1000.0
    consecutive_errors = 0
    last_screenshot_b64 = ""
    last_snapshot_text = ""
    last_url = ""
    last_title = ""

    while True:
        try:
            # 若浏览器未启动或已被 IdleReclaimer 回收，推流保持 idle 状态，不主动启动浏览器
            if state.active_count() == 0 or (user_id in state._entries and state.is_dirty(user_id)):
                yield _format_sse(
                    {
                        "ts": asyncio.get_event_loop().time(),
                        "screenshot_b64": "",
                        "url": "",
                        "title": "",
                        "snapshot": "",
                        "action_summary": "",
                        "status": "idle",
                    }
                )
                await asyncio.sleep(poll_sec)
                continue

            if user_id not in state._entries:
                state.ensure(user_id)
                state.mark_active(user_id)

            if mcp.session_id is None:
                sid = await mcp.initialize()
                state.set_mcp_session_id(user_id, sid)

            screenshot_bytes = await mcp.take_screenshot_bytes()
            if screenshot_bytes is not None:
                last_screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")

            snapshot_result = await mcp.call_tool("browser_snapshot", {})
            snapshot_text = _extract_first_text(snapshot_result).strip()
            if snapshot_text:
                last_snapshot_text = snapshot_text
                url, title = _extract_page_info(snapshot_text)
                if url:
                    last_url = url
                if title:
                    last_title = title

            consecutive_errors = 0
            # 注意：推流仅为观察者视角，绝不能在此调用 state.mark_active(user_id)！
            # mark_active 仅在 Agent 实际调用 MCP 工具时触发，这样无操作 30 分钟后才能被正确回收。
            yield _format_sse(
                {
                    "ts": asyncio.get_event_loop().time(),
                    "screenshot_b64": last_screenshot_b64,
                    "url": last_url,
                    "title": last_title,
                    "snapshot": last_snapshot_text,
                    "action_summary": "",
                    "status": "active",
                }
            )
        except MCPClientError as exc:
            consecutive_errors += 1
            logger.warning("stream_events MCP error for %s (consecutive %d): %s", user_id, consecutive_errors, exc)
            if "Session not found" in str(exc):
                mcp._session_id = None

            if consecutive_errors >= 3:
                state.mark_all_dirty()
                state.ensure(user_id)
                yield _format_sse({"status": "unavailable"})
            else:
                # 瞬时错误（如页面正在跳转或并发执行），保持 active 并复用上一帧
                yield _format_sse(
                    {
                        "ts": asyncio.get_event_loop().time(),
                        "screenshot_b64": last_screenshot_b64,
                        "url": last_url,
                        "title": last_title,
                        "snapshot": last_snapshot_text,
                        "action_summary": "",
                        "status": "active",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            consecutive_errors += 1
            logger.exception(
                "stream_events unexpected error for %s (consecutive %d): %s", user_id, consecutive_errors, exc
            )
            if consecutive_errors >= 3:
                mcp._session_id = None
                yield _format_sse({"status": "unavailable"})
        await asyncio.sleep(poll_sec)
