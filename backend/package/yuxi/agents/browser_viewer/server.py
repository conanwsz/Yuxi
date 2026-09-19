"""FastAPI 入口。提供 ensure、SSE 推流、健康检查、MCP 内部调用代理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from collections.abc import Callable

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
    state.mark_all_dirty()  # viewer 重启后所有旧 entry 视为 dirty
    mcp = mcp_factory(config)  # BrowserMCPClient 内部惰性创建 httpx client

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

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        await mcp.aclose()

    @app.get("/health")
    async def health() -> dict:
        m = app.state.mcp
        healthy = await m.health()
        return {"status": "ok" if healthy else "degraded"}

    @app.post("/browser/context/ensure")
    async def ensure(req: EnsureRequest) -> dict:
        if state.active_count() >= config.max_contexts:
            raise HTTPException(status_code=503, detail="max concurrent contexts reached")
        ctx_id = state.ensure(req.user_id)
        state.mark_active(req.user_id)
        m = app.state.mcp
        try:
            session_id = await m.initialize()
            state.set_mcp_session_id(req.user_id, session_id)
        except MCPClientError as exc:
            logger.warning("ensure: mcp.initialize failed for %s: %s", req.user_id, exc)
        return {"context_id": ctx_id}

    @app.get("/browser/stream/{user_id}")
    async def stream(user_id: str, request: Request) -> StreamingResponse:
        m = app.state.mcp

        async def event_gen() -> AsyncIterator[str]:
            try:
                async for chunk in stream_events(user_id, state, m, config):
                    if await request.is_disconnected():
                        state.mark_disconnected(user_id)
                        return
                    yield chunk
            finally:
                state.mark_disconnected(user_id)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.post("/browser/mcp/call")
    async def mcp_call(req: CallRequest) -> dict:
        m = app.state.mcp
        state.ensure(req.user_id)
        state.mark_active(req.user_id)
        # 第一次调用前 session 可能没建；自动 initialize
        if state._entries[req.user_id].mcp_session_id is None:  # noqa: SLF001
            try:
                sid = await m.initialize()
                state.set_mcp_session_id(req.user_id, sid)
            except MCPClientError as exc:
                raise HTTPException(status_code=502, detail=f"initialize failed: {exc}") from exc
        try:
            result = await m.call_tool(req.tool, req.args)
        except MCPClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"result": result}

    return app


def main() -> None:
    import uvicorn

    config = BrowserViewerConfig.from_env()
    # 容器内必须 bind 0.0.0.0，docker compose ports 映射才能从主机访问
    app = build_app(config=config, mcp_factory=BrowserMCPClient)
    uvicorn.run(app, host="0.0.0.0", port=config.port, log_level="info")


if __name__ == "__main__":
    main()
