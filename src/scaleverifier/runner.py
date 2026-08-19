from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ScaleVerifierError
from .gitops import setup_from_bundle
from .store import Store
from .util import compact_timestamp, parse_name_value, slugify, write_json


def resolve_bundle(target: str, store: Store) -> Path:
    direct = Path(target).expanduser()
    if direct.is_dir() and (direct / "task.json").exists():
        return direct.resolve()
    if target == "latest":
        bundles = sorted(
            [item for item in store.benchmarks.glob("*") if (item / "task.json").exists()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if bundles:
            return bundles[0]
    candidate = store.benchmarks / target
    if candidate.is_dir() and (candidate / "task.json").exists():
        return candidate.resolve()
    raise ScaleVerifierError(
        f"Benchmark not found: {target}. Compile the session first with `scaleverifier compile {target}`."
    )


def verify_candidate(bundle: Path, repo: Path) -> Dict[str, Any]:
    verifier = bundle / "verifier.py"
    if not verifier.exists():
        raise ScaleVerifierError(f"Bundle has no verifier.py: {bundle}")
    result = subprocess.run(
        [sys.executable, str(verifier), "--repo", str(repo), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ScaleVerifierError(
            f"Verifier did not return JSON:\n{result.stdout}\n{result.stderr}"
        ) from exc
    report["verifier_exit_code"] = result.returncode
    return report


def replay_bundle(
    bundle: Path,
    destination: Path,
    command: Optional[str] = None,
) -> Path:
    workspace = setup_from_bundle(bundle, destination.resolve())
    if command:
        task = (bundle / "task.md").read_text(encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "SCALEVERIFIER_TASK": task,
                "SCALEVERIFIER_TASK_FILE": str((bundle / "task.md").resolve()),
                "SCALEVERIFIER_WORKSPACE": str(workspace),
            }
        )
        result = subprocess.run(shlex.split(command), cwd=workspace, env=environment)
        if result.returncode != 0:
            raise ScaleVerifierError(f"Replay command exited with status {result.returncode}")
    return workspace


def _run_agent(
    *,
    bundle: Path,
    store: Store,
    name: str,
    command: str,
    timeout: int,
) -> Dict[str, Any]:
    run_dir = store.runs / bundle.name / f"{compact_timestamp()}-{slugify(name)}"
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True)
    setup_from_bundle(bundle, workspace)
    task = (bundle / "task.md").read_text(encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "SCALEVERIFIER_TASK": task,
            "SCALEVERIFIER_TASK_FILE": str((bundle / "task.md").resolve()),
            "SCALEVERIFIER_WORKSPACE": str(workspace.resolve()),
        }
    )
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        agent_exit_code = process.returncode
        output = process.stdout
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        agent_exit_code = 124
        output = (exc.stdout or "") + "\nTIMEOUT"
        timed_out = True
    duration = round(time.monotonic() - started, 3)
    (run_dir / "agent.log").write_text(output, encoding="utf-8", errors="replace")
    report = verify_candidate(bundle, workspace)
    result = {
        "name": name,
        "command": command,
        "agent_exit_code": agent_exit_code,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "passed": report["passed"],
        "score": report["score"],
        "verifier": report,
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "result.json", result)
    return result


def _score_candidate(bundle: Path, name: str, path: str) -> Dict[str, Any]:
    repo = Path(path).expanduser().resolve()
    if not repo.is_dir():
        raise ScaleVerifierError(f"Candidate path is not a directory: {repo}")
    report = verify_candidate(bundle, repo)
    return {
        "name": name,
        "command": None,
        "agent_exit_code": None,
        "timed_out": False,
        "duration_seconds": sum(check.get("duration_seconds", 0) for check in report["checks"]),
        "passed": report["passed"],
        "score": report["score"],
        "verifier": report,
        "run_dir": str(repo),
    }


def benchmark(
    bundle: Path,
    *,
    agents: List[str],
    candidates: List[str],
    timeout: int,
    store: Store,
) -> List[Dict[str, Any]]:
    if not agents and not candidates:
        raise ScaleVerifierError(
            "Provide at least one --agent NAME=COMMAND or --candidate NAME=PATH"
        )
    results = []
    for value in agents:
        name, command = parse_name_value(value, "--agent")
        results.append(
            _run_agent(
                bundle=bundle,
                store=store,
                name=name,
                command=command,
                timeout=timeout,
            )
        )
    for value in candidates:
        name, path = parse_name_value(value, "--candidate")
        results.append(_score_candidate(bundle, name, path))
    return results
