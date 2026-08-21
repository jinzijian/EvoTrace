import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from evotrace.catalog import has_matching_passed_run, record_run
from evotrace.compiler import compile_session
from evotrace.errors import EvoTraceError
from evotrace.recorder import record_command
from evotrace.runner import benchmark, replay_bundle, verify_candidate
from evotrace.store import Store
from evotrace.util import read_json
from evotrace.validation import (
    evaluate_candidate_patch_in_docker,
    evaluate_reference_with_verifier_in_docker,
    validate_bundle_in_docker,
)


def docker_available():
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE)


class EndToEndTests(unittest.TestCase):
    def test_record_compile_replay_and_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\n\n"
                "class CalcTest(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "base")

            store = Store(root / "store")
            script = "from pathlib import Path; Path('calc.py').write_text('def add(a, b):\\n    return a + b\\n')"
            with working_directory(repo):
                _, trajectory = record_command(
                    [sys.executable, "-c", script],
                    task="Fix add so the test passes.",
                    verification_commands=["python -m unittest discover -s tests"],
                    store=store,
                    use_pty=False,
                )
            bundle, manifest = compile_session(trajectory["session_id"], store=store)
            self.assertEqual(manifest["verifier"]["command_source"], "explicit")
            self.assertEqual(manifest["sandbox"]["execution"], "container_only")
            self.assertEqual(manifest["sandbox"]["host_mounts"], [])
            self.assertTrue((bundle / "environment" / "base.tar.gz").exists())
            self.assertTrue((bundle / "verifier.py").exists())
            self.assertTrue((bundle / "sandbox-policy.json").exists())
            dockerfile = (bundle / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("USER 65532:65532", dockerfile)
            self.assertIn("/evotrace/task.md", dockerfile)
            self.assertIn("EVOTRACE_TASK_FILE", dockerfile)
            with self.assertRaisesRegex(EvoTraceError, "sandbox contract"):
                benchmark(
                    bundle,
                    agents=["unsafe=echo hello"],
                    candidates=[],
                    timeout=10,
                    store=store,
                )

            shell_candidate = root / "shell-candidate"
            subprocess.run(
                [str(bundle / "setup.sh"), str(shell_candidate)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            self.assertIn("return a - b", (shell_candidate / "calc.py").read_text())

            candidate = root / "candidate"
            replay_bundle(bundle, candidate)
            before = verify_candidate(bundle, candidate)
            self.assertFalse(before["passed"])

            hacked = root / "hacked"
            replay_bundle(bundle, hacked)
            (hacked / "tests" / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\n\n"
                "class CalcTest(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), -1)\n",
                encoding="utf-8",
            )
            hacked_report = verify_candidate(bundle, hacked)
            self.assertFalse(hacked_report["passed"], hacked_report)
            protected = [
                check
                for check in hacked_report["checks"]
                if check["name"] == "protected tests were not modified"
            ]
            self.assertEqual(len(protected), 1)
            self.assertFalse(protected[0]["passed"])

            (candidate / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
            after = verify_candidate(bundle, candidate)
            self.assertTrue(after["passed"], after)
            self.assertEqual(after["score"], 1.0)

    @unittest.skipUnless(docker_available(), "Docker daemon is required")
    def test_docker_two_state_validation_closes_asset_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
            bundle, _ = compile_session(trajectory["session_id"], store=store)
            report = validate_bundle_in_docker(bundle, timeout=120)
            self.assertTrue(report["passed"], report)
            self.assertTrue(report["gates"]["base_behavioral_failure"])
            self.assertTrue(report["gates"]["reference_passes"])

            candidate = root / "deepseek-candidate"
            replay_bundle(bundle, candidate)
            (candidate / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
            patch_path = root / "candidate.patch"
            generated = subprocess.run(
                ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
                cwd=candidate,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            patch_path.write_bytes(generated.stdout)
            candidate_report = evaluate_candidate_patch_in_docker(
                bundle,
                patch_path,
                timeout=120,
            )
            self.assertTrue(candidate_report["passed"], candidate_report)
            self.assertEqual(candidate_report["controls"]["network"], "none")
            self.assertEqual(candidate_report["controls"]["host_mounts"], [])

            stronger_verifier = read_json(bundle / "verifier.json")
            stronger_verifier["commands"].append("git diff --check")
            verifier_gate = evaluate_reference_with_verifier_in_docker(
                bundle,
                stronger_verifier,
                timeout=120,
            )
            self.assertTrue(verifier_gate["passed"], verifier_gate)
            record_run(
                store,
                kind="docker-validation",
                session_id=trajectory["session_id"],
                result=report,
                passed=True,
                score=1.0,
                bundle=bundle,
            )
            self.assertTrue(
                has_matching_passed_run(store, trajectory["session_id"], bundle)
            )


if __name__ == "__main__":
    unittest.main()
