from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from yuxi.storage.postgres import manager as manager_module
from yuxi.storage.postgres.manager import PostgresManager


def test_langgraph_pool_checks_connections_before_checkout(monkeypatch):
    """Checkpoint 连接池必须淘汰 PostgreSQL 重启后留下的坏连接。"""
    captured = {}

    class Pool:
        @staticmethod
        async def check_connection(_connection):
            return None

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://user:pass@postgres/yuxi")
    monkeypatch.setattr(manager_module, "AsyncConnectionPool", Pool)
    monkeypatch.setattr(manager_module, "create_async_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(manager_module, "async_sessionmaker", lambda **_kwargs: object())

    manager = object.__new__(PostgresManager)
    manager.__init__()

    manager.initialize()

    assert captured["check"] is Pool.check_connection
