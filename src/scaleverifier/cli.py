from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from glob import glob
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analytics import failure_summary
from .compiler import compile_session
from .errors import ScaleVerifierError
from .importers import import_claude, import_codex
from .recorder import record_command
from .runner import benchmark, replay_bundle, resolve_bundle, verify_candidate
from .store import Store
from .util import console


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scaleverifier",
        description="Turn real coding-agent usage into executable evaluations.",
    )
    parser.add_argument("--version", action="version", version=f"ScaleVerifier {__version__}")
    parser.add_argument(
        "--home",
        type=Path,
        help="Storage directory (default: .scaleverifier in the current Git repository)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    record = subparsers.add_parser("record", help="Run and record a coding agent")
    record.add_argument("--task", help="Task given to the agent")
    record.add_argument("--agent", help="Agent label (defaults to command name)")
    record.add_argument(
        "--verify", action="append", default=[], help="Verifier command; repeatable"
    )
    record.add_argument("--no-pty", action="store_true", help="Disable interactive PTY recording")
    record.add_argument("command", nargs=argparse.REMAINDER)

    importer = subparsers.add_parser("import", help="Import existing local agent history")
    importer.add_argument("source", choices=["codex", "claude"])
    importer.add_argument("paths", nargs="*", type=Path)
    importer.add_argument(
        "--last",
        type=int,
        default=1,
        help="When no path is given, import the N most recent local sessions",
    )

    sessions = subparsers.add_parser("sessions", help="List recorded and imported sessions")
    sessions.add_argument("--json", action="store_true")

    compile_parser = subparsers.add_parser("compile", help="Compile a session into an eval bundle")
    compile_parser.add_argument("session", help="Session id or 'latest'")
    compile_parser.add_argument("--task", help="Override the recovered task")
    compile_parser.add_argument(
        "--verify", action="append", default=[], help="Verifier command; repeatable"
    )

    replay = subparsers.add_parser("replay", help="Restore a compiled task in a fresh workspace")
    replay.add_argument("benchmark", help="Bundle path, session id, or 'latest'")
    replay.add_argument("--dest", type=Path, required=True)
    replay.add_argument("--run", help="Optional command to run in the restored workspace")

    verify = subparsers.add_parser("verify", help="Run a bundle verifier against a checkout")
    verify.add_argument("benchmark", help="Bundle path, session id, or 'latest'")
    verify.add_argument("--repo", type=Path, default=Path.cwd())
    verify.add_argument("--json", action="store_true")

    bench = subparsers.add_parser("benchmark", help="Run agents or score existing candidates")
    bench.add_argument("benchmark", help="Bundle path, session id, or 'latest'")
    bench.add_argument("--agent", action="append", default=[], metavar="NAME=COMMAND")
    bench.add_argument("--candidate", action="append", default=[], metavar="NAME=PATH")
    bench.add_argument("--timeout", type=int, default=1800)
    bench.add_argument("--json", action="store_true")

    failures = subparsers.add_parser("failures", help="Mine failure signals from local sessions")
    failures.add_argument("--json", action="store_true")

    subparsers.add_parser("doctor", help="Check local runtime integrations")
    return parser


def _clean_remainder(command: List[str]) -> List[str]:
    return command[1:] if command and command[0] == "--" else command


def _discover_history(source: str, count: int) -> List[Path]:
    if count < 1:
        raise ScaleVerifierError("--last must be at least 1")
    home = Path.home()
    if source == "codex":
        pattern = str(home / ".codex" / "sessions" / "**" / "*.jsonl")
        paths = [Path(item) for item in glob(pattern, recursive=True)]
    else:
        pattern = str(home / ".claude" / "projects" / "**" / "*.jsonl")
        paths = [
            Path(item)
            for item in glob(pattern, recursive=True)
            if "subagents" not in Path(item).parts
        ]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:count]


def _print_sessions(store: Store, as_json: bool) -> None:
    rows = []
    for _, trajectory in store.list_sessions():
        rows.append(
            {
                "id": trajectory.get("session_id"),
                "agent": (trajectory.get("source") or {}).get("agent"),
                "status": (trajectory.get("outcome") or {}).get("status"),
                "task": ((trajectory.get("task") or {}).get("text") or "").replace("\n", " ")[:70],
            }
        )
    if as_json:
        console(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        console("No sessions yet. Try: scaleverifier import codex <session.jsonl>")
        return
    console(f"{'SESSION':<40} {'AGENT':<12} {'STATUS':<16} TASK")
    for row in rows:
        console(f"{row['id']:<40} {str(row['agent']):<12} {str(row['status']):<16} {row['task']}")


def _print_benchmark(results, as_json: bool) -> None:
    if as_json:
        console(json.dumps(results, indent=2, ensure_ascii=False))
        return
    console(f"{'CANDIDATE':<22} {'RESULT':<10} {'SCORE':>8} {'SECONDS':>10}")
    for item in sorted(results, key=lambda value: value["score"], reverse=True):
        result = "PASS" if item["passed"] else "FAIL"
        console(
            f"{item['name']:<22} {result:<10} {item['score'] * 100:>7.1f}% {item['duration_seconds']:>10.1f}"
        )


def _tool_version(name: str) -> tuple[str, str]:
    location = shutil.which(name)
    if not location:
        return "--", "not found"
    try:
        result = subprocess.run(
            [location, "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "!!", str(exc).splitlines()[0]
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else location
    return ("OK", detail) if result.returncode == 0 else ("!!", detail)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    store = Store(args.home) if args.home else Store()
    try:
        if args.subcommand == "record":
            command = _clean_remainder(args.command)
            session_dir, trajectory = record_command(
                command,
                task=args.task,
                agent=args.agent,
                verification_commands=args.verify,
                store=store,
                use_pty=not args.no_pty,
            )
            console(f"\nRecorded {trajectory['session_id']}")
            console(f"Session: {session_dir}")
            console(f"Next: scaleverifier compile {trajectory['session_id']}")
            return trajectory["outcome"]["exit_code"]

        if args.subcommand == "import":
            imported = []
            importer = import_codex if args.source == "codex" else import_claude
            paths = args.paths or _discover_history(args.source, args.last)
            if not paths:
                raise ScaleVerifierError(f"No local {args.source} session history found")
            for path in paths:
                _, trajectory = importer(path.expanduser().resolve(), store)
                imported.append(trajectory)
                console(f"Imported {trajectory['session_id']}")
            console(f"\n{len(imported)} session(s) normalized locally.")
            console("Next: scaleverifier sessions")
            return 0

        if args.subcommand == "sessions":
            _print_sessions(store, args.json)
            return 0

        if args.subcommand == "compile":
            bundle, manifest = compile_session(
                args.session,
                task_override=args.task,
                extra_commands=args.verify,
                store=store,
            )
            verifier = manifest["verifier"]
            console("Benchmark ready.")
            console(f"Task:        {manifest['id']}")
            console(
                f"Environment: {manifest['environment']['kind']} ({manifest['reproducibility']['confidence']} confidence)"
            )
            console(
                f"Verifier:    {len(verifier['commands'])} command(s), source={verifier['command_source']}"
            )
            if verifier["warning"]:
                console(f"Warning:     {verifier['warning']}")
            console(f"Bundle:      {bundle}")
            console("\nNext: scaleverifier replay latest --dest /tmp/scaleverifier-task")
            return 0

        if args.subcommand == "replay":
            bundle = resolve_bundle(args.benchmark, store)
            workspace = replay_bundle(bundle, args.dest, args.run)
            console(f"Workspace ready: {workspace}")
            console(f"Task: {bundle / 'task.md'}")
            return 0

        if args.subcommand == "verify":
            bundle = resolve_bundle(args.benchmark, store)
            report = verify_candidate(bundle, args.repo.resolve())
            if args.json:
                console(json.dumps(report, indent=2))
            else:
                for check in report["checks"]:
                    console(f"[{'PASS' if check['passed'] else 'FAIL'}] {check['name']}")
                console(f"Score: {report['score'] * 100:.1f}%")
            return 0 if report["passed"] else 1

        if args.subcommand == "benchmark":
            bundle = resolve_bundle(args.benchmark, store)
            results = benchmark(
                bundle,
                agents=args.agent,
                candidates=args.candidate,
                timeout=args.timeout,
                store=store,
            )
            _print_benchmark(results, args.json)
            return 0 if all(item["passed"] for item in results) else 1

        if args.subcommand == "failures":
            counts, details = failure_summary(store)
            if args.json:
                console(json.dumps({"counts": counts, "sessions": details}, indent=2))
            elif not counts:
                console("No failure signals found in local sessions.")
            else:
                console(f"Failure signals found across {len(details)} session(s)\n")
                for category, count in counts.most_common():
                    console(f"{count:>4}  {category}")
            return 0

        if args.subcommand == "doctor":
            tools = ["git", "docker", "codex", "claude"]
            console("ScaleVerifier integrations\n")
            for tool in tools:
                status, detail = _tool_version(tool)
                console(f"[{status}] {tool:<8} {detail}")
            console(f"\nStorage: {store.root}")
            return 0
    except ScaleVerifierError as exc:
        console(f"error: {exc}", error=True)
        return 2
    return 0
