import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from evotrace.calibration import calibrate_bundle, choose_adjustment
from evotrace.compiler import compile_session
from evotrace.recorder import record_command
from evotrace.store import Store


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


class SequenceSolver:
    def __init__(self, successful):
        self.successful = list(successful)
        self.index = 0

    def solve(self, workspace, task, *, timeout):
        passed = self.successful[self.index]
        self.index += 1
        if passed:
            (workspace / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n",
                encoding="utf-8",
            )
        subprocess.run(
            [sys.executable, "-m", "unittest", "-q"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return {
            "exit_code": 0,
            "timed_out": False,
            "stdout": "done",
            "stderr": "",
            "permission_mode": "workspace-write",
            "session_id": f"fake-{self.index}",
            "provider": "deepseek-official",
            "model": "fake-deepseek",
            "tool_calls": 2,
            "observed_external_file_access": False,
        }


class CalibrationTests(unittest.TestCase):
    def make_bundle(self, root):
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test")
        (repo / "calc.py").write_text(
            "def add(a, b):\n    return a - b\n",
            encoding="utf-8",
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
        bundle, _ = compile_session(trajectory["session_id"], store=store)
        return store, bundle

    def test_adjustment_never_rejects_reference_equivalent_solutions(self):
        decision = choose_adjustment(
            pass_count=5,
            target_passes=2,
            passing_reference_equivalent=5,
            active_hints=[],
            remaining_hints=["look at the failure"],
            remaining_verifiers=["git diff --check"],
        )
        self.assertEqual(decision["action"], "stop")
        self.assertEqual(decision["status"], "too_easy")

    def test_adjustment_adds_and_removes_hints(self):
        harder = choose_adjustment(
            pass_count=4,
            target_passes=2,
            passing_reference_equivalent=1,
            active_hints=["inspect calc.py"],
            remaining_hints=[],
            remaining_verifiers=[],
        )
        self.assertEqual(harder["action"], "remove_hint")
        easier = choose_adjustment(
            pass_count=0,
            target_passes=2,
            passing_reference_equivalent=0,
            active_hints=[],
            remaining_hints=["run tests"],
            remaining_verifiers=[],
        )
        self.assertEqual(easier["action"], "add_hint")

    def test_five_way_calibration_persists_attempts_and_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, bundle = self.make_bundle(root)
            solver = SequenceSolver([True, True, False, False, False])

            def evaluate(_bundle, candidate_patch, **_kwargs):
                patch_text = candidate_patch.read_text(encoding="utf-8")
                passed = "return a + b" in patch_text
                return {
                    "protocol": "docker-candidate-patch-v0.1",
                    "passed": passed,
                    "score": 1.0 if passed else 0.0,
                    "checks": [],
                }

            with patch(
                "evotrace.calibration.evaluate_candidate_patch_in_docker",
                side_effect=evaluate,
            ):
                outcome = calibrate_bundle(
                    bundle,
                    store=store,
                    solver=solver,
                    trials=5,
                    target_passes=2,
                    max_rounds=1,
                    timeout=30,
                )
            self.assertEqual(outcome.status, "calibrated")
            self.assertEqual(outcome.pass_count, 2)
            self.assertTrue((outcome.calibration_dir / "calibration.json").exists())
            attempts = list(outcome.calibration_dir.glob("attempts/**/*.json"))
            self.assertEqual(len(attempts), 5)
            for solution in outcome.calibration_dir.glob("attempts/**/solution.patch"):
                self.assertNotIn(b"__pycache__", solution.read_bytes())
            self.assertTrue((outcome.run_dir / "run.json").exists())


if __name__ == "__main__":
    unittest.main()
