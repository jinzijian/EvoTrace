from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import EvoTraceError
from .gitops import setup_from_bundle
from .store import Store
from .util import parse_name_value


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
    raise EvoTraceError(
        f"Benchmark not found: {target}. Build the session first with `evotrace build {target}`."
    )


def verify_candidate(bundle: Path, repo: Path) -> Dict[str, Any]:
    verifier = bundle / "verifier.py"
    if not verifier.exists():
        raise EvoTraceError(f"Bundle has no verifier.py: {bundle}")
    result = subprocess.run(
        [sys.executable, str(verifier), "--repo", str(repo), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvoTraceError(
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
                "EVOTRACE_TASK": task,
                "EVOTRACE_TASK_FILE": str((bundle / "task.md").resolve()),
                "EVOTRACE_WORKSPACE": str(workspace),
                "SCALEVERIFIER_TASK": task,
                "SCALEVERIFIER_TASK_FILE": str((bundle / "task.md").resolve()),
                "SCALEVERIFIER_WORKSPACE": str(workspace),
            }
        )
        result = subprocess.run(shlex.split(command), cwd=workspace, env=environment)
        if result.returncode != 0:
            raise EvoTraceError(f"Replay command exited with status {result.returncode}")
    return workspace


def _score_candidate(bundle: Path, name: str, path: str) -> Dict[str, Any]:
    repo = Path(path).expanduser().resolve()
    if not repo.is_dir():
        raise EvoTraceError(f"Candidate path is not a directory: {repo}")
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
        raise EvoTraceError("Provide at least one --candidate NAME=PATH")
    if agents:
        raise EvoTraceError(
            "Host agent execution is disabled by the sandbox contract. "
            "Build the eval with `evotrace build`, then run the agent inside the generated Docker image."
        )
    results = []
    for value in candidates:
        name, path = parse_name_value(value, "--candidate")
        results.append(_score_candidate(bundle, name, path))
    return results
