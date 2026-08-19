import json
import tempfile
import unittest
from pathlib import Path

from scaleverifier.importers import import_claude, import_codex
from scaleverifier.store import Store
from scaleverifier.util import load_jsonl


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


if __name__ == "__main__":
    unittest.main()
