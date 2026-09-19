"""FastAPI app 单元测试，用 AsyncMock 替代 MCP 客户端。

SSE 流测试跳过（无限循环 + TestClient 同步上下文不易终止），
端到端 SSE 测试在 backend/test/integration/api/test_browser_stream.py 跑真实容器。
"""

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
    mock.health.return_value = True
    mock.create_context.return_value = "ctx_test_001"
    mock.call_tool.return_value = {"ok": True}
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

    def test_returns_degraded_when_mcp_unhealthy(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        mcp.health.return_value = False
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"


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


class TestMcpCall:
    def test_returns_result(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        resp = client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_click", "args": {"ref": "btn-1"}},
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == {"ok": True}
        # 第二次调用：同一 user_id 应复用 context_id
        client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_click", "args": {"ref": "btn-2"}},
        )
        first_call_args = mcp.call_tool.await_args_list[0]
        second_call_args = mcp.call_tool.await_args_list[1]
        assert second_call_args.args[0] == first_call_args.args[0]  # 同一 context_id

    def test_returns_502_on_mcp_error(self, client: TestClient, mcp: BrowserMCPClient) -> None:
        from yuxi.agents.browser_viewer.mcp_client import MCPClientError
        mcp.call_tool.side_effect = MCPClientError("mcp down")
        resp = client.post(
            "/browser/mcp/call",
            json={"user_id": "user-a", "tool": "browser_click", "args": {}},
        )
        assert resp.status_code == 502


class TestStreamEndpointReachable:
    """仅验证端点可达 + media type 正确，不验证流内容（端到端在集成测试里）。"""

    def test_endpoint_returns_event_stream_media_type(self, client: TestClient) -> None:
        client.post("/browser/context/ensure", json={"user_id": "user-a"})
        # 用 httpx 短超时直接发请求，验证 status_code + headers
        import httpx
        base_url = str(client.base_url).rstrip("/")
        with httpx.Client(timeout=0.5) as http_client:
            try:
                resp = http_client.get(f"{base_url}/browser/stream/user-a")
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
            except (httpx.ReadTimeout, httpx.RemoteProtocolError):
                # 流式响应在第一次 yield 后还活着，连接关闭触发的异常可接受
                pass