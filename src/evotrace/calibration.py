from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .catalog import bundle_sha256, record_run, related_runs
from .errors import EvoTraceError
from .gitops import (
    DEFAULT_IGNORED_PARTS,
    SENSITIVE_UNTRACKED_NAMES,
    SENSITIVE_UNTRACKED_SUFFIXES,
    changed_paths,
    git,
    setup_from_bundle,
)
from .store import Store
from .util import read_json, short_id, utc_now, write_json
from .validation import (
    evaluate_candidate_patch_in_docker,
    evaluate_reference_with_verifier_in_docker,
)

CALIBRATION_PROTOCOL = "deepseek-self-play-v0.1"
MAX_PATCH_BYTES = 20 * 1024 * 1024


class Solver(Protocol):
    def solve(self, workspace: Path, task: str, *, timeout: int) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class CalibrationOutcome:
    calibration_id: str
    calibration_dir: Path
    run_dir: Path
    status: str
    pass_count: int
    trials: int
    target_passes: int
    rounds: int
    report: Dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dsh_executable() -> Path:
    suffix = "dsh.cmd" if os.name == "nt" else "dsh"
    local = _project_root() / "node_modules" / ".bin" / suffix
    if local.exists():
        return local
    discovered = shutil.which("dsh")
    if discovered:
        return Path(discovered)
    raise EvoTraceError("DeepSeek Harness is unavailable. Run `pnpm install` first.")


def _session_files(dsh_home: Path) -> set[Path]:
    sessions = dsh_home / "sessions"
    return set(sessions.glob("**/session.jsonl.zstd")) if sessions.exists() else set()


def _read_session_metadata(
    session_path: Optional[Path],
    *,
    workspace: Path,
    forbidden_roots: List[Path],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "session_id": session_path.parent.name if session_path else None,
        "provider": None,
        "model": None,
        "tool_calls": 0,
        "observed_external_file_access": None,
    }
    if session_path is None:
        return metadata
    zstd = shutil.which("zstd")
    if not zstd:
        return metadata
    decoded = subprocess.run(
        [zstd, "-dc", str(session_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if decoded.returncode != 0:
        return metadata
    external = False
    workspace_text = str(workspace.resolve())
    forbidden = [str(path.resolve()) for path in forbidden_roots]

    def outside_workspace(value: str) -> bool:
        if not value.startswith("/"):
            return ".." in Path(value).parts
        if value.startswith(workspace_text):
            return False
        return not value.startswith(("/usr/", "/bin/", "/opt/homebrew/", "/dev/"))

    for line in decoded.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "request/header" and not metadata["model"]:
            config = ((data.get("header") or {}).get("config") or {})
            metadata["provider"] = config.get("provider")
            metadata["model"] = config.get("model")
        if event_type != "tool/call":
            continue
        metadata["tool_calls"] += 1
        arguments = str(data.get("arguments") or "")
        if any(root and root in arguments for root in forbidden):
            external = True
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = {}
        for key in ("file_path", "path", "workdir"):
            value = parsed.get(key)
            if isinstance(value, str) and outside_workspace(value):
                external = True
        command = parsed.get("command")
        if isinstance(command, str):
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = command.split()
            if any(outside_workspace(token) for token in tokens if token.startswith(("/", ".."))):
                external = True
    metadata["observed_external_file_access"] = external
    return metadata


class DeepSeekHarnessSolver:
    def __init__(
        self,
        store: Store,
        *,
        permission_mode: str = "workspace-write",
    ) -> None:
        self.store = store
        self.dsh_home = Path(
            os.environ.get("DSH_HOME") or store.root / "harness"
        ).expanduser().resolve()
        self.permission_mode = permission_mode
        self.executable = _dsh_executable()
        credentials = self.dsh_home / ".credentials.yaml"
        if not credentials.exists():
            raise EvoTraceError(
                "DeepSeek Harness has no configured credentials. Open EvoTrace Settings "
                "and configure a DeepSeek provider first."
            )

    def run_prompt(self, workspace: Path, prompt: str, *, timeout: int) -> Dict[str, Any]:
        before = _session_files(self.dsh_home)
        environment = os.environ.copy()
        environment.update(
            {
                "DSH_HOME": str(self.dsh_home),
                "DSH_PERMISSION_MODE": self.permission_mode,
                "DSH_TELEMETRY_DISABLED": "1",
            }
        )
        started = utc_now()
        try:
            result = subprocess.run(
                [str(self.executable), "--profile", "headless", prompt],
                cwd=workspace,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            result = subprocess.CompletedProcess(
                [str(self.executable), "--profile", "headless"],
                124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
            )
            timed_out = True
        after = _session_files(self.dsh_home)
        created = sorted(
            after - before,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        session_path = created[0] if created else None
        session = _read_session_metadata(
            session_path,
            workspace=workspace,
            forbidden_roots=[self.store.root, _project_root()],
        )
        return {
            "started_at": started,
            "finished_at": utc_now(),
            "exit_code": result.returncode,
            "timed_out": timed_out,
            "stdout": str(result.stdout)[-16000:],
            "stderr": str(result.stderr)[-8000:],
            "session_log_path": str(session_path) if session_path else None,
            "permission_mode": self.permission_mode,
            **session,
        }

    def solve(self, workspace: Path, task: str, *, timeout: int) -> Dict[str, Any]:
        prompt = (
            "You are solving an EvoTrace self-play coding task in the current directory. "
            "Work only inside the current directory. Do not inspect parent directories, "
            "EvoTrace stores, session data, hidden tests, reference patches, or external files. "
            "Do not use the web. Inspect the repository, make the smallest correct change, run "
            "the visible tests you can find, and finish with a concise report.\n\n"
            f"Task:\n{task.strip()}"
        )
        return self.run_prompt(workspace, prompt, timeout=timeout)


def _patch_id(path: Path) -> Optional[str]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=path.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.decode("utf-8", errors="replace").split()[0]


def _sensitive_path(path: str) -> bool:
    value = Path(path)
    return bool(
        value.name in SENSITIVE_UNTRACKED_NAMES
        or value.suffix.lower() in SENSITIVE_UNTRACKED_SUFFIXES
        or any(part in DEFAULT_IGNORED_PARTS for part in value.parts)
    )


def _capture_solution_patch(workspace: Path, destination: Path) -> tuple[List[str], Optional[str]]:
    paths = changed_paths(workspace)
    unsafe = [path for path in paths if _sensitive_path(path)]
    if unsafe:
        raise EvoTraceError(
            "Solver created or modified excluded paths: " + ", ".join(unsafe[:10])
        )
    raw_untracked = [
        item
        for item in git(
            workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split("\0")
        if item
    ]
    sensitive_untracked = [
        item
        for item in raw_untracked
        if Path(item).name in SENSITIVE_UNTRACKED_NAMES
        or Path(item).suffix.lower() in SENSITIVE_UNTRACKED_SUFFIXES
    ]
    if sensitive_untracked:
        raise EvoTraceError(
            "Solver created sensitive untracked paths: "
            + ", ".join(sensitive_untracked[:10])
        )
    untracked = [
        item
        for item in raw_untracked
        if not any(part in DEFAULT_IGNORED_PARTS for part in Path(item).parts)
    ]
    if untracked:
        subprocess.run(
            ["git", "add", "--intent-to-add", "--", *untracked],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    result = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise EvoTraceError(
            "Could not capture solver patch: "
            + result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    if len(result.stdout) > MAX_PATCH_BYTES:
        raise EvoTraceError(
            f"Solver patch is too large ({len(result.stdout)} bytes; max {MAX_PATCH_BYTES})"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result.stdout)
    return paths, _patch_id(destination)


def _commit_task_state(workspace: Path) -> None:
    subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "EvoTrace task state"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _reference_changed_paths(bundle: Path) -> List[str]:
    patch = bundle / "patches" / "reference.patch"
    if not patch.exists():
        return []
    values = []
    for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"diff --git a/(.+) b/(.+)$", line)
        if match:
            values.append(match.group(2))
    protected = set((read_json(bundle / "verifier.json").get("protected_paths") or []))
    return sorted({value for value in values if value not in protected})


def _base_failure_hint(store: Store, session_id: str) -> Optional[str]:
    for _, run in related_runs(store, session_id):
        if run.get("kind") != "docker-validation":
            continue
        checks = (((run.get("result") or {}).get("base") or {}).get("checks") or [])
        for check in checks:
            output = str(check.get("output") or "").strip()
            if not check.get("passed") and output:
                compact = " ".join(output.split())
                return f"Baseline verifier signal: {compact[:600]}"
    return None


def _hint_catalog(store: Store, bundle: Path, session_id: str) -> List[str]:
    verifier = read_json(bundle / "verifier.json")
    hints = ["Run the repository's existing tests first and inspect the first failure."]
    paths = _reference_changed_paths(bundle)
    if paths:
        hints.append("The historical successful trajectory touched: " + ", ".join(paths[:8]))
    failure = _base_failure_hint(store, session_id)
    if failure:
        hints.append(failure)
    commands = verifier.get("commands") or []
    if commands:
        hints.append("A useful local check is: " + str(commands[0]))
    result = []
    for hint in hints:
        if hint not in result:
            result.append(hint)
    return result


def _verifier_catalog(bundle: Path, verifier: Dict[str, Any]) -> List[str]:
    current = set(str(command) for command in verifier.get("commands") or [])
    environment = read_json(bundle / "environment" / "environment.json")
    candidates: List[str] = []
    if environment.get("kind") == "python":
        joined = "\n".join(current)
        if "unittest" not in joined:
            candidates.append("python -m unittest discover -q")
        requirements = " ".join(environment.get("requirements") or [])
        installs = " ".join(environment.get("install_commands") or [])
        if "pytest" in (requirements + installs).lower() and "pytest" not in joined:
            candidates.append("python -m pytest -q")
        candidates.append("python -m compileall -q .")
    candidates.append("git diff --check")
    return [command for command in candidates if command not in current]


def _task_with_hints(task: str, hints: List[str]) -> str:
    text = task.strip()
    if not hints:
        return text
    rendered = "\n".join(f"- {hint}" for hint in hints)
    return f"{text}\n\nHints:\n{rendered}"


def choose_adjustment(
    *,
    pass_count: int,
    target_passes: int,
    passing_reference_equivalent: int,
    active_hints: List[str],
    remaining_hints: List[str],
    remaining_verifiers: List[str],
) -> Dict[str, Any]:
    """Choose an auditable next curriculum action without falsifying success."""

    if pass_count == target_passes:
        return {"action": "stop", "status": "calibrated"}
    if pass_count < target_passes:
        if remaining_hints:
            return {
                "action": "add_hint",
                "value": remaining_hints[0],
                "status": "adjusting",
            }
        return {"action": "stop", "status": "too_hard"}
    if pass_count and passing_reference_equivalent == pass_count:
        return {
            "action": "stop",
            "status": "too_easy",
            "reason": "all passing solutions are reference-equivalent",
        }
    if active_hints:
        return {
            "action": "remove_hint",
            "value": active_hints[-1],
            "status": "adjusting",
        }
    if remaining_verifiers:
        return {
            "action": "add_verifier",
            "value": remaining_verifiers[0],
            "status": "adjusting",
        }
    return {"action": "stop", "status": "too_easy"}


def _run_attempt(
    *,
    bundle: Path,
    solver: Solver,
    task: str,
    verifier: Dict[str, Any],
    attempt_dir: Path,
    timeout: int,
    reference_patch_id: Optional[str],
) -> Dict[str, Any]:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    patch_path = attempt_dir / "solution.patch"
    with tempfile.TemporaryDirectory(prefix="evotrace-self-play-") as temporary:
        workspace = Path(temporary) / "workspace"
        setup_from_bundle(bundle, workspace)
        _commit_task_state(workspace)
        solver_result = solver.solve(workspace, task, timeout=timeout)
        try:
            paths, solution_patch_id = _capture_solution_patch(workspace, patch_path)
            evaluation = evaluate_candidate_patch_in_docker(
                bundle,
                patch_path,
                verifier=verifier,
                timeout=timeout,
            )
            capture_error = None
        except (EvoTraceError, subprocess.CalledProcessError) as exc:
            paths = []
            solution_patch_id = None
            capture_error = str(exc)
            if not patch_path.exists():
                patch_path.write_bytes(b"")
            evaluation = {
                "protocol": "docker-candidate-patch-v0.1",
                "passed": False,
                "score": 0.0,
                "error": capture_error,
            }
    output_path = attempt_dir / "agent-output.txt"
    output_path.write_text(
        str(solver_result.get("stdout") or "")
        + ("\n\nSTDERR:\n" + str(solver_result.get("stderr")) if solver_result.get("stderr") else ""),
        encoding="utf-8",
    )
    record = {
        "schema_version": "0.1",
        "created_at": utc_now(),
        "passed": bool(evaluation.get("passed")),
        "score": float(evaluation.get("score") or 0.0),
        "changed_paths": paths,
        "solution_patch_id": solution_patch_id,
        "reference_equivalent": bool(
            solution_patch_id
            and reference_patch_id
            and solution_patch_id == reference_patch_id
        ),
        "capture_error": capture_error,
        "solver": {
            key: solver_result.get(key)
            for key in (
                "exit_code",
                "timed_out",
                "permission_mode",
                "session_id",
                "provider",
                "model",
                "tool_calls",
                "observed_external_file_access",
            )
        },
        "evaluation": evaluation,
        "artifacts": {
            "solution_patch": "solution.patch",
            "agent_output": "agent-output.txt",
        },
    }
    write_json(attempt_dir / "attempt.json", record)
    return record


def calibrate_bundle(
    bundle: Path,
    *,
    store: Store,
    solver: Optional[Solver] = None,
    trials: int = 5,
    target_passes: int = 2,
    max_rounds: int = 3,
    timeout: int = 600,
    initial_hints: int = 0,
    progress: Optional[Callable[[str], None]] = None,
) -> CalibrationOutcome:
    """Self-play an asset and adapt hints/verifier overlays toward a target pass count."""

    bundle = bundle.resolve()
    if trials < 1 or trials > 20:
        raise EvoTraceError("Calibration trials must be between 1 and 20")
    if target_passes < 0 or target_passes > trials:
        raise EvoTraceError("Target passes must be between 0 and the trial count")
    if max_rounds < 1 or max_rounds > 10:
        raise EvoTraceError("Calibration rounds must be between 1 and 10")
    if timeout < 1:
        raise EvoTraceError("Calibration timeout must be at least 1 second")
    manifest = read_json(bundle / "task.json")
    verifier = read_json(bundle / "verifier.json")
    session_id = str(manifest.get("id") or bundle.name)
    task = str(manifest.get("task") or "").strip()
    if not task:
        raise EvoTraceError("The asset has no task text to calibrate")
    reference_patch = bundle / "patches" / "reference.patch"
    reference_patch_id = _patch_id(reference_patch)
    if not reference_patch_id:
        raise EvoTraceError("Self-play calibration requires a non-empty reference patch")

    target_store = store
    target_store.initialize()
    calibration_id = short_id("cal")
    calibration_dir = target_store.calibrations / session_id / calibration_id
    calibration_dir.mkdir(parents=True)
    hints = _hint_catalog(target_store, bundle, session_id)
    if initial_hints < 0 or initial_hints > len(hints):
        raise EvoTraceError(f"--initial-hints must be between 0 and {len(hints)}")
    active_hints = hints[:initial_hints]
    remaining_hints = hints[initial_hints:]
    remaining_verifiers = _verifier_catalog(bundle, verifier)
    active_verifier = copy.deepcopy(verifier)
    agent = solver or DeepSeekHarnessSolver(target_store)
    notify = progress or (lambda _: None)
    rounds: List[Dict[str, Any]] = []
    status = "uncalibrated"
    last_passes = 0

    report: Dict[str, Any] = {
        "schema_version": "0.1",
        "protocol": CALIBRATION_PROTOCOL,
        "id": calibration_id,
        "created_at": utc_now(),
        "session_id": session_id,
        "bundle_sha256": bundle_sha256(bundle),
        "target": {"trials": trials, "passes": target_passes},
        "reference_patch_id": reference_patch_id,
        "rounds": rounds,
        "status": status,
        "policy": {
            "reference_hidden_from_solver": True,
            "canonical_bundle_mutated": False,
            "verifier_additions_must_pass_reference": True,
            "correct_reference_equivalent_solutions_are_never_rejected_to_hit_target": True,
        },
    }
    write_json(calibration_dir / "calibration.json", report)

    for round_number in range(1, max_rounds + 1):
        version_id = f"version-{round_number:02d}"
        version_dir = calibration_dir / "versions" / version_id
        version_dir.mkdir(parents=True)
        rendered_task = _task_with_hints(task, active_hints)
        (version_dir / "task.md").write_text(rendered_task + "\n", encoding="utf-8")
        write_json(version_dir / "verifier.json", active_verifier)
        write_json(
            version_dir / "version.json",
            {
                "id": version_id,
                "task": task,
                "hints": active_hints,
                "verifier_commands": active_verifier.get("commands") or [],
            },
        )
        notify(
            f"Round {round_number}/{max_rounds}: {trials} independent DeepSeek attempts "
            f"with {len(active_hints)} hint(s) and "
            f"{len(active_verifier.get('commands') or [])} verifier command(s)."
        )
        attempts = []
        for trial in range(1, trials + 1):
            notify(f"  Attempt {trial}/{trials}...")
            attempt = _run_attempt(
                bundle=bundle,
                solver=agent,
                task=rendered_task,
                verifier=active_verifier,
                attempt_dir=calibration_dir
                / "attempts"
                / f"round-{round_number:02d}"
                / f"attempt-{trial:02d}",
                timeout=timeout,
                reference_patch_id=reference_patch_id,
            )
            attempts.append(attempt)
            notify(
                f"    {'PASS' if attempt['passed'] else 'FAIL'} "
                f"({attempt['score'] * 100:.1f}%)"
            )
        pass_count = sum(bool(attempt["passed"]) for attempt in attempts)
        equivalent = sum(
            bool(attempt["passed"] and attempt["reference_equivalent"])
            for attempt in attempts
        )
        last_passes = pass_count
        adjustment = choose_adjustment(
            pass_count=pass_count,
            target_passes=target_passes,
            passing_reference_equivalent=equivalent,
            active_hints=active_hints,
            remaining_hints=remaining_hints,
            remaining_verifiers=remaining_verifiers,
        )
        round_record = {
            "round": round_number,
            "version": version_id,
            "pass_count": pass_count,
            "trial_count": trials,
            "pass_rate": round(pass_count / trials, 4),
            "reference_equivalent_passes": equivalent,
            "hints": list(active_hints),
            "verifier_commands": list(active_verifier.get("commands") or []),
            "attempts": [
                str(
                    Path("attempts")
                    / f"round-{round_number:02d}"
                    / f"attempt-{index:02d}"
                    / "attempt.json"
                )
                for index in range(1, trials + 1)
            ],
            "adjustment": adjustment,
        }
        rounds.append(round_record)
        status = str(adjustment["status"])
        report["status"] = status
        report["rounds"] = rounds
        write_json(calibration_dir / "calibration.json", report)
        notify(f"  Result: {pass_count}/{trials}; action={adjustment['action']} ({status}).")
        if adjustment["action"] == "stop":
            break
        if adjustment["action"] == "add_hint":
            value = str(adjustment["value"])
            active_hints.append(value)
            remaining_hints.remove(value)
            continue
        if adjustment["action"] == "remove_hint":
            value = str(adjustment["value"])
            active_hints.remove(value)
            if value not in remaining_hints:
                remaining_hints.insert(0, value)
            continue
        if adjustment["action"] == "add_verifier":
            value = str(adjustment["value"])
            proposed = copy.deepcopy(active_verifier)
            proposed["commands"] = [*(proposed.get("commands") or []), value]
            gate = evaluate_reference_with_verifier_in_docker(
                bundle,
                proposed,
                timeout=timeout,
            )
            round_record["verifier_gate"] = {
                "command": value,
                "reference_passed": bool(gate.get("passed")),
                "score": gate.get("score"),
            }
            remaining_verifiers.remove(value)
            if gate.get("passed"):
                active_verifier = proposed
            else:
                round_record["adjustment"] = {
                    "action": "reject_verifier",
                    "value": value,
                    "status": "adjusting",
                }
            write_json(calibration_dir / "calibration.json", report)

    report["finished_at"] = utc_now()
    report["status"] = status
    report["summary"] = {
        "passes": last_passes,
        "trials": trials,
        "target_passes": target_passes,
        "rounds": len(rounds),
        "calibrated": status == "calibrated",
    }
    write_json(calibration_dir / "calibration.json", report)
    run_dir, _ = record_run(
        target_store,
        kind="self-play-calibration",
        session_id=session_id,
        result=report,
        passed=status == "calibrated",
        score=last_passes / trials,
        bundle=bundle,
    )
    return CalibrationOutcome(
        calibration_id=calibration_id,
        calibration_dir=calibration_dir,
        run_dir=run_dir,
        status=status,
        pass_count=last_passes,
        trials=trials,
        target_passes=target_passes,
        rounds=len(rounds),
        report=report,
    )
