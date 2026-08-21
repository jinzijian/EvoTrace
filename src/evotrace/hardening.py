from __future__ import annotations

import json
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .calibration import CalibrationOutcome, DeepSeekHarnessSolver, calibrate_bundle
from .catalog import record_run
from .compiler import (
    SANDBOX_POLICY,
    SETUP_SCRIPT,
    VERIFIER_SCRIPT,
    VERIFY_PREFIXES,
    _dockerfile,
    _task_yaml,
)
from .difficulty import assess_difficulty
from .errors import EvoTraceError
from .gitops import changed_paths, git, setup_from_bundle
from .store import Store
from .util import read_json, short_id, unique, utc_now, write_json, write_jsonl
from .validation import validate_bundle_in_docker

HARDENING_PROTOCOL = "deepseek-task-hardening-v0.1"
HIDDEN_TEST_ROOT = "evotrace_hidden_tests"
SHELL_META = re.compile(r"[;&|><`$\n\r]")


@dataclass(frozen=True)
class HardeningOutcome:
    child_id: str
    bundle: Path
    validation_run: Path
    calibration: CalibrationOutcome
    report: Dict[str, Any]


def _json_object(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    values = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values[-1] if values else None


def _empty_tar(path: Path) -> None:
    with tarfile.open(path, "w:gz"):
        pass


def _write_diff(workspace: Path, paths: List[str], destination: Path) -> None:
    if not paths:
        destination.write_bytes(b"")
        return
    untracked = [
        value
        for value in git(workspace, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        if value
    ]
    if untracked:
        subprocess.run(
            ["git", "add", "--intent-to-add", "--", *untracked],
            cwd=workspace,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    result = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD", "--", *paths],
        cwd=workspace,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise EvoTraceError(
            "Could not capture hardened task patch: "
            + result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    destination.write_bytes(result.stdout)


def _safe_commands(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    commands = []
    for raw in value[:4]:
        command = str(raw).strip()
        if not command or SHELL_META.search(command):
            continue
        if any(pattern.search(command) for pattern in VERIFY_PREFIXES):
            commands.append(command)
    return unique(commands)


def _apply_parent_reference(parent: Path, workspace: Path) -> None:
    reference = parent / "patches" / "reference.patch"
    if reference.is_file() and reference.stat().st_size:
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(reference)],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise EvoTraceError(
                "Could not apply the parent reference before hardening: "
                + result.stderr.decode("utf-8", errors="replace")[-2000:]
            )
    snapshot = parent / "environment" / "untracked-reference.tar.gz"
    if snapshot.is_file() and snapshot.stat().st_size:
        subprocess.run(
            ["tar", "-xzf", str(snapshot), "-C", str(workspace)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "EvoTrace parent solved state"],
        cwd=workspace,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write_catalog_records(
    store: Store,
    *,
    child_id: str,
    parent_manifest: Dict[str, Any],
    task: str,
    commands: List[str],
    difficulty: Dict[str, Any],
    solver: Dict[str, Any],
) -> None:
    session = store.sessions / child_id
    session.mkdir(parents=True, exist_ok=True)
    repository = parent_manifest.get("repository") or {}
    trajectory = {
        "schema_version": "0.1",
        "session_id": child_id,
        "created_at": utc_now(),
        "source": {"kind": "synthetic_hardening", "agent": "deepseek-harness"},
        "task": {"text": task, "source": "hardener"},
        "repository": {
            "origin": repository.get("origin"),
            "base_commit": f"derived:{parent_manifest.get('id')}",
            "reconstruction_confidence": "high",
        },
        "verification": {"commands": commands},
        "outcome": {"status": "hardened_task_generated"},
        "hardening": {"parent_id": parent_manifest.get("id"), "solver": solver},
    }
    write_json(session / "trajectory.json", trajectory)
    write_jsonl(session / "events.jsonl", [])
    candidate = {
        "schema_version": "0.1",
        "session_id": child_id,
        "source": "deepseek-harness",
        "mined_at": utc_now(),
        "curator": {"kind": "hardening_pipeline", "version": "0.1", "model_used": True},
        "score": 9,
        "difficulty": difficulty,
        "labels": [
            "useful",
            "execution_verifiable",
            "needs_hardening",
            f"difficulty_{difficulty['tier']}",
            "synthetic_hardened_task",
        ],
        "evidence": [
            "derived from a solved verified task",
            "fresh reference implementation generated",
            "hidden verifier tests generated separately",
            f"{len(commands)} verification command(s) recovered",
        ],
        "signals": {
            "repository_available": True,
            "reconstruction_confidence": "high",
            "verification_commands": commands,
            "reference_patch_available": True,
            "coding_scope": True,
            "sensitive_content": False,
            "cloud_review": "generated_with_consent",
        },
    }
    write_json(store.candidates / f"{child_id}.json", candidate)


def _update_empirical_difficulty(
    store: Store,
    child_id: str,
    calibration: CalibrationOutcome,
) -> Dict[str, Any]:
    path = store.candidates / f"{child_id}.json"
    candidate = read_json(path)
    difficulty = candidate.get("difficulty") or {}
    if calibration.status == "calibrated":
        empirical_tier = "medium"
    elif calibration.status == "too_easy":
        empirical_tier = "easy"
    else:
        empirical_tier = "hard"
    difficulty["empirical"] = {
        "status": calibration.status,
        "passes": calibration.pass_count,
        "trials": calibration.trials,
        "target_passes": calibration.target_passes,
        "tier": empirical_tier,
    }
    difficulty["training_eligible"] = calibration.status == "calibrated"
    difficulty["tier"] = empirical_tier
    candidate["difficulty"] = difficulty
    labels = [
        label
        for label in candidate.get("labels") or []
        if label not in {"needs_hardening", "training_ready_execution"}
        and not str(label).startswith("difficulty_")
    ]
    labels.append(f"difficulty_{empirical_tier}")
    labels.append(
        "training_ready_execution"
        if calibration.status == "calibrated"
        else "needs_hardening"
    )
    candidate["labels"] = labels
    write_json(path, candidate)
    return difficulty


def harden_bundle(
    parent: Path,
    *,
    store: Store,
    trials: int = 5,
    target_passes: int = 2,
    timeout: int = 600,
    progress: Optional[Callable[[str], None]] = None,
) -> HardeningOutcome:
    """Generate a harder child task, validate it, then measure it with self-play."""

    parent = parent.resolve()
    store.initialize()
    manifest = read_json(parent / "task.json")
    parent_id = str(manifest.get("id") or parent.name)
    agent = DeepSeekHarnessSolver(store)
    notify = progress or (lambda _: None)
    child_id = f"{parent_id}-hard-{short_id('task').split('-', 1)[1]}"
    bundle = store.benchmarks / child_id
    if bundle.exists():
        raise EvoTraceError(f"Hardened bundle already exists: {bundle}")

    notify("Hardening 1/3: DeepSeek designs and solves a harder follow-up task.")
    with tempfile.TemporaryDirectory(prefix="evotrace-hardening-") as temporary:
        workspace = Path(temporary) / "workspace"
        setup_from_bundle(parent, workspace)
        _apply_parent_reference(parent, workspace)
        base_archive = Path(temporary) / "base.tar.gz"
        subprocess.run(
            ["git", "archive", "--format=tar.gz", f"--output={base_archive}", "HEAD"],
            cwd=workspace,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        prompt = f"""You are the EvoTrace Task Hardener. The current repository is the solved state of an easy task.
Design one substantially harder, realistic follow-up coding task and implement its reference solution.

Hard constraints:
- The new task must require multi-step reasoning and change at least two implementation files.
- It must include backward-compatibility plus at least two edge/error cases.
- Put every new verifier test under `{HIDDEN_TEST_ROOT}/`; do not edit existing tests.
- For Python, use only stdlib unittest in the hidden tests.
- Run both existing regression tests and the new hidden tests.
- Do not use the web, /tmp, parent directories, or absolute paths.
- Write `{HIDDEN_TEST_ROOT}` tests as if they will be hidden from the future solver.
- Write `.evotrace-hardening.json` with keys: task (complete standalone task text),
  verification_commands (array), difficulty_rationale (array), and implementation_paths (array).
- Do not mention the hidden test path or the reference solution in the task text.

The goal is not to make a larger patch cosmetically. It is to create a deeper executable task whose behavior is
unambiguous and whose solution cannot be reduced to changing one operator or constant."""
        result = agent.run_prompt(workspace, prompt, timeout=timeout)
        metadata_path = workspace / ".evotrace-hardening.json"
        metadata = read_json(metadata_path) if metadata_path.is_file() else _json_object(
            str(result.get("stdout") or "")
        )
        if not metadata:
            raise EvoTraceError("Task Hardener did not produce .evotrace-hardening.json")
        metadata_path.unlink(missing_ok=True)
        paths = changed_paths(workspace)
        hidden_paths = [path for path in paths if Path(path).parts[:1] == (HIDDEN_TEST_ROOT,)]
        implementation_paths = [path for path in paths if path not in hidden_paths]
        if len(implementation_paths) < 2:
            raise EvoTraceError(
                "Hardened task did not change at least two implementation files: "
                + ", ".join(implementation_paths)
            )
        if not hidden_paths:
            raise EvoTraceError("Hardened task did not generate hidden verifier tests")
        task = str(metadata.get("task") or "").strip()
        if len(task) < 120:
            raise EvoTraceError("Hardened task text is too short to specify a multi-step contract")
        commands = _safe_commands(metadata.get("verification_commands"))
        parent_commands = list((read_json(parent / "verifier.json").get("commands") or []))
        commands = unique([*parent_commands, *commands])
        if not commands:
            raise EvoTraceError("Hardened task did not produce an allowlisted verifier command")

        bundle.mkdir(parents=True)
        (bundle / "environment").mkdir()
        (bundle / "patches").mkdir()
        (bundle / "environment" / "base.tar.gz").write_bytes(base_archive.read_bytes())
        _empty_tar(bundle / "environment" / "untracked-initial.tar.gz")
        _empty_tar(bundle / "environment" / "untracked-reference.tar.gz")
        (bundle / "patches" / "initial.patch").write_bytes(b"")
        _write_diff(workspace, implementation_paths, bundle / "patches" / "reference.patch")
        _write_diff(workspace, hidden_paths, bundle / "patches" / "hidden-tests.patch")

    if not (bundle / "patches" / "reference.patch").stat().st_size:
        raise EvoTraceError("Hardened task has no reference implementation patch")
    environment = manifest.get("environment") or read_json(parent / "environment" / "environment.json")
    verifier = {
        "schema_version": "0.1",
        "commands": commands,
        "command_source": "deepseek_hardener",
        "timeout_seconds": 600,
        "require_change": True,
        "protected_paths": hidden_paths,
        "warning": None,
    }
    solver_record = {
        key: result.get(key)
        for key in (
            "session_id",
            "provider",
            "model",
            "tool_calls",
            "observed_external_file_access",
            "permission_mode",
        )
    }
    structural_difficulty = assess_difficulty(
        task=task,
        tool_calls=int(result.get("tool_calls") or 0),
        edit_calls=len(implementation_paths),
        verification_commands=commands,
        reference_patch=bundle / "patches" / "reference.patch",
        recovery=True,
        human_corrected=False,
    )
    child_manifest = {
        "schema_version": "0.1",
        "id": child_id,
        "compiled_at": utc_now(),
        "task": task,
        "source": {"kind": "synthetic_hardening", "parent_id": parent_id},
        "repository": manifest.get("repository") or {},
        "environment": environment,
        "verifier": verifier,
        "difficulty": structural_difficulty,
        "lineage": {
            "protocol": HARDENING_PROTOCOL,
            "parent_id": parent_id,
            "designer": solver_record,
            "difficulty_rationale": metadata.get("difficulty_rationale") or [],
            "implementation_paths": implementation_paths,
            "hidden_test_count": len(hidden_paths),
        },
        "reproducibility": {
            "base_tree": True,
            "parent_reference_as_base": True,
            "reference_patch": True,
            "hidden_tests": True,
            "confidence": "generated",
        },
        "sandbox": SANDBOX_POLICY,
    }
    write_json(bundle / "task.json", child_manifest)
    write_json(bundle / "environment" / "environment.json", environment)
    write_json(bundle / "verifier.json", verifier)
    write_json(bundle / "sandbox-policy.json", SANDBOX_POLICY)
    write_json(bundle / "hardening.json", child_manifest["lineage"])
    (bundle / "task.md").write_text(task + "\n", encoding="utf-8")
    (bundle / "task.yaml").write_text(
        _task_yaml(child_id, task, "synthetic_hardening", len(commands)), encoding="utf-8"
    )
    (bundle / "verifier.py").write_text(VERIFIER_SCRIPT, encoding="utf-8")
    (bundle / "setup.sh").write_text(SETUP_SCRIPT, encoding="utf-8")
    (bundle / "setup.sh").chmod(0o755)
    (bundle / "verifier.py").chmod(0o755)
    (bundle / "Dockerfile").write_text(
        _dockerfile(environment, hidden_tests=True), encoding="utf-8"
    )
    _write_catalog_records(
        store,
        child_id=child_id,
        parent_manifest=manifest,
        task=task,
        commands=commands,
        difficulty=structural_difficulty,
        solver=solver_record,
    )

    notify("Hardening 2/3: Docker must reject the new base and accept the new reference.")
    validation = validate_bundle_in_docker(bundle, timeout=max(timeout, 900))
    validation_run, _ = record_run(
        store,
        kind="docker-validation",
        session_id=child_id,
        result=validation,
        passed=bool(validation.get("passed")),
        score=float((validation.get("reference") or {}).get("score") or 0.0),
        bundle=bundle,
    )
    if not validation.get("passed"):
        raise EvoTraceError(
            f"Generated harder task failed two-state validation. Evidence: {validation_run}"
        )

    notify("Hardening 3/3: DeepSeek self-play measures the child task difficulty.")
    calibration = calibrate_bundle(
        bundle,
        store=store,
        solver=agent,
        trials=trials,
        target_passes=target_passes,
        max_rounds=3,
        timeout=timeout,
        progress=notify,
    )
    empirical = _update_empirical_difficulty(store, child_id, calibration)
    report = {
        "schema_version": "0.1",
        "protocol": HARDENING_PROTOCOL,
        "parent_id": parent_id,
        "child_id": child_id,
        "bundle": str(bundle),
        "validation_run": str(validation_run),
        "structural_difficulty": structural_difficulty,
        "empirical_difficulty": empirical.get("empirical"),
        "status": (
            "training_ready"
            if calibration.status == "calibrated"
            else "needs_further_hardening"
            if calibration.status == "too_easy"
            else "too_hard"
        ),
    }
    write_json(bundle / "hardening-result.json", report)
    return HardeningOutcome(
        child_id=child_id,
        bundle=bundle,
        validation_run=validation_run,
        calibration=calibration,
        report=report,
    )
