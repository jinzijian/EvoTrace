from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analytics import failure_summary
from .builder import build_mined_candidates
from .compiler import compile_session
from .errors import EvoTraceError
from .history import import_discovered_history
from .importers import import_claude, import_codex, import_codex_prompt_history
from .miner import mine_store
from .recorder import record_command
from .runner import benchmark, replay_bundle, resolve_bundle, verify_candidate
from .store import Store
from .util import console


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evotrace",
        description=(
            "Turn every Claude Code and Codex session into reusable training, "
            "evaluation, and verification assets."
        ),
    )
    parser.add_argument("--version", action="version", version=f"EvoTrace {__version__}")
    parser.add_argument(
        "--home",
        type=Path,
        help="Storage directory (default: ~/.evotrace or $EVOTRACE_HOME)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    initialize = subparsers.add_parser(
        "init", help="Import existing sessions and mine useful assets in one command"
    )
    initialize.add_argument("--source", choices=["all", "codex", "claude"], default="all")
    initialize.add_argument(
        "--last",
        type=int,
        help="Index only the N most recently modified session files",
    )
    initialize.add_argument(
        "--refresh",
        action="store_true",
        help="Re-index files even when they are unchanged",
    )

    record = subparsers.add_parser("record", help="Run and record a coding agent")
    record.add_argument("--task", help="Task given to the agent")
    record.add_argument("--agent", help="Agent label (defaults to command name)")
    record.add_argument(
        "--verify", action="append", default=[], help="Verifier command; repeatable"
    )
    record.add_argument("--no-pty", action="store_true", help="Disable interactive PTY recording")
    record.add_argument("command", nargs=argparse.REMAINDER)

    importer = subparsers.add_parser("import", help="Index existing local agent history")
    importer.add_argument("source", nargs="?", choices=["all", "codex", "claude"], default="all")
    importer.add_argument("paths", nargs="*", type=Path)
    importer.add_argument(
        "--last",
        type=int,
        help="When no path is given, index only the N most recently modified session files",
    )
    importer.add_argument(
        "--refresh",
        action="store_true",
        help="Re-index files even when their size and modification time are unchanged",
    )

    mine = subparsers.add_parser("mine", help="Find useful training and eval candidates")
    mine.add_argument("--source", choices=["all", "codex", "claude"], default="all")
    mine.add_argument("--min-score", type=int, default=0)
    mine.add_argument("--json", action="store_true")

    build = subparsers.add_parser("build", help="Build mined sessions into eval bundles")
    build.add_argument(
        "session", nargs="?", help="Specific session id (default: top executable candidates)"
    )
    build.add_argument("--limit", type=int, default=10)
    build.add_argument("--verify", action="append", default=[], help="Verifier command; repeatable")
    build.add_argument("--json", action="store_true")

    watch = subparsers.add_parser("watch", help="Incrementally index new local sessions")
    watch.add_argument("--source", choices=["all", "codex", "claude"], default="all")
    watch.add_argument("--interval", type=int, default=300, help="Polling interval in seconds")
    watch.add_argument("--once", action="store_true", help="Run one incremental pass and exit")
    watch.add_argument("--no-mine", action="store_true", help="Skip mining after each import pass")

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

    bench = subparsers.add_parser("benchmark", help="Score existing candidate checkouts")
    bench.add_argument("benchmark", help="Bundle path, session id, or 'latest'")
    bench.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="NAME=COMMAND",
        help="Disabled: agents must run inside the generated Docker sandbox",
    )
    bench.add_argument("--candidate", action="append", default=[], metavar="NAME=PATH")
    bench.add_argument("--timeout", type=int, default=1800)
    bench.add_argument("--json", action="store_true")

    failures = subparsers.add_parser("failures", help="Mine failure signals from local sessions")
    failures.add_argument("--json", action="store_true")

    subparsers.add_parser("doctor", help="Check local runtime integrations")
    return parser


def _clean_remainder(command: List[str]) -> List[str]:
    return command[1:] if command and command[0] == "--" else command


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
        console("No sessions yet. Try: evotrace import")
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


def _import_explicit(source: str, paths: List[Path], store: Store) -> List[dict]:
    if source == "all":
        raise EvoTraceError(
            "Explicit paths require `evotrace import codex ...` or `evotrace import claude ...`"
        )
    imported = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if source == "codex" and resolved.name == "history.jsonl":
            results = import_codex_prompt_history(resolved, store)
        else:
            importer = import_codex if source == "codex" else import_claude
            results = [importer(resolved, store)]
        for _, trajectory in results:
            imported.append(trajectory)
    return imported


def _print_mining(summary) -> None:
    console(f"Found                         {summary.total}")
    console(f"Useful                        {summary.useful}")
    console(f"Human corrected               {summary.human_corrected}")
    console(f"Execution-verifiable          {summary.execution_verifiable}")
    console(f"Preference candidates         {summary.preference_candidates}")
    console(f"Recovery trajectories         {summary.recovery_trajectories}")
    console(f"Low-value / trivial           {summary.low_value}")
    console("\nCurator: evidence heuristic v0.2 (no model or data upload)")


def _watch(store: Store, *, source: str, interval: int, once: bool, run_mining: bool) -> None:
    if interval < 5 and not once:
        raise EvoTraceError("--interval must be at least 5 seconds")
    while True:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            summary = import_discovered_history(store, source=source, incremental=True)
            console(
                f"[{stamp}] processed={summary.processed_files} "
                f"unchanged={summary.skipped_unchanged_files} sessions={len(summary.session_ids)}"
            )
            if run_mining:
                mined = mine_store(store, source=source)
                console(
                    f"[{stamp}] useful={mined.useful} executable={mined.execution_verifiable} "
                    f"preference={mined.preference_candidates}"
                )
        except EvoTraceError as exc:
            console(f"[{stamp}] {exc}", error=True)
            if once:
                raise
        if once:
            return
        time.sleep(interval)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    store = Store(args.home) if args.home else Store()
    try:
        if args.subcommand == "init":
            imported = import_discovered_history(
                store,
                source=args.source,
                last=args.last,
                incremental=not args.refresh,
            )
            mined = mine_store(store, source=args.source)
            console("EvoTrace is ready.\n")
            console(f"Sessions indexed              {len(imported.session_ids)}")
            console(f"New or refreshed files       {imported.processed_files}")
            console(f"Useful assets                 {mined.useful}")
            console(f"Preference candidates         {mined.preference_candidates}")
            console(f"Execution-verifiable          {mined.execution_verifiable}")
            console(f"Recovery trajectories         {mined.recovery_trajectories}")
            console("\nEverything stayed local. No model or data upload was used.")
            if mined.execution_verifiable:
                console("Next: evotrace build")
            else:
                console("Next: evotrace mine --json")
            return 0

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
            console(f"Next: evotrace mine && evotrace build {trajectory['session_id']}")
            return trajectory["outcome"]["exit_code"]

        if args.subcommand == "import":
            if args.paths:
                imported = _import_explicit(args.source, args.paths, store)
                console(f"Found {len(imported)} session(s)")
                console(f"Indexed {len(imported)} session(s) locally")
            else:
                summary = import_discovered_history(
                    store,
                    source=args.source,
                    last=args.last,
                    incremental=not args.refresh,
                )
                console(f"Found {len(summary.session_ids)} session(s)")
                console(f"Discovered files             {summary.discovered_files}")
                console(f"Indexed files                {summary.processed_files}")
                console(f"Unchanged files              {summary.skipped_unchanged_files}")
                console(f"Created sessions             {summary.created_sessions}")
                console(f"Refreshed sessions           {summary.refreshed_sessions}")
                console(f"Kept richer session records  {summary.kept_richer_sessions}")
            console("\nNext: evotrace mine")
            return 0

        if args.subcommand == "mine":
            if not 0 <= args.min_score <= 10:
                raise EvoTraceError("--min-score must be between 0 and 10")
            summary = mine_store(
                store,
                source=args.source,
                minimum_score=args.min_score,
            )
            if args.json:
                console(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
            else:
                _print_mining(summary)
                if summary.execution_verifiable:
                    console("\nNext: evotrace build")
            return 0

        if args.subcommand == "build":
            results = build_mined_candidates(
                store,
                session_id=args.session,
                limit=args.limit,
                extra_commands=args.verify,
            )
            if args.json:
                console(json.dumps([asdict(item) for item in results], indent=2))
            else:
                console(f"{'SESSION':<40} {'STATUS':<15} BUNDLE / REASON")
                for item in results:
                    detail = item.bundle or item.error or ""
                    console(f"{item.session_id:<40} {item.status:<15} {detail}")
                built = sum(item.status in {"built", "already_built"} for item in results)
                console(f"\n{built}/{len(results)} eval bundle(s) ready.")
            return 0 if all(item.status != "skipped" for item in results) else 1

        if args.subcommand == "watch":
            _watch(
                store,
                source=args.source,
                interval=args.interval,
                once=args.once,
                run_mining=not args.no_mine,
            )
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
            console("\nNext: evotrace replay latest --dest /tmp/evotrace-task")
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
            console("EvoTrace integrations\n")
            for tool in tools:
                status, detail = _tool_version(tool)
                console(f"[{status}] {tool:<8} {detail}")
            console(f"\nStorage: {store.root}")
            return 0
    except EvoTraceError as exc:
        console(f"error: {exc}", error=True)
        return 2
    return 0
