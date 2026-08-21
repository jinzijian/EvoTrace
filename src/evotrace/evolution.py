from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .calibration import (
    DeepSeekHarnessSolver,
    _capture_solution_patch,
    _commit_task_state,
    _patch_id,
    _run_attempt,
)
from .catalog import bundle_sha256, has_matching_passed_run, record_run
from .errors import EvoTraceError
from .gitops import setup_from_bundle
from .privacy import redact_for_cloud_text, redact_text
from .store import Store
from .util import read_json, short_id, utc_now, write_json

EVOLUTION_PROTOCOL = "execution-experience-evolution-v0.1"
MAX_CAPSULE_BYTES = 64 * 1024
MAX_EXPERIENCE_BYTES = 16 * 1024


class ExperienceAgent(Protocol):
    def run_prompt(self, workspace: Path, prompt: str, *, timeout: int) -> Dict[str, Any]: ...

    def solve(self, workspace: Path, task: str, *, timeout: int) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class EvolutionOutcome:
    evolution_id: str
    evolution_dir: Path
    run_dir: Path
    status: str
    utility: str
    baseline_passes: int
    conditioned_passes: int
    trials: int
    report: Dict[str, Any]


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item) for item in value)))
    if not isinstance(value, dict):
        return ""
    if value.get("type") == "reasoning":
        return ""
    if isinstance(value.get("text"), str):
        return str(value["text"])
    return _content_text(value.get("content"))


def _decode_session(path: Path) -> List[Dict[str, Any]]:
    zstd = shutil.which("zstd")
    if not zstd or not path.is_file():
        return []
    result = subprocess.run(
        [zstd, "-dc", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    events = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _safe_capsule_text(text: str, workspace: Path) -> str:
    workspace_text = str(workspace.resolve())
    value = text.replace(workspace_text + "/", "workspace:")
    value = value.replace(workspace_text, "workspace")
    return redact_for_cloud_text(redact_text(value))


def trajectory_capsule(
    solver_result: Dict[str, Any],
    *,
    workspace: Path,
) -> tuple[str, Dict[str, Any]]:
    supplied = solver_result.get("trajectory_capsule")
    supplied_stats = solver_result.get("trajectory_stats")
    if isinstance(supplied, str):
        return supplied[:MAX_CAPSULE_BYTES], dict(supplied_stats or {})
    session_value = solver_result.get("session_log_path")
    events = _decode_session(Path(session_value)) if session_value else []
    lines: List[str] = []
    calls: Dict[str, Dict[str, Any]] = {}
    execution_commands: List[str] = []
    tool_results = 0
    runtime_output_bytes = 0
    for event in events:
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "tool/call":
            call_id = str(data.get("callId") or "")
            name = str(data.get("name") or "unknown")
            arguments = str(data.get("arguments") or "")
            safe_arguments = _safe_capsule_text(arguments, workspace)
            calls[call_id] = {"name": name, "arguments": safe_arguments}
            lines.append(f"TOOL {name}: {safe_arguments}")
            if name == "bash":
                try:
                    command = json.loads(arguments).get("command")
                except (json.JSONDecodeError, AttributeError):
                    command = None
                if command:
                    execution_commands.append(str(command))
        elif event_type == "tool/result":
            message = data.get("message") or {}
            source = message.get("source") or {}
            call_id = str(source.get("callId") or "")
            content = _content_text(message.get("content"))
            if not content:
                continue
            safe_content = _safe_capsule_text(content, workspace)
            runtime_output_bytes += len(safe_content.encode("utf-8"))
            tool_results += 1
            tool_name = str((calls.get(call_id) or {}).get("name") or "tool")
            lines.append(f"RESULT {tool_name}: {safe_content[:6000]}")
        elif event_type == "assistant/message":
            message = data.get("message") or {}
            text = _content_text(message.get("content"))
            if text:
                lines.append(f"AGENT: {_safe_capsule_text(text, workspace)[:4000]}")
        if sum(len(line) for line in lines) >= MAX_CAPSULE_BYTES:
            break
    if not lines:
        fallback = _safe_capsule_text(str(solver_result.get("stdout") or ""), workspace)
        lines.append("AGENT: " + fallback[:MAX_CAPSULE_BYTES])
    capsule = "\n\n".join(lines)
    encoded = capsule.encode("utf-8")[:MAX_CAPSULE_BYTES]
    capsule = encoded.decode("utf-8", errors="ignore")
    return capsule, {
        "tool_calls": len(calls),
        "tool_results": tool_results,
        "execution_commands": execution_commands,
        "runtime_output_bytes": runtime_output_bytes,
        "capsule_bytes": len(capsule.encode("utf-8")),
        "reasoning_blocks_included": False,
    }


def grade_execution_exploration(
    *,
    stats: Dict[str, Any],
    changed_paths: List[str],
) -> Dict[str, Any]:
    commands = [str(value) for value in stats.get("execution_commands") or []]
    checks = [
        {
            "name": "agent used multiple repository tools",
            "passed": int(stats.get("tool_calls") or 0) >= 4,
        },
        {
            "name": "agent executed the repository",
            "passed": bool(commands),
        },
        {
            "name": "execution produced runtime evidence",
            "passed": int(stats.get("runtime_output_bytes") or 0) >= 40,
        },
        {
            "name": "agent created instrumentation or exploration changes",
            "passed": bool(changed_paths),
        },
        {
            "name": "trajectory contains tool results",
            "passed": int(stats.get("tool_results") or 0) >= 2,
        },
        {
            "name": "agent stayed inside the disposable workspace",
            "passed": stats.get("observed_external_file_access") is False,
        },
    ]
    passed_count = sum(bool(check["passed"]) for check in checks)
    return {
        "passed": passed_count >= 4 and checks[-1]["passed"],
        "score": passed_count / len(checks),
        "checks": checks,
        "execution_commands": commands,
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    candidates = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates[-1] if candidates else None


def _normalize_experience(value: Optional[Dict[str, Any]], fallback: str) -> Dict[str, Any]:
    source = value or {}

    def strings(key: str, limit: int = 12) -> List[str]:
        raw = source.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(item)[:1200] for item in raw[:limit] if str(item).strip()]

    summary = str(source.get("summary") or fallback).strip()[:4000]
    return {
        "schema_version": "0.1",
        "kind": "execution-experience",
        "summary": summary,
        "runtime_facts": strings("runtime_facts"),
        "useful_commands": strings("useful_commands"),
        "code_locations": strings("code_locations"),
        "failure_modes": strings("failure_modes"),
        "do_not_assume": strings("do_not_assume"),
        "confidence": str(source.get("confidence") or "unknown")[:40],
    }


def _experience_markdown(experience: Dict[str, Any]) -> str:
    lines = ["# Reusable execution experience", "", experience["summary"]]
    sections = [
        ("Runtime facts", "runtime_facts"),
        ("Useful commands", "useful_commands"),
        ("Code locations", "code_locations"),
        ("Failure modes", "failure_modes"),
        ("Do not assume", "do_not_assume"),
    ]
    for title, key in sections:
        values = experience.get(key) or []
        if not values:
            continue
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {value}" for value in values)
    return "\n".join(lines).strip() + "\n"


def compress_experience(
    agent: ExperienceAgent,
    *,
    capsule: str,
    timeout: int,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="evotrace-compression-") as temporary:
        workspace = Path(temporary) / "workspace"
        workspace.mkdir()
        (workspace / "trajectory-capsule.txt").write_text(capsule, encoding="utf-8")
        prompt = (
            "You are the EvoTrace Experience Compressor. Read trajectory-capsule.txt. "
            "Compress only execution-grounded knowledge that could help another coding agent "
            "understand or execute this repository. Do not invent facts, do not include hidden "
            "reasoning, and do not reproduce the transcript. Output one JSON object with exactly "
            "these keys: summary (string), runtime_facts (array), useful_commands (array), "
            "code_locations (array), failure_modes (array), do_not_assume (array), and confidence "
            "(low|medium|high)."
        )
        result = agent.run_prompt(workspace, prompt, timeout=timeout)
    parsed = _extract_json_object(str(result.get("stdout") or ""))
    fallback = "Execution exploration completed, but structured compression was unavailable."
    experience = _normalize_experience(parsed, fallback)
    raw_bytes = len(capsule.encode("utf-8"))
    packet_bytes = len(json.dumps(experience, ensure_ascii=False).encode("utf-8"))
    provenance = {
        key: result.get(key)
        for key in (
            "session_id",
            "provider",
            "model",
            "tool_calls",
            "observed_external_file_access",
        )
    }
    provenance.update(
        {
            "raw_capsule_bytes": raw_bytes,
            "experience_packet_bytes": packet_bytes,
            "compression_ratio": round(packet_bytes / raw_bytes, 4) if raw_bytes else None,
            "structured_output": parsed is not None,
        }
    )
    return experience, provenance


def _same_repository(source: Dict[str, Any], held_out: Dict[str, Any]) -> bool:
    first = (source.get("repository") or {}).get("origin")
    second = (held_out.get("repository") or {}).get("origin")
    if first and second:
        return first == second
    return source.get("id") == held_out.get("id")


def _utility_status(
    *,
    baseline_passes: int,
    conditioned_passes: int,
    trials: int,
) -> str:
    if conditioned_passes > baseline_passes:
        return "useful"
    if conditioned_passes < baseline_passes:
        return "harmful"
    if baseline_passes == trials:
        return "ceiling"
    if baseline_passes == 0:
        return "no_gain"
    return "neutral"


def evolve_experience(
    source_bundle: Path,
    held_out_bundle: Path,
    *,
    store: Store,
    agent: Optional[ExperienceAgent] = None,
    trials: int = 5,
    target_passes: int = 2,
    timeout: int = 600,
    progress: Optional[Callable[[str], None]] = None,
) -> EvolutionOutcome:
    """Explore a repo, compress execution experience, then test downstream utility."""

    source_bundle = source_bundle.resolve()
    held_out_bundle = held_out_bundle.resolve()
    if trials < 1 or trials > 20:
        raise EvoTraceError("Evolution trials must be between 1 and 20")
    if target_passes < 0 or target_passes > trials:
        raise EvoTraceError("Target passes must be between 0 and trials")
    if timeout < 1:
        raise EvoTraceError("Evolution timeout must be at least 1 second")
    source_manifest = read_json(source_bundle / "task.json")
    held_out_manifest = read_json(held_out_bundle / "task.json")
    if not _same_repository(source_manifest, held_out_manifest):
        raise EvoTraceError("Source and held-out assets must come from the same repository")
    held_out_task = str(held_out_manifest.get("task") or "").strip()
    if not held_out_task:
        raise EvoTraceError("Held-out asset has no task text")
    held_out_verifier = read_json(held_out_bundle / "verifier.json")
    reference_patch_id = _patch_id(held_out_bundle / "patches" / "reference.patch")
    if not reference_patch_id:
        raise EvoTraceError("Held-out experience evaluation requires a reference patch")

    store.initialize()
    source_id = str(source_manifest.get("id") or source_bundle.name)
    held_out_id = str(held_out_manifest.get("id") or held_out_bundle.name)
    evolution_id = short_id("evo")
    evolution_dir = store.evolutions / held_out_id / evolution_id
    evolution_dir.mkdir(parents=True)
    runner = agent or DeepSeekHarnessSolver(store)
    notify = progress or (lambda _: None)

    notify("Stage 1/3: DeepSeek generates and executes repository exploration tasks.")
    exploration_dir = evolution_dir / "exploration"
    exploration_dir.mkdir()
    exploration_patch = exploration_dir / "instrumentation.patch"
    with tempfile.TemporaryDirectory(prefix="evotrace-exploration-") as temporary:
        workspace = Path(temporary) / "workspace"
        setup_from_bundle(source_bundle, workspace)
        _commit_task_state(workspace)
        exploration_prompt = (
            "You are the EvoTrace Execution Explorer. Work only in the current repository and do "
            "not inspect parent directories, session data, reference patches, or hidden tests. "
            "First invent 2-4 concrete execution questions about how this repository behaves at "
            "runtime. Then answer them by running real commands. Add temporary instrumentation, "
            "debug prints, or run scripts when useful. Prefer observations from actual execution "
            "over static guesses. Do not use /tmp or any absolute path; keep temporary files "
            "inside the current repository and delete them when finished. Do not use the web. "
            "Finish with a concise list of executed "
            "questions, commands, observed runtime facts, useful code locations, and uncertainty."
        )
        exploration = runner.run_prompt(workspace, exploration_prompt, timeout=timeout)
        try:
            changed, instrumentation_patch_id = _capture_solution_patch(
                workspace, exploration_patch
            )
            capture_error = None
        except (EvoTraceError, subprocess.CalledProcessError) as exc:
            changed = []
            instrumentation_patch_id = None
            capture_error = str(exc)
            exploration_patch.write_bytes(b"")
        capsule, stats = trajectory_capsule(exploration, workspace=workspace)
        stats["observed_external_file_access"] = exploration.get(
            "observed_external_file_access"
        )
    (exploration_dir / "trajectory-capsule.txt").write_text(capsule, encoding="utf-8")
    (exploration_dir / "agent-output.txt").write_text(
        str(exploration.get("stdout") or ""), encoding="utf-8"
    )
    exploration_grade = grade_execution_exploration(stats=stats, changed_paths=changed)
    write_json(
        exploration_dir / "exploration.json",
        {
            "solver": {
                key: exploration.get(key)
                for key in (
                    "session_id",
                    "provider",
                    "model",
                    "tool_calls",
                    "observed_external_file_access",
                    "permission_mode",
                )
            },
            "trajectory": stats,
            "changed_paths": changed,
            "instrumentation_patch_id": instrumentation_patch_id,
            "capture_error": capture_error,
            "grade": exploration_grade,
        },
    )

    notify("Stage 2/3: compressing the execution trajectory into an experience packet.")
    experience, compression = compress_experience(runner, capsule=capsule, timeout=timeout)
    experience_dir = evolution_dir / "experience"
    experience_dir.mkdir()
    write_json(experience_dir / "experience.json", experience)
    (experience_dir / "experience.md").write_text(
        _experience_markdown(experience), encoding="utf-8"
    )
    write_json(experience_dir / "compression.json", compression)

    notify(
        f"Stage 3/3: {trials} paired baseline/experience attempts on the held-out task."
    )
    experience_text = _experience_markdown(experience)
    baseline_attempts = []
    conditioned_attempts = []
    for trial in range(1, trials + 1):
        notify(f"  Pair {trial}/{trials}: baseline...")
        baseline = _run_attempt(
            bundle=held_out_bundle,
            solver=runner,
            task=held_out_task,
            verifier=held_out_verifier,
            attempt_dir=evolution_dir / "held-out" / f"pair-{trial:02d}" / "baseline",
            timeout=timeout,
            reference_patch_id=reference_patch_id,
        )
        baseline_attempts.append(baseline)
        notify(f"    baseline {'PASS' if baseline['passed'] else 'FAIL'}")
        conditioned_task = (
            held_out_task
            + "\n\nReusable execution experience from a prior repository exploration follows. "
            "It may be incomplete; verify every claim against the current checkout.\n\n"
            + experience_text[:MAX_EXPERIENCE_BYTES]
        )
        notify(f"  Pair {trial}/{trials}: experience-conditioned...")
        conditioned = _run_attempt(
            bundle=held_out_bundle,
            solver=runner,
            task=conditioned_task,
            verifier=held_out_verifier,
            attempt_dir=evolution_dir
            / "held-out"
            / f"pair-{trial:02d}"
            / "conditioned",
            timeout=timeout,
            reference_patch_id=reference_patch_id,
        )
        conditioned_attempts.append(conditioned)
        notify(f"    conditioned {'PASS' if conditioned['passed'] else 'FAIL'}")

    baseline_passes = sum(bool(attempt["passed"]) for attempt in baseline_attempts)
    conditioned_passes = sum(bool(attempt["passed"]) for attempt in conditioned_attempts)
    baseline_rate = baseline_passes / trials
    conditioned_rate = conditioned_passes / trials
    utility = _utility_status(
        baseline_passes=baseline_passes,
        conditioned_passes=conditioned_passes,
        trials=trials,
    )
    source_digest = bundle_sha256(source_bundle)
    held_out_digest = bundle_sha256(held_out_bundle)
    source_reference_patch_id = _patch_id(source_bundle / "patches" / "reference.patch")
    independence_checks = {
        "different_bundle_path": source_bundle != held_out_bundle,
        "different_bundle_digest": source_digest != held_out_digest,
        "different_task_id": source_id != held_out_id,
        "different_task_text": str(source_manifest.get("task") or "").strip()
        != held_out_task,
        "different_reference_patch": bool(
            source_reference_patch_id
            and source_reference_patch_id != reference_patch_id
        ),
    }
    independent = all(independence_checks.values())
    held_out_validated = has_matching_passed_run(store, held_out_id, held_out_bundle)
    solver_boundary_passed = all(
        (attempt.get("solver") or {}).get("observed_external_file_access") is False
        for attempt in [*baseline_attempts, *conditioned_attempts]
    )
    compressor_boundary_passed = compression.get("observed_external_file_access") is False
    certified = bool(
        independent
        and trials >= 3
        and exploration_grade["passed"]
        and compressor_boundary_passed
        and solver_boundary_passed
        and compression.get("structured_output") is True
        and held_out_validated
        and conditioned_passes > baseline_passes
    )
    if not independent:
        status = "smoke_only"
    elif certified:
        status = "experience_verified"
    else:
        status = "experience_not_verified"

    if baseline_passes > target_passes:
        curriculum = {
            "direction": "harder",
            "reason": "baseline success exceeds the target bucket",
        }
    elif conditioned_passes > baseline_passes:
        curriculum = {
            "direction": "retain_experience",
            "reason": "compressed experience improves held-out execution",
        }
    elif conditioned_passes == 0:
        curriculum = {
            "direction": "easier_or_reexplore",
            "reason": "neither baseline nor compressed experience solves the task",
        }
    else:
        curriculum = {
            "direction": "keep_difficulty",
            "reason": "success is within the target bucket without measured experience gain",
        }

    report = {
        "schema_version": "0.1",
        "protocol": EVOLUTION_PROTOCOL,
        "id": evolution_id,
        "created_at": utc_now(),
        "source_session_id": source_id,
        "held_out_session_id": held_out_id,
        "source_bundle_sha256": source_digest,
        "held_out_bundle_sha256": held_out_digest,
        "held_out_independent": independent,
        "held_out_validated": held_out_validated,
        "independence_checks": independence_checks,
        "status": status,
        "utility": utility,
        "exploration": {
            "passed": exploration_grade["passed"],
            "score": exploration_grade["score"],
            "artifact": "exploration/exploration.json",
        },
        "compression": {
            **compression,
            "artifact": "experience/experience.json",
        },
        "paired_evaluation": {
            "trials": trials,
            "seed_controlled": False,
            "baseline_passes": baseline_passes,
            "conditioned_passes": conditioned_passes,
            "baseline_pass_rate": round(baseline_rate, 4),
            "conditioned_pass_rate": round(conditioned_rate, 4),
            "absolute_gain": round(conditioned_rate - baseline_rate, 4),
        },
        "functional_compression_verified": certified,
        "boundary_audit": {
            "explorer_workspace_only": stats.get("observed_external_file_access") is False,
            "compressor_workspace_only": compressor_boundary_passed,
            "all_solver_attempts_workspace_only": solver_boundary_passed,
            "passed": bool(
                stats.get("observed_external_file_access") is False
                and compressor_boundary_passed
                and solver_boundary_passed
            ),
        },
        "curriculum": curriculum,
        "policy": {
            "raw_trajectory_withheld_from_downstream_solver": True,
            "reference_hidden_from_all_solvers": True,
            "same_asset_runs_are_non_certifying": True,
            "canonical_bundles_mutated": False,
        },
    }
    write_json(evolution_dir / "evolution.json", report)
    run_dir, _ = record_run(
        store,
        kind="experience-evaluation",
        session_id=held_out_id,
        result=report,
        passed=certified,
        score=conditioned_rate,
        bundle=held_out_bundle,
    )
    return EvolutionOutcome(
        evolution_id=evolution_id,
        evolution_dir=evolution_dir,
        run_dir=run_dir,
        status=status,
        utility=utility,
        baseline_passes=baseline_passes,
        conditioned_passes=conditioned_passes,
        trials=trials,
        report=report,
    )
