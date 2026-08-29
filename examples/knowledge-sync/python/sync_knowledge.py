#!/usr/bin/env python3
"""将本地目录安全同步到超级楚楚知识库的最小标准库示例。"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".docx",
    ".html",
    ".htm",
    ".json",
    ".csv",
    ".xls",
    ".xlsx",
    ".pdf",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".zip",
}
TERMINAL_TASK_STATUSES = {"success", "failed", "cancelled"}


class ApiError(RuntimeError):
    """保留 HTTP 状态和响应正文，便于调用方决定是否重试。"""

    def __init__(self, status: int | None, message: str, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class UploadedFile:
    """上传阶段返回、提交知识处理任务所需的字段。"""

    file_path: str
    content_hash: str
    size: int


class YuxiKnowledgeClient:
    """封装知识同步所需的最小超级楚楚 HTTP API。"""

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("YUXI_BASE_URL 必须以 http:// 或 https:// 开头")
        if not api_key.startswith("yxkey_"):
            raise ValueError("YUXI_API_KEY 必须是 yxkey_ 开头的 API Key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type

        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read()
            parsed = _decode_json_or_text(raw)
            detail = parsed.get("detail") if isinstance(parsed, dict) else parsed
            raise ApiError(error.code, f"{method} {path} 失败：HTTP {error.code}，{detail}", parsed) from error
        except URLError as error:
            raise ApiError(None, f"无法连接超级楚楚：{error.reason}") from error

        return _decode_json_or_text(raw)

    def list_databases(self) -> list[dict[str, Any]]:
        """列出当前集成身份可访问的知识库。"""
        payload = self._request("GET", "/api/knowledge/databases")
        return list(payload.get("databases") or [])

    def resolve_or_create_database(
        self,
        kb_id: str | None,
        create_name: str | None,
        embedding_model_spec: str | None,
    ) -> str:
        """优先使用固定 ID，否则按名称查找并在缺失时创建。"""
        databases = self.list_databases()
        if kb_id:
            if not any(item.get("kb_id") == kb_id for item in databases):
                raise ApiError(404, f"当前身份无法访问知识库 {kb_id}")
            return kb_id

        if not create_name:
            raise ValueError("未设置 YUXI_KB_ID 时必须传 --create-kb-name")
        matched = [item for item in databases if item.get("name") == create_name]
        if len(matched) == 1:
            return str(matched[0]["kb_id"])
        if len(matched) > 1:
            raise ValueError(f"存在多个同名知识库：{create_name}，请显式设置 YUXI_KB_ID")
        if not embedding_model_spec:
            raise ValueError("自动创建知识库必须传 --embedding-model-spec")

        payload = self._request(
            "POST",
            "/api/knowledge/databases",
            json_body={
                "database_name": create_name,
                "description": "由第三方知识同步示例维护",
                "embedding_model_spec": embedding_model_spec,
                "kb_type": "milvus",
            },
        )
        return str(payload["kb_id"])

    def upload_file(self, kb_id: str, file_path: Path) -> UploadedFile:
        """用 multipart/form-data 上传一个原始文件。"""
        boundary = f"----super-chuchu-{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = _multipart_file_body(boundary, file_path, content_type)
        query = urlencode({"kb_id": kb_id})
        payload = self._request(
            "POST",
            f"/api/knowledge/files/upload?{query}",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return UploadedFile(
            file_path=str(payload["file_path"]),
            content_hash=str(payload["content_hash"]),
            size=int(payload["size"]),
        )

    def submit_ingest(self, kb_id: str, uploaded: UploadedFile) -> str:
        """提交添加、解析和自动向量入库任务。"""
        payload = self._request(
            "POST",
            f"/api/knowledge/databases/{quote(kb_id, safe='')}/documents",
            json_body={
                "items": [uploaded.file_path],
                "params": {
                    "content_type": "file",
                    "content_hashes": {uploaded.file_path: uploaded.content_hash},
                    "file_sizes": {uploaded.file_path: uploaded.size},
                    "auto_index": True,
                },
            },
        )
        return str(payload["task_id"])

    def wait_task(self, task_id: str, poll_interval: float, total_timeout: float) -> dict[str, Any]:
        """轮询后台任务，并在失败、取消或超时时显式终止。"""
        deadline = time.monotonic() + total_timeout
        encoded_task_id = quote(task_id, safe="")
        while True:
            payload = self._request("GET", f"/api/tasks/{encoded_task_id}")
            task = payload["task"]
            status = task.get("status")
            progress = float(task.get("progress") or 0)
            message = task.get("message") or ""
            print(f"    task={task_id} status={status} progress={progress:.0f}% {message}")

            if status in TERMINAL_TASK_STATUSES:
                if status != "success":
                    raise ApiError(None, f"知识处理任务 {status}：{task.get('error') or message}", task)
                return task
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待任务 {task_id} 超过 {total_timeout:.0f} 秒")
            time.sleep(poll_interval)

    def delete_document(self, kb_id: str, file_id: str) -> None:
        """删除一个已知 file_id 的知识文档。"""
        self._request(
            "DELETE",
            f"/api/knowledge/databases/{quote(kb_id, safe='')}/documents/{quote(file_id, safe='')}",
        )

    def query(self, kb_id: str, question: str) -> Any:
        """执行一次知识检索验证。"""
        return self._request(
            "POST",
            f"/api/knowledge/databases/{quote(kb_id, safe='')}/query",
            json_body={"query": question, "meta": {}},
        )


def _decode_json_or_text(raw: bytes) -> Any:
    """优先解析 JSON，保留非 JSON 响应文本用于报错。"""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _multipart_file_body(boundary: str, file_path: Path, content_type: str) -> bytes:
    """构造单文件 multipart 请求体；示例上限与服务端 100 MB 限制一致。"""
    filename = file_path.name.replace('"', "_")
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    return prefix + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")


def calculate_sha256(file_path: Path) -> str:
    """流式计算文件 SHA-256，避免为本地去重一次性读入大文件。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_files(source_dir: Path) -> dict[str, Path]:
    """按稳定相对路径返回支持同步的普通文件。"""
    if not source_dir.is_dir():
        raise ValueError(f"来源目录不存在：{source_dir}")
    return {
        path.relative_to(source_dir).as_posix(): path
        for path in sorted(source_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    }


def extract_file_id(task: dict[str, Any]) -> str:
    """从知识处理任务结果中提取唯一的新文档 file_id。"""
    result = task.get("result") or {}
    items = result.get("items") or []
    file_ids = {
        str(item["file_id"])
        for item in items
        if isinstance(item, dict) and item.get("file_id") and item.get("status") != "failed"
    }
    if len(file_ids) != 1:
        raise ValueError(f"任务结果没有返回唯一 file_id：{sorted(file_ids)}")
    return file_ids.pop()


def load_state(state_path: Path, kb_id: str) -> dict[str, Any]:
    """读取同步状态，并阻止误把一个状态文件用于不同知识库。"""
    if not state_path.exists():
        return {"version": 1, "kb_id": kb_id, "files": {}}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("version") != 1:
        raise ValueError(f"不支持的状态文件版本：{state.get('version')}")
    if state.get("kb_id") != kb_id:
        raise ValueError(f"状态文件属于知识库 {state.get('kb_id')}，当前目标是 {kb_id}")
    if not isinstance(state.get("files"), dict):
        raise ValueError("状态文件 files 必须是对象")
    return state


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    """同目录临时文件替换，避免中断时写出半个 JSON。"""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(state_path)


def cleanup_stale_versions(
    client: YuxiKnowledgeClient,
    kb_id: str,
    entry: dict[str, Any],
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """重试删除更新过程中遗留的旧版本，返回成功数和失败数。"""
    stale_ids = [str(file_id) for file_id in entry.get("stale_file_ids") or [] if file_id]
    remaining = []
    deleted = 0
    failed = 0
    for file_id in stale_ids:
        if dry_run:
            print(f"CLEAN  待删除旧版本 {file_id}")
            remaining.append(file_id)
            continue
        try:
            client.delete_document(kb_id, file_id)
            deleted += 1
        except ApiError as error:
            print(f"ERROR  清理旧版本 {file_id}: {error}", file=sys.stderr)
            remaining.append(file_id)
            failed += 1

    if remaining:
        entry["stale_file_ids"] = remaining
    else:
        entry.pop("stale_file_ids", None)
    return deleted, failed


def sync_directory(
    client: YuxiKnowledgeClient,
    kb_id: str,
    source_dir: Path,
    state_path: Path,
    *,
    poll_interval: float,
    task_timeout: float,
    verify_query: str | None,
    delete_removed: bool,
    dry_run: bool,
) -> dict[str, int]:
    """执行目录级增量同步，并在每个成功变更后持久化状态。"""
    state = load_state(state_path, kb_id)
    known_files: dict[str, dict[str, Any]] = state["files"]
    discovered = discover_files(source_dir)
    summary = {"added": 0, "updated": 0, "skipped": 0, "deleted": 0, "cleaned": 0, "failed": 0}

    for relative_path, file_path in discovered.items():
        local_hash = calculate_sha256(file_path)
        previous = known_files.get(relative_path)
        if previous and previous.get("stale_file_ids"):
            cleaned, cleanup_failed = cleanup_stale_versions(client, kb_id, previous, dry_run=dry_run)
            summary["cleaned"] += cleaned
            summary["failed"] += cleanup_failed
            if not dry_run:
                save_state(state_path, state)
        if previous and previous.get("content_hash") == local_hash:
            print(f"SKIP   {relative_path}")
            summary["skipped"] += 1
            continue

        action = "UPDATE" if previous else "ADD"
        print(f"{action:<6} {relative_path}")
        if dry_run:
            summary["updated" if previous else "added"] += 1
            continue

        try:
            uploaded = client.upload_file(kb_id, file_path)
            if uploaded.content_hash != local_hash:
                raise ValueError("服务端 content_hash 与本地 SHA-256 不一致")
            task_id = client.submit_ingest(kb_id, uploaded)
            task = client.wait_task(task_id, poll_interval, task_timeout)
            new_file_id = extract_file_id(task)

            if verify_query:
                verification = client.query(kb_id, verify_query)
                if not isinstance(verification, dict) or verification.get("status") != "success":
                    client.delete_document(kb_id, new_file_id)
                    raise ValueError(f"检索验证失败：{verification}")
                if not verification.get("result"):
                    client.delete_document(kb_id, new_file_id)
                    raise ValueError("检索验证没有返回结果")

            stale_file_ids = list(previous.get("stale_file_ids") or []) if previous else []
            if previous and previous.get("file_id") and previous["file_id"] != new_file_id:
                stale_file_ids.append(str(previous["file_id"]))
            new_entry = {
                "content_hash": local_hash,
                "file_id": new_file_id,
                "synced_at": datetime.now(UTC).isoformat(),
            }
            if stale_file_ids:
                new_entry["stale_file_ids"] = sorted(set(stale_file_ids))
            known_files[relative_path] = new_entry
            save_state(state_path, state)

            cleaned, cleanup_failed = cleanup_stale_versions(client, kb_id, new_entry, dry_run=False)
            summary["cleaned"] += cleaned
            summary["failed"] += cleanup_failed
            save_state(state_path, state)
            summary["updated" if previous else "added"] += 1
        except (ApiError, OSError, TimeoutError, ValueError) as error:
            summary["failed"] += 1
            print(f"ERROR  {relative_path}: {error}", file=sys.stderr)

    removed = sorted(set(known_files) - set(discovered))
    for relative_path in removed:
        previous = known_files[relative_path]
        if not delete_removed:
            print(f"STALE  {relative_path}（未传 --delete-removed，保留远端文档）")
            continue
        print(f"DELETE {relative_path}")
        if dry_run:
            summary["deleted"] += 1
            continue
        try:
            client.delete_document(kb_id, str(previous["file_id"]))
            del known_files[relative_path]
            save_state(state_path, state)
            summary["deleted"] += 1
        except (ApiError, OSError, ValueError) as error:
            summary["failed"] += 1
            print(f"ERROR  {relative_path}: {error}", file=sys.stderr)

    return summary


def build_parser() -> argparse.ArgumentParser:
    """创建命令行解析器。"""
    parser = argparse.ArgumentParser(description="将本地目录增量同步到超级楚楚知识库")
    parser.add_argument("source_dir", type=Path, help="待同步文件目录")
    parser.add_argument("--state-file", type=Path, default=Path(".yuxi-sync-state.json"))
    parser.add_argument("--create-kb-name", help="没有 YUXI_KB_ID 时按此名称查找或创建知识库")
    parser.add_argument("--embedding-model-spec", help="自动创建知识库使用的 Embedding 模型标识")
    parser.add_argument("--verify-query", help="每个新版本成功后、删除旧版本前执行的检索验证问题")
    parser.add_argument("--delete-removed", action="store_true", help="删除来源目录中已消失的远端文档")
    parser.add_argument("--dry-run", action="store_true", help="只计算并显示同步计划，不发送写请求")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="任务轮询间隔秒数")
    parser.add_argument("--task-timeout", type=float, default=1800.0, help="单个处理任务总超时秒数")
    parser.add_argument("--http-timeout", type=float, default=120.0, help="单次 HTTP 请求超时秒数")
    return parser


def main() -> int:
    """读取环境、执行同步并以非零退出码暴露失败。"""
    args = build_parser().parse_args()
    base_url = os.environ.get("YUXI_BASE_URL", "").strip()
    api_key = os.environ.get("YUXI_API_KEY", "").strip()
    kb_id = os.environ.get("YUXI_KB_ID", "").strip() or None
    if not base_url or not api_key:
        print("必须设置 YUXI_BASE_URL 和 YUXI_API_KEY", file=sys.stderr)
        return 2

    try:
        client = YuxiKnowledgeClient(base_url, api_key, timeout=args.http_timeout)
        if args.dry_run and not kb_id:
            raise ValueError("--dry-run 必须设置 YUXI_KB_ID，避免自动创建知识库")
        resolved_kb_id = client.resolve_or_create_database(
            kb_id,
            args.create_kb_name,
            args.embedding_model_spec,
        )
        print(f"目标知识库：{resolved_kb_id}")
        summary = sync_directory(
            client,
            resolved_kb_id,
            args.source_dir.resolve(),
            args.state_file.resolve(),
            poll_interval=args.poll_interval,
            task_timeout=args.task_timeout,
            verify_query=args.verify_query,
            delete_removed=args.delete_removed,
            dry_run=args.dry_run,
        )
        print("同步汇总：" + json.dumps(summary, ensure_ascii=False))
        return 1 if summary["failed"] else 0
    except (ApiError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        print(f"同步终止：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
