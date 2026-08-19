import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scaleverifier.errors import ScaleVerifierError

import evotrace
import scaleverifier
from evotrace.cli import _parser
from evotrace.errors import EvoTraceError
from evotrace.store import Store


class BrandingTests(unittest.TestCase):
    def test_primary_and_legacy_namespaces_share_version_and_error_type(self):
        self.assertEqual(evotrace.__version__, "0.3.0")
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


if __name__ == "__main__":
    unittest.main()
