"""HTTP MCP 客户端，连接官方 mcp-playwright 服务。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from yuxi.agents.browser_viewer.config import BrowserViewerConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {502, 503, 504}


class MCPClientError(RuntimeError):
    """调用 mcp-playwright 失败（已重试 1 次）。"""


class BrowserMCPClient:
    """async context manager。HTTP POST 到 mcp-playwright 的 MCP 端点。

    协议简化为：所有调用都走 POST {mcp_url}/tools/{name}，body {context_id, args}。
    实现阶段需要根据 mcp-playwright 实际镜像的 HTTP 端点调整（如 /messages 路径）。
    """

    def __init__(self, config: BrowserViewerConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BrowserMCPClient":
        self._client = httpx.AsyncClient(
            base_url=self._config.mcp_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BrowserMCPClient used outside context manager")
        return self._client

    async def _post_with_retry(self, path: str, json: dict[str, Any]) -> httpx.Response:
        last_status: int | None = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self.client.post(path, json=json)
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if resp.status_code not in _RETRYABLE_STATUS:
                return resp
            last_status = resp.status_code
        raise MCPClientError(
            f"POST {path} failed after retry: "
            f"status={last_status}" if last_status is not None
            else f"exception={last_exc}"
        )

    async def health(self) -> bool:
        try:
            resp = await self.client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def create_context(self, user_id: str) -> str:
        resp = await self._post_with_retry(
            "/tools/browser_create_context",
            json={"user_id": user_id},
        )
        resp.raise_for_status()
        return resp.json()["context_id"]

    async def close_context(self, context_id: str) -> None:
        await self._post_with_retry(
            "/tools/browser_close_context",
            json={"context_id": context_id},
        )

    async def take_screenshot(self, context_id: str) -> bytes:
        resp = await self._post_with_retry(
            "/tools/browser_take_screenshot",
            json={"context_id": context_id, "format": "png"},
        )
        resp.raise_for_status()
        return resp.content

    async def get_snapshot(self, context_id: str) -> str:
        resp = await self._post_with_retry(
            "/tools/browser_snapshot",
            json={"context_id": context_id},
        )
        resp.raise_for_status()
        return resp.text

    async def call_tool(self, context_id: str, name: str, args: dict) -> dict:
        resp = await self._post_with_retry(
            f"/tools/{name}",
            json={"context_id": context_id, **args},
        )
        resp.raise_for_status()
        return resp.json()