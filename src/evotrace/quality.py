from __future__ import annotations

from typing import Any, Dict, List


def task_is_meaningful(task: Any) -> bool:
    """Return whether a recovered task contains an actual user request."""

    text = str(task or "").strip()
    if not text:
        return False
    marker = "## My request:"
    if marker in text:
        request = text.rsplit(marker, 1)[1].strip()
        if not request:
            return False
    if "Pasted text contains the user's request." in text and marker not in text:
        return False
    return True


def execution_build_gaps(
    candidate: Dict[str, Any], trajectory: Dict[str, Any]
) -> List[str]:
    """Return deterministic gaps that must be closed before bundle generation."""

    labels = set(candidate.get("labels") or [])
    signals = candidate.get("signals") or {}
    repository = trajectory.get("repository") or {}
    task = (trajectory.get("task") or {}).get("text")
    source_kind = str((trajectory.get("source") or {}).get("kind") or "")
    confidence = str(
        signals.get("reconstruction_confidence")
        or repository.get("reconstruction_confidence")
        or "none"
    )
    gaps: List[str] = []
    if "execution_verifiable" not in labels:
        gaps.append("candidate is not routed as execution-verifiable")
    if not task_is_meaningful(task):
        gaps.append("recovered task is empty or an import placeholder")
    if not repository.get("base_commit"):
        gaps.append("repository base commit is missing")
    if source_kind != "command" and confidence not in {"high", "medium"}:
        gaps.append(
            f"repository reconstruction confidence is {confidence}; high or medium is required"
        )
    if not signals.get("reference_patch_available"):
        gaps.append("reference patch is missing")
    if not signals.get("verification_commands"):
        gaps.append("behavioral verification commands are missing")
    return gaps


def bundle_manifest_is_runnable(manifest: Dict[str, Any]) -> bool:
    """Reject legacy bundles that were emitted before build-readiness gates."""

    reproducibility = manifest.get("reproducibility") or {}
    verifier = manifest.get("verifier") or {}
    return bool(
        task_is_meaningful(manifest.get("task"))
        and reproducibility.get("confidence") in {"high", "medium"}
        and reproducibility.get("reference_patch")
        and verifier.get("commands")
        and not (manifest.get("environment") or {}).get("unsupported_reasons")
    )
