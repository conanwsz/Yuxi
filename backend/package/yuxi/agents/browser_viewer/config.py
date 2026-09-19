"""从环境变量加载 viewer 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserViewerConfig:
    mcp_url: str
    max_contexts: int
    idle_timeout_seconds: int
    sse_poll_interval_ms: int
    sse_disconnect_grace_seconds: int
    port: int

    @classmethod
    def from_env(cls) -> BrowserViewerConfig:
        return cls(
            mcp_url=os.environ.get("MCP_PLAYWRIGHT_URL", "http://mcp-playwright:8931"),
            max_contexts=int(os.environ.get("BROWSER_VIEWER_MAX_CONTEXTS", "30")),
            idle_timeout_seconds=int(os.environ.get("BROWSER_VIEWER_IDLE_TIMEOUT", "1800")),
            sse_poll_interval_ms=int(os.environ.get("BROWSER_VIEWER_SSE_POLL_MS", "1500")),
            sse_disconnect_grace_seconds=int(
                os.environ.get("BROWSER_VIEWER_DISCONNECT_GRACE", "300")
            ),
            port=int(os.environ.get("BROWSER_VIEWER_PORT", "8932")),
        )
