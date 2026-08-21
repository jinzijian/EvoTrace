import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from evotrace.importers import import_claude, import_codex
from evotrace.store import Store
from evotrace.util import load_jsonl


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


class ImporterTests(unittest.TestCase):
    def test_imports_codex_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "codex.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "payload": {"id": "codex-123", "cwd": str(root), "git": {}},
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "payload": {"type": "user_message", "message": "Fix addition"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": "python -m pytest -q"}),
                            "call_id": "call-1",
                        },
                    },
                ],
            )
            session_dir, trajectory = import_codex(source, Store(root / "store"))
            events = list(load_jsonl(session_dir / "events.jsonl"))
            self.assertEqual(trajectory["task"]["text"], "Fix addition")
            self.assertEqual([event["kind"] for event in events], ["message.user", "tool.call"])
            self.assertEqual(events[1]["data"]["arguments"]["cmd"], "python -m pytest -q")

    def test_imports_claude_tool_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "claude.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "user",
                        "sessionId": "claude-123",
                        "cwd": str(root),
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"role": "user", "content": "Fix addition"},
                    },
                    {
                        "type": "assistant",
                        "sessionId": "claude-123",
                        "cwd": str(root),
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "I will run tests."},
                                {
                                    "type": "tool_use",
                                    "id": "tool-1",
                                    "name": "Bash",
                                    "input": {"command": "python -m pytest -q"},
                                },
                            ],
                        },
                    },
                ],
            )
            session_dir, trajectory = import_claude(source, Store(root / "store"))
            events = list(load_jsonl(session_dir / "events.jsonl"))
            self.assertEqual(trajectory["task"]["text"], "Fix addition")
            self.assertIn("tool.call", [event["kind"] for event in events])

    def test_claude_import_skips_client_metadata_and_recovers_reference_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            source = root / "claude.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "user",
                        "sessionId": "claude-recovery",
                        "cwd": str(repo),
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {
                            "role": "user",
                            "content": "<ide_opened_file>app.py</ide_opened_file>",
                        },
                    },
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "<ide_selection>print('metadata')</ide_selection>",
                        },
                    },
                    {
                        "type": "user",
                        "sessionId": "claude-recovery",
                        "cwd": str(repo),
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {"role": "user", "content": "Fix the value."},
                    },
                    {
                        "type": "assistant",
                        "sessionId": "claude-recovery",
                        "cwd": str(repo),
                        "timestamp": "2026-01-01T00:00:02Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "edit-1",
                                    "name": "Edit",
                                    "input": {
                                        "file_path": str(repo / "app.py"),
                                        "old_string": "value = 1",
                                        "new_string": "value = 2",
                                        "replace_all": False,
                                    },
                                }
                            ],
                        },
                    },
                ],
            )
            session_dir, trajectory = import_claude(source, Store(root / "store"))
            patch = session_dir / "patches" / "final.patch"
            self.assertEqual(trajectory["task"]["text"], "Fix the value.")
            self.assertGreater(patch.stat().st_size, 0)
            self.assertEqual(
                trajectory["snapshots"]["final"]["recovery"]["status"],
                "recovered",
            )

    def test_codex_patch_events_recover_reference_and_nested_exec_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            source = root / "codex.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "payload": {
                            "id": "codex-patch",
                            "cwd": str(repo),
                            "git": {"commit_hash": commit},
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "payload": {"type": "user_message", "message": "Fix the value."},
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "payload": {
                            "type": "patch_apply_end",
                            "success": True,
                            "call_id": "patch-1",
                            "changes": {
                                "app.py": {
                                    "type": "update",
                                    "unified_diff": "@@ -1 +1 @@\n-value = 1\n+value = 2\n",
                                    "move_path": None,
                                }
                            },
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-01-01T00:00:03Z",
                        "payload": {
                            "type": "custom_tool_call",
                            "name": "exec",
                            "call_id": "exec-1",
                            "input": 'await tools.exec_command({cmd: "python -m pytest -q"})',
                        },
                    },
                ],
            )
            session_dir, trajectory = import_codex(source, Store(root / "store"))
            events = list(load_jsonl(session_dir / "events.jsonl"))
            self.assertGreater((session_dir / "patches" / "final.patch").stat().st_size, 0)
            self.assertEqual(
                trajectory["snapshots"]["final"]["recovery"]["status"],
                "recovered",
            )
            self.assertTrue(
                any(
                    event.get("kind") == "tool.call"
                    and (event.get("data") or {}).get("name") == "exec_command"
                    for event in events
                )
            )


if __name__ == "__main__":
    unittest.main()
