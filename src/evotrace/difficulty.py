from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

DEPTH_PATTERN = re.compile(
    r"(?i)(?:\b(?:across|backward compatibility|concurren|distributed|edge cases?|"
    r"end[- ]to[- ]end|integration|migration|multi[- ](?:file|module|service)|performance|"
    r"race condition|refactor|transaction)\b|"
    r"(?:并发|兼容|迁移|集成|多个文件|多模块|性能|竞态|端到端|重构))"
)


def patch_stats(path: Path) -> Dict[str, int]:
    if not path.is_file():
        return {"files": 0, "hunks": 0, "changed_lines": 0}
    text = path.read_text(encoding="utf-8", errors="replace")
    files = len(re.findall(r"(?m)^diff --git ", text))
    hunks = len(re.findall(r"(?m)^@@ ", text))
    changed_lines = sum(
        1
        for line in text.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    return {"files": files, "hunks": hunks, "changed_lines": changed_lines}


def assess_difficulty(
    *,
    task: str,
    tool_calls: int,
    edit_calls: int,
    verification_commands: List[str],
    reference_patch: Path,
    recovery: bool,
    human_corrected: bool,
) -> Dict[str, Any]:
    """Estimate task depth from deterministic trajectory and patch evidence."""

    patch = patch_stats(reference_patch)
    checks = [
        ("at least 8 tool calls", tool_calls >= 8, 1),
        ("at least 30 tool calls", tool_calls >= 30, 1),
        ("multiple edit operations", edit_calls >= 2, 1),
        ("five or more edit operations", edit_calls >= 5, 1),
        ("reference spans multiple files", patch["files"] >= 2, 1),
        ("reference spans at least three hunks", patch["hunks"] >= 3, 1),
        ("reference changes at least 20 lines", patch["changed_lines"] >= 20, 1),
        ("multiple verification commands", len(verification_commands) >= 2, 1),
        ("failure recovery or human correction observed", recovery or human_corrected, 1),
        ("task describes integration or system-level depth", bool(DEPTH_PATTERN.search(task)), 1),
    ]
    score = sum(points for _, passed, points in checks if passed)
    if score <= 1:
        tier = "trivial"
    elif score <= 3:
        tier = "easy"
    elif score <= 6:
        tier = "medium"
    else:
        tier = "hard"
    evidence = [name for name, passed, _ in checks if passed]
    gaps = [name for name, passed, _ in checks if not passed]
    return {
        "schema_version": "0.1",
        "kind": "deterministic_trajectory_depth",
        "score": score,
        "tier": tier,
        "training_eligible": score >= 4,
        "evidence": evidence,
        "gaps": gaps,
        "reference_patch": patch,
    }
