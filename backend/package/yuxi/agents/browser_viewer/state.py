"""viewer 状态：user_id → context_id 映射 + 活跃时间 + dirty 标记。

所有状态在内存中；viewer 重启时调用 mark_all_dirty()。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class _UserEntry:
    context_id: str
    last_active_ts: float
    disconnected_ts: float | None = None
    dirty: bool = False
    mcp_session_id: str | None = None  # 真实 MCP session id（protocol 层的）


@dataclass
class BrowserViewerState:
    _entries: dict[str, _UserEntry] = field(default_factory=dict)
    _idle_timeout_seconds: int = 1800

    def ensure(self, user_id: str) -> str:
        entry = self._entries.get(user_id)
        if entry is None or entry.dirty:
            new_id = f"ctx_{secrets.token_hex(8)}"
            self._entries[user_id] = _UserEntry(
                context_id=new_id,
                last_active_ts=time.monotonic(),
            )
            return new_id
        return entry.context_id

    def set_mcp_session_id(self, user_id: str, session_id: str | None) -> None:
        entry = self._entries.get(user_id)
        if entry is None:
            return
        entry.mcp_session_id = session_id

    def mark_active(self, user_id: str, ts: float | None = None) -> None:
        entry = self._entries.get(user_id)
        if entry is None:
            return
        entry.last_active_ts = ts if ts is not None else time.monotonic()
        entry.disconnected_ts = None

    def mark_disconnected(self, user_id: str, ts: float | None = None) -> None:
        entry = self._entries.get(user_id)
        if entry is None:
            return
        entry.disconnected_ts = ts if ts is not None else time.monotonic()

    def close(self, user_id: str) -> bool:
        if user_id not in self._entries:
            return False
        del self._entries[user_id]
        return True

    def is_dirty(self, user_id: str) -> bool:
        entry = self._entries.get(user_id)
        return entry.dirty if entry else False

    def active_count(self) -> int:
        return sum(1 for e in self._entries.values() if not e.dirty)

    def evict_idle(self, now_ts: float, grace_after_disconnect: int) -> list[str]:
        evicted: list[str] = []
        for user_id, entry in list(self._entries.items()):
            if entry.dirty:
                continue
            if entry.disconnected_ts is not None:
                cutoff = entry.disconnected_ts + grace_after_disconnect
            else:
                cutoff = entry.last_active_ts + self._idle_timeout_seconds
            if now_ts >= cutoff:
                del self._entries[user_id]
                evicted.append(user_id)
        return evicted

    def mark_all_dirty(self) -> None:
        for entry in self._entries.values():
            entry.dirty = True
