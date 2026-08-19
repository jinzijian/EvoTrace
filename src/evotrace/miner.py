from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analytics import CORRECTION
from .compiler import commands_from_events
from .store import Store
from .util import load_jsonl, utc_now, write_json

EDIT_TOOLS = {
    "apply_patch",
    "edit",
    "multiedit",
    "notebookedit",
    "str_replace_editor",
    "write",
    "write_file",
}
FAILURE_PATTERN = re.compile(
    r"(?i)(?:\b[1-9]\d* failed\b|tests? failed|exit(?:ed)? (?:with )?(?:code|status) [1-9]\d*)"
)
SUCCESS_PATTERN = re.compile(
    r"(?i)(?:\b0 failed\b|tests? passed|exit(?:ed)? (?:with )?(?:code|status) 0|\bpassed\b)"
)


@dataclass
class MiningSummary:
    total: int
    useful: int
    human_corrected: int
    execution_verifiable: int
    preference_candidates: int
    recovery_trajectories: int
    low_value: int
    candidates: List[Dict[str, Any]]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _is_edit_tool(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    normalized = name.lower().replace("-", "_")
    return normalized in EDIT_TOOLS or normalized.endswith("_edit")


def mine_session(session_dir: Path, trajectory: Dict[str, Any]) -> Dict[str, Any]:
    events_path = session_dir / "events.jsonl"
    events = list(load_jsonl(events_path)) if events_path.exists() else []
    user_messages = 0
    assistant_messages = 0
    tool_calls = 0
    edit_calls = 0
    saw_work_before_user = False
    human_corrected = False
    failure_positions = []
    success_positions = []
    edit_positions = []

    for index, event in enumerate(events):
        kind = event.get("kind")
        data = event.get("data") or {}
        if kind == "message.user":
            user_messages += 1
            text = _text(data.get("text", ""))
            if saw_work_before_user and CORRECTION.search(text):
                human_corrected = True
        elif kind == "message.assistant":
            assistant_messages += 1
            saw_work_before_user = True
        elif kind == "tool.call":
            tool_calls += 1
            saw_work_before_user = True
            if _is_edit_tool(data.get("name")):
                edit_calls += 1
                edit_positions.append(index)
        elif kind == "tool.result":
            output = _text(data.get("output", ""))
            if data.get("is_error") or FAILURE_PATTERN.search(output):
                failure_positions.append(index)
            elif SUCCESS_PATTERN.search(output):
                success_positions.append(index)

    verification_commands = commands_from_events(events_path)
    repository = trajectory.get("repository") or {}
    repo_root = repository.get("root")
    repo_available = bool(repo_root and Path(repo_root).expanduser().is_dir())
    reconstruction = repository.get("reconstruction_confidence")
    if reconstruction is None:
        reconstruction = "high" if repository.get("base_commit_source") == "session" else "low"
    replayable = bool(
        repo_available and repository.get("base_commit") and reconstruction in {"high", "medium"}
    )
    recovered_after_failure = bool(
        failure_positions
        and any(
            position > failure_positions[0] for position in [*success_positions, *edit_positions]
        )
    )
    recovery = recovered_after_failure or (
        human_corrected and bool(edit_positions) and user_messages >= 2
    )
    preference = bool(
        (human_corrected and edit_calls > 0)
        or (edit_calls >= 2 and user_messages >= 2 and assistant_messages >= 2)
    )
    execution = bool(verification_commands and replayable and edit_calls > 0)
    task = ((trajectory.get("task") or {}).get("text") or "").strip()
    task_nontrivial = len(task) >= 40

    evidence = []
    score = 0
    if task_nontrivial:
        score += 1
        evidence.append("non-trivial task text recovered")
    if tool_calls >= 3:
        score += 1
        evidence.append(f"{tool_calls} tool calls")
    if edit_calls:
        score += 2
        evidence.append(f"{edit_calls} code-edit tool calls")
    if verification_commands:
        score += 2
        evidence.append(f"{len(verification_commands)} verification command(s) recovered")
    if replayable:
        score += 1
        evidence.append(f"repository base reconstructed with {reconstruction} confidence")
    if human_corrected:
        score += 3
        evidence.append("human correction language after agent work")
    if recovery:
        score += 2
        evidence.append("failure/correction followed by recovery work")
    score = min(score, 10)
    useful = score >= 4 and bool(task)

    labels = []
    if useful:
        labels.append("useful")
    else:
        labels.append("low_value_or_trivial")
    if human_corrected:
        labels.append("human_corrected")
    if execution:
        labels.append("execution_verifiable")
    if preference:
        labels.append("preference_candidate")
    if recovery:
        labels.append("recovery_trajectory")
    if useful and assistant_messages:
        labels.append("sft_candidate")

    return {
        "schema_version": "0.1",
        "session_id": trajectory.get("session_id"),
        "source": (trajectory.get("source") or {}).get("agent"),
        "mined_at": utc_now(),
        "curator": {
            "kind": "evidence_heuristic",
            "version": "0.2",
            "model_used": False,
        },
        "score": score,
        "labels": labels,
        "evidence": evidence,
        "signals": {
            "user_messages": user_messages,
            "assistant_messages": assistant_messages,
            "tool_calls": tool_calls,
            "edit_calls": edit_calls,
            "verification_commands": verification_commands,
            "human_corrected": human_corrected,
            "recovery_observed": recovery,
            "repository_available": repo_available,
            "reconstruction_confidence": reconstruction,
        },
    }


def mine_store(
    store: Store, *, source: Optional[str] = None, minimum_score: int = 0
) -> MiningSummary:
    store.initialize()
    candidates = []
    counts: Counter[str] = Counter()
    for session_dir, trajectory in store.list_sessions():
        agent = (trajectory.get("source") or {}).get("agent")
        if source and source != "all" and agent != source:
            continue
        candidate = mine_session(session_dir, trajectory)
        write_json(store.candidates / f"{trajectory['session_id']}.json", candidate)
        for label in candidate["labels"]:
            counts[label] += 1
        if candidate["score"] >= minimum_score:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    total = counts["useful"] + counts["low_value_or_trivial"]
    index = {
        "schema_version": "0.1",
        "curator": "evidence_heuristic_v0.2",
        "mined_at": utc_now(),
        "total": total,
        "counts": dict(counts),
        "candidates": [item["session_id"] for item in candidates],
    }
    write_json(store.root / "mining-index.json", index)
    return MiningSummary(
        total=total,
        useful=counts["useful"],
        human_corrected=counts["human_corrected"],
        execution_verifiable=counts["execution_verifiable"],
        preference_candidates=counts["preference_candidate"],
        recovery_trajectories=counts["recovery_trajectory"],
        low_value=counts["low_value_or_trivial"],
        candidates=candidates,
    )
