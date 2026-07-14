"""Attachment prompt injection middleware.

Read uploaded file metadata from LangGraph state and inject readable paths
with file-type-specific handling instructions into the system prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from yuxi.utils import logger

ATTACHMENT_PROMPT_MARKER = "<!-- attachment_context -->"
_SPREADSHEET_EXTENSIONS = frozenset({".csv", ".ods", ".tsv", ".xls", ".xlsb", ".xlsm", ".xlsx"})


class AttachmentState(AgentState):
    """Extended state schema with uploaded files."""

    uploads: NotRequired[list[dict]]


def _build_attachment_prompt(uploads: Sequence[dict]) -> str | None:
    """Render uploads into a concise prompt block."""
    if not uploads:
        return None

    upload_infos: list[str] = []
    for upload in uploads:
        path = upload.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        file_name = upload.get("file_name", "未知文件")
        original_path = upload.get("original_path")
        source_path = original_path if isinstance(original_path, str) and original_path.strip() else path
        if PurePosixPath(source_path).suffix.lower() in _SPREADSHEET_EXTENSIONS:
            upload_infos.append(f"- {file_name}\n  - 原始文件: {source_path}")
            if path != source_path:
                upload_infos.append(f"  - 文本预览: {path}")
            upload_infos.append(
                "  - 处理方式: 不要对 Excel 原始文件调用 `read_file`；"
                "使用 `execute` 运行 Python，通过 pandas/openpyxl 读取和分析，产物写入 outputs。"
            )
            continue
        upload_infos.append(f"- {file_name}: {path}")

    if not upload_infos:
        return None

    lines = [
        "用户上传了以下文件：",
        "",
        *upload_infos,
        "",
        "文本和图片可以使用 `read_file`；其他二进制文件应使用对应解析工具或在 sandbox 中执行代码处理。",
    ]
    return "\n".join(lines)


class AttachmentMiddleware(AgentMiddleware[AttachmentState]):
    """Inject upload context from state.uploads into system prompt."""

    state_schema = AttachmentState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        uploads = request.state.get("uploads", [])
        logger.info(f"AttachmentMiddleware: found {len(uploads)} uploads in state")

        if uploads:
            attachment_prompt = _build_attachment_prompt(uploads)
            if attachment_prompt:
                logger.info("AttachmentMiddleware: injecting attachment prompt")
                existing_blocks = list(request.system_message.content_blocks) if request.system_message else []
                existing_text = "\n".join(
                    block.get("text", "")
                    for block in existing_blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                )

                if ATTACHMENT_PROMPT_MARKER in existing_text:
                    logger.info("AttachmentMiddleware: attachment prompt already injected, skip")
                    return await handler(request)

                merged_blocks = existing_blocks + [
                    {"type": "text", "text": f"{ATTACHMENT_PROMPT_MARKER}\n{attachment_prompt}"}
                ]
                request = request.override(system_message=SystemMessage(content=merged_blocks))

        return await handler(request)


save_attachments_to_fs = AttachmentMiddleware()
inject_attachment_context = save_attachments_to_fs
