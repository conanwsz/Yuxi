"""BrowserMCPClient 单元测试（mock 新 MCP JSON-RPC 协议）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


def _mock_response(
    status_code: int = 200,
    text: str = "",
    headers: dict | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    """构造 httpx.Response 风格的 mock。headers 用于返回 mcp-session-id。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.content = content if content is not None else b""
    resp.headers = httpx.Headers(headers or {})
    resp.raise_for_status = MagicMock()
    return resp


def _sse(payload: dict) -> str:
    import json
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


class TestInitialize:
    async def test_returns_session_id_from_header(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                return_value=_mock_response(
                    200,
                    text=_sse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
                    headers={"mcp-session-id": "sess-abc-123"},
                )
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                sid = await client.initialize()
                assert sid == "sess-abc-123"
                assert client.session_id == "sess-abc-123"

    async def test_raises_when_no_session_header(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(return_value=_mock_response(200, text=_sse({"result": {}})))
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                with pytest.raises(MCPClientError, match="mcp-session-id"):
                    await client.initialize()


class TestCallTool:
    async def test_passes_session_id_header(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            init_resp = _mock_response(
                200,
                text=_sse({"result": {}}),
                headers={"mcp-session-id": "sess-xyz"},
            )
            call_resp = _mock_response(
                200,
                text=_sse({"result": {"content": [{"type": "text", "text": "ok"}]}}),
            )
            inner.post = AsyncMock(side_effect=[init_resp, call_resp])
            inner.aclose = AsyncMock()
            MockClient.return_value = inner

            async with BrowserMCPClient(config) as client:
                await client.initialize()
                result = await client.call_tool("browser_navigate", {"url": "https://x"})
                assert result["content"][0]["text"] == "ok"

                # 第二次调用必须带 mcp-session-id header
                call_kwargs = inner.post.await_args_list[1].kwargs
                assert call_kwargs["headers"]["mcp-session-id"] == "sess-xyz"

    async def test_raises_on_isError_true(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[
                    _mock_response(200, text=_sse({"result": {}}), headers={"mcp-session-id": "s"}),
                    _mock_response(
                        200,
                        text=_sse({
                            "result": {
                                "isError": True,
                                "content": [{"type": "text", "text": "boom"}],
                            }
                        }),
                    ),
                ]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                await client.initialize()
                with pytest.raises(MCPClientError, match="boom"):
                    await client.call_tool("browser_click", {})

    async def test_raises_on_jsonrpc_error(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[
                    _mock_response(200, text=_sse({"result": {}}), headers={"mcp-session-id": "s"}),
                    _mock_response(
                        200,
                        text=_sse({"error": {"code": -32600, "message": "bad"}}),
                    ),
                ]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                await client.initialize()
                with pytest.raises(MCPClientError, match="bad"):
                    await client.call_tool("browser_click", {})


class TestHealth:
    async def test_true_when_initialize_ok(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                return_value=_mock_response(
                    200,
                    text=_sse({"result": {}}),
                    headers={"mcp-session-id": "s"},
                )
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                assert await client.health() is True

    async def test_false_on_http_error(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(side_effect=httpx.ConnectError("down"))
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                assert await client.health() is False


class TestRetry:
    async def test_retries_once_on_5xx(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[
                    _mock_response(503),
                    _mock_response(200, text=_sse({"result": {}}), headers={"mcp-session-id": "s"}),
                ]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                sid = await client.initialize()
                assert sid == "s"
                assert inner.post.await_count == 2


class TestClose:
    async def test_calls_browser_close(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[
                    _mock_response(200, text=_sse({"result": {}}), headers={"mcp-session-id": "s"}),
                    _mock_response(
                        200,
                        text=_sse({"result": {"content": [{"type": "text", "text": "closed"}]}}),
                    ),
                ]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                await client.initialize()
                await client.close()
                # 第二次调用是 browser_close
                call_kwargs = inner.post.await_args_list[1].kwargs
                body = call_kwargs["json"]
                assert body["method"] == "tools/call"
                assert body["params"]["name"] == "browser_close"

    async def test_noop_when_no_session(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock()
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                await client.close()  # 不抛异常
                inner.post.assert_not_called()