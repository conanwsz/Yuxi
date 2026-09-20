"""验证 Yuxi MCP 服务注册表中包含 mcp-playwright 默认配置。

纯单元测试：只检查 _DEFAULT_MCP_SERVERS 字典内容，不需要 DB / docker。
"""

from __future__ import annotations


def test_mcp_playwright_in_default_servers():
    from yuxi.agents.mcp.service import _DEFAULT_MCP_SERVERS

    assert "mcp-playwright" in _DEFAULT_MCP_SERVERS, "mcp-playwright 应在 _DEFAULT_MCP_SERVERS 中"

    cfg = _DEFAULT_MCP_SERVERS["mcp-playwright"]
    assert cfg.get("transport") == "streamable_http", "mcp-playwright 应使用 streamable_http transport"
    assert "mcp-playwright:8931" in cfg.get("url", ""), "URL 应指向 mcp-playwright:8931"


def test_mcp_playwright_url_format():
    from yuxi.agents.mcp.service import _DEFAULT_MCP_SERVERS

    cfg = _DEFAULT_MCP_SERVERS["mcp-playwright"]
    url = cfg.get("url", "")
    assert url.startswith("http://"), f"URL 应以 http:// 开头: {url}"
    assert url.endswith(":8931"), f"URL 应以 :8931 结尾: {url}"


def test_default_servers_still_include_chart():
    """防止新增 mcp-playwright 时把 mcp-server-chart 误删。"""
    from yuxi.agents.mcp.service import _DEFAULT_MCP_SERVERS

    assert "mcp-server-chart" in _DEFAULT_MCP_SERVERS, "原有 mcp-server-chart 不应被误删"
