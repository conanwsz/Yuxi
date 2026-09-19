from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.mcp import service as mcp_service
from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.models_business import MCPServer


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


def test_stdio_mcp_config_ignores_stale_http_fields():
    server = MCPServer(
        slug="stdio-demo",
        name="stdio demo",
        transport="stdio",
        url="https://stale.example.com/mcp",
        command="uvx",
        args=["demo-mcp"],
        env={"API_KEY": "secret"},
        headers={"Authorization": "Bearer stale"},
        timeout=10,
        sse_read_timeout=20,
        created_by="admin",
        updated_by="admin",
    )

    assert server.to_mcp_config() == {
        "transport": "stdio",
        "command": "uvx",
        "args": ["demo-mcp"],
        "env": {"API_KEY": "secret"},
    }


def test_http_mcp_config_ignores_stale_stdio_fields():
    server = MCPServer(
        slug="http-demo",
        name="http demo",
        transport="streamable_http",
        url="https://example.com/mcp",
        command="stale-command",
        args=["stale-arg"],
        env={"STALE": "value"},
        headers={"Authorization": "Bearer token"},
        timeout=10,
        sse_read_timeout=20,
        created_by="admin",
        updated_by="admin",
    )

    assert server.to_mcp_config() == {
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer token"},
        "timeout": 10,
        "sse_read_timeout": 20,
    }


async def test_get_mcp_tools_can_surface_connection_errors(monkeypatch):
    class FailingClient:
        async def get_tools(self):
            raise RuntimeError("connection failed")

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del server_name, db
        return {"transport": "stdio", "command": "demo"}

    async def fake_get_mcp_client(server_configs):
        del server_configs
        return FailingClient()

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    with pytest.raises(RuntimeError, match="connection failed"):
        await mcp_service.get_mcp_tools("demo", raise_on_error=True)


async def test_ensure_builtin_mcp_servers_removes_retired_system_server(monkeypatch, mcp_session):
    retired_server = MCPServer(
        slug="sequentialthinking",
        name="sequentialthinking",
        description="old builtin",
        transport="streamable_http",
        url="https://remote.mcpservers.org/sequentialthinking/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(retired_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    retired = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    chart = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart"))
    assert retired is None
    assert chart is not None


async def test_ensure_builtin_mcp_servers_preserves_user_server_with_retired_slug(monkeypatch, mcp_session):
    user_server = MCPServer(
        slug="sequentialthinking",
        name="用户自定义 MCP",
        description="user managed",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(user_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    assert server is not None
    assert server.created_by == "admin"


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {"demo": {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}},
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"transport": "stdio", "command": "demo-v1", "disabled_tools": []},
        {"transport": "stdio", "command": "demo-v2", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["command"])
        tool = SimpleNamespace(name=f"tool_for_{config['command']}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await mcp_service.get_mcp_tools("demo")
    tools_v1_second = await mcp_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await mcp_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"transport": "stdio", "command": "cmd-a", "disabled_tools": []},
        "beta": {"transport": "stdio", "command": "cmd-b", "disabled_tools": []},
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del names, db
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(mcp_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", server_configs),
        ("beta", server_configs),
    ]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_refreshes_when_ttl_expires(monkeypatch):
    """上游 MCP 服务在我们不知情的情况下增减工具时，仅靠 config_hash 失效不够；
    TTL 到期后必须强制重拉，否则新工具（如 create_schedule）一直看不到。"""

    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-ttl", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return config

    upstream_tools: list[SimpleNamespace] = [SimpleNamespace(name="tool_old", metadata={})]
    build_calls: list[int] = []

    async def fake_get_mcp_client(server_configs):
        del server_configs
        build_calls.append(len(upstream_tools))
        return _FakeClient(list(upstream_tools))

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr(mcp_service, "_mcp_tools_cache_ttl_seconds", lambda: 0.05)

    # 第一次拉：上游 1 个工具。
    tools_first = await mcp_service.get_mcp_tools("demo")
    assert [t.name for t in tools_first] == ["tool_old"]

    # 上游悄悄加了 tool_new（DB 配置未变，config_hash 不变 → 旧逻辑会一直命中缓存）。
    upstream_tools.append(SimpleNamespace(name="tool_new", metadata={}))

    # TTL 内的第二次：还是缓存的 1 个工具。
    tools_within_ttl = await mcp_service.get_mcp_tools("demo")
    assert [t.name for t in tools_within_ttl] == ["tool_old"]

    # 等过 TTL 后再拉：应能拿到 2 个工具。
    import asyncio
    await asyncio.sleep(0.06)
    tools_after_ttl = await mcp_service.get_mcp_tools("demo")
    assert sorted(t.name for t in tools_after_ttl) == ["tool_new", "tool_old"]
    # 第一次 + TTL 后第二次 = 2 次拉取；TTL 内的那次命中缓存，没拉。
    assert build_calls == [1, 2]

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_zero_ttl_disables_cache(monkeypatch):
    """MCP_TOOLS_CACHE_TTL_SECONDS<=0 时，每次都重拉，方便排障。"""

    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-zerottl", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    build_calls: list[int] = []

    async def fake_get_mcp_client(server_configs):
        del server_configs
        build_calls.append(1)
        return _FakeClient([SimpleNamespace(name="x", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr(mcp_service, "_mcp_tools_cache_ttl_seconds", lambda: 0.0)

    await mcp_service.get_mcp_tools("demo")
    await mcp_service.get_mcp_tools("demo")
    await mcp_service.get_mcp_tools("demo")

    assert build_calls == [1, 1, 1]

    mcp_service.clear_mcp_cache()


async def test_clear_mcp_server_tools_cache_also_clears_loaded_at(monkeypatch):
    """clear_mcp_server_tools_cache 必须把 _mcp_tools_cache_loaded_at 一并清掉，
    否则下次同 config 仍会被认成"刚加载过"立即命中一个其实已经过期的缓存。"""

    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-clear", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        del server_configs
        return _FakeClient([SimpleNamespace(name="x", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr(mcp_service, "_mcp_tools_cache_ttl_seconds", lambda: 60.0)

    await mcp_service.get_mcp_tools("demo")
    cache_keys = list(mcp_service._mcp_tools_cache_loaded_at)
    assert cache_keys, "首次加载后应记录 loaded_at"

    mcp_service.clear_mcp_server_tools_cache("demo")
    assert mcp_service._mcp_tools_cache == {}
    assert mcp_service._mcp_tools_cache_loaded_at == {}

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_injects_caller_token_and_emp_no_header_for_http_transport(monkeypatch):
    """caller_token 和 caller_emp_no 在 sse/streamable_http transport 下应分别注入对应 header。"""
    mcp_service.clear_mcp_cache()

    captured_configs: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return {
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer static-token"},
        }

    async def fake_get_mcp_client(server_configs):
        captured_configs.append(server_configs["demo"].copy())
        return _FakeClient([SimpleNamespace(name="get_schedule", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    await mcp_service.get_mcp_tools("demo", caller_token="user-jwt-abc", caller_emp_no="EMP999")

    assert len(captured_configs) == 1
    headers = captured_configs[0].get("headers", {})
    # 用户 token 及工号注入成功，静态 token 保留
    assert headers.get("X-Superchuchu-Token") == "user-jwt-abc"
    assert headers.get("X-User-Emp-No") == "EMP999"
    assert headers.get("Authorization") == "Bearer static-token"

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_does_not_inject_caller_headers_for_stdio_transport(monkeypatch):
    """stdio transport 不支持 HTTP headers，caller_token 和 caller_emp_no 均不应注入。"""
    mcp_service.clear_mcp_cache()

    captured_configs: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return {"transport": "stdio", "command": "demo-cmd"}

    async def fake_get_mcp_client(server_configs):
        captured_configs.append(server_configs["demo"].copy())
        return _FakeClient([SimpleNamespace(name="some_tool", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    await mcp_service.get_mcp_tools("demo", caller_token="user-jwt-abc", caller_emp_no="EMP999")

    assert len(captured_configs) == 1
    # stdio 配置中不应出现 X-Superchuchu-Token 或 X-User-Emp-No
    assert "X-Superchuchu-Token" not in captured_configs[0].get("headers", {})
    assert "X-User-Emp-No" not in captured_configs[0].get("headers", {})
    assert "headers" not in captured_configs[0]

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_caller_token_does_not_affect_cache_key(monkeypatch):
    """caller_token 不参与缓存 key 计算：不同 token 调用时共享同一份工具缓存。"""
    mcp_service.clear_mcp_cache()

    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return {"transport": "streamable_http", "url": "https://example.com/mcp"}

    async def fake_get_mcp_client(server_configs):
        build_calls.append("called")
        return _FakeClient([SimpleNamespace(name="some_tool", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    # 第一次调用（token=user-A）建连并写缓存
    tools_a = await mcp_service.get_mcp_tools("demo", caller_token="token-user-a", caller_emp_no="EMP-A")
    # 第二次调用（token=user-B）应命中缓存，不重新建连
    tools_b = await mcp_service.get_mcp_tools("demo", caller_token="token-user-b", caller_emp_no="EMP-B")

    assert len(tools_a) == 1
    assert len(tools_b) == 1
    # 仅建连一次，说明缓存命中
    assert build_calls == ["called"]

    mcp_service.clear_mcp_cache()
