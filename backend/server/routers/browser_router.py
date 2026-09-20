"""浏览器 Viewer 代理路由

将前端对 /api/browser/* 的请求代理转发至 browser-viewer 容器服务。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

browser_router = APIRouter(prefix="/browser", tags=["browser"])

BROWSER_VIEWER_URL = os.environ.get("BROWSER_VIEWER_URL", "http://browser-viewer:8932").rstrip("/")


@browser_router.get("/health")
async def browser_health() -> dict:
    """获取 browser-viewer 服务健康状态。"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{BROWSER_VIEWER_URL}/health")
            if resp.status_code == status.HTTP_200_OK:
                return resp.json()
            return {"status": "degraded", "code": resp.status_code}
    except Exception as exc:
        logger.debug("browser-viewer health check failed: %s", exc)
        return {"status": "unavailable"}


@browser_router.post("/context/ensure")
async def ensure_context(request: Request) -> JSONResponse:
    """确保 user_id 对应的 context 已就绪。"""
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BROWSER_VIEWER_URL}/browser/context/ensure", json=body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.HTTPError as exc:
        logger.warning("ensure_context proxy error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": f"browser-viewer unreachable: {exc}"},
        )


@browser_router.post("/mcp/call")
async def mcp_call(request: Request) -> JSONResponse:
    """内部 MCP 调用代理。"""
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BROWSER_VIEWER_URL}/browser/mcp/call", json=body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.HTTPError as exc:
        logger.warning("mcp_call proxy error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": f"browser-viewer unreachable: {exc}"},
        )


@browser_router.post("/activity")
async def browser_activity(request: Request) -> JSONResponse:
    """通知 browser-viewer 用户有浏览器操作活动。"""
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{BROWSER_VIEWER_URL}/browser/activity", json=body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.HTTPError as exc:
        logger.warning("browser_activity proxy error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": f"browser-viewer unreachable: {exc}"},
        )


@browser_router.get("/stream/{user_id}")
async def stream_browser_view(user_id: str, request: Request) -> StreamingResponse:
    """代理 browser-viewer 的 SSE 推流，向前端提供浏览器实时截图与状态。"""
    client = httpx.AsyncClient(timeout=None)

    async def event_generator() -> AsyncIterator[str]:
        try:
            async with client.stream("GET", f"{BROWSER_VIEWER_URL}/browser/stream/{user_id}") as resp:
                if resp.status_code != status.HTTP_200_OK:
                    yield f"data: {json.dumps({'status': 'unavailable'})}\n\n"
                    return
                async for chunk in resp.aiter_text():
                    if await request.is_disconnected():
                        break
                    yield chunk
        except Exception as exc:
            logger.debug("stream_browser_view error for %s: %s", user_id, exc)
            yield f"data: {json.dumps({'status': 'unavailable'})}\n\n"
        finally:
            await client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
