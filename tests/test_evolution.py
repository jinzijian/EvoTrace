import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from evotrace.catalog import record_run
from evotrace.compiler import compile_session
from evotrace.evolution import evolve_experience, grade_execution_exploration
from evotrace.recorder import record_command
from evotrace.store import Store
from evotrace.util import read_json, write_json


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    try:
        import os

        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class FakeExperienceAgent:
    def __init__(self):
        self.solves = 0

    def run_prompt(self, workspace, prompt, *, timeout):
        if "Execution Explorer" in prompt:
            (workspace / "probe.py").write_text(
                "from calc import add\nprint(add(2, 3))\n", encoding="utf-8"
            )
            capsule = (
                "TOOL bash: python probe.py\n\nRESULT bash: -1\n\n"
                "TOOL read: calc.py\n\nRESULT read: return a - b"
            )
            return {
                "stdout": "Observed -1 while probing addition.",
                "trajectory_capsule": capsule,
                "trajectory_stats": {
                    "tool_calls": 5,
                    "tool_results": 3,
                    "execution_commands": ["python probe.py"],
                    "runtime_output_bytes": 80,
                    "capsule_bytes": len(capsule),
                    "reasoning_blocks_included": False,
                },
                "provider": "deepseek-official",
                "model": "fake-deepseek",
                "tool_calls": 5,
                "observed_external_file_access": False,
                "permission_mode": "workspace-write",
            }
        return {
            "stdout": (
                '{"summary":"The runtime probe exposes incorrect subtraction.",'
                '"runtime_facts":["add(2, 3) returns -1 before the fix"],'
                '"useful_commands":["python probe.py"],'
                '"code_locations":["calc.py:add"],'
                '"failure_modes":["addition uses subtraction"],'
                '"do_not_assume":["do not trust the function name"],'
                '"confidence":"high"}'
            ),
            "provider": "deepseek-official",
            "model": "fake-deepseek",
            "tool_calls": 1,
            "observed_external_file_access": False,
        }

    def solve(self, workspace, task, *, timeout):
        self.solves += 1
        if "Reusable execution experience" in task:
            (workspace / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
        return {
            "exit_code": 0,
            "timed_out": False,
            "stdout": "done",
            "stderr": "",
            "permission_mode": "workspace-write",
            "session_id": f"fake-{self.solves}",
            "provider": "deepseek-official",
            "model": "fake-deepseek",
            "tool_calls": 2,
            "observed_external_file_access": False,
        }


class EvolutionTests(unittest.TestCase):
    def make_bundles(self, root):
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test")
        (repo / "calc.py").write_text(
            "def add(a, b):\n    return a - b\n", encoding="utf-8"
        )
        (repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n"
            "class CalcTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "base")
        store = Store(root / "store")
        script = (
            "from pathlib import Path; "
            "Path('calc.py').write_text('def add(a, b):\\n    return a + b\\n')"
        )
        with working_directory(repo):
            _, trajectory = record_command(
                [sys.executable, "-c", script],
                task="Fix addition.",
                verification_commands=["python -m unittest -q"],
                store=store,
                use_pty=False,
            )
        source, _ = compile_session(trajectory["session_id"], store=store)
        source_manifest = read_json(source / "task.json")
        source_manifest["repository"]["origin"] = "https://example.com/calc.git"
        write_json(source / "task.json", source_manifest)
        held_out = root / "held-out"
        shutil.copytree(source, held_out)
        held_out_manifest = read_json(held_out / "task.json")
        held_out_manifest["id"] = "held-out-addition"
        held_out_manifest["task"] = "Repair add based on observed repository behavior."
        write_json(held_out / "task.json", held_out_manifest)
        reference = held_out / "patches" / "reference.patch"
        reference.write_text(
            reference.read_text(encoding="utf-8").replace(
                "return a + b", "return a + b  # held-out reference"
            ),
            encoding="utf-8",
        )
        record_run(
            store,
            kind="docker-validation",
            session_id="held-out-addition",
            result={
                "protocol": "docker-two-state-v0.1",
                "provider": "docker",
                "passed": True,
                "image": {"reference_isolated": True},
                "gates": {
                    "base_behavioral_failure": True,
                    "reference_passes": True,
                },
                "controls": {
                    "network": "none",
                    "host_mounts": [],
                    "docker_socket": False,
                    "privileged": False,
                    "no_new_privileges": True,
                },
            },
            passed=True,
            score=1.0,
            bundle=held_out,
        )
        return store, source, held_out

    def test_independent_held_out_gain_verifies_functional_compression(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, source, held_out = self.make_bundles(Path(temporary))

            def evaluate(_bundle, candidate_patch, **_kwargs):
                passed = "return a + b" in candidate_patch.read_text(encoding="utf-8")
                return {"passed": passed, "score": 1.0 if passed else 0.0, "checks": []}

            with patch(
                "evotrace.calibration.evaluate_candidate_patch_in_docker",
                side_effect=evaluate,
            ):
                outcome = evolve_experience(
                    source,
                    held_out,
                    store=store,
                    agent=FakeExperienceAgent(),
                    trials=3,
                    target_passes=2,
                    timeout=30,
                )
            self.assertEqual(outcome.status, "experience_verified")
            self.assertEqual(outcome.utility, "useful")
            self.assertEqual(outcome.baseline_passes, 0)
            self.assertEqual(outcome.conditioned_passes, 3)
            self.assertTrue(outcome.report["functional_compression_verified"])
            self.assertTrue(outcome.report["held_out_validated"])
            self.assertTrue(outcome.report["boundary_audit"]["passed"])
            self.assertTrue((outcome.evolution_dir / "experience" / "experience.md").exists())
            capsule = outcome.evolution_dir / "exploration" / "trajectory-capsule.txt"
            self.assertIn("python probe.py", capsule.read_text(encoding="utf-8"))
            self.assertTrue((outcome.run_dir / "run.json").exists())

    def test_same_asset_is_never_certified(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, source, _ = self.make_bundles(Path(temporary))

            def evaluate(_bundle, candidate_patch, **_kwargs):
                passed = "return a + b" in candidate_patch.read_text(encoding="utf-8")
                return {"passed": passed, "score": 1.0 if passed else 0.0, "checks": []}

            with patch(
                "evotrace.calibration.evaluate_candidate_patch_in_docker",
                side_effect=evaluate,
            ):
                outcome = evolve_experience(
                    source,
                    source,
                    store=store,
                    agent=FakeExperienceAgent(),
                    trials=3,
                    timeout=30,
                )
            self.assertEqual(outcome.status, "smoke_only")
            self.assertFalse(outcome.report["functional_compression_verified"])

    def test_exploration_grade_requires_runtime_grounding(self):
        report = grade_execution_exploration(stats={}, changed_paths=[])
        self.assertFalse(report["passed"])
        self.assertEqual(report["score"], 0.0)

    def test_external_workspace_access_fails_exploration_gate(self):
        report = grade_execution_exploration(
            stats={
                "tool_calls": 5,
                "tool_results": 5,
                "execution_commands": ["python probe.py"],
                "runtime_output_bytes": 100,
                "observed_external_file_access": True,
            },
            changed_paths=["probe.py"],
        )
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
