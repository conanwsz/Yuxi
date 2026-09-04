from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from yuxi.services import chat_service as svc

pytestmark = pytest.mark.unit


def test_note_tool_timing_records_start_once_and_end() -> None:
    timings: dict[str, dict[str, str]] = {}

    svc._note_tool_timing(timings, "call-1", "start")
    first_started = timings["call-1"]["started_at"]
    svc._note_tool_timing(timings, "call-1", "start")
    svc._note_tool_timing(timings, "call-1", "end")

    assert timings["call-1"]["started_at"] == first_started
    assert "completed_at" in timings["call-1"]
    assert timings["call-1"]["completed_at"] >= first_started


def test_note_tool_timing_ignores_blank_ids() -> None:
    timings: dict[str, dict[str, str]] = {}
    svc._note_tool_timing(timings, "  ", "start")
    svc._note_tool_timing(timings, None, "end")
    assert timings == {}


def test_note_tool_timing_from_tools_event_ignores_other_threads() -> None:
    timings: dict[str, dict[str, str]] = {}
    svc._note_tool_timing_from_tools_event(
        timings,
        {
            "method": "tools",
            "thread_id": "child",
            "data": {"event": "tool-started", "tool_call_id": "call-1"},
        },
        current_thread_id="parent",
    )
    assert timings == {}

    svc._note_tool_timing_from_tools_event(
        timings,
        {
            "method": "tools",
            "thread_id": "parent",
            "data": {"event": "tool-started", "tool_call_id": "call-1"},
        },
        current_thread_id="parent",
    )
    svc._note_tool_timing_from_tools_event(
        timings,
        {
            "method": "tools",
            "data": {"event": "tool-finished", "tool_call_id": "call-1"},
        },
        current_thread_id="parent",
    )
    assert "started_at" in timings["call-1"]
    assert "completed_at" in timings["call-1"]


def test_note_tool_timing_uses_aware_utc() -> None:
    timings: dict[str, dict[str, str]] = {}
    before = datetime.now(UTC) - timedelta(seconds=2)
    svc._note_tool_timing(timings, "call-1", "start")
    after = datetime.now(UTC) + timedelta(seconds=2)

    recorded = datetime.fromisoformat(timings["call-1"]["started_at"].replace("Z", "+00:00"))
    assert before <= recorded <= after


def test_note_tool_timing_from_message_event_records_start() -> None:
    timings: dict[str, dict[str, str]] = {}
    svc._note_tool_timing_from_message_event(
        timings,
        {"type": "tool_call", "tool_call_id": "call-2", "thread_id": "parent"},
        current_thread_id="parent",
    )
    svc._note_tool_timing_from_message_event(
        timings,
        {"type": "tool_call_delta", "tool_call_id": "call-3", "thread_id": "parent"},
        current_thread_id="parent",
    )
    assert "started_at" in timings["call-2"]
    assert "completed_at" not in timings["call-2"]
    assert "call-3" not in timings


def test_relevant_tool_timings_keeps_current_message_ids() -> None:
    timings = {
        "call-1": {"started_at": "a", "completed_at": "b"},
        "call-2": {"started_at": "c"},
        "call-3": {"started_at": "d"},
    }
    assert svc._relevant_tool_timings(timings, ["call-1", "call-2"]) == {
        "call-1": {"started_at": "a", "completed_at": "b"},
        "call-2": {"started_at": "c"},
    }


@pytest.mark.asyncio
async def test_save_ai_message_attaches_tool_timings_to_extra_metadata() -> None:
    class FakeConvRepo:
        def __init__(self):
            self.saved_messages: list[dict] = []
            self.tool_calls: list[dict] = []
            self.db = SimpleNamespace()

        async def add_message_by_thread_id(self, **kwargs):
            self.saved_messages.append(kwargs)
            return SimpleNamespace(id=1)

        async def add_tool_call(self, **kwargs):
            self.tool_calls.append(kwargs)
            return SimpleNamespace(id=len(self.tool_calls))

    conv_repo = FakeConvRepo()
    timings = {"call-1": {"started_at": "2026-09-04T02:00:00Z", "completed_at": "2026-09-04T02:00:01Z"}}

    await svc._save_ai_message(
        conv_repo,
        "thread-1",
        {"id": "ai-1", "content": "", "tool_calls": [{"id": "call-1", "name": "grep", "args": {}}]},
        tool_timings=timings,
    )

    extra_metadata = conv_repo.saved_messages[0]["extra_metadata"]
    assert extra_metadata["tool_timings"] == timings


@pytest.mark.asyncio
async def test_save_messages_merges_timings_into_existing_ai_message() -> None:
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": []})

    class FakeAgent:
        async def get_graph(self, *, context):
            return FakeGraph()

    class FakeConvRepo:
        def __init__(self):
            self.db = SimpleNamespace()
            self.merged = None

        async def get_messages_by_thread_id(self, _thread_id: str):
            return []

        async def merge_tool_timings(self, thread_id: str, tool_timings=None):
            self.merged = (thread_id, tool_timings)

    conv_repo = FakeConvRepo()
    timings = {"call-1": {"started_at": "2026-09-04T02:00:00Z", "completed_at": "2026-09-04T02:00:03Z"}}

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=object(),
        tool_timings=timings,
    )

    assert conv_repo.merged == ("thread-1", timings)


@pytest.mark.asyncio
async def test_merge_tool_timings_appends_completed_at_to_existing_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from yuxi.repositories.conversation_repository import ConversationRepository

    message = SimpleNamespace(
        role="assistant",
        extra_metadata={"tool_timings": {"call-1": {"started_at": "2026-09-04T02:00:00Z"}}},
        tool_calls=[SimpleNamespace(langgraph_tool_call_id="call-1")],
    )
    flagged: list[tuple[object, str]] = []

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

    repo = ConversationRepository(FakeDB())

    async def fake_get(_thread_id: str):
        return [message]

    monkeypatch.setattr(repo, "get_messages_by_thread_id", fake_get)
    monkeypatch.setattr(
        "yuxi.repositories.conversation_repository.flag_modified",
        lambda obj, key: flagged.append((obj, key)),
    )

    await repo.merge_tool_timings(
        "thread-1",
        {"call-1": {"started_at": "2026-09-04T02:00:00Z", "completed_at": "2026-09-04T02:00:03Z"}},
    )

    assert message.extra_metadata["tool_timings"]["call-1"]["completed_at"] == "2026-09-04T02:00:03Z"
    assert flagged == [(message, "extra_metadata")]
    assert repo.db.committed is True
