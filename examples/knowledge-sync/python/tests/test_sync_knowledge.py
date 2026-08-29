"""知识同步示例中不依赖网络的关键行为测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "sync_knowledge.py"
SPEC = importlib.util.spec_from_file_location("sync_knowledge", MODULE_PATH)
assert SPEC and SPEC.loader
sync_knowledge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_knowledge
SPEC.loader.exec_module(sync_knowledge)


class SyncKnowledgeTest(unittest.TestCase):
    def test_discover_files_filters_and_uses_posix_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "manual.md").write_text("knowledge", encoding="utf-8")
            (root / "ignored.exe").write_bytes(b"ignored")

            files = sync_knowledge.discover_files(root)

            self.assertEqual(list(files), ["nested/manual.md"])

    def test_calculate_sha256_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manual.txt"
            target.write_bytes(b"super-chuchu")

            digest = sync_knowledge.calculate_sha256(target)

            self.assertEqual(digest, "3f379d480da08ab6757c22c3962c67228313dcf06bf55d9aaa572823ac6b7ea6")

    def test_extract_file_id_requires_one_successful_item(self):
        task = {
            "result": {
                "items": [
                    {"file_id": "file-new", "status": "indexed"},
                    {"file_id": "file-failed", "status": "failed"},
                ]
            }
        }

        self.assertEqual(sync_knowledge.extract_file_id(task), "file-new")

    def test_save_and_load_state_rejects_another_knowledge_base(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = {"version": 1, "kb_id": "kb-one", "files": {}}
            sync_knowledge.save_state(state_path, state)

            loaded = sync_knowledge.load_state(state_path, "kb-one")
            self.assertEqual(loaded, state)
            with self.assertRaisesRegex(ValueError, "kb-one"):
                sync_knowledge.load_state(state_path, "kb-two")

            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)

    def test_update_waits_for_new_file_before_deleting_old_version(self):
        class FakeClient:
            def __init__(self):
                self.events = []

            def upload_file(self, kb_id, file_path):
                self.events.append(("upload", kb_id, file_path.name))
                return sync_knowledge.UploadedFile(
                    "minio://new",
                    sync_knowledge.calculate_sha256(file_path),
                    file_path.stat().st_size,
                )

            def submit_ingest(self, kb_id, uploaded):
                self.events.append(("submit", kb_id, uploaded.file_path))
                return "task-new"

            def wait_task(self, task_id, poll_interval, total_timeout):
                self.events.append(("wait", task_id))
                return {"result": {"items": [{"file_id": "file-new", "status": "indexed"}]}}

            def query(self, kb_id, question):
                self.events.append(("query", kb_id, question))
                return {"status": "success", "result": [{"content": "new content"}]}

            def delete_document(self, kb_id, file_id):
                self.events.append(("delete", kb_id, file_id))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "manual.md").write_text("new content", encoding="utf-8")
            state_path = root / "state.json"
            sync_knowledge.save_state(
                state_path,
                {
                    "version": 1,
                    "kb_id": "kb-one",
                    "files": {
                        "manual.md": {
                            "content_hash": "old-hash",
                            "file_id": "file-old",
                            "synced_at": "old",
                        }
                    },
                },
            )
            client = FakeClient()

            summary = sync_knowledge.sync_directory(
                client,
                "kb-one",
                source,
                state_path,
                poll_interval=0,
                task_timeout=1,
                verify_query="new content",
                delete_removed=False,
                dry_run=False,
            )

            self.assertEqual(
                [event[0] for event in client.events],
                ["upload", "submit", "wait", "query", "delete"],
            )
            self.assertEqual(client.events[-1], ("delete", "kb-one", "file-old"))
            self.assertEqual(summary["updated"], 1)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["files"]["manual.md"]["file_id"], "file-new")


if __name__ == "__main__":
    unittest.main()
