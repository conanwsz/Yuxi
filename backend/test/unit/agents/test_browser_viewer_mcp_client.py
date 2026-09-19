"""BrowserMCPClient 单元测试，用 AsyncMock 隔离 mcp-playwright。"""

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
    json_data: dict | None = None,
    content: bytes | None = None,
    text: str | None = None,
) -> httpx.Response:
    """构造 httpx.Response 风格的 mock，可 await。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.content = content if content is not None else b""
    resp.text = text or ""
    resp.raise_for_status = MagicMock()
    return resp


class TestHealth:
    async def test_returns_true_on_success(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.get = AsyncMock(return_value=_mock_response(200))
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                assert await client.health() is True

    async def test_returns_false_on_connection_error(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.get = AsyncMock(side_effect=httpx.ConnectError("mcp down"))
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                assert await client.health() is False


class TestCreateContext:
    async def test_returns_context_id(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                return_value=_mock_response(200, json_data={"context_id": "ctx_abc123"})
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                ctx_id = await client.create_context("user-a")
                assert ctx_id == "ctx_abc123"


class TestErrorHandling:
    async def test_take_screenshot_retries_once_on_5xx(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[
                    _mock_response(503),
                    _mock_response(200, content=b"\x89PNG\r\n\x1a\n"),
                ]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                data = await client.take_screenshot("ctx_x")
                assert data.startswith(b"\x89PNG")
                assert inner.post.await_count == 2

    async def test_take_screenshot_raises_after_two_failures(self, config: BrowserViewerConfig) -> None:
        with patch("yuxi.agents.browser_viewer.mcp_client.httpx.AsyncClient") as MockClient:
            inner = AsyncMock()
            inner.post = AsyncMock(
                side_effect=[_mock_response(503), _mock_response(503)]
            )
            inner.aclose = AsyncMock()
            MockClient.return_value = inner
            async with BrowserMCPClient(config) as client:
                with pytest.raises(MCPClientError):
                    await client.take_screenshot("ctx_x")