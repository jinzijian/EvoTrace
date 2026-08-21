from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analytics import CORRECTION
from .compiler import commands_from_events
from .difficulty import assess_difficulty
from .store import Store
from .util import load_jsonl, read_json, utc_now, write_json

EDIT_TOOLS = {
    "apply_patch",
    "edit",
    "multiedit",
    "notebookedit",
    "str_replace_editor",
    "write",
    "write_file",
}
SHELL_TOOLS = {"bash", "exec_command", "shell", "terminal"}
MUTATION_PATTERNS = [
    re.compile(r"(?i)(?:^|[\s;&|])(?:apply_patch|patch|git\s+apply)(?:\s|$)"),
    re.compile(r"(?i)(?:^|[\s;&|])(?:sed\s+-i|sed\b[^\n]*\s-i|perl\s+-pi)(?:\s|$)"),
    re.compile(r"(?i)(?:^|[\s;&|])tee(?:\s|$)"),
    re.compile(r"(?i)(?:^|[\s;&|'\"])(?:cat|printf|echo)\b[^\n]*(?:>>|>)"),
    re.compile(r"(?i)\.write_(?:text|bytes)\s*\("),
    re.compile(r"(?i)\bopen\s*\([^\n]{0,160},\s*[\"'](?:w|a|x)"),
    re.compile(r"(?i)(?:^|[\s;&|'\"])(?:touch|mkdir|mv|cp)(?:\s|$)"),
    re.compile(r"(?i)(?:^|[\s;&|'\"])(?:rm|unlink|rmtree)(?:\s|$)"),
]
DISCARD_PATTERNS = [
    re.compile(r"(?i)(?:^|[\s;&|])(?:rm|unlink|rmtree)(?:\s|$)"),
    re.compile(r"(?i)(?:^|[\s;&|])git\s+(?:restore|revert|reset|checkout\s+--)(?:\s|$)"),
]
REMOTE_PATTERN = re.compile(r"(?i)(?:^|[;&|]\s*)ssh(?:\s|$)")
TECHNICAL_PATTERN = re.compile(
    r"(?i)(?:\b(?:api|app|backend|benchmark|bug|build|cli|code|commit|compiler|cuda|"
    r"database|debug|docker|eval|frontend|function|git|github|implementation|issue|"
    r"javascript|model|npm|package|parser|patch|pipeline|pytest|python|repo|repository|"
    r"sdk|server|ssh|test|typescript|verifier)\b|"
    r"(?:修复|代码|仓库|测试|实现|模型|训练|评测|验证器|服务器|环境|接口))"
)
TECHNICAL_COMMAND_PATTERN = re.compile(
    r"(?i)(?:^|[\s;&|])(?:cargo|docker|git|go|make|mypy|npm|nox|pnpm|pyright|pytest|"
    r"python|ruff|tox|uv|yarn)(?:\s|$)|\.(?:c|cc|cpp|go|js|jsx|py|rs|ts|tsx)\b"
)
SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:\b(?:bank|billing|credit card|immigration|interview outreach|invoice|legal|"
    r"petition|subscription|tax|visa)\b|"
    r"(?:移民|签证|推荐信|求职|订阅|信用卡|银行|账单|律师|申请材料|套磁|邮件联系))"
)
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
    non_coding: int
    sensitive: int
    training_ready_execution: int
    needs_hardening: int
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


def _command(data: Dict[str, Any]) -> str:
    arguments = data.get("arguments")
    if not isinstance(arguments, dict):
        return ""
    name = str(data.get("name") or "").lower().replace("-", "_")
    if name in SHELL_TOOLS:
        value = arguments.get("cmd") or arguments.get("command")
        return value if isinstance(value, str) else ""
    # Some agent runtimes wrap tool orchestration in a JavaScript call. Only
    # inspect it for explicit file-mutation syntax; never execute it.
    if name in {"js", "javascript"}:
        value = arguments.get("code")
        return value if isinstance(value, str) else ""
    return ""


def _matches_any(patterns: List[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _edit_scope(data: Dict[str, Any]) -> Optional[str]:
    if _is_edit_tool(data.get("name")):
        return "local"
    command = _command(data)
    if not command or not _matches_any(MUTATION_PATTERNS, command):
        return None
    return "remote" if REMOTE_PATTERN.search(command) else "local"


def _is_discard(data: Dict[str, Any]) -> bool:
    command = _command(data)
    return bool(command and _matches_any(DISCARD_PATTERNS, command))


def _is_technical_tool(data: Dict[str, Any]) -> bool:
    if _is_edit_tool(data.get("name")):
        return True
    command = _command(data)
    return bool(command and TECHNICAL_COMMAND_PATTERN.search(command))


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
    correction_positions = []
    discard_positions = []
    remote_edit_calls = 0
    technical_tool_calls = 0
    user_texts = []

    for index, event in enumerate(events):
        kind = event.get("kind")
        data = event.get("data") or {}
        if kind == "message.user":
            user_messages += 1
            text = _text(data.get("text", ""))
            user_texts.append(text)
            if saw_work_before_user and CORRECTION.search(text):
                human_corrected = True
                correction_positions.append(index)
        elif kind == "message.assistant":
            assistant_messages += 1
            saw_work_before_user = True
        elif kind == "tool.call":
            tool_calls += 1
            saw_work_before_user = True
            if _is_technical_tool(data):
                technical_tool_calls += 1
            scope = _edit_scope(data)
            if scope == "local":
                edit_calls += 1
                edit_positions.append(index)
            elif scope == "remote":
                remote_edit_calls += 1
            if _is_discard(data):
                discard_positions.append(index)
        elif kind == "tool.result":
            output = _text(data.get("output", ""))
            if data.get("is_error") or FAILURE_PATTERN.search(output):
                failure_positions.append(index)
            elif SUCCESS_PATTERN.search(output):
                success_positions.append(index)

    verification_commands = commands_from_events(events_path)
    reference_patch = session_dir / "patches" / "final.patch"
    reference_available = bool(
        reference_patch.exists() and reference_patch.stat().st_size > 0
    )
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
    corrected_then_edited = any(
        edit_position > correction_position
        for correction_position in correction_positions
        for edit_position in edit_positions
    )
    edited_before_correction = any(
        edit_position < correction_position
        for correction_position in correction_positions
        for edit_position in edit_positions
    )
    discarded_after_edit = any(
        discard_position > edit_position
        for discard_position in discard_positions
        for edit_position in edit_positions
    )
    recovery = recovered_after_failure or corrected_then_edited
    preference = bool(
        (corrected_then_edited and edited_before_correction)
        or (discarded_after_edit and user_messages >= 2 and assistant_messages >= 2)
    )
    execution = bool(
        verification_commands and replayable and edit_calls > 0 and reference_available
    )
    task = ((trajectory.get("task") or {}).get("text") or "").strip()
    source_metadata = trajectory.get("source") or {}
    nested_subagent = bool(
        source_metadata.get("parent_session_id")
        or source_metadata.get("thread_source") == "subagent"
    )
    task_nontrivial = len(task) >= 40
    joined_user_text = "\n".join(user_texts)
    technical_task = bool(TECHNICAL_PATTERN.search(joined_user_text or task))
    coding_scope = bool(verification_commands or technical_task or technical_tool_calls >= 2)
    sensitive_content = bool(SENSITIVE_PATTERN.search(joined_user_text or task))
    difficulty = assess_difficulty(
        task=task,
        tool_calls=tool_calls,
        edit_calls=edit_calls,
        verification_commands=verification_commands,
        reference_patch=reference_patch,
        recovery=recovery,
        human_corrected=human_corrected,
    )

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
    if reference_available:
        score += 1
        evidence.append("reference patch reconstructed from local edit evidence")
    if human_corrected:
        score += 3
        evidence.append("human correction language after agent work")
    if recovery:
        score += 2
        evidence.append("failure/correction followed by recovery work")
    score = min(score, 10)
    if coding_scope:
        evidence.append("coding or research-engineering evidence observed")
    useful = score >= 4 and bool(task) and coding_scope

    labels = []
    if useful:
        labels.append("useful")
    else:
        labels.append("low_value_or_trivial")
    if human_corrected:
        labels.append("human_corrected")
    if execution:
        labels.append("execution_verifiable")
        if difficulty["training_eligible"]:
            labels.append("training_ready_execution")
        else:
            labels.append("needs_hardening")
    labels.append(f"difficulty_{difficulty['tier']}")
    if preference:
        labels.append("preference_candidate")
    if recovery:
        labels.append("recovery_trajectory")
    if useful and assistant_messages:
        labels.append("sft_candidate")
    if not coding_scope:
        labels.append("non_coding_or_personal")
    if sensitive_content:
        labels.append("sensitive_content")
    if discarded_after_edit:
        labels.append("discarded_work_observed")
    if nested_subagent:
        labels.append("nested_subagent")

    return {
        "schema_version": "0.1",
        "session_id": trajectory.get("session_id"),
        "source": (trajectory.get("source") or {}).get("agent"),
        "mined_at": utc_now(),
        "curator": {
            "kind": "evidence_heuristic",
            "version": "0.4",
            "model_used": False,
        },
        "score": score,
        "difficulty": difficulty,
        "labels": labels,
        "evidence": evidence,
        "signals": {
            "user_messages": user_messages,
            "assistant_messages": assistant_messages,
            "tool_calls": tool_calls,
            "edit_calls": edit_calls,
            "remote_edit_calls": remote_edit_calls,
            "discard_calls": len(discard_positions),
            "technical_tool_calls": technical_tool_calls,
            "verification_commands": verification_commands,
            "reference_patch_available": reference_available,
            "human_corrected": human_corrected,
            "recovery_observed": recovery,
            "repository_available": repo_available,
            "reconstruction_confidence": reconstruction,
            "coding_scope": coding_scope,
            "sensitive_content": sensitive_content,
            "cloud_review": (
                "local_only"
                if sensitive_content
                else ("eligible" if coding_scope else "not_applicable")
            ),
            "nested_subagent": nested_subagent,
            "parent_session_id": source_metadata.get("parent_session_id"),
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
        candidate_path = store.candidates / f"{trajectory['session_id']}.json"
        if candidate_path.exists():
            existing = read_json(candidate_path)
            if existing.get("model_review"):
                candidate["model_review"] = existing["model_review"]
        write_json(candidate_path, candidate)
        for label in candidate["labels"]:
            counts[label] += 1
        if candidate["score"] >= minimum_score:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    total = counts["useful"] + counts["low_value_or_trivial"]
    index = {
        "schema_version": "0.1",
        "curator": "evidence_heuristic_v0.4",
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
        non_coding=counts["non_coding_or_personal"],
        sensitive=counts["sensitive_content"],
        training_ready_execution=counts["training_ready_execution"],
        needs_hardening=counts["needs_hardening"],
        candidates=candidates,
    )
