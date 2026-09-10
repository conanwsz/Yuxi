from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yuxi.agents.middlewares.model_content_sanitizer import (
    ModelContentSanitizerMiddleware,
    sanitize_unsupported_binary_tool_messages,
)


def _make_poison_block() -> dict:
    """A block as produced by finalize_tool_call_chunk when args JSON is malformed."""
    return {
        "type": "invalid_tool_call",
        "id": "call_1",
        "name": "analyze",
        "args": '{"path": "x.xlsx"',
        "error": "Failed to parse tool call arguments as JSON",
    }


def test_sanitizer_strips_invalid_tool_call_blocks_from_mixed_ai_message() -> None:
    poison = AIMessage(
        content=[{"type": "text", "text": "部分输出"}, _make_poison_block()],
        invalid_tool_calls=[_make_poison_block()],
    )
    healthy = AIMessage(content=[{"type": "text", "text": "正常输出"}])

    sanitized = sanitize_unsupported_binary_tool_messages([HumanMessage("分析表格"), poison, healthy])

    assert sanitized[0].content == "分析表格"
    assert sanitized[1] is not poison
    assert sanitized[1].content == [{"type": "text", "text": "部分输出"}]
    assert sanitized[1].invalid_tool_calls == []
    assert sanitized[2] is healthy


def test_sanitizer_strips_pure_poison_ai_message_content() -> None:
    poison = AIMessage(content=[_make_poison_block()])

    [sanitized] = sanitize_unsupported_binary_tool_messages([poison])

    assert isinstance(sanitized.content, list)
    assert sanitized.content == []
    assert sanitized.invalid_tool_calls == []


def test_sanitizer_passes_through_healthy_ai_message_untouched() -> None:
    healthy = AIMessage(
        content=[{"type": "text", "text": "你好"}, {"type": "text", "text": "分析完成"}],
        tool_calls=[
            {
                "name": "analyze",
                "args": {"path": "x.xlsx"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    assert sanitize_unsupported_binary_tool_messages([healthy]) == [healthy]


def test_sanitizer_ignores_ai_message_with_string_content() -> None:
    plain = AIMessage(content="普通文本回复")

    assert sanitize_unsupported_binary_tool_messages([plain]) == [plain]


def test_sanitizer_replaces_historical_file_tool_block_with_text_guidance() -> None:
    original = ToolMessage(
        content_blocks=[
            {
                "type": "file",
                "base64": "UEsDBA==",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ],
        name="read_file",
        tool_call_id="call-file",
        additional_kwargs={"read_file_path": "/home/gem/user-data/uploads/report.xlsx"},
    )

    sanitized = sanitize_unsupported_binary_tool_messages([HumanMessage("分析表格"), original])

    assert sanitized[0].content == "分析表格"
    assert sanitized[1] is not original
    assert isinstance(sanitized[1].content, str)
    assert "/home/gem/user-data/uploads/report.xlsx" in sanitized[1].content
    assert "execute" in sanitized[1].content
    assert original.content_blocks[0]["type"] == "file"


def test_sanitizer_preserves_image_tool_blocks() -> None:
    image = ToolMessage(
        content_blocks=[{"type": "image", "base64": "iVBORw0KGgo=", "mime_type": "image/png"}],
        name="read_file",
        tool_call_id="call-image",
    )

    assert sanitize_unsupported_binary_tool_messages([image]) == [image]


def test_sanitizer_handles_provider_document_shape() -> None:
    document = ToolMessage(
        content=[
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="},
            }
        ],
        name="read_file",
        tool_call_id="call-document",
    )

    [sanitized] = sanitize_unsupported_binary_tool_messages([document])

    assert isinstance(sanitized.content, str)
    assert sanitized.tool_call_id == "call-document"
    assert document.content[0]["type"] == "document"


@pytest.mark.asyncio
async def test_middleware_passes_sanitized_history_to_model_handler() -> None:
    binary = ToolMessage(
        content_blocks=[{"type": "file", "base64": "UEsDBA==", "mime_type": "application/zip"}],
        name="read_file",
        tool_call_id="call-file",
    )
    poison = AIMessage(content=[{"type": "text", "text": "思考中"}, _make_poison_block()])

    class FakeRequest:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kwargs):
            return FakeRequest(kwargs.get("messages", self.messages))

    captured = SimpleNamespace(messages=None)

    async def handler(request):
        captured.messages = request.messages
        return "ok"

    result = await ModelContentSanitizerMiddleware().awrap_model_call(FakeRequest([binary, poison]), handler)

    assert result == "ok"
    assert isinstance(captured.messages[0].content, str)
    assert captured.messages[0].tool_call_id == "call-file"
    assert captured.messages[1].content == [{"type": "text", "text": "思考中"}]
    assert captured.messages[1].invalid_tool_calls == []
