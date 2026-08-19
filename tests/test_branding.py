import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scaleverifier.errors import ScaleVerifierError

import evotrace
import scaleverifier
from evotrace.cli import _parser, main
from evotrace.errors import EvoTraceError
from evotrace.store import Store


class BrandingTests(unittest.TestCase):
    def test_primary_and_legacy_namespaces_share_version_and_error_type(self):
        self.assertEqual(evotrace.__version__, "0.3.1")
        self.assertEqual(scaleverifier.__version__, evotrace.__version__)
        self.assertIs(ScaleVerifierError, EvoTraceError)
        self.assertEqual(_parser().prog, "evotrace")

    def test_new_store_default_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {
                "HOME": str(home),
                "EVOTRACE_HOME": "",
                "SCALEVERIFIER_HOME": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(Store().root, (home / ".evotrace").resolve())
                (home / ".scaleverifier").mkdir()
                self.assertEqual(Store().root, (home / ".scaleverifier").resolve())
                (home / ".evotrace").mkdir()
                self.assertEqual(Store().root, (home / ".evotrace").resolve())

    def test_evotrace_home_takes_priority_over_legacy_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "EVOTRACE_HOME": str(root / "new"),
                    "SCALEVERIFIER_HOME": str(root / "legacy"),
                },
            ):
                self.assertEqual(Store().root, (root / "new").resolve())

    def test_init_imports_and_mines_in_one_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            session = codex_home / "sessions" / "2026" / "example.jsonl"
            session.parent.mkdir(parents=True)
            records = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {"id": "init-example", "cwd": str(root)},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "payload": {
                        "type": "user_message",
                        "message": "Fix a meaningful parser regression without breaking existing clients.",
                    },
                },
            ]
            session.write_text(
                "".join(json.dumps(item) + "\n" for item in records),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                with redirect_stdout(output):
                    exit_code = main(
                        ["--home", str(root / "store"), "init", "--source", "codex"]
                    )
            self.assertEqual(exit_code, 0)
            self.assertIn("EvoTrace is ready.", output.getvalue())
            self.assertIn("Sessions indexed              1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
