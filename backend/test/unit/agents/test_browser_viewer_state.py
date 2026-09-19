"""BrowserViewerState 单元测试。"""

from __future__ import annotations

import pytest

from yuxi.agents.browser_viewer.state import BrowserViewerState


@pytest.fixture
def state() -> BrowserViewerState:
    return BrowserViewerState()


class TestEnsure:
    def test_returns_context_id_on_first_call(self, state: BrowserViewerState) -> None:
        ctx_id = state.ensure("user-a")
        assert isinstance(ctx_id, str)
        assert len(ctx_id) > 0

    def test_idempotent_returns_same_context_id(self, state: BrowserViewerState) -> None:
        first = state.ensure("user-a")
        second = state.ensure("user-a")
        assert first == second


class TestActiveCount:
    def test_zero_when_no_contexts(self, state: BrowserViewerState) -> None:
        assert state.active_count() == 0

    def test_increments_on_ensure(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.ensure("user-b")
        assert state.active_count() == 2


class TestClose:
    def test_close_existing_returns_true(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        assert state.close("user-a") is True
        assert state.active_count() == 0

    def test_close_non_existing_returns_false(self, state: BrowserViewerState) -> None:
        assert state.close("user-a") is False


class TestEvictIdle:
    def test_no_evict_when_within_window(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        # now_ts=1000 < last_active + idle_timeout=1800 → 不回收
        evicted = state.evict_idle(now_ts=1000.0, grace_after_disconnect=300)
        assert evicted == []

    def test_evicts_when_idle_exceeded(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        # active_ts=0, idle_timeout=1800, now=2000 → 应被回收
        evicted = state.evict_idle(now_ts=2000.0, grace_after_disconnect=300)
        assert "user-a" in evicted
        assert state.active_count() == 0

    def test_disconnected_user_has_grace_period(self, state: BrowserViewerState) -> None:
        state.ensure("user-a")
        state.mark_active("user-a", ts=0.0)
        state.mark_disconnected("user-a", ts=1000.0)
        # disconnect_ts=1000, grace=600, now=1500 → cutoff=1600, 1500<1600 不回收
        evicted = state.evict_idle(now_ts=1500.0, grace_after_disconnect=600)
        assert evicted == []


class TestDirty:
    def test_ensure_after_mark_all_dirty_returns_new_context(self, state: BrowserViewerState) -> None:
        original = state.ensure("user-a")
        state.mark_all_dirty()
        new = state.ensure("user-a")
        assert new != original