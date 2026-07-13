from yuxi.agents.middlewares.attachment import _build_attachment_prompt
from yuxi.services.conversation_service import _build_state_uploads


def test_attachment_prompt_routes_spreadsheet_to_execute() -> None:
    prompt = _build_attachment_prompt(
        [
            {
                "file_name": "report.xlsx",
                "path": "/home/gem/user-data/uploads/report.xlsx",
                "original_path": "/home/gem/user-data/uploads/report.xlsx",
            }
        ]
    )

    assert prompt is not None
    assert "execute" in prompt
    assert "pandas/openpyxl" in prompt
    assert "不要对 Excel 原始文件调用 `read_file`" in prompt


def test_attachment_prompt_keeps_original_spreadsheet_and_markdown_preview_paths() -> None:
    prompt = _build_attachment_prompt(
        [
            {
                "file_name": "report.xlsx",
                "path": "/home/gem/user-data/uploads/attachments/report.md",
                "original_path": "/home/gem/user-data/uploads/report.xlsx",
            }
        ]
    )

    assert prompt is not None
    assert "原始文件: /home/gem/user-data/uploads/report.xlsx" in prompt
    assert "文本预览: /home/gem/user-data/uploads/attachments/report.md" in prompt


def test_state_uploads_preserve_original_path_for_runtime_analysis() -> None:
    uploads = _build_state_uploads(
        [
            {
                "file_id": "file-1",
                "file_name": "report.xlsx",
                "path": "/home/gem/user-data/uploads/attachments/report.md",
                "original_path": "/home/gem/user-data/uploads/report.xlsx",
            }
        ]
    )

    assert uploads[0]["original_path"] == "/home/gem/user-data/uploads/report.xlsx"
