import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerTests(unittest.TestCase):
    def test_shell_installer_prefers_uv_and_prints_first_command(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            capture = root / "uv-arguments.txt"
            (binaries / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (binaries / "uv").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$EVOTRACE_CAPTURE"\n',
                encoding="utf-8",
            )
            for path in [binaries / "git", binaries / "uv"]:
                path.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "EVOTRACE_CAPTURE": str(capture),
                    "EVOTRACE_INSTALL_SOURCE": "git+https://example.com/evotrace.git",
                }
            )
            result = subprocess.run(
                ["/bin/sh", str(project / "install.sh")],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                capture.read_text(encoding="utf-8").splitlines(),
                [
                    "tool",
                    "install",
                    "--force",
                    "git+https://example.com/evotrace.git",
                ],
            )
            self.assertIn("evotrace init", result.stdout)
            self.assertIn("No session data was read", result.stdout)


if __name__ == "__main__":
    unittest.main()
