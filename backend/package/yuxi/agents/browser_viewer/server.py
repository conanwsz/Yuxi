"""FastAPI 入口。提供 ensure、SSE 推流、健康检查、MCP 内部调用代理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from yuxi.agents.browser_viewer.config import BrowserViewerConfig
from yuxi.agents.browser_viewer.idle_reclaimer import IdleReclaimer
from yuxi.agents.browser_viewer.mcp_client import BrowserMCPClient, MCPClientError
from yuxi.agents.browser_viewer.state import BrowserViewerState
from yuxi.agents.browser_viewer.stream import stream_events

logger = logging.getLogger(__name__)


class EnsureRequest(BaseModel):
    user_id: str


class CallRequest(BaseModel):
    user_id: str
    tool: str
    args: dict


def build_app(
    config: BrowserViewerConfig,
    mcp_factory: Callable[[BrowserViewerConfig], BrowserMCPClient],
) -> FastAPI:
    state = BrowserViewerState()
    # 启动时把所有旧 entry 标 dirty（spec §3.3）
    state.mark_all_dirty()
    # 在 build 时直接初始化 mcp，方便 TestClient 不进入 startup 也能用
    mcp = mcp_factory(config)

    app = FastAPI(title="browser-viewer")
    app.state.config = config
    app.state.state = state
    app.state.mcp = mcp

    @app.on_event("startup")
    async def _on_startup() -> None:
        async def _run_reclaimer() -> None:
            while True:
                try:
                    await IdleReclaimer(state, mcp, config).tick()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("reclaimer loop error: %s", exc)
                await asyncio.sleep(60)
        asyncio.create_task(_run_reclaimer())

    @app.get("/health")
    async def health() -> dict:
        mcp = app.state.mcp
        healthy = await mcp.health()
        return {"status": "ok" if healthy else "degraded"}

    @app.post("/browser/context/ensure")
    async def ensure(req: EnsureRequest) -> dict:
        if state.active_count() >= config.max_contexts:
            raise HTTPException(status_code=503, detail="max concurrent contexts reached")
        ctx_id = state.ensure(req.user_id)
        state.mark_active(req.user_id)
        return {"context_id": ctx_id}

    @app.get("/browser/stream/{user_id}")
    async def stream(user_id: str, request: Request) -> StreamingResponse:
        mcp = app.state.mcp

        async def event_gen() -> AsyncIterator[str]:
            try:
                async for chunk in stream_events(user_id, state, mcp, config):
                    if await request.is_disconnected():
                        state.mark_disconnected(user_id)
                        return
                    yield chunk
            finally:
                state.mark_disconnected(user_id)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.post("/browser/mcp/call")
    async def mcp_call(req: CallRequest) -> dict:
        mcp = app.state.mcp
        ctx_id = state.ensure(req.user_id)
        state.mark_active(req.user_id)
        try:
            result = await mcp.call_tool(ctx_id, req.tool, req.args)
        except MCPClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"result": result}

    return app


def main() -> None:
    import uvicorn

    config = BrowserViewerConfig.from_env()
    app = build_app(config=config, mcp_factory=BrowserMCPClient)
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="info")


if __name__ == "__main__":
    main()