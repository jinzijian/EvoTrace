import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from scaleverifier.compiler import compile_session
from scaleverifier.errors import ScaleVerifierError
from scaleverifier.recorder import record_command
from scaleverifier.runner import benchmark, replay_bundle, verify_candidate
from scaleverifier.store import Store


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
            with self.assertRaisesRegex(ScaleVerifierError, "sandbox contract"):
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


if __name__ == "__main__":
    unittest.main()
