from __future__ import annotations

import argparse
import json
import os
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
from .calibration import calibrate_bundle
from .catalog import (
    asset_types,
    candidate_task,
    compact,
    export_asset,
    has_matching_passed_run,
    readiness,
    readiness_status,
    record_run,
    related_runs,
    resolve_candidate,
    search_candidates,
    visible_candidates,
)
from .compiler import compile_session
from .curator_agent import DEFAULT_MODEL, OpenAICuratorAgent, curate_store
from .errors import EvoTraceError
from .evolution import evolve_experience
from .hardening import harden_bundle
from .history import import_discovered_history
from .importers import import_claude, import_codex, import_codex_prompt_history
from .miner import mine_store
from .quality import bundle_manifest_is_runnable
from .recorder import record_command
from .runner import benchmark, replay_bundle, resolve_bundle, verify_candidate
from .store import Store
from .util import console, read_json
from .validation import validate_bundle_in_docker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evotrace",
        description=(
            "Compile real-world Claude Code and Codex trajectories into verified "
            "post-training assets."
        ),
    )
    parser.add_argument("--version", action="version", version=f"EvoTrace {__version__}")
    parser.add_argument(
        "--home",
        type=Path,
        help="Storage directory (default: ~/.evotrace or $EVOTRACE_HOME)",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

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

    candidates = subparsers.add_parser(
        "candidates", help="Browse valuable training and evaluation candidates"
    )
    candidates.add_argument("--all", action="store_true", help="Include low-value candidates")
    candidates.add_argument("--min-score", type=int, default=4)
    candidates.add_argument(
        "--label",
        choices=[
            "useful",
            "human_corrected",
            "execution_verifiable",
            "training_ready_execution",
            "needs_hardening",
            "preference_candidate",
            "recovery_trajectory",
            "sft_candidate",
            "sensitive_content",
        ],
    )
    candidates.add_argument("--limit", type=int, default=20)
    candidates.add_argument("--json", action="store_true")

    search = subparsers.add_parser("search", help="Search mined trajectories")
    search.add_argument("query", nargs="+", help="Words to find in tasks, evidence, or repos")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="Explain one mined candidate or built asset")
    show.add_argument("candidate", help="Candidate number, session id, or unique id prefix")
    show.add_argument("--json", action="store_true")

    assets = subparsers.add_parser("assets", help="List compiled EvoTrace assets")
    assets.add_argument("--limit", type=int, default=20)
    assets.add_argument("--json", action="store_true")

    runs = subparsers.add_parser("runs", help="List saved verification runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--json", action="store_true")

    export = subparsers.add_parser("export", help="Export one built portable asset")
    export.add_argument("candidate", help="Candidate number, session id, or unique id prefix")
    export.add_argument("--output", type=Path)
    export.add_argument(
        "--allow-sensitive",
        action="store_true",
        help="Explicitly export a candidate marked as sensitive",
    )

    curate = subparsers.add_parser(
        "curate", help="Review mined evidence capsules with the EvoTrace Curator Agent"
    )
    curate.add_argument("--source", choices=["all", "codex", "claude"], default="all")
    curate.add_argument("--min-score", type=int, default=4)
    curate.add_argument("--limit", type=int, default=10)
    curate.add_argument("--session", help="Review one exact session id")
    curate.add_argument("--provider", choices=["openai"], default="openai")
    curate.add_argument("--model", default=DEFAULT_MODEL)
    curate.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh", "max"],
        default="medium",
    )
    curate.add_argument("--timeout", type=int, default=120)
    curate.add_argument(
        "--allow-upload",
        action="store_true",
        help="Explicitly allow sanitized evidence capsules to be sent to the provider",
    )
    curate.add_argument(
        "--refresh", action="store_true", help="Replace an existing model review"
    )
    curate.add_argument("--json", action="store_true")

    build = subparsers.add_parser("build", help="Build mined sessions into eval bundles")
    build.add_argument(
        "session", nargs="?", help="Specific session id (default: top executable candidates)"
    )
    build.add_argument("--limit", type=int, default=10)
    build.add_argument("--verify", action="append", default=[], help="Verifier command; repeatable")
    build.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild an existing asset and archive the previous generated bundle",
    )
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

    validate = subparsers.add_parser(
        "validate",
        help="Validate base and reference states inside the Docker sandbox",
    )
    validate.add_argument("benchmark", help="Bundle path, candidate number, or session id")
    validate.add_argument("--timeout", type=int, default=900)
    validate.add_argument("--rebuild-image", action="store_true")
    validate.add_argument("--json", action="store_true")

    calibrate = subparsers.add_parser(
        "calibrate",
        help="Self-play an asset and tune hints/verifiers toward a target pass rate",
    )
    calibrate.add_argument("benchmark", help="Bundle path, candidate number, or session id")
    calibrate.add_argument("--trials", type=int, default=5)
    calibrate.add_argument("--target-passes", type=int, default=2)
    calibrate.add_argument("--max-rounds", type=int, default=3)
    calibrate.add_argument("--timeout", type=int, default=600)
    calibrate.add_argument("--initial-hints", type=int, default=0)
    calibrate.add_argument(
        "--allow-upload",
        action="store_true",
        help="Allow task-only solver context to be sent to the configured DeepSeek provider",
    )
    calibrate.add_argument("--json", action="store_true")

    harden = subparsers.add_parser(
        "harden",
        help="Derive, validate, and self-play a harder child task from a verified seed",
    )
    harden.add_argument("benchmark", help="Verified parent bundle path, candidate number, or id")
    harden.add_argument("--trials", type=int, default=5)
    harden.add_argument("--target-passes", type=int, default=2)
    harden.add_argument("--timeout", type=int, default=600)
    harden.add_argument(
        "--allow-upload",
        action="store_true",
        help="Allow the solved seed task to reach the configured DeepSeek provider",
    )
    harden.add_argument("--json", action="store_true")

    evolve = subparsers.add_parser(
        "evolve",
        help="Explore, compress, and test execution experience on a held-out task",
    )
    evolve.add_argument("source", help="Source bundle path, candidate number, or session id")
    evolve.add_argument(
        "--held-out",
        help=(
            "Independent same-repository bundle used for paired evaluation "
            "(defaults to source for a non-certifying smoke test)"
        ),
    )
    evolve.add_argument("--trials", type=int, default=5)
    evolve.add_argument("--target-passes", type=int, default=2)
    evolve.add_argument("--timeout", type=int, default=600)
    evolve.add_argument(
        "--allow-upload",
        action="store_true",
        help="Allow task and sanitized execution context to reach the configured provider",
    )
    evolve.add_argument("--json", action="store_true")

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


def _hint(command: str) -> str:
    return f"/{command}" if os.environ.get("EVOTRACE_SLASH_MODE") == "1" else f"et {command}"


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
        console(f"No sessions yet. Try: {_hint('import')}")
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


def _codex_integration_status() -> tuple[str, str]:
    cli_status, cli_detail = _tool_version("codex")
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    history_files = []
    sessions = codex_home / "sessions"
    if sessions.is_dir():
        history_files.extend(sessions.rglob("*.jsonl"))
    history = codex_home / "history.jsonl"
    if history.is_file():
        history_files.append(history)
    history_count = len({item.resolve() for item in history_files})
    if history_count:
        cli_note = f"CLI {cli_detail}" if cli_status == "OK" else f"CLI unavailable ({cli_detail})"
        return "OK", f"history import available ({history_count} file(s)); {cli_note}"
    if cli_status == "OK":
        return "OK", f"{cli_detail}; no local history found"
    return cli_status, cli_detail


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
    console(f"Training-ready execution      {summary.training_ready_execution}")
    console(f"Needs hardening               {summary.needs_hardening}")
    console(f"Preference candidates         {summary.preference_candidates}")
    console(f"Recovery trajectories         {summary.recovery_trajectories}")
    console(f"Low-value / trivial           {summary.low_value}")
    console(f"Non-coding / personal         {summary.non_coding}")
    console(f"Sensitive content             {summary.sensitive}")
    console("\nCurator: evidence + difficulty heuristic v0.4 (no model or data upload)")


def _candidate_rows(store: Store, candidates: List[dict]) -> List[dict]:
    global_numbers = {
        str(candidate.get("session_id")): index
        for index, candidate in enumerate(visible_candidates(store), 1)
    }
    rows = []
    for candidate in candidates:
        session_id = str(candidate.get("session_id") or "")
        try:
            _, trajectory = store.load_session(session_id)
            stages = readiness(store, candidate, trajectory)
        except EvoTraceError:
            stages = []
        types = asset_types(candidate)
        rows.append(
            {
                "number": global_numbers.get(session_id),
                "session_id": candidate.get("session_id"),
                "score": candidate.get("score", 0),
                "difficulty": candidate.get("difficulty") or {},
                "source": candidate.get("source") or "--",
                "asset_types": types,
                "readiness": f"{sum(done for _, done in stages)}/{len(stages)}" if stages else "--",
                "status": readiness_status(stages) if stages else "Unavailable",
                "task": candidate_task(store, candidate),
                "labels": candidate.get("labels") or [],
            }
        )
    return rows


def _print_candidates(store: Store, candidates: List[dict], *, as_json: bool) -> None:
    rows = _candidate_rows(store, candidates)
    if as_json:
        console(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        console(f"No matching candidates. Run `{_hint('mine')}` or loosen the filters.")
        return
    console("EvoTrace Candidates\n")
    console(
        f"{'#':>3}  {'VALUE':>5}  {'DIFF':<8}  {'SOURCE':<8}  "
        f"{'ASSET TYPE':<22}  {'READY':<7}  TASK"
    )
    for row in rows:
        types = compact(" + ".join(row["asset_types"]) or "Candidate", 22)
        number = str(row["number"]) if row["number"] else "--"
        console(
            f"{number:>3}  {row['score']:>5}  "
            f"{str((row['difficulty'] or {}).get('tier') or '--'):<8}  "
            f"{str(row['source']):<8}  "
            f"{types:<22}  {row['readiness']:<7}  {compact(row['task'], 62)}"
        )
    console(f"\nShowing {len(rows)} candidate(s).")
    console(f"Open:  {_hint('show 1')}")
    console(f"Build: {_hint('build 1')}")


def _repository_label(trajectory: dict) -> str:
    repository = trajectory.get("repository") or {}
    origin = str(repository.get("origin") or "").rstrip("/")
    if origin:
        name = origin.removesuffix(".git").rsplit("/", 2)
        return "/".join(name[-2:]) if len(name) >= 2 else name[-1]
    root = repository.get("root")
    return Path(root).name if root else "Unavailable"


def _candidate_detail(store: Store, candidate: dict) -> dict:
    session_id = str(candidate.get("session_id") or "")
    _, trajectory = store.load_session(session_id)
    stages = readiness(store, candidate, trajectory)
    review = ((candidate.get("model_review") or {}).get("review") or {})
    bundle = store.benchmarks / session_id
    runs = [record for _, record in related_runs(store, session_id)]
    return {
        "session_id": session_id,
        "task": candidate_task(store, candidate),
        "source": candidate.get("source"),
        "repository": _repository_label(trajectory),
        "score": candidate.get("score", 0),
        "difficulty": candidate.get("difficulty") or {},
        "labels": candidate.get("labels") or [],
        "asset_types": asset_types(candidate),
        "evidence": candidate.get("evidence") or [],
        "signals": candidate.get("signals") or {},
        "model_review": candidate.get("model_review"),
        "rationale": review.get("rationale"),
        "missing_requirements": review.get("missing_requirements") or [],
        "readiness": [{"stage": name, "complete": done} for name, done in stages],
        "status": readiness_status(stages),
        "bundle": str(bundle) if (bundle / "task.json").exists() else None,
        "runs": runs,
    }


def _print_candidate_detail(store: Store, candidate: dict, *, as_json: bool) -> None:
    detail = _candidate_detail(store, candidate)
    if as_json:
        console(json.dumps(detail, indent=2, ensure_ascii=False))
        return
    task = detail["task"] or "Untitled coding session"
    console(compact(task, 96))
    console("─" * min(96, max(40, len(compact(task, 96)))))
    console(f"\nSession      {detail['session_id']}")
    console(f"Source       {detail['source'] or '--'}")
    console(f"Repository   {detail['repository']}")
    console(f"Score        {detail['score']} / 10")
    difficulty = detail["difficulty"]
    console(
        f"Difficulty   {difficulty.get('tier', 'unknown')} "
        f"({difficulty.get('score', 0)} / 10)"
    )
    console(f"Status       {detail['status']}")
    console(f"Detected     {' · '.join(detail['asset_types']) or 'Candidate'}")

    console("\nWhy valuable")
    evidence = detail["evidence"]
    if evidence:
        for item in evidence:
            console(f"  ✓ {item}")
    else:
        console("  ○ No positive evidence recorded")
    console("\nDifficulty evidence")
    for item in difficulty.get("evidence") or []:
        console(f"  ✓ {item}")
    if not difficulty.get("training_eligible", False):
        console("  ○ Below the training-ready gate; use /harden before RL or marketplace use")
    if detail.get("rationale"):
        console(f"\nCurator      {compact(detail['rationale'], 180)}")

    console("\nTraining assets")
    for value in detail["asset_types"]:
        console(f"  ✓ {value} candidate")
    if not detail["asset_types"]:
        console("  ○ No reusable asset type assigned")

    console("\nRL readiness")
    for stage in detail["readiness"]:
        console(f"  {'✓' if stage['complete'] else '○'} {stage['stage']}")
    if detail["missing_requirements"]:
        console("\nStill needed")
        for item in detail["missing_requirements"]:
            console(f"  ○ {item}")

    if detail["bundle"]:
        console(f"\nBundle       {detail['bundle']}")
        if detail["status"] == "Verified":
            next_command = _hint("export " + detail["session_id"])
            console(f"Next:         {next_command}")
        else:
            console(f"Next:         {_hint('validate ' + detail['session_id'])}")
    elif "execution_verifiable" in detail["labels"]:
        next_command = _hint("build " + detail["session_id"])
        console(f"\nNext:         {next_command}")
    else:
        console("\nNext:         review this candidate before export or training use")


def _asset_rows(store: Store) -> List[dict]:
    candidate_numbers = {
        str(candidate.get("session_id")): index
        for index, candidate in enumerate(visible_candidates(store), 1)
    }
    rows = []
    for path, manifest in store.list_bundles():
        session_id = str(manifest.get("id") or path.name)
        candidate = next(
            (item for item in store.list_candidates() if item.get("session_id") == session_id),
            {},
        )
        verified = has_matching_passed_run(store, session_id, path)
        difficulty = candidate.get("difficulty") or manifest.get("difficulty") or {}
        calibration_status = next(
            (
                str((record.get("result") or {}).get("status") or "")
                for _, record in related_runs(store, session_id)
                if record.get("kind") == "self-play-calibration"
            ),
            "",
        )
        if verified:
            status = "Verified"
        elif bundle_manifest_is_runnable(manifest):
            status = "Bundle ready"
        else:
            status = "Needs rebuild"
        if calibration_status == "too_easy":
            status += " · Too easy"
        rows.append(
            {
                "number": candidate_numbers.get(session_id),
                "session_id": session_id,
                "status": status,
                "difficulty": difficulty,
                "asset_types": asset_types(candidate),
                "verifier_commands": len((manifest.get("verifier") or {}).get("commands") or []),
                "task": manifest.get("task") or "",
                "bundle": str(path),
            }
        )
    return sorted(
        rows,
        key=lambda row: int((row.get("difficulty") or {}).get("score") or 0),
        reverse=True,
    )


def _print_assets(store: Store, *, limit: int, as_json: bool) -> None:
    rows = _asset_rows(store)[:limit]
    if as_json:
        console(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        console(
            f"No compiled assets yet. Run `{_hint('candidates')}`, then `{_hint('build 1')}`."
        )
        return
    console("EvoTrace Assets\n")
    console(f"{'#':>3}  {'STATUS':<21}  {'DIFF':<8}  {'CHECKS':>6}  {'TYPE':<20}  TASK")
    for row in rows:
        number = str(row["number"]) if row["number"] else "--"
        types = compact(" + ".join(row["asset_types"]) or "Executable bundle", 20)
        difficulty = str((row["difficulty"] or {}).get("tier") or "--")
        console(
            f"{number:>3}  {row['status']:<21}  {difficulty:<8}  "
            f"{row['verifier_commands']:>6}  {types:<20}  {compact(row['task'], 60)}"
        )
    console("\nInspect: et show <number-or-id>")
    console("Export:  et export <number-or-id>")


def _print_runs(store: Store, *, limit: int, as_json: bool) -> None:
    rows = [record for _, record in store.list_runs()[:limit]]
    if as_json:
        console(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        console("No saved verification runs yet.")
        return
    console("EvoTrace Runs\n")
    console(f"{'RESULT':<23}  {'KIND':<21}  {'SCORE':>7}  {'SESSION':<38}  CREATED")
    for row in rows:
        if row.get("kind") in {"self-play-calibration", "experience-evaluation"}:
            result = str(((row.get("result") or {}).get("status") or "unknown")).upper()
        else:
            result = "PASS" if row.get("passed") else "FAIL"
        score = row.get("score")
        score_text = f"{float(score) * 100:.1f}%" if score is not None else "--"
        console(
            f"{result:<23}  {str(row.get('kind') or '--'):<21}  {score_text:>7}  "
            f"{compact(row.get('session_id'), 38):<38}  {row.get('created_at') or '--'}"
        )


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


def _bundle_target(store: Store, selector: str) -> str:
    if selector.isdigit():
        return str(resolve_candidate(store, selector).get("session_id") or selector)
    return selector


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    store = Store(args.home) if args.home else Store()
    if args.subcommand is None:
        from .interactive import run_interactive

        return run_interactive(
            store,
            execute=lambda command: main(["--home", str(store.root), *command]),
        )
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
            console(f"Training-ready execution      {mined.training_ready_execution}")
            console(f"Needs hardening               {mined.needs_hardening}")
            console(f"Recovery trajectories         {mined.recovery_trajectories}")
            visible = [
                item
                for item in visible_candidates(store)
                if args.source == "all" or item.get("source") == args.source
            ]
            console(f"Top-level candidates          {len(visible)}")
            console("\nEverything stayed local. No model or data upload was used.")
            top = visible[:5]
            if top:
                console("")
                _print_candidates(store, top, as_json=False)
            else:
                console(f"\nNext: {_hint('mine --json')}")
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
            console(f"Next: {_hint('mine')}, then {_hint('build ' + trajectory['session_id'])}")
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
            console(f"\nNext: {_hint('mine')}")
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
                top = [
                    item
                    for item in visible_candidates(store, minimum_score=args.min_score)
                    if args.source == "all" or item.get("source") == args.source
                ][:5]
                if top:
                    console("")
                    _print_candidates(store, top, as_json=False)
            return 0

        if args.subcommand == "candidates":
            if not 0 <= args.min_score <= 10:
                raise EvoTraceError("--min-score must be between 0 and 10")
            if args.limit < 1:
                raise EvoTraceError("--limit must be at least 1")
            candidates = visible_candidates(
                store,
                include_all=args.all,
                minimum_score=args.min_score,
                label=args.label,
            )[: args.limit]
            _print_candidates(store, candidates, as_json=args.json)
            return 0

        if args.subcommand == "search":
            if args.limit < 1:
                raise EvoTraceError("--limit must be at least 1")
            query = " ".join(args.query)
            _print_candidates(
                store,
                search_candidates(store, query, limit=args.limit),
                as_json=args.json,
            )
            return 0

        if args.subcommand == "show":
            candidate = resolve_candidate(store, args.candidate)
            _print_candidate_detail(store, candidate, as_json=args.json)
            return 0

        if args.subcommand == "assets":
            if args.limit < 1:
                raise EvoTraceError("--limit must be at least 1")
            _print_assets(store, limit=args.limit, as_json=args.json)
            return 0

        if args.subcommand == "runs":
            if args.limit < 1:
                raise EvoTraceError("--limit must be at least 1")
            _print_runs(store, limit=args.limit, as_json=args.json)
            return 0

        if args.subcommand == "export":
            destination = export_asset(
                store,
                args.candidate,
                output=args.output,
                allow_sensitive=args.allow_sensitive,
            )
            console("Portable asset exported.")
            console(f"Archive: {destination}")
            console("Nothing was uploaded; this archive remains local until you move it.")
            return 0

        if args.subcommand == "curate":
            if not 0 <= args.min_score <= 10:
                raise EvoTraceError("--min-score must be between 0 and 10")
            if args.timeout < 1:
                raise EvoTraceError("--timeout must be at least 1 second")
            if not args.allow_upload:
                raise EvoTraceError(
                    "Cloud curation is disabled by default. Re-run with --allow-upload after "
                    "reviewing the evidence-capsule policy."
                )
            # Re-run deterministic mining first so the model always receives the
            # latest local evidence. Existing model provenance is preserved.
            mine_store(store, source=args.source)
            agent = OpenAICuratorAgent(
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                timeout=args.timeout,
            )
            summary = curate_store(
                store,
                agent=agent,
                allow_upload=args.allow_upload,
                source=args.source,
                minimum_score=args.min_score,
                limit=args.limit,
                session_id=args.session,
                refresh=args.refresh,
            )
            if args.json:
                console(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
            else:
                console(f"Curator model                 {summary.model}")
                console(f"Candidates considered         {summary.considered}")
                console(f"Reviewed                      {summary.reviewed}")
                console(f"Keep                          {summary.kept}")
                console(f"Needs review                  {summary.needs_review}")
                console(f"Exclude                       {summary.excluded}")
                console(f"Sensitive skipped locally     {summary.skipped_sensitive}")
                console(f"Existing reviews skipped      {summary.skipped_existing}")
                console(f"Failed                        {summary.failed}")
            return 1 if summary.failed else 0

        if args.subcommand == "build":
            session_id = args.session
            if session_id:
                session_id = str(resolve_candidate(store, session_id).get("session_id") or "")
                console(f"Building candidate {session_id}...")
            results = build_mined_candidates(
                store,
                session_id=session_id,
                limit=args.limit,
                extra_commands=args.verify,
                rebuild=args.rebuild,
            )
            if args.json:
                console(json.dumps([asdict(item) for item in results], indent=2))
            else:
                console(f"{'SESSION':<40} {'STATUS':<15} BUNDLE / REASON")
                for item in results:
                    detail = item.bundle or item.error or ""
                    console(f"{item.session_id:<40} {item.status:<15} {detail}")
                built = sum(
                    item.status in {"built", "rebuilt", "already_built"} for item in results
                )
                console(f"\n{built}/{len(results)} eval bundle(s) ready.")
                if built:
                    console(f"Inspect: et show {results[0].session_id}")
                    console(f"Validate: et validate {results[0].session_id}")
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
            session_id = args.session
            if session_id.isdigit():
                session_id = str(resolve_candidate(store, session_id).get("session_id") or "")
            bundle, manifest = compile_session(
                session_id,
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
            console(f"\nNext: {_hint('replay latest --dest /tmp/evotrace-task')}")
            return 0

        if args.subcommand == "replay":
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            workspace = replay_bundle(bundle, args.dest, args.run)
            console(f"Workspace ready: {workspace}")
            console(f"Task: {bundle / 'task.md'}")
            return 0

        if args.subcommand == "verify":
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            report = verify_candidate(bundle, args.repo.resolve())
            manifest = read_json(bundle / "task.json")
            run_dir, _ = record_run(
                store,
                kind="verify",
                session_id=str(manifest.get("id") or bundle.name),
                result=report,
                passed=bool(report["passed"]),
                score=float(report["score"]),
                bundle=bundle,
            )
            if args.json:
                console(json.dumps(report, indent=2))
            else:
                for check in report["checks"]:
                    console(f"[{'PASS' if check['passed'] else 'FAIL'}] {check['name']}")
                console(f"Score: {report['score'] * 100:.1f}%")
                console(f"Run:   {run_dir}")
            return 0 if report["passed"] else 1

        if args.subcommand == "validate":
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            report = validate_bundle_in_docker(
                bundle,
                timeout=args.timeout,
                rebuild_image=args.rebuild_image,
            )
            manifest = read_json(bundle / "task.json")
            run_dir, _ = record_run(
                store,
                kind="docker-validation",
                session_id=str(manifest.get("id") or bundle.name),
                result=report,
                passed=bool(report["passed"]),
                score=float((report.get("reference") or {}).get("score") or 0.0),
                bundle=bundle,
            )
            if args.json:
                console(json.dumps(report, indent=2))
            else:
                base_gate = (report.get("gates") or {}).get("base_behavioral_failure")
                reference_gate = (report.get("gates") or {}).get("reference_passes")
                console(f"[{'PASS' if base_gate else 'FAIL'}] base state is rejected")
                console(f"[{'PASS' if reference_gate else 'FAIL'}] reference state is accepted")
                console(f"Result: {'Verified' if report['passed'] else 'Not verified'}")
                console(f"Image:  {report['image']['tag']}")
                console(f"Run:    {run_dir}")
            return 0 if report["passed"] else 1

        if args.subcommand == "calibrate":
            if not args.allow_upload:
                raise EvoTraceError(
                    "Self-play calls the configured DeepSeek provider. Re-run with "
                    "--allow-upload after reviewing the task-only upload policy."
                )
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            outcome = calibrate_bundle(
                bundle,
                store=store,
                trials=args.trials,
                target_passes=args.target_passes,
                max_rounds=args.max_rounds,
                timeout=args.timeout,
                initial_hints=args.initial_hints,
                progress=None if args.json else console,
            )
            if args.json:
                console(json.dumps(outcome.report, indent=2, ensure_ascii=False))
            else:
                console("\nEvoTrace Self-Play Calibration")
                console(f"Result:       {outcome.pass_count}/{outcome.trials} passed")
                console(f"Target:       {outcome.target_passes}/{outcome.trials}")
                console(f"Status:       {outcome.status}")
                console(f"Rounds:       {outcome.rounds}")
                console(f"Calibration:  {outcome.calibration_dir}")
                console(f"Run:          {outcome.run_dir}")
                if outcome.status == "too_easy":
                    console(
                        "Recommendation: retire this task from the target curriculum bucket "
                        "or generate a semantically harder task; correct solutions were not "
                        "artificially rejected."
                    )
            return 0

        if args.subcommand == "harden":
            if not args.allow_upload:
                raise EvoTraceError(
                    "Task hardening calls the configured DeepSeek provider. Re-run with "
                    "--allow-upload after reviewing the solved-seed upload policy."
                )
            if args.trials < 1:
                raise EvoTraceError("--trials must be at least 1")
            if not 0 <= args.target_passes <= args.trials:
                raise EvoTraceError("--target-passes must be between 0 and --trials")
            if args.timeout < 1:
                raise EvoTraceError("--timeout must be at least 1 second")
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            manifest = read_json(bundle / "task.json")
            session_id = str(manifest.get("id") or bundle.name)
            if not has_matching_passed_run(store, session_id, bundle):
                raise EvoTraceError(
                    "Hardening requires a verified parent bundle for the exact current digest. "
                    f"Run `{_hint('validate ' + session_id)}` first."
                )
            outcome = harden_bundle(
                bundle,
                store=store,
                trials=args.trials,
                target_passes=args.target_passes,
                timeout=args.timeout,
                progress=None if args.json else console,
            )
            if args.json:
                console(json.dumps(outcome.report, indent=2, ensure_ascii=False))
            else:
                console("\nEvoTrace Task Hardening")
                console(f"Parent:       {session_id}")
                console(f"Child:        {outcome.child_id}")
                console(f"Status:       {outcome.report['status']}")
                console(
                    f"Self-play:    {outcome.calibration.pass_count}/"
                    f"{outcome.calibration.trials} passed"
                )
                console(f"Bundle:       {outcome.bundle}")
                console(f"Validation:   {outcome.validation_run}")
            return 0

        if args.subcommand == "evolve":
            if not args.allow_upload:
                raise EvoTraceError(
                    "Evolution calls the configured DeepSeek provider. Re-run with "
                    "--allow-upload after reviewing the task and sanitized trajectory policy."
                )
            source_bundle = resolve_bundle(_bundle_target(store, args.source), store)
            held_out_selector = args.held_out or args.source
            held_out_bundle = resolve_bundle(_bundle_target(store, held_out_selector), store)
            outcome = evolve_experience(
                source_bundle,
                held_out_bundle,
                store=store,
                trials=args.trials,
                target_passes=args.target_passes,
                timeout=args.timeout,
                progress=None if args.json else console,
            )
            if args.json:
                console(json.dumps(outcome.report, indent=2, ensure_ascii=False))
            else:
                paired = outcome.report["paired_evaluation"]
                console("\nEvoTrace Execution Experience Evolution")
                console(
                    f"Baseline:        {outcome.baseline_passes}/{outcome.trials} passed"
                )
                console(
                    f"With experience: {outcome.conditioned_passes}/{outcome.trials} passed"
                )
                console(f"Absolute gain:   {paired['absolute_gain']:+.1%}")
                console(f"Utility:         {outcome.utility}")
                console(f"Status:          {outcome.status}")
                console(
                    "Held-out:        "
                    + (
                        "independent + validated"
                        if outcome.report["held_out_independent"]
                        and outcome.report.get("held_out_validated")
                        else "not certifiably independent"
                    )
                )
                console(f"Curriculum:      {outcome.report['curriculum']['direction']}")
                console(f"Evolution:       {outcome.evolution_dir}")
                console(f"Run:             {outcome.run_dir}")
                if outcome.status == "smoke_only":
                    console(
                        "Note: this evaluation checks the wiring only. Supply a distinct, validated "
                        "same-repository asset with --held-out to test functional compression."
                    )
            return 0

        if args.subcommand == "benchmark":
            bundle = resolve_bundle(_bundle_target(store, args.benchmark), store)
            results = benchmark(
                bundle,
                agents=args.agent,
                candidates=args.candidate,
                timeout=args.timeout,
                store=store,
            )
            manifest = read_json(bundle / "task.json")
            passed = all(item["passed"] for item in results)
            score = sum(float(item["score"]) for item in results) / len(results)
            record_run(
                store,
                kind="benchmark",
                session_id=str(manifest.get("id") or bundle.name),
                result=results,
                passed=passed,
                score=score,
                bundle=bundle,
            )
            _print_benchmark(results, args.json)
            return 0 if passed else 1

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
            tools = ["git", "docker"]
            console("EvoTrace integrations\n")
            for tool in tools:
                status, detail = _tool_version(tool)
                console(f"[{status}] {tool:<8} {detail}")
            status, detail = _codex_integration_status()
            console(f"[{status}] {'codex':<8} {detail}")
            status, detail = _tool_version("claude")
            console(f"[{status}] {'claude':<8} {detail}")
            key_status = "set" if os.environ.get("OPENAI_API_KEY") else "not set (optional)"
            console(f"[{'OK' if os.environ.get('OPENAI_API_KEY') else '--'}] openai  {key_status}")
            console(f"\nStorage: {store.root}")
            return 0
    except EvoTraceError as exc:
        console(f"error: {exc}", error=True)
        return 2
    return 0
