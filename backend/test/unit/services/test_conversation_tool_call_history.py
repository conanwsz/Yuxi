from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services.conversation_service import serialize_history_tool_call

pytestmark = pytest.mark.unit


def test_serialize_history_tool_call_includes_timings() -> None:
    tool_call = SimpleNamespace(
        id=12,
        langgraph_tool_call_id="call-1",
        tool_name="grep",
        tool_input={"pattern": "foo"},
        tool_output="ok",
        status="success",
        error_message=None,
    )

    payload = serialize_history_tool_call(
        tool_call,
        {
            "tool_timings": {
                "call-1": {
                    "started_at": "2026-09-04T02:00:00Z",
                    "completed_at": "2026-09-04T02:00:01.200Z",
                }
            }
        },
    )

    assert payload["id"] == "call-1"
    assert payload["name"] == "grep"
    assert payload["started_at"] == "2026-09-04T02:00:00Z"
    assert payload["completed_at"] == "2026-09-04T02:00:01.200Z"


def test_serialize_history_tool_call_omits_missing_timings() -> None:
    tool_call = SimpleNamespace(
        id=12,
        langgraph_tool_call_id="call-1",
        tool_name="ls",
        tool_input={},
        tool_output=None,
        status="pending",
        error_message=None,
    )

    payload = serialize_history_tool_call(tool_call, {})
    assert "started_at" not in payload
    assert "completed_at" not in payload
