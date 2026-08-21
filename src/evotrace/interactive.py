from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from .store import Store
from .util import console


@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    description: str


COMMANDS = (
    SlashCommand("home", "/home", "Show workspace summary"),
    SlashCommand("init", "/init", "Import history and mine it locally"),
    SlashCommand("import", "/import [all|codex|claude]", "Import local agent history"),
    SlashCommand("mine", "/mine", "Refresh candidate scoring"),
    SlashCommand("candidates", "/candidates", "Browse valuable trajectories"),
    SlashCommand("search", "/search <words>", "Search tasks, evidence, and repositories"),
    SlashCommand("show", "/show <number>", "Open one candidate"),
    SlashCommand("build", "/build <number>", "Build an environment and verifier candidate"),
    SlashCommand("assets", "/assets", "Browse compiled assets"),
    SlashCommand("validate", "/validate <number>", "Validate base/reference states in Docker"),
    SlashCommand(
        "calibrate",
        "/calibrate <number> --allow-upload",
        "Run 5-way DeepSeek self-play and calibrate toward 2 passes",
    ),
    SlashCommand(
        "evolve",
        "/evolve <source> [--held-out ID] --allow-upload",
        "Explore, compress, and test experience on a held-out task",
    ),
    SlashCommand("verify", "/verify <number> [--repo PATH]", "Run a verifier on a checkout"),
    SlashCommand("runs", "/runs", "Browse saved verifier runs"),
    SlashCommand("export", "/export <number>", "Create a local portable archive"),
    SlashCommand("doctor", "/doctor", "Check local integrations"),
    SlashCommand("help", "/help [command]", "Explain slash commands"),
    SlashCommand("clear", "/clear", "Clear the terminal"),
    SlashCommand("exit", "/exit", "Leave EvoTrace"),
)

ALIASES = {
    "list": "candidates",
    "open": "show",
    "quit": "exit",
    "?": "help",
}


def _prompt_reader() -> tuple[Callable[[str], str], bool]:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return input, False
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
    except ImportError:  # pragma: no cover - fallback for source-only use
        return input, False

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/") or " " in text:
                return
            needle = text[1:].casefold()
            for command in COMMANDS:
                if command.name.startswith(needle):
                    yield Completion(
                        f"/{command.name}",
                        start_position=-len(text),
                        display=command.usage,
                        display_meta=command.description,
                    )

    session: PromptSession[str] = PromptSession(
        completer=SlashCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=8,
    )
    return session.prompt, True


def _command(name: str) -> Optional[SlashCommand]:
    canonical = ALIASES.get(name, name)
    return next((item for item in COMMANDS if item.name == canonical), None)


def _print_palette() -> None:
    console("Slash commands\n")
    for command in COMMANDS:
        console(f"  {command.usage:<38} {command.description}")
    console("\nTip: type ordinary words to search your mined trajectories.")


def _print_help(name: Optional[str] = None) -> None:
    if not name:
        _print_palette()
        return
    command = _command(name.lstrip("/"))
    if not command:
        console(f"Unknown command: /{name.lstrip('/')}")
        console("Type / to browse available commands.")
        return
    console(command.usage)
    console(command.description)


def _print_home(store: Store) -> None:
    sessions = len(store.list_sessions())
    candidates = store.list_candidates()
    useful = sum("useful" in (item.get("labels") or []) for item in candidates)
    assets = len(store.list_bundles())
    runs = len(store.list_runs())
    console("╭─ EvoTrace ─────────────────────────────────────────────────────────╮")
    console("│ Turn real-world coding-agent history into post-training assets.   │")
    console("╰────────────────────────────────────────────────────────────────────╯")
    console(
        f"\n  {sessions} sessions  ·  {useful} candidates  ·  "
        f"{assets} assets  ·  {runs} runs"
    )
    console(f"  Local workspace: {store.root}")
    if candidates:
        console("\n  Start with /candidates or type ordinary words to search your history.")
    else:
        console("\n  Start with /init to import Claude Code and Codex history locally.")
    console("  Type / to open the live command palette. Nothing is uploaded by default.\n")


def _install_completion() -> Optional[Callable[[str, int], Optional[str]]]:
    try:
        import readline
    except ImportError:  # pragma: no cover - Windows without pyreadline
        return None

    names = sorted({f"/{item.name}" for item in COMMANDS} | {f"/{item}" for item in ALIASES})
    previous = readline.get_completer()

    def complete(text: str, state: int) -> Optional[str]:
        matches = [name for name in names if name.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")
    return previous


def _restore_completion(previous: Optional[Callable[[str, int], Optional[str]]]) -> None:
    try:
        import readline
    except ImportError:  # pragma: no cover - Windows without pyreadline
        return
    readline.set_completer(previous)


def _execute_slash(
    line: str,
    *,
    execute: Callable[[List[str]], int],
) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped == "/":
        _print_palette()
        return True
    if not stripped.startswith("/"):
        if stripped.isdigit():
            arguments = ["show", stripped]
        else:
            arguments = ["search", stripped]
    else:
        try:
            arguments = shlex.split(stripped[1:])
        except ValueError as exc:
            console(f"Could not parse command: {exc}", error=True)
            return True
        if not arguments:
            _print_palette()
            return True

    name = ALIASES.get(arguments[0], arguments[0])
    if name == "exit":
        return False
    if name == "help":
        _print_help(arguments[1] if len(arguments) > 1 else None)
        return True
    if name == "home":
        return True
    if name == "clear":
        console("\033[2J\033[H")
        return True
    if not _command(name):
        console(f"Unknown command: /{arguments[0]}", error=True)
        console("Type / to browse available commands.")
        return True

    previous_mode = os.environ.get("EVOTRACE_SLASH_MODE")
    os.environ["EVOTRACE_SLASH_MODE"] = "1"
    try:
        execute([name, *arguments[1:]])
    except SystemExit:
        # argparse already printed a precise usage error. Keep the workspace open.
        pass
    finally:
        if previous_mode is None:
            os.environ.pop("EVOTRACE_SLASH_MODE", None)
        else:
            os.environ["EVOTRACE_SLASH_MODE"] = previous_mode
    return True


def run_interactive(
    store: Store,
    *,
    execute: Callable[[List[str]], int],
    input_fn: Optional[Callable[[str], str]] = None,
) -> int:
    if input_fn:
        reader = input_fn
        rich_prompt = False
    else:
        reader, rich_prompt = _prompt_reader()
    previous_completer = None if rich_prompt else _install_completion()
    _print_home(store)
    try:
        while True:
            try:
                line = reader("evotrace › ")
            except (EOFError, KeyboardInterrupt):
                console("")
                break
            if line.strip() == "/home":
                _print_home(store)
                continue
            if line.strip() == "/clear":
                console("\033[2J\033[H")
                _print_home(store)
                continue
            if not _execute_slash(line, execute=execute):
                break
            if line.strip():
                console("")
    finally:
        if not rich_prompt:
            _restore_completion(previous_completer)
    console("EvoTrace closed. Your data remains local.")
    return 0
