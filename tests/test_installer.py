import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerTests(unittest.TestCase):
    def test_shell_installer_installs_harness_and_does_not_import_history(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            capture = root / "commands.txt"
            install_root = root / "installed" / "app"
            command_bin = root / "user-bin"

            self._script(
                binaries / "git",
                """#!/bin/sh
printf 'git %s\\n' "$*" >> "$EVOTRACE_CAPTURE"
if [ "$1" = clone ]; then
  mkdir -p "$5/harness"
  : > "$5/harness/launcher.mjs"
  : > "$5/package.json"
  : > "$5/pnpm-lock.yaml"
fi
""",
            )
            self._script(
                binaries / "node",
                """#!/bin/sh
printf 'node %s\\n' "$*" >> "$EVOTRACE_CAPTURE"
exit 0
""",
            )
            self._script(
                binaries / "python3",
                """#!/bin/sh
printf 'python3 %s\\n' "$*" >> "$EVOTRACE_CAPTURE"
if [ "$1" = -m ] && [ "$2" = venv ]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python"
  chmod +x "$3/bin/python"
fi
exit 0
""",
            )
            self._script(
                binaries / "pnpm",
                """#!/bin/sh
printf 'pnpm %s\\n' "$*" >> "$EVOTRACE_CAPTURE"
exit 0
""",
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "EVOTRACE_CAPTURE": str(capture),
                    "EVOTRACE_INSTALL_SOURCE": "https://example.com/EvoTrace.git",
                    "EVOTRACE_INSTALL_ROOT": str(install_root),
                    "EVOTRACE_BIN_DIR": str(command_bin),
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
            commands = capture.read_text(encoding="utf-8")
            self.assertIn("git clone --depth 1 https://example.com/EvoTrace.git", commands)
            self.assertIn("pnpm install --prod --frozen-lockfile", commands)
            self.assertIn(f"node {install_root}/harness/launcher.mjs setup", commands)
            self.assertTrue((command_bin / "evotrace").is_symlink())
            self.assertIn("Launch the Harness app", result.stdout)
            self.assertIn("run /init", result.stdout)
            self.assertIn("No session data was read", result.stdout)

    def test_shell_installer_rejects_an_incomplete_release_payload(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            install_root = root / "installed" / "app"
            command_bin = root / "user-bin"
            self._script(
                binaries / "git",
                """#!/bin/sh
if [ "$1" = clone ]; then
  mkdir -p "$5"
fi
""",
            )
            self._script(binaries / "node", "#!/bin/sh\nexit 0\n")
            self._script(binaries / "python3", "#!/bin/sh\nexit 0\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "EVOTRACE_INSTALL_SOURCE": "https://example.com/EvoTrace.git",
                    "EVOTRACE_INSTALL_ROOT": str(install_root),
                    "EVOTRACE_BIN_DIR": str(command_bin),
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
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("release payload is incomplete", result.stderr)
            self.assertFalse((command_bin / "evotrace").exists())

    @staticmethod
    def _script(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
