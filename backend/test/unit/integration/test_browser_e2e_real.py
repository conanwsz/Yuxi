"""浏览器 viewer 真实 E2E 测试（不依赖 pg_manager，只依赖 docker 容器）。

放在 test/unit/integration/ 而非 test/integration/api/ 是因为：
- test/integration/conftest.py 的 autouse fixture 会初始化 pg_manager，需要 postgres 服务
- 本测试只检查 browser-viewer + mcp-playwright 容器交互，不需要数据库

依赖：
- docker compose up -d mcp-playwright browser-viewer
- browser-viewer 监听 http://127.0.0.1:8932
"""

from __future__ import annotations

import base64

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
    body = resp.json()
    assert body["status"] == "ok"


def test_full_flow_navigate_and_screenshot():
    """完整链路：ensure → navigate → take_screenshot → 拿到 base64 PNG。

    注意：使用唯一 user_id（带时间戳）避免与其他测试或 viewer 残留 context 冲突。
    viewer 有 30 并发上限；长时间运行的 viewer 可能因其他测试残留占满，本测试若失败
    应先重启 viewer 容器清空 state。
    """
    import time as _time

    user_id = f"e2e-{int(_time.time())}"

    # 1) ensure 建 session
    ensure = httpx.post(
        f"{VIEWER_URL}/browser/context/ensure",
        json={"user_id": user_id},
        timeout=10.0,
    )
    assert ensure.status_code == 200, (
        f"ensure 失败 (可能 viewer 残留 context 太多): "
        f"{ensure.status_code} {ensure.text}"
    )
    ctx_id = ensure.json()["context_id"]
    assert ctx_id

    # 2) navigate via viewer's mcp_call endpoint
    nav = httpx.post(
        f"{VIEWER_URL}/browser/mcp/call",
        json={
            "user_id": user_id,
            "tool": "browser_navigate",
            "args": {"url": "https://example.com"},
        },
        timeout=30.0,
    )
    assert nav.status_code == 200, nav.text
    nav_text = nav.json()["result"]["content"][0]["text"]
    assert "Example Domain" in nav_text, f"navigate 异常: {nav_text}"

    # 3) take_screenshot 通过 viewer → mcp-playwright → 拿到 base64 PNG
    shot = httpx.post(
        f"{VIEWER_URL}/browser/mcp/call",
        json={
            "user_id": user_id,
            "tool": "browser_take_screenshot",
            "args": {"type": "png"},
        },
        timeout=60.0,
    )
    assert shot.status_code == 200, shot.text
    content = shot.json()["result"]["content"]
    image_items = [c for c in content if c.get("type") == "image"]
    assert len(image_items) == 1, f"应返回 1 个 image content，实际 {len(image_items)}"
    png_bytes = base64.b64decode(image_items[0]["data"])
    assert len(png_bytes) > 1000, f"PNG 太小: {len(png_bytes)} bytes"
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", f"非合法 PNG header: {png_bytes[:8].hex()}"


# 注：并发 503 测试在 test_browser_viewer_stream.py::TestEnsureContext::test_rejects_when_over_max
# 单元测试已覆盖，E2E 不重复（避免污染 viewer state）。