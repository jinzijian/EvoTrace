import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scaleverifier.history import discover_history, import_discovered_history
from scaleverifier.miner import mine_store
from scaleverifier.store import Store
from scaleverifier.util import load_jsonl, write_json, write_jsonl


def make_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )


class HistoryTests(unittest.TestCase):
    def test_discovers_configured_codex_and_claude_homes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            claude = root / "claude"
            make_jsonl(codex / "sessions" / "2026" / "session.jsonl", [{}])
            make_jsonl(codex / "history.jsonl", [{}])
            make_jsonl(claude / "projects" / "repo" / "session.jsonl", [{}])
            make_jsonl(
                claude / "projects" / "repo" / "subagents" / "child.jsonl",
                [{}],
            )
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex), "CLAUDE_CONFIG_DIR": str(claude)},
            ):
                files = discover_history()
            self.assertEqual(len(files), 3)
            self.assertEqual(
                {(item.source, item.kind) for item in files},
                {("codex", "session"), ("codex", "prompt_history"), ("claude", "session")},
            )

    def test_rich_codex_session_wins_over_prompt_history_and_import_is_incremental(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            rich = codex / "sessions" / "2026" / "session.jsonl"
            make_jsonl(
                rich,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "payload": {"id": "same-session", "cwd": str(root)},
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "payload": {"type": "user_message", "message": "Fix the parser"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": "python -m pytest -q"}),
                        },
                    },
                ],
            )
            make_jsonl(
                codex / "history.jsonl",
                [{"session_id": "same-session", "text": "Fix the parser", "ts": 1}],
            )
            store = Store(root / "store")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex)}):
                first = import_discovered_history(store, source="codex")
                second = import_discovered_history(store, source="codex")
            session_dir, trajectory = store.load_session("codex-same-session")
            events = list(load_jsonl(session_dir / "events.jsonl"))
            self.assertEqual(len(events), 2)
            self.assertEqual(trajectory["source"]["path"], str(rich))
            self.assertEqual(first.discovered_files, 2)
            self.assertEqual(second.processed_files, 0)
            self.assertEqual(second.skipped_unchanged_files, 2)


class MiningTests(unittest.TestCase):
    def test_classifies_correction_recovery_preference_and_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
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

            store = Store(root / "store")
            store.initialize()
            session = store.sessions / "codex-example"
            session.mkdir()
            write_json(
                session / "trajectory.json",
                {
                    "session_id": "codex-example",
                    "created_at": "2026-01-01T00:00:00Z",
                    "source": {"agent": "codex"},
                    "task": {
                        "text": "Fix the parser behavior and preserve compatibility with all existing clients."
                    },
                    "repository": {
                        "root": str(repo),
                        "base_commit": commit,
                        "base_commit_source": "session",
                        "reconstruction_confidence": "high",
                    },
                },
            )
            write_jsonl(
                session / "events.jsonl",
                [
                    {"kind": "message.user", "data": {"text": "Fix parser behavior"}},
                    {"kind": "message.assistant", "data": {"text": "Working on it"}},
                    {"kind": "tool.call", "data": {"name": "apply_patch", "arguments": {}}},
                    {
                        "kind": "message.user",
                        "data": {"text": "No, that is wrong; keep backward compatibility."},
                    },
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "python -m pytest -q"},
                        },
                    },
                    {"kind": "tool.result", "data": {"output": "1 failed"}},
                    {"kind": "tool.call", "data": {"name": "apply_patch", "arguments": {}}},
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "python -m pytest -q"},
                        },
                    },
                    {"kind": "tool.result", "data": {"output": "12 passed"}},
                ],
            )
            summary = mine_store(store)
            candidate = summary.candidates[0]
            self.assertEqual(summary.total, 1)
            self.assertEqual(candidate["curator"]["model_used"], False)
            self.assertTrue(
                {
                    "useful",
                    "human_corrected",
                    "execution_verifiable",
                    "preference_candidate",
                    "recovery_trajectory",
                }.issubset(candidate["labels"])
            )


if __name__ == "__main__":
    unittest.main()
