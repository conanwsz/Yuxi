"""端到端测试：浏览器工具 MCP 完整链路。

依赖：
- docker compose up -d（包含 mcp-playwright + browser-viewer + api + worker + postgres）
- Yuxi 默认 mcp-playwright 配置已注册（Task 6 完成）

测试流程：
1. 创建启用 mcp-playwright MCP 的最小 agent
2. chat 触发 navigate → 验证 viewer SSE 推流截图

如果全栈未启动，自动 skip。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

API_URL = "http://127.0.0.1:5050"
VIEWER_URL = "http://127.0.0.1:8932"

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


def _stack_reachable() -> bool:
    try:
        api_resp = httpx.get(f"{API_URL}/api/health", timeout=2.0)
        viewer_resp = httpx.get(f"{VIEWER_URL}/health", timeout=2.0)
        return api_resp.status_code in (200, 404) and viewer_resp.status_code == 200
    except (httpx.HTTPError, httpx.RequestError):
        return False


@pytest.mark.skipif(
    not _stack_reachable(),
    reason="完整 stack 未启动（需 docker compose up -d 含 mcp-playwright + browser-viewer + api）",
)
async def test_browser_mcp_visible_in_agent_tool_list():
    """验证 mcp-playwright MCP 启用后，agent 工具列表出现 browser_* 工具。"""
    pytest.skip("此测试需要完整 stack + 测试用户；待 Y2 阶段在真实环境跑通后启用")
    # 占位实现（不执行）：
    # 1. 登录获取 auth headers
    # 2. 创建 agent，mcps=["mcp-playwright"]
    # 3. 调 /api/agent/{slug}/tools
    # 4. 断言 tool_names 中包含 browser_navigate 等