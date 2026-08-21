from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .errors import EvoTraceError
from .quality import bundle_manifest_is_runnable, task_is_meaningful
from .store import Store
from .util import load_jsonl, read_json, short_id, utc_now, write_json, write_jsonl

LABEL_NAMES = {
    "execution_verifiable": "RL environment",
    "preference_candidate": "Preference pair",
    "recovery_trajectory": "Recovery trajectory",
    "sft_candidate": "SFT trajectory",
}


def _hint(command: str) -> str:
    return f"/{command}" if os.environ.get("EVOTRACE_SLASH_MODE") == "1" else f"et {command}"


def compact(text: Any, limit: int = 70) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def candidate_task(store: Store, candidate: Dict[str, Any]) -> str:
    review = ((candidate.get("model_review") or {}).get("review") or {})
    if review.get("task_summary"):
        return str(review["task_summary"])
    try:
        _, trajectory = store.load_session(str(candidate.get("session_id") or ""))
    except EvoTraceError:
        return ""
    return str((trajectory.get("task") or {}).get("text") or "")


def asset_types(candidate: Dict[str, Any]) -> List[str]:
    review_types = (
        ((candidate.get("model_review") or {}).get("review") or {}).get("asset_types") or []
    )
    if review_types:
        pretty = {
            "sft": "SFT",
            "preference": "Preference pair",
            "qa": "QA",
            "execution_reward": "Execution reward",
            "eval": "Eval",
            "recovery": "Recovery",
        }
        return [pretty.get(str(value), str(value)) for value in review_types]
    labels = set(candidate.get("labels") or [])
    names = [name for label, name in LABEL_NAMES.items() if label in labels]
    if "needs_hardening" in labels and "RL environment" in names:
        names[names.index("RL environment")] = "Hardening seed"
    return names


def visible_candidates(
    store: Store,
    *,
    include_all: bool = False,
    minimum_score: int = 4,
    label: Optional[str] = None,
) -> List[Dict[str, Any]]:
    result = []
    for candidate in store.list_candidates():
        labels = set(candidate.get("labels") or [])
        if not include_all and "nested_subagent" in labels:
            continue
        if not include_all and "useful" not in labels:
            continue
        if int(candidate.get("score") or 0) < minimum_score:
            continue
        if label and label not in labels:
            continue
        result.append(candidate)
    return sorted(
        result,
        key=lambda item: (
            int((item.get("difficulty") or {}).get("score") or 0),
            int(item.get("score") or 0),
        ),
        reverse=True,
    )


def search_candidates(
    store: Store, query: str, *, limit: int = 20
) -> List[Dict[str, Any]]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    matches = []
    for candidate in visible_candidates(store):
        session_id = str(candidate.get("session_id") or "")
        try:
            _, trajectory = store.load_session(session_id)
        except EvoTraceError:
            trajectory = {}
        repository = trajectory.get("repository") or {}
        haystack = "\n".join(
            [
                session_id,
                candidate_task(store, candidate),
                str(repository.get("origin") or ""),
                str(repository.get("root") or ""),
                " ".join(str(item) for item in candidate.get("labels") or []),
                " ".join(str(item) for item in candidate.get("evidence") or []),
                " ".join(asset_types(candidate)),
            ]
        ).casefold()
        if all(term in haystack for term in terms):
            matches.append(candidate)
        if len(matches) >= limit:
            break
    return matches


def resolve_candidate(store: Store, selector: str) -> Dict[str, Any]:
    candidates = store.list_candidates()
    if not candidates:
        raise EvoTraceError("No mined candidates found. Run `et mine` first.")

    if selector.isdigit():
        visible = visible_candidates(store)
        index = int(selector)
        if index < 1 or index > len(visible):
            raise EvoTraceError(
                f"Candidate number {index} is out of range. "
                f"Run `{_hint('candidates')}` to list choices."
            )
        return visible[index - 1]

    exact = [item for item in candidates if item.get("session_id") == selector]
    if exact:
        return exact[0]
    matches = [
        item for item in candidates if str(item.get("session_id") or "").startswith(selector)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise EvoTraceError(f"Candidate prefix is ambiguous: {selector}")
    raise EvoTraceError(
        f"Candidate not found: {selector}. Run `{_hint('candidates')}` first."
    )


def related_runs(store: Store, session_id: str) -> List[tuple[Path, Dict[str, Any]]]:
    return [
        item for item in store.list_runs() if item[1].get("session_id") == session_id
    ]


def bundle_sha256(bundle: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        digest.update(path.relative_to(bundle).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def has_matching_passed_run(store: Store, session_id: str, bundle: Path) -> bool:
    if not (bundle / "task.json").exists():
        return False
    current_digest = bundle_sha256(bundle)
    return any(
        is_conforming_validation_run(run)
        and run.get("bundle_sha256") == current_digest
        for _, run in related_runs(store, session_id)
    )


def is_conforming_validation_run(run: Dict[str, Any]) -> bool:
    result = run.get("result") or {}
    controls = result.get("controls") or {}
    gates = result.get("gates") or {}
    image = result.get("image") or {}
    return bool(
        run.get("kind") == "docker-validation"
        and run.get("passed")
        and result.get("protocol") == "docker-two-state-v0.1"
        and result.get("provider") == "docker"
        and result.get("passed")
        and image.get("reference_isolated") is True
        and gates.get("base_behavioral_failure") is True
        and gates.get("reference_passes") is True
        and controls.get("network") == "none"
        and controls.get("host_mounts") == []
        and controls.get("docker_socket") is False
        and controls.get("privileged") is False
        and controls.get("no_new_privileges") is True
    )


def readiness(
    store: Store, candidate: Dict[str, Any], trajectory: Dict[str, Any]
) -> List[tuple[str, bool]]:
    session_id = str(candidate.get("session_id") or "")
    signals = candidate.get("signals") or {}
    repository = trajectory.get("repository") or {}
    bundle = store.benchmarks / session_id
    validated = has_matching_passed_run(store, session_id, bundle)
    bundle_manifest: Dict[str, Any] = {}
    if (bundle / "task.json").exists():
        try:
            bundle_manifest = read_json(bundle / "task.json")
        except EvoTraceError:
            bundle_manifest = {}
    runnable_bundle = bundle_manifest_is_runnable(bundle_manifest)
    return [
        ("Trajectory indexed", bool(session_id and (store.sessions / session_id).is_dir())),
        ("Task reconstructed", task_is_meaningful((trajectory.get("task") or {}).get("text"))),
        (
            "Repository base recovered",
            bool(
                signals.get("repository_available")
                and repository.get("base_commit")
                and signals.get("reconstruction_confidence") in {"high", "medium"}
            ),
        ),
        ("Verification evidence recovered", bool(signals.get("verification_commands"))),
        ("Environment bundle built", runnable_bundle),
        ("Verifier validated", validated),
    ]


def readiness_status(stages: Iterable[tuple[str, bool]]) -> str:
    values = list(stages)
    complete = {name: done for name, done in values}
    if all(complete.values()):
        return "Verified"
    prerequisites = [
        "Trajectory indexed",
        "Task reconstructed",
        "Repository base recovered",
        "Verification evidence recovered",
    ]
    prerequisites_ready = all(complete.get(name, False) for name in prerequisites)
    if prerequisites_ready and complete.get("Environment bundle built"):
        return "Bundle ready"
    if prerequisites_ready:
        return "Buildable"
    return "Needs evidence"


def record_run(
    store: Store,
    *,
    kind: str,
    session_id: str,
    result: Any,
    passed: bool,
    score: Optional[float] = None,
    bundle: Optional[Path] = None,
) -> tuple[Path, Dict[str, Any]]:
    store.initialize()
    run_id = short_id("run")
    run_dir = store.runs / run_id
    run_dir.mkdir()
    record = {
        "schema_version": "0.1",
        "id": run_id,
        "created_at": utc_now(),
        "kind": kind,
        "session_id": session_id,
        "passed": bool(passed),
        "score": score,
        "bundle_sha256": bundle_sha256(bundle) if bundle else None,
        "result": result,
    }
    write_json(run_dir / "run.json", record)
    return run_dir, record


def _portable_trajectory(trajectory: Dict[str, Any]) -> Dict[str, Any]:
    value = json.loads(json.dumps(trajectory))
    source = value.get("source") or {}
    source.pop("path", None)
    repository = value.get("repository") or {}
    repository.pop("root", None)
    return value


def _portable_run(run: Dict[str, Any]) -> Dict[str, Any]:
    value = json.loads(json.dumps(run))
    result = value.get("result")
    if isinstance(result, dict):
        result.pop("run_dir", None)
    return value


def export_asset(
    store: Store,
    selector: str,
    *,
    output: Optional[Path] = None,
    allow_sensitive: bool = False,
) -> Path:
    candidate = resolve_candidate(store, selector)
    session_id = str(candidate.get("session_id") or "")
    signals = candidate.get("signals") or {}
    if signals.get("sensitive_content") and not allow_sensitive:
        raise EvoTraceError(
            "This candidate is marked sensitive. Review it locally, then pass "
            "`--allow-sensitive` to export it explicitly."
        )
    bundle = store.benchmarks / session_id
    if not (bundle / "task.json").exists():
        raise EvoTraceError(
            f"Asset is not built yet. Run `{_hint('build ' + selector)}` first."
        )

    destination = (output or Path.cwd() / f"{session_id}.evotrace.tar.gz").expanduser()
    if destination.exists():
        raise EvoTraceError(f"Export destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    session_dir, trajectory = store.load_session(session_id)
    with tempfile.TemporaryDirectory(prefix="evotrace-export-") as temporary:
        root = Path(temporary) / session_id
        root.mkdir()
        export_manifest = {
            "schema_version": "0.1",
            "format": "evotrace-portable-asset",
            "exported_at": utc_now(),
            "session_id": session_id,
            "sensitive_content": bool(signals.get("sensitive_content")),
            "contents": ["candidate.json", "trajectory.json", "events.jsonl", "bundle/"],
        }
        write_json(root / "manifest.json", export_manifest)
        write_json(root / "candidate.json", candidate)
        write_json(root / "trajectory.json", _portable_trajectory(trajectory))
        events = session_dir / "events.jsonl"
        if events.exists():
            write_jsonl(root / "events.jsonl", load_jsonl(events))
        else:
            (root / "events.jsonl").touch()
        shutil.copytree(bundle, root / "bundle")
        runs = related_runs(store, session_id)
        if runs:
            run_root = root / "runs"
            run_root.mkdir()
            for run_path, _ in runs:
                write_json(
                    run_root / f"{run_path.name}.json",
                    _portable_run(read_json(run_path / "run.json")),
                )
            export_manifest["contents"].append("runs/")
            write_json(root / "manifest.json", export_manifest)
        calibrations = store.calibrations / session_id
        if calibrations.is_dir():
            shutil.copytree(calibrations, root / "calibrations")
            export_manifest["contents"].append("calibrations/")
            write_json(root / "manifest.json", export_manifest)
        evolutions = store.evolutions / session_id
        if evolutions.is_dir():
            shutil.copytree(evolutions, root / "evolutions")
            export_manifest["contents"].append("evolutions/")
            write_json(root / "manifest.json", export_manifest)
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(root, arcname=session_id)
    return destination.resolve()
