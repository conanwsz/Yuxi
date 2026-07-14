"""Remove unsupported binary tool blocks before provider serialization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, ToolMessage

from yuxi.utils import logger

_SANITIZED_MARKER = "yuxi_unsupported_binary_sanitized"


def sanitize_unsupported_binary_tool_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Replace file/document tool blocks with provider-safe text guidance."""
    sanitized: list[AnyMessage] = []
    replaced = 0
    for message in messages:
        if not isinstance(message, ToolMessage) or not any(
            isinstance(block, dict) and block.get("type") == "file" for block in message.content_blocks
        ):
            sanitized.append(message)
            continue

        additional_kwargs = dict(message.additional_kwargs or {})
        path = additional_kwargs.get("read_file_path")
        path_text = f" Path: {path}." if isinstance(path, str) and path else ""
        additional_kwargs[_SANITIZED_MARKER] = True
        sanitized.append(
            message.model_copy(
                update={
                    "content": (
                        "Binary tool content was omitted because this model endpoint does not accept file/document "
                        f"content blocks.{path_text} Use execute or a dedicated parser and return text or "
                        "artifact paths."
                    ),
                    "additional_kwargs": additional_kwargs,
                }
            )
        )
        replaced += 1

    if replaced:
        logger.warning(f"Sanitized {replaced} unsupported binary tool message(s) before model call")
    return sanitized


class ModelContentSanitizerMiddleware(AgentMiddleware):
    """Keep historical binary ToolMessages out of all provider payloads."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(request.override(messages=sanitize_unsupported_binary_tool_messages(request.messages)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(request.override(messages=sanitize_unsupported_binary_tool_messages(request.messages)))


sanitize_model_content = ModelContentSanitizerMiddleware()
