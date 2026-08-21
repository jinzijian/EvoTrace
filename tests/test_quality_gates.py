import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evotrace.builder import build_mined_candidates
from evotrace.catalog import readiness_status, visible_candidates
from evotrace.cli import _codex_integration_status
from evotrace.compiler import infer_environment
from evotrace.importers.codex import import_codex
from evotrace.quality import bundle_manifest_is_runnable, task_is_meaningful
from evotrace.store import Store
from evotrace.util import write_json, write_jsonl


class QualityGateTests(unittest.TestCase):
    def test_placeholder_task_is_not_meaningful(self):
        placeholder = """# Files pasted by the user:

Pasted text contains the user's request.

## My request:
"""
        self.assertFalse(task_is_meaningful(placeholder))
        self.assertTrue(task_is_meaningful("Fix retry ordering without breaking old clients."))

    def test_explicit_build_rejects_low_confidence_placeholder(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "store")
            store.initialize()
            session_id = "codex-low-confidence"
            session = store.sessions / session_id
            session.mkdir()
            write_json(
                session / "trajectory.json",
                {
                    "session_id": session_id,
                    "source": {"kind": "history_import", "agent": "codex"},
                    "task": {
                        "text": "Pasted text contains the user's request.\n\n## My request:"
                    },
                    "repository": {
                        "root": temporary,
                        "base_commit": "abc123",
                        "reconstruction_confidence": "low",
                    },
                    "verification": {"commands": ["python -m pytest -q"]},
                },
            )
            write_json(
                store.candidates / f"{session_id}.json",
                {
                    "session_id": session_id,
                    "labels": ["useful", "execution_verifiable"],
                    "signals": {
                        "repository_available": True,
                        "reconstruction_confidence": "low",
                        "reference_patch_available": True,
                        "verification_commands": ["python -m pytest -q"],
                    },
                },
            )
            result = build_mined_candidates(store, session_id=session_id)
            self.assertEqual(result[0].status, "skipped")
            self.assertIn("placeholder", result[0].error or "")
            self.assertIn("confidence is low", result[0].error or "")
            self.assertFalse((store.benchmarks / session_id).exists())

    def test_environment_inference_supports_mixed_python_node_verifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            for name in ["pyproject.toml", "uv.lock", "package.json", "pnpm-lock.yaml"]:
                (repo / name).write_text("", encoding="utf-8")
            environment = infer_environment(
                repo,
                verifier_commands=[
                    "uv run ruff check src",
                    "python3 -m pytest -q",
                    "pnpm test",
                ],
            )
            self.assertEqual(environment["kind"], "python+node")
            self.assertEqual(environment["base_image"], "node:22-bookworm-slim")
            installs = "\n".join(environment["install_commands"])
            self.assertIn("pytest", installs)
            self.assertIn("ruff", installs)
            self.assertIn("pnpm install --frozen-lockfile", installs)
            self.assertEqual(environment["unsupported_reasons"], [])

    def test_environment_inference_rejects_node_command_without_base_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "pyproject.toml").write_text("", encoding="utf-8")
            environment = infer_environment(repo, verifier_commands=["pnpm test"])
            self.assertTrue(environment["unsupported_reasons"])
            self.assertIn("package.json", environment["unsupported_reasons"][0])

    def test_status_and_legacy_bundle_require_ordered_runnable_evidence(self):
        self.assertEqual(
            readiness_status(
                [
                    ("Trajectory indexed", True),
                    ("Task reconstructed", False),
                    ("Repository base recovered", True),
                    ("Verification evidence recovered", True),
                    ("Environment bundle built", True),
                    ("Verifier validated", False),
                ]
            ),
            "Needs evidence",
        )
        self.assertFalse(
            bundle_manifest_is_runnable(
                {
                    "task": "Fix the issue.",
                    "reproducibility": {"confidence": "low", "reference_patch": True},
                    "verifier": {"commands": ["python -m pytest -q"]},
                    "environment": {},
                }
            )
        )

    def test_codex_import_skips_empty_attachment_wrapper_for_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "codex.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "payload": {"id": "task-selection", "source": "cli"},
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "payload": {
                            "type": "user_message",
                            "message": "Pasted text contains the user's request.\n\n## My request:",
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "payload": {
                            "type": "user_message",
                            "message": "Build a GitHub project from the attached product thesis.",
                        },
                    },
                ],
            )
            _, trajectory = import_codex(source, Store(root / "store"))
            self.assertEqual(
                trajectory["task"]["text"],
                "Build a GitHub project from the attached product thesis.",
            )

    def test_doctor_reports_history_import_when_codex_launcher_is_broken(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            session = codex_home / "sessions" / "2026" / "example.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text("{}\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), patch(
                "evotrace.cli._tool_version", return_value=("!!", "launcher ENOENT")
            ):
                status, detail = _codex_integration_status()
            self.assertEqual(status, "OK")
            self.assertIn("history import available", detail)
            self.assertIn("CLI unavailable", detail)

    def test_nested_codex_subagents_are_kept_but_hidden_from_default_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "subagent.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "payload": {
                            "id": "child-session",
                            "thread_source": "subagent",
                            "parent_thread_id": "parent-session",
                            "agent_path": "/root/task_hardener",
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "payload": {"type": "user_message", "message": "Fix the child task."},
                    },
                ],
            )
            store = Store(root / "store")
            _, trajectory = import_codex(source, store)
            self.assertEqual(trajectory["source"]["parent_session_id"], "parent-session")
            write_json(
                store.candidates / "parent.json",
                {"session_id": "parent", "score": 5, "labels": ["useful"]},
            )
            write_json(
                store.candidates / "child.json",
                {
                    "session_id": trajectory["session_id"],
                    "score": 9,
                    "labels": ["useful", "nested_subagent"],
                },
            )
            self.assertEqual(
                [item["session_id"] for item in visible_candidates(store)],
                ["parent"],
            )
            self.assertEqual(len(visible_candidates(store, include_all=True)), 2)


if __name__ == "__main__":
    unittest.main()
