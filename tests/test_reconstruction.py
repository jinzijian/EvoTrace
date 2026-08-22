import subprocess
import tempfile
import unittest
from pathlib import Path

from evotrace.errors import EvoTraceError
from evotrace.gitops import extract_archive
from evotrace.reconstruction import recover_reference_patch


class ReconstructionTests(unittest.TestCase):
    def test_empty_tree_base_commit_is_unavailable_instead_of_crashing(self):
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
            subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", "empty"],
                cwd=repo,
                check=True,
            )
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            result = recover_reference_patch(
                root / "session",
                {"root": str(repo), "base_commit": commit},
                [],
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "base commit has no tracked files")

    def test_extract_archive_raises_domain_error_for_unreadable_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "broken.tar.gz"
            archive.write_bytes(b"\x00" * 32)
            with self.assertRaises(EvoTraceError):
                extract_archive(archive, root / "workspace")


if __name__ == "__main__":
    unittest.main()
