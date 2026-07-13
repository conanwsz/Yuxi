from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from yuxi.agents.middlewares.model_content_sanitizer import (
    ModelContentSanitizerMiddleware,
    sanitize_unsupported_binary_tool_messages,
)


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

    class FakeRequest:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kwargs):
            return FakeRequest(kwargs.get("messages", self.messages))

    captured = SimpleNamespace(messages=None)

    async def handler(request):
        captured.messages = request.messages
        return "ok"

    result = await ModelContentSanitizerMiddleware().awrap_model_call(FakeRequest([binary]), handler)

    assert result == "ok"
    assert isinstance(captured.messages[0].content, str)
    assert captured.messages[0].tool_call_id == "call-file"
