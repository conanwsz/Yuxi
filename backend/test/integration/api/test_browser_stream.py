"""浏览器 viewer SSE 推流集成测试（依赖 docker compose）。

依赖：
- docker compose up -d mcp-playwright browser-viewer
- browser-viewer 监听 127.0.0.1:8932
- mcp-playwright 监听 mcp-playwright:8931

如果服务未启动，自动 skip（用 pytest.skip）。
"""

from __future__ import annotations

import json

import httpx
import pytest

VIEWER_URL = "http://127.0.0.1:8932"


def _viewer_reachable() -> bool:
    try:
        resp = httpx.get(f"{VIEWER_URL}/health", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, httpx.RequestError):
        return False


pytestmark = pytest.mark.skipif(
    not _viewer_reachable(),
    reason="browser-viewer 未启动（需 docker compose up -d mcp-playwright browser-viewer）",
)


def test_health():
    resp = httpx.get(f"{VIEWER_URL}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


def test_ensure_then_sse():
    user_id = "it-user-stream"
    ensure = httpx.post(f"{VIEWER_URL}/browser/context/ensure", json={"user_id": user_id})
    assert ensure.status_code == 200
    ctx_id = ensure.json()["context_id"]
    assert ctx_id, "ensure 应返回 context_id"

    with httpx.stream("GET", f"{VIEWER_URL}/browser/stream/{user_id}", timeout=10.0) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        found_event = False
        for chunk in resp.iter_text():
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: "):])
                    assert "screenshot_b64" in payload or payload.get("status") in (
                        "active",
                        "unavailable",
                    )
                    found_event = True
                    break
            if found_event:
                break
        assert found_event, "10s 内未收到任何 SSE data 事件"


def test_503_on_concurrency_overflow():
    """确保 31 个并发 ensure 返回 503（默认 max=30）。"""
    user_ids = [f"it-user-overflow-{i}" for i in range(31)]
    responses = [
        httpx.post(f"{VIEWER_URL}/browser/context/ensure", json={"user_id": uid})
        for uid in user_ids
    ]
    statuses = [r.status_code for r in responses]
    # 至少有一个 503
    assert 503 in statuses, f"31 个并发 ensure 都成功，预期至少一个 503: {statuses}"
    # 清理
    for r in responses:
        try:
            r.close()
        except Exception:
            pass