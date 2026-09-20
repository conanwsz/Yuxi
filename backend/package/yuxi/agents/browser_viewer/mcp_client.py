"""MCP 客户端，连接 MS Playwright MCP 服务（官方 mcp/playwright 镜像）。

协议细节（已实测）：
- 端点：`POST /mcp`（streamable HTTP transport）
- 请求体：JSON-RPC 2.0
- 响应：text/event-stream（每行 `event: message\\ndata: {...}\\n\\n`）
- 会话管理：响应 Header `mcp-session-id`，后续请求必须带上

截图机制：browser_take_screenshot 工具只返回文件路径文本；
所以 take_screenshot 走两步：调 browser_take_screenshot 拿到文件名 → 从共享 output-dir 读 PNG 字节。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from yuxi.agents.browser_viewer.config import BrowserViewerConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {502, 503, 504}

CLIENT_INFO = {"name": "yuxi-browser-viewer", "version": "0.7.1"}
PROTOCOL_VERSION = "2024-11-05"


class MCPClientError(RuntimeError):
    """调用 mcp-playwright 失败（已重试 1 次或 JSON-RPC error）。"""


class BrowserMCPClient:
    """封装 MCP streamable-HTTP 客户端。

    httpx.AsyncClient 在第一次使用时惰性创建；用 `await aclose()` 显式关闭（FastAPI shutdown）。
    也支持 async context manager（用于测试）。

    每个 BrowserMCPClient 实例对应一个 user session。生命周期：
    1. `mcp = BrowserMCPClient(cfg)`
    2. `await mcp.initialize()` —— 调 initialize，存 session_id，启动 ping/pong 保活
    3. `await mcp.call_tool(name, args)` —— 多次
    4. `await mcp.close()` —— 浏览器侧 close_context（清 session_id，停 ping 保活）
    5. `await mcp.aclose()` —— httpx close（FastAPI shutdown 时调）
    """

    def __init__(self, config: BrowserViewerConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._request_id = 0
        self._ping_task: asyncio.Task | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.mcp_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        return self._client

    def _start_ping_listener(self) -> None:
        """启动后台 SSE 监听任务，响应 mcp-playwright 的 heartbeat ping。"""
        self._stop_ping_listener()
        if self._session_id is not None:
            self._ping_task = asyncio.create_task(self._listen_ping_events())

    def _stop_ping_listener(self) -> None:
        """停止后台 SSE 监听任务。"""
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

    async def _listen_ping_events(self) -> None:
        """监听 GET /mcp 的 SSE 事件，收到 ping 立即回复 pong 以保活 session。"""
        sid = self._session_id
        while self._session_id == sid and sid is not None:
            try:
                headers = {
                    "Accept": "text/event-stream",
                    "mcp-session-id": sid,
                }
                async with self.client.stream(
                    "GET", "/mcp", headers=headers, timeout=httpx.Timeout(60.0, connect=5.0)
                ) as resp:
                    if resp.status_code != 200 or not hasattr(resp, "aiter_lines"):
                        break
                    async for line in resp.aiter_lines():
                        if self._session_id != sid:
                            return
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if not data_str:
                                continue
                            try:
                                msg = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(msg, dict) and msg.get("method") == "ping":
                                ping_id = msg.get("id")
                                await self.client.post(
                                    "/mcp",
                                    json={"jsonrpc": "2.0", "id": ping_id, "result": {}},
                                    headers={
                                        "Content-Type": "application/json",
                                        "Accept": "application/json, text/event-stream",
                                        "mcp-session-id": sid,
                                    },
                                    timeout=httpx.Timeout(5.0),
                                )
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                if self._session_id != sid:
                    return
                logger.debug("MCP SSE ping listener reconnecting: %s", exc)
                await asyncio.sleep(0.5)

    async def aclose(self) -> None:
        self._stop_ping_listener()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def __aenter__(self) -> BrowserMCPClient:
        self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        return self._ensure_client()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _post_jsonrpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None | bool = True,
    ) -> dict[str, Any]:
        """发 JSON-RPC 请求，返回解析后的 result 字段。

        session_id 默认为 True 表示用 self._session_id；传 None 表示不发送；传字符串显式指定。
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id is True:
            if self._session_id is not None:
                headers["mcp-session-id"] = self._session_id
        elif session_id is not None:
            headers["mcp-session-id"] = session_id

        body = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
        }
        if params is not None:
            body["params"] = params

        resp = await self._post_with_retry("/mcp", body, headers=headers)
        if resp.status_code == 404 or "Session not found" in resp.text:
            self._session_id = None
            self._stop_ping_listener()
            raise MCPClientError("Session not found")
        return _parse_sse_response(resp.text)

    async def _post_with_retry(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self.client.post(path, json=json_body, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if resp.status_code not in _RETRYABLE_STATUS or attempt == 1:
                return resp
        raise MCPClientError(f"POST {path} failed after retry: {last_exc}")

    async def health(self) -> bool:
        """健康检查：能成功调 initialize（无需 session）即视为健康。"""
        try:
            self._request_id += 1
            resp = await self.client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": CLIENT_INFO,
                    },
                    "id": self._request_id,
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def initialize(self) -> str:
        """建立 MCP session。从 response header 拿 session_id，存入 self。

        返回 session_id 字符串。
        """
        self._request_id += 1
        body = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            "id": self._request_id,
        }
        resp = await self._post_with_retry(
            "/mcp",
            body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        if resp.status_code != 200:
            raise MCPClientError(f"initialize HTTP {resp.status_code}: {resp.text[:200]}")
        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            raise MCPClientError("initialize response missing mcp-session-id header")
        self._session_id = session_id
        self._start_ping_listener()
        parsed = _parse_sse_response(resp.text)
        if "error" in parsed:
            raise MCPClientError(f"initialize JSON-RPC error: {parsed['error']}")
        return session_id

    async def tools_list(self) -> list[dict[str, Any]]:
        """列举所有可用工具。返回 [{"name": ..., "description": ..., "inputSchema": ...}]"""
        result = await self._post_jsonrpc("tools/list", params={})
        if "error" in result:
            raise MCPClientError(f"tools/list failed: {result['error']}")
        payload = result.get("result", result)
        return payload.get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """通用 tools/call。返回 tools/call 的 result 字段（去掉 JSON-RPC 包裹）。

        result 结构：{"content": [{"type":"text","text":"..."}], "isError": bool}
        """
        rpc_result = await self._post_jsonrpc(
            "tools/call",
            params={"name": name, "arguments": arguments or {}},
        )
        if "error" in rpc_result:
            raise MCPClientError(f"tools/call {name} failed: {rpc_result['error']}")
        result = rpc_result.get("result", {})
        if result.get("isError"):
            error_text = _extract_text_content(result.get("content", []))
            raise MCPClientError(f"tools/call {name} returned error: {error_text}")
        return result

    async def close(self, session_id: str | None = None) -> None:
        """关闭浏览器 context。

        MS Playwright MCP 用 browser_close 工具关掉当前 session 的浏览器。
        失败不抛异常（外部 idle_reclaimer 会 catch）。
        """
        self._stop_ping_listener()
        sid = session_id or self._session_id
        if sid is None:
            return
        if self._session_id == sid:
            self._session_id = None
        try:
            await self._post_jsonrpc(
                "tools/call",
                params={"name": "browser_close", "arguments": {}},
                session_id=sid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser_close failed: %s", exc)

    async def take_screenshot_bytes(self) -> bytes | None:
        """调 browser_take_screenshot，从 response 的 image content 直接拿 base64 PNG。

        MS Playwright MCP 返回 content 包含 type='image' + base64 data + mimeType。
        返回 None 表示截图失败。
        """
        try:
            result = await self.call_tool("browser_take_screenshot", {"type": "png"})
        except MCPClientError as exc:
            logger.warning("browser_take_screenshot failed: %s", exc)
            return None
        for item in result.get("content", []):
            if item.get("type") == "image" and item.get("data"):
                import base64

                return base64.b64decode(item["data"])
        logger.warning("no image content in screenshot response")
        return None


def _parse_sse_response(text: str) -> dict[str, Any]:
    """解析 text/event-stream 响应，提取最后一个 data 行的 JSON。"""
    last_data: str | None = None
    for line in text.splitlines():
        if line.startswith("data: "):
            last_data = line[len("data: ") :]
        elif line.startswith("data:"):
            last_data = line[len("data:") :].lstrip()
    if last_data is None:
        raise MCPClientError(f"no data: line in SSE response: {text[:200]}")
    try:
        parsed = json.loads(last_data)
    except json.JSONDecodeError as exc:
        raise MCPClientError(f"invalid JSON in SSE data: {last_data[:200]}") from exc
    return parsed


def _extract_text_content(content: list[dict[str, Any]]) -> str:
    """从 MCP result.content 提取所有 text 类型片段拼接。"""
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


def _extract_markdown_link_target(text: str) -> str | None:
    """提取 markdown 链接的目标 URL: [label](target) -> target"""
    import re

    m = re.search(r"\]\(([^)]+)\)", text)
    if m:
        return m.group(1).strip()
    return None
