from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import bundle_sha256
from .errors import EvoTraceError
from .util import read_json, utc_now

VALIDATION_PROTOCOL = "docker-two-state-v0.1"
REFERENCE_SCRIPT = r"""set -eu
git reset --hard HEAD >/dev/null
git clean -fd >/dev/null
if [ -s /evotrace/reference.patch ]; then
  git apply --whitespace=nowarn /evotrace/reference.patch
fi
if [ -s /evotrace/untracked-reference.tar.gz ]; then
  tar -xzf /evotrace/untracked-reference.tar.gz -C /workspace
fi
python3 /evotrace/verifier.py --repo /workspace --json
"""


def _tail(value: str, limit: int = 16000) -> str:
    return value[-limit:]


def _run(
    command: List[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise EvoTraceError("Docker is not installed or not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise EvoTraceError(f"Docker operation timed out after {timeout} seconds") from exc


def _validate_policy(bundle: Path) -> Dict[str, Any]:
    policy = read_json(bundle / "sandbox-policy.json")
    required = {
        "execution": "container_only",
        "workspace": "ephemeral_container_copy",
        "host_repository": "read_only_source",
        "host_mounts": [],
        "docker_socket": False,
        "privileged": False,
        "network": "none_by_default",
        "no_new_privileges": True,
    }
    mismatches = {
        key: {"expected": expected, "actual": policy.get(key)}
        for key, expected in required.items()
        if policy.get(key) != expected
    }
    if mismatches:
        raise EvoTraceError(f"Bundle sandbox policy is not conforming: {mismatches}")
    return policy


def _image_tags(bundle: Path) -> tuple[str, str]:
    safe = re.sub(r"[^a-z0-9_.-]+", "-", bundle.name.lower()).strip("-.")
    safe = safe[:48] or "asset"
    suffix = f"{safe}-{bundle_sha256(bundle)[:12]}"
    return f"evotrace-task:{suffix}", f"evotrace-reference:{suffix}"


def _parse_report(result: subprocess.CompletedProcess[str], state: str) -> Dict[str, Any]:
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvoTraceError(
            f"{state} verifier did not return JSON. stdout={_tail(result.stdout)!r} "
            f"stderr={_tail(result.stderr)!r}"
        ) from exc
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        raise EvoTraceError(f"{state} verifier returned an invalid report")
    return {
        "state": state,
        "container_exit_code": result.returncode,
        "passed": bool(report.get("passed")),
        "score": float(report.get("score") or 0.0),
        "checks": report["checks"],
        "stderr": _tail(result.stderr),
    }


def _runtime_command(image: str, command: List[str]) -> List[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--user",
        "65532:65532",
        "--env",
        "HOME=/tmp/evotrace-home",
        "--env",
        "XDG_CACHE_HOME=/tmp/evotrace-cache",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=536870912",
        image,
        *command,
    ]


def _ensure_image(
    bundle: Path,
    *,
    target: str,
    image: str,
    timeout: int,
    rebuild_image: bool = False,
) -> Dict[str, Any]:
    inspected = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        timeout=30,
    )
    reused = inspected.returncode == 0 and not rebuild_image
    build_log = ""
    if not reused:
        built = _run(
            ["docker", "build", "--target", target, "--tag", image, "."],
            cwd=bundle,
            timeout=max(timeout, 1800),
        )
        build_log = _tail(built.stdout + "\n" + built.stderr)
        if built.returncode != 0:
            raise EvoTraceError(f"Docker {target} image build failed:\n{build_log}")
        inspected = _run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            timeout=30,
        )
    if inspected.returncode != 0:
        raise EvoTraceError(f"Could not inspect Docker image {image}")
    return {
        "tag": image,
        "id": inspected.stdout.strip(),
        "reused": reused,
        "build_log_tail": build_log,
    }


def _container_create_command(image: str) -> List[str]:
    return [
        "docker",
        "create",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--user",
        "65532:65532",
        "--env",
        "HOME=/tmp/evotrace-home",
        "--env",
        "XDG_CACHE_HOME=/tmp/evotrace-cache",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=536870912",
        "--entrypoint",
        "sleep",
        image,
        "1800",
    ]


def _copy_to_container(source: Path, container_id: str, destination: str) -> None:
    copied = _run(
        ["docker", "cp", str(source), f"{container_id}:{destination}"],
        timeout=60,
    )
    if copied.returncode != 0:
        raise EvoTraceError(
            f"Could not copy calibration input into Docker: "
            f"{_tail(copied.stdout + copied.stderr)}"
        )


def evaluate_candidate_patch_in_docker(
    bundle: Path,
    patch: Path,
    *,
    verifier: Optional[Dict[str, Any]] = None,
    timeout: int = 900,
    rebuild_image: bool = False,
) -> Dict[str, Any]:
    """Apply an agent patch to the task-only image and score it without host mounts."""

    bundle = bundle.resolve()
    patch = patch.resolve()
    if timeout < 1:
        raise EvoTraceError("Evaluation timeout must be at least 1 second")
    if shutil.which("docker") is None:
        raise EvoTraceError("Docker is required for isolated candidate evaluation")
    if not patch.is_file():
        raise EvoTraceError(f"Candidate patch not found: {patch}")
    _validate_policy(bundle)
    task_image, _ = _image_tags(bundle)
    image = _ensure_image(
        bundle,
        target="task",
        image=task_image,
        timeout=timeout,
        rebuild_image=rebuild_image,
    )
    verifier_config = verifier or read_json(bundle / "verifier.json")
    if not verifier_config.get("commands"):
        raise EvoTraceError("Candidate evaluation requires a behavioral verifier command")

    created = _run(_container_create_command(task_image), timeout=60)
    if created.returncode != 0:
        raise EvoTraceError(
            "Could not create candidate evaluation container: "
            + _tail(created.stdout + created.stderr)
        )
    container_id = created.stdout.strip()
    if not container_id:
        raise EvoTraceError("Docker did not return a candidate evaluation container id")

    try:
        with tempfile.TemporaryDirectory(prefix="evotrace-verifier-overlay-") as temporary:
            verifier_path = Path(temporary) / "verifier.json"
            verifier_path.write_text(
                json.dumps(verifier_config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _copy_to_container(patch, container_id, "/evotrace/candidate.patch")
            _copy_to_container(verifier_path, container_id, "/evotrace/verifier.json")
        started = _run(["docker", "start", container_id], timeout=60)
        if started.returncode != 0:
            raise EvoTraceError(
                "Could not start candidate evaluation container: "
                + _tail(started.stdout + started.stderr)
            )
        applied = _run(
            [
                "docker",
                "exec",
                "--user",
                "65532:65532",
                "--workdir",
                "/workspace",
                container_id,
                "git",
                "apply",
                "--whitespace=nowarn",
                "/evotrace/candidate.patch",
            ],
            timeout=min(timeout, 120),
        )
        if applied.returncode != 0:
            return {
                "schema_version": "0.1",
                "protocol": "docker-candidate-patch-v0.1",
                "provider": "docker",
                "passed": False,
                "score": 0.0,
                "patch_applied": False,
                "error": _tail(applied.stdout + applied.stderr),
                "image": image,
                "controls": _evaluation_controls(),
            }
        executed = _run(
            [
                "docker",
                "exec",
                "--user",
                "65532:65532",
                "--workdir",
                "/workspace",
                container_id,
                "python3",
                "/evotrace/verifier.py",
                "--repo",
                "/workspace",
                "--json",
            ],
            timeout=timeout,
        )
        report = _parse_report(executed, "candidate")
        return {
            "schema_version": "0.1",
            "protocol": "docker-candidate-patch-v0.1",
            "provider": "docker",
            "passed": report["passed"] and executed.returncode == 0,
            "score": report["score"],
            "patch_applied": True,
            "checks": report["checks"],
            "stderr": report["stderr"],
            "image": image,
            "controls": _evaluation_controls(),
        }
    finally:
        _run(["docker", "rm", "--force", container_id], timeout=60)


def evaluate_reference_with_verifier_in_docker(
    bundle: Path,
    verifier: Dict[str, Any],
    *,
    timeout: int = 900,
) -> Dict[str, Any]:
    """Gate a calibration verifier overlay against the hidden reference state."""

    bundle = bundle.resolve()
    if timeout < 1:
        raise EvoTraceError("Evaluation timeout must be at least 1 second")
    if shutil.which("docker") is None:
        raise EvoTraceError("Docker is required for isolated verifier evaluation")
    _validate_policy(bundle)
    if not verifier.get("commands"):
        raise EvoTraceError("A calibration verifier must contain behavioral commands")
    _, reference_image = _image_tags(bundle)
    image = _ensure_image(
        bundle,
        target="validation-reference",
        image=reference_image,
        timeout=timeout,
    )
    created = _run(_container_create_command(reference_image), timeout=60)
    if created.returncode != 0:
        raise EvoTraceError(
            "Could not create reference verifier container: "
            + _tail(created.stdout + created.stderr)
        )
    container_id = created.stdout.strip()
    if not container_id:
        raise EvoTraceError("Docker did not return a reference verifier container id")
    try:
        with tempfile.TemporaryDirectory(prefix="evotrace-reference-verifier-") as temporary:
            verifier_path = Path(temporary) / "verifier.json"
            verifier_path.write_text(
                json.dumps(verifier, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _copy_to_container(verifier_path, container_id, "/evotrace/verifier.json")
        started = _run(["docker", "start", container_id], timeout=60)
        if started.returncode != 0:
            raise EvoTraceError(
                "Could not start reference verifier container: "
                + _tail(started.stdout + started.stderr)
            )
        executed = _run(
            [
                "docker",
                "exec",
                "--user",
                "65532:65532",
                "--workdir",
                "/workspace",
                container_id,
                "/bin/sh",
                "-lc",
                REFERENCE_SCRIPT,
            ],
            timeout=timeout,
        )
        report = _parse_report(executed, "calibration-reference")
        return {
            "schema_version": "0.1",
            "protocol": "docker-verifier-overlay-v0.1",
            "provider": "docker",
            "passed": report["passed"] and executed.returncode == 0,
            "score": report["score"],
            "checks": report["checks"],
            "stderr": report["stderr"],
            "image": image,
            "controls": _evaluation_controls(),
        }
    finally:
        _run(["docker", "rm", "--force", container_id], timeout=60)


def _evaluation_controls() -> Dict[str, Any]:
    return {
        "network": "none",
        "capabilities": "drop_all",
        "no_new_privileges": True,
        "host_mounts": [],
        "docker_socket": False,
        "privileged": False,
        "runtime_user": "65532:65532",
    }


def validate_bundle_in_docker(
    bundle: Path,
    *,
    timeout: int = 900,
    rebuild_image: bool = False,
) -> Dict[str, Any]:
    """Build and independently validate base/reference states in Docker."""

    bundle = bundle.resolve()
    if timeout < 1:
        raise EvoTraceError("Validation timeout must be at least 1 second")
    if shutil.which("docker") is None:
        raise EvoTraceError("Docker is required for isolated validation")
    _validate_policy(bundle)
    verifier = read_json(bundle / "verifier.json")
    commands = verifier.get("commands") or []
    if not commands:
        raise EvoTraceError("A behavioral verifier command is required before validation")
    reference_patch = bundle / "patches" / "reference.patch"
    reference_untracked = bundle / "environment" / "untracked-reference.tar.gz"
    if not (
        reference_patch.exists()
        and reference_patch.stat().st_size > 0
        or reference_untracked.exists()
        and reference_untracked.stat().st_size > 0
    ):
        raise EvoTraceError(
            "A non-empty reference patch or reference untracked snapshot is required"
        )

    task_image, reference_image = _image_tags(bundle)
    build_results: List[subprocess.CompletedProcess[str]] = []
    for target, image in (("task", task_image), ("validation-reference", reference_image)):
        image_exists = _run(
            ["docker", "image", "inspect", image],
            timeout=30,
        ).returncode == 0
        if rebuild_image or not image_exists:
            built = _run(
                ["docker", "build", "--target", target, "--tag", image, "."],
                cwd=bundle,
                timeout=max(timeout, 1800),
            )
            build_results.append(built)
            if built.returncode != 0:
                raise EvoTraceError(
                    f"Docker {target} image build failed:\n"
                    + _tail(built.stdout + "\n" + built.stderr)
                )
    inspect = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", task_image],
        timeout=30,
    )
    reference_inspect = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference_image],
        timeout=30,
    )
    if inspect.returncode != 0 or reference_inspect.returncode != 0:
        raise EvoTraceError("Could not inspect the validation images")

    base_result = _run(
        _runtime_command(
            task_image,
            ["python3", "/evotrace/verifier.py", "--repo", "/workspace", "--json"],
        ),
        timeout=timeout,
    )
    reference_result = _run(
        _runtime_command(reference_image, ["/bin/sh", "-lc", REFERENCE_SCRIPT]),
        timeout=timeout,
    )
    base = _parse_report(base_result, "base")
    reference = _parse_report(reference_result, "reference")
    behavioral_count = len(commands)
    base_behavioral_checks = base["checks"][:behavioral_count]
    base_rejected = bool(base_behavioral_checks) and any(
        not bool(check.get("passed")) for check in base_behavioral_checks
    )
    reference_accepted = reference["passed"] and reference_result.returncode == 0
    passed = base_rejected and reference_accepted
    return {
        "schema_version": "0.1",
        "protocol": VALIDATION_PROTOCOL,
        "provider": "docker",
        "created_at": utc_now(),
        "passed": passed,
        "image": {
            "tag": task_image,
            "id": inspect.stdout.strip(),
            "reference_tag": reference_image,
            "reference_id": reference_inspect.stdout.strip(),
            "reference_isolated": True,
        },
        "bundle_sha256": bundle_sha256(bundle),
        "build": {
            "reused": not build_results,
            "exit_code": None if not build_results else 0,
            "log_tail": _tail("".join(item.stdout + item.stderr for item in build_results)),
        },
        "controls": {
            "network": "none",
            "capabilities": "drop_all",
            "no_new_privileges": True,
            "host_mounts": [],
            "docker_socket": False,
            "privileged": False,
            "runtime_user": "65532:65532",
        },
        "base": base,
        "reference": reference,
        "gates": {
            "base_behavioral_failure": base_rejected,
            "reference_passes": reference_accepted,
        },
    }
