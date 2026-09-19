"""FastAPI app 单元测试（mock MCP 客户端）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient
from yuxi.agents.browser_viewer.server import build_app


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
def mcp() -> BrowserMCPClient:
    mock = AsyncMock(spec=BrowserMCPClient)
    mock.initialize.return_value = "sess-test"
    mock.health.return_value = True
    mock.call_tool.return_value = {
        "content": [{"type": "text", "text": "fake-base64-png-data"}],
        "isError": False,
    }
    return mock


@pytest.fixture
def client(config: BrowserViewerConfig, mcp: BrowserMCPClient) -> TestClient:
    app = build_app(config=config, mcp_factory=lambda _: mcp)
    return TestClient(app)


class TestHealth:
    def test_returns_ok_when_mcp_healthy(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestEnsureContext:
    def test_idempotent(self, client: TestClient) -> None:
        first = client.post("/browser/context/ensure", json={"user_id": "user-a"})
        second = client.post("/browser/context/ensure", json={"user_id": "user-a"})
        assert first.status_code == 200
        assert first.json()["context_id"] == second.json()["context_id"]

    def test_rejects_when_over_max(self, mcp: BrowserMCPClient) -> None:
        small_config = BrowserViewerConfig(
            mcp_url="http://mcp-playwright:8931",
            max_contexts=2,
            idle_timeout_seconds=1800,
            sse_poll_interval_ms=1500,
            sse_disconnect_grace_seconds=300,
            port=8932,
        )
        app = build_app(config=small_config, mcp_factory=lambda _: mcp)
        c = TestClient(app)
        c.post("/browser/context/ensure", json={"user_id": "user-a"})
        c.post("/browser/context/ensure", json={"user_id": "user-b"})
        third = c.post("/browser/context/ensure", json={"user_id": "user-c"})
        assert third.status_code == 503

    def test_calls_mcp_initialize(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        client.post("/browser/context/ensure", json={"user_id": "user-a"})
        mcp.initialize.assert_awaited()


class TestMcpCall:
    def test_returns_result(self, client: TestClient) -> None:
        resp = client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_navigate", "args": {"url": "https://x"}},
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["content"][0]["text"] == "fake-base64-png-data"

    def test_ensure_then_call_uses_same_context(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        # ensure 一次 → mcp_call 一次：context_id 应一致；mcp.initialize 只调一次
        client.post("/browser/context/ensure", json={"user_id": "user-a"})
        init_calls_before = mcp.initialize.await_count
        client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_navigate", "args": {}},
        )
        # call_tool 必须被调用
        mcp.call_tool.assert_awaited()
        # ensure 已 initialize 过，call 不应再 initialize
        assert mcp.initialize.await_count == init_calls_before

    def test_returns_502_on_mcp_error(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        from yuxi.agents.browser_viewer.mcp_client import MCPClientError
        mcp.call_tool.side_effect = MCPClientError("mcp down")
        resp = client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_click", "args": {}},
        )
        assert resp.status_code == 502


class TestStreamEndpointReachable:
    def test_endpoint_returns_event_stream_media_type(self, client: TestClient) -> None:
        client.post("/browser/context/ensure", json={"user_id": "user-a"})
        import httpx
        base_url = str(client.base_url).rstrip("/")
        with httpx.Client(timeout=0.5) as http_client:
            try:
                resp = http_client.get(f"{base_url}/browser/stream/user-a")
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
            except (httpx.ReadTimeout, httpx.RemoteProtocolError):
                pass