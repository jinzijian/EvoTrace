import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evotrace.curator_agent import OpenAICuratorAgent, build_evidence_capsule, curate_store
from evotrace.errors import EvoTraceError
from evotrace.miner import mine_store
from evotrace.store import Store
from evotrace.util import read_json, write_json, write_jsonl


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE)


def make_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    return repo, commit


def make_session(store: Store, repo: Path, commit: str, session_id: str, task: str) -> Path:
    store.initialize()
    session = store.sessions / session_id
    session.mkdir()
    write_json(
        session / "trajectory.json",
        {
            "session_id": session_id,
            "created_at": "2026-01-01T00:00:00Z",
            "source": {"agent": "codex"},
            "task": {"text": task},
            "repository": {
                "root": str(repo),
                "base_commit": commit,
                "base_commit_source": "session",
                "reconstruction_confidence": "high",
            },
        },
    )
    (session / "patches").mkdir()
    (session / "patches" / "final.patch").write_text(
        "diff --git a/app.py b/app.py\n",
        encoding="utf-8",
    )
    return session


class CuratorSignalTests(unittest.TestCase):
    def test_exec_command_mutation_unlocks_execution_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, commit = make_repo(root)
            store = Store(root / "store")
            session = make_session(
                store,
                repo,
                commit,
                "codex-local-edit",
                "Fix the Python parser bug and run the full pytest suite.",
            )
            write_jsonl(
                session / "events.jsonl",
                [
                    {"kind": "message.user", "data": {"text": "Fix the parser bug"}},
                    {"kind": "message.assistant", "data": {"text": "I will patch it."}},
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {
                                "cmd": "python -c \"from pathlib import Path; "
                                "Path('app.py').write_text('value = 2\\n')\""
                            },
                        },
                    },
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "python -m pytest -q"},
                        },
                    },
                    {"kind": "tool.result", "data": {"output": "4 passed"}},
                ],
            )
            candidate = mine_store(store).candidates[0]
            self.assertEqual(candidate["signals"]["edit_calls"], 1)
            self.assertIn("execution_verifiable", candidate["labels"])

    def test_remote_mutation_does_not_claim_local_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, commit = make_repo(root)
            store = Store(root / "store")
            session = make_session(
                store,
                repo,
                commit,
                "codex-remote-edit",
                "Fix the Python service on the SSH server and run pytest.",
            )
            write_jsonl(
                session / "events.jsonl",
                [
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "ssh build-host 'cat > app.py'"},
                        },
                    },
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "python -m pytest -q"},
                        },
                    },
                ],
            )
            candidate = mine_store(store).candidates[0]
            self.assertEqual(candidate["signals"]["edit_calls"], 0)
            self.assertEqual(candidate["signals"]["remote_edit_calls"], 1)
            self.assertNotIn("execution_verifiable", candidate["labels"])

    def test_non_coding_sensitive_session_is_not_useful(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, commit = make_repo(root)
            store = Store(root / "store")
            session = make_session(
                store,
                repo,
                commit,
                "codex-private",
                "Help revise my immigration petition and personal legal documents.",
            )
            write_jsonl(
                session / "events.jsonl",
                [
                    {"kind": "message.assistant", "data": {"text": "Drafting."}},
                    {"kind": "tool.call", "data": {"name": "browser", "arguments": {}}},
                    {"kind": "tool.call", "data": {"name": "browser", "arguments": {}}},
                    {"kind": "tool.call", "data": {"name": "browser", "arguments": {}}},
                    {
                        "kind": "message.user",
                        "data": {"text": "This is wrong; revise the legal argument."},
                    },
                ],
            )
            candidate = mine_store(store).candidates[0]
            self.assertNotIn("useful", candidate["labels"])
            self.assertIn("non_coding_or_personal", candidate["labels"])
            self.assertIn("sensitive_content", candidate["labels"])
            self.assertEqual(candidate["signals"]["cloud_review"], "local_only")


class OpenAICuratorTests(unittest.TestCase):
    def test_curator_uses_sanitized_capsule_and_persists_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, commit = make_repo(root)
            store = Store(root / "store")
            session = make_session(
                store,
                repo,
                commit,
                "codex-cloud-review",
                "Fix the parser for owner@example.com using sk-abcdefghijklmnopqrstuvwxyz.",
            )
            write_jsonl(
                session / "events.jsonl",
                [
                    {"kind": "message.user", "data": {"text": str(Path.home())}},
                    {"kind": "message.assistant", "data": {"text": "Working."}},
                    {"kind": "tool.call", "data": {"name": "apply_patch", "arguments": {}}},
                    {
                        "kind": "tool.call",
                        "data": {
                            "name": "exec_command",
                            "arguments": {"cmd": "python -m pytest -q"},
                        },
                    },
                ],
            )
            mine_store(store)

            review = {
                "valuable": True,
                "coding_relevance": "high",
                "privacy_risk": "low",
                "disposition": "keep",
                "asset_types": ["sft", "eval", "execution_reward"],
                "execution_readiness": "ready",
                "preference_readiness": "not_applicable",
                "task_summary": "Fix a parser and verify it.",
                "rationale": "The task has a local edit, a replayable base, and tests.",
                "evidence": ["Local edit observed", "Pytest command recovered"],
                "missing_requirements": [],
            }

            def fake_transport(payload):
                serialized = json.dumps(payload, ensure_ascii=False)
                self.assertEqual(payload["model"], "gpt-5.6-sol")
                self.assertFalse(payload["store"])
                self.assertNotIn("owner@example.com", serialized)
                self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", serialized)
                self.assertNotIn(str(Path.home()), serialized)
                return {
                    "id": "resp_test",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(review)}],
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                }

            agent = OpenAICuratorAgent(transport=fake_transport)
            summary = curate_store(
                store,
                agent=agent,
                allow_upload=True,
                source="codex",
                limit=1,
            )
            self.assertEqual(summary.reviewed, 1)
            self.assertEqual(summary.kept, 1)
            candidate = read_json(store.candidates / "codex-cloud-review.json")
            self.assertEqual(candidate["model_review"]["model"], "gpt-5.6-sol")
            self.assertEqual(candidate["model_review"]["review"]["disposition"], "keep")

    def test_cloud_curation_requires_explicit_upload_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = OpenAICuratorAgent(transport=lambda payload: {})
            with self.assertRaisesRegex(EvoTraceError, "--allow-upload"):
                curate_store(Store(Path(temporary) / "store"), agent=agent, allow_upload=False)

    def test_openai_agent_requires_environment_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(EvoTraceError, "OPENAI_API_KEY"):
                OpenAICuratorAgent()

    def test_capsule_excludes_raw_tool_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, commit = make_repo(root)
            store = Store(root / "store")
            session = make_session(
                store, repo, commit, "codex-capsule", "Fix the Python parser and tests."
            )
            write_jsonl(
                session / "events.jsonl",
                [{"kind": "tool.result", "data": {"output": "SECRET_SOURCE_CONTENT"}}],
            )
            candidate = mine_store(store).candidates[0]
            capsule = build_evidence_capsule(session, read_json(session / "trajectory.json"), candidate)
            self.assertNotIn("SECRET_SOURCE_CONTENT", json.dumps(capsule))
