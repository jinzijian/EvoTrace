import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from evotrace.cli import main
from evotrace.interactive import run_interactive
from evotrace.store import Store


class InteractiveTests(unittest.TestCase):
    def test_et_without_subcommand_opens_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "store"
            with patch("evotrace.interactive.run_interactive", return_value=0) as shell:
                code = main(["--home", str(home)])

            self.assertEqual(code, 0)
            self.assertEqual(shell.call_args.args[0].root, home.resolve())
            self.assertTrue(callable(shell.call_args.kwargs["execute"]))

    def test_slash_palette_search_shortcuts_and_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "store")
            commands = iter(["/", "payment retry", "1", "/list", "/exit"])
            dispatched = []
            output = io.StringIO()

            with redirect_stdout(output):
                code = run_interactive(
                    store,
                    execute=lambda arguments: dispatched.append(arguments) or 0,
                    input_fn=lambda _: next(commands),
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                dispatched,
                [
                    ["search", "payment retry"],
                    ["show", "1"],
                    ["candidates"],
                ],
            )
            self.assertIn("Slash commands", output.getvalue())
            self.assertIn("/candidates", output.getvalue())
            self.assertIn("EvoTrace closed", output.getvalue())

    def test_unknown_command_does_not_close_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "store")
            commands = iter(["/missing", "/doctor", "/exit"])
            dispatched = []
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                run_interactive(
                    store,
                    execute=lambda arguments: dispatched.append(arguments) or 0,
                    input_fn=lambda _: next(commands),
                )

            self.assertEqual(dispatched, [["doctor"]])
            self.assertIn("Unknown command: /missing", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
