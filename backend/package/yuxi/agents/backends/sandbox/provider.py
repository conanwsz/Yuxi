from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass

from yuxi.utils.logging_config import logger

from .paths import _workspace_uid_dirname
from .provisioner_client import ProvisionerClient, SandboxRecord


def sandbox_provisioner_token() -> str:
    token = (os.getenv("SANDBOX_PROVISIONER_TOKEN") or "").strip()
    if len(token) < 32:
        raise ValueError("SANDBOX_PROVISIONER_TOKEN must contain at least 32 characters")
    return token


def sandbox_id_for_thread(thread_id: str, skills_thread_id: str | None = None, *, uid: str | None = None) -> str:
    file_thread_id = str(thread_id or "").strip()
    skills_id = str(skills_thread_id or file_thread_id).strip()
    uid_id = str(uid or "").strip()
    scope = file_thread_id if skills_id == file_thread_id else f"{file_thread_id}:{skills_id}"
    identity = f"{uid_id}:{scope}" if uid_id else scope
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return digest[:12]


def _sandbox_key(uid: str, file_thread_id: str, skills_thread_id: str) -> str:
    return f"{uid}::{file_thread_id}::{skills_thread_id}"


def normalize_env(env: dict | None) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in env.items() if str(key)}


def merge_user_agent_env(
    uid: str,
    env: dict | None,
    *,
    is_oidc: bool,
    entity_code: str | None = None,
    department_code: str | None = None,
) -> dict[str, str]:
    """合成沙盒环境变量，并强制 OIDC uid 与系统用户身份保持一致。

    OIDC 用户在沙箱侧也会补齐 ``entity_code`` / ``dept_code``，使其与后端 API 暴露的环境
    变量一致，确保 Agent 运行时的环境变量视图与前端 ``/api/user/agent-env`` 完全对齐。
    """

    merged = normalize_env(env)
    if is_oidc:
        merged["uid"] = str(uid)
        if entity_code:
            merged["entity_code"] = entity_code
        if department_code:
            merged["dept_code"] = department_code
    return merged


def postgres_conninfo() -> str:
    db_url = os.getenv("POSTGRES_URL", "").strip()
    return db_url.replace("+asyncpg", "").replace("+psycopg", "")


def load_user_agent_env(uid: str) -> dict[str, str]:
    conninfo = postgres_conninfo()
    if not conninfo:
        return {}

    try:
        import psycopg

        with psycopg.connect(conninfo, connect_timeout=3) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT agent_envs.env,
                           EXISTS (
                               SELECT 1
                               FROM external_identities
                               JOIN users ON users.id = external_identities.user_id
                               WHERE users.uid = %s AND users.is_deleted = 0
                           ) AS is_oidc,
                           departments.entity_code,
                           departments.department_code
                    FROM (VALUES (1)) AS seed(value)
                    LEFT JOIN agent_envs ON agent_envs.uid = %s
                    LEFT JOIN users ON users.uid = %s AND users.is_deleted = 0
                    LEFT JOIN departments ON departments.id = users.department_id
                    """,
                    (uid, uid, uid),
                )
                row = cursor.fetchone()
    except Exception as exc:
        raise RuntimeError(f"failed to load agent env for uid {uid}: {exc}") from exc

    if not row:
        return merge_user_agent_env(uid, {}, is_oidc=False)

    value, is_oidc, entity_code, department_code = row
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"stored agent env for uid {uid} is not valid JSON") from exc
    return merge_user_agent_env(
        uid,
        value,
        is_oidc=bool(is_oidc),
        entity_code=entity_code,
        department_code=department_code,
    )


@dataclass(slots=True)
class SandboxConnection:
    cache_key: str
    thread_id: str
    file_thread_id: str
    skills_thread_id: str
    uid: str
    sandbox_id: str
    sandbox_url: str


class ProvisionerSandboxProvider:
    def __init__(self):
        provider_name = (os.getenv("SANDBOX_PROVIDER") or "provisioner").strip().lower()
        if provider_name != "provisioner":
            raise ValueError("Only SANDBOX_PROVIDER=provisioner is supported.")
        provisioner_url = (os.getenv("SANDBOX_PROVISIONER_URL") or "http://sandbox-provisioner:8002").strip()

        self._client = ProvisionerClient(provisioner_url, token=sandbox_provisioner_token())
        self._lock = threading.Lock()
        self._thread_locks: dict[str, threading.Lock] = {}
        self._connections: dict[str, SandboxConnection] = {}
        self._last_touch_at: dict[str, float] = {}
        self._touch_interval_seconds = int(os.getenv("SANDBOX_KEEPALIVE_INTERVAL_SECONDS") or 30)

    def _thread_lock(self, cache_key: str) -> threading.Lock:
        with self._lock:
            lock = self._thread_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._thread_locks[cache_key] = lock
            return lock

    def _record_to_connection(
        self,
        *,
        cache_key: str,
        thread_id: str,
        file_thread_id: str,
        skills_thread_id: str,
        uid: str,
        record: SandboxRecord,
    ) -> SandboxConnection:
        connection = SandboxConnection(
            cache_key=cache_key,
            thread_id=thread_id,
            file_thread_id=file_thread_id,
            skills_thread_id=skills_thread_id,
            uid=uid,
            sandbox_id=record.sandbox_id,
            sandbox_url=record.sandbox_url,
        )
        self._connections[cache_key] = connection
        self._last_touch_at[cache_key] = time.time()
        return connection

    def _should_touch(self, cache_key: str) -> bool:
        if self._touch_interval_seconds <= 0:
            return False
        last_touch = self._last_touch_at.get(cache_key)
        if last_touch is None:
            return True
        return (time.time() - last_touch) >= self._touch_interval_seconds

    def _touch_if_needed(self, connection: SandboxConnection) -> bool:
        if not self._should_touch(connection.cache_key):
            return True
        is_alive = self._client.touch(connection.sandbox_id)
        self._last_touch_at[connection.cache_key] = time.time()
        return is_alive

    def acquire(
        self,
        thread_id: str,
        *,
        uid: str,
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> str:
        file_id = str(file_thread_id or thread_id).strip()
        skills_id = str(skills_thread_id or thread_id).strip()
        cache_key = _sandbox_key(uid, file_id, skills_id)
        lock = self._thread_lock(cache_key)
        with lock:
            current = self._connections.get(cache_key)
            if current:
                if current.uid != uid:
                    raise RuntimeError(f"sandbox scope {cache_key} belongs to uid {current.uid}, not {uid}")
                try:
                    if self._touch_if_needed(current):
                        return current.sandbox_id
                    self._connections.pop(cache_key, None)
                    self._last_touch_at.pop(cache_key, None)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Failed to touch sandbox {current.sandbox_id} for {cache_key}: {exc}")
                    return current.sandbox_id

            sandbox_id = sandbox_id_for_thread(file_id, skills_id, uid=uid)
            logger.info(f"Ensuring sandbox {sandbox_id} for file thread {file_id} and skills thread {skills_id}")
            record = self._client.create(
                sandbox_id,
                thread_id,
                _workspace_uid_dirname(uid),
                load_user_agent_env(uid),
                file_thread_id=file_id,
                skills_thread_id=skills_id,
            )

            connection = self._record_to_connection(
                cache_key=cache_key,
                thread_id=thread_id,
                file_thread_id=file_id,
                skills_thread_id=skills_id,
                uid=uid,
                record=record,
            )
            return connection.sandbox_id

    def get(
        self,
        thread_id: str,
        *,
        uid: str,
        create_if_missing: bool = False,
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> SandboxConnection | None:
        file_id = str(file_thread_id or thread_id).strip()
        skills_id = str(skills_thread_id or thread_id).strip()
        cache_key = _sandbox_key(uid, file_id, skills_id)
        lock = self._thread_lock(cache_key)
        with lock:
            current = self._connections.get(cache_key)
            if current:
                if current.uid != uid:
                    raise RuntimeError(f"sandbox scope {cache_key} belongs to uid {current.uid}, not {uid}")
                try:
                    if self._touch_if_needed(current):
                        return current
                    self._connections.pop(cache_key, None)
                    self._last_touch_at.pop(cache_key, None)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Failed to touch sandbox {current.sandbox_id} for {cache_key}: {exc}")
                    return current

            sandbox_id = sandbox_id_for_thread(file_id, skills_id, uid=uid)
            if create_if_missing:
                record = self._client.create(
                    sandbox_id,
                    thread_id,
                    _workspace_uid_dirname(uid),
                    load_user_agent_env(uid),
                    file_thread_id=file_id,
                    skills_thread_id=skills_id,
                )
            else:
                record = self._client.discover(sandbox_id)
                if record is None:
                    return None

            return self._record_to_connection(
                cache_key=cache_key,
                thread_id=thread_id,
                file_thread_id=file_id,
                skills_thread_id=skills_id,
                uid=uid,
                record=record,
            )

    def shutdown(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            self._last_touch_at.clear()

        for connection in connections:
            try:
                self._client.delete(connection.sandbox_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to release sandbox {connection.sandbox_id} for {connection.cache_key}: {exc}")


_sandbox_provider: ProvisionerSandboxProvider | None = None
_sandbox_provider_lock = threading.Lock()


def init_sandbox_provider() -> ProvisionerSandboxProvider:
    global _sandbox_provider
    with _sandbox_provider_lock:
        if _sandbox_provider is None:
            _sandbox_provider = ProvisionerSandboxProvider()
        return _sandbox_provider


def get_sandbox_provider() -> ProvisionerSandboxProvider:
    provider = _sandbox_provider
    if provider is not None:
        return provider
    return init_sandbox_provider()


def shutdown_sandbox_provider() -> None:
    global _sandbox_provider
    with _sandbox_provider_lock:
        provider = _sandbox_provider
        _sandbox_provider = None
    if provider is not None:
        provider.shutdown()
