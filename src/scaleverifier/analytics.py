from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from .store import Store
from .util import load_jsonl

CORRECTION = re.compile(
    r"(?i)\b(?:wrong|incorrect|not what i|you missed|still broken|doesn.t work|"
    r"不对|错了|不是这个|还没|漏了|没有修好)\b"
)
SUCCESS_CLAIM = re.compile(r"(?i)\b(?:done|completed|fixed|implemented|all tests pass|完成|修复)\b")
FAILED_TEST = re.compile(r"(?i)(?:\b[1-9]\d* failed\b|tests? failed|exit code [1-9]\d*)")


def classify_session(path: Path, trajectory: Dict[str, Any]) -> List[str]:
    categories: List[str] = []
    outcome = trajectory.get("outcome") or {}
    if outcome.get("exit_code") not in (None, 0):
        categories.append("agent process failed")
    events_path = path / "events.jsonl"
    if not events_path.exists():
        return categories
    assistant_claimed_success = False
    saw_failed_test = False
    for event in load_jsonl(events_path):
        data = event.get("data") or {}
        text = data.get("text") or data.get("output") or ""
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        if event.get("kind") == "message.assistant" and SUCCESS_CLAIM.search(text):
            assistant_claimed_success = True
        if FAILED_TEST.search(text):
            saw_failed_test = True
        if (
            event.get("kind") == "message.user"
            and assistant_claimed_success
            and CORRECTION.search(text)
        ):
            categories.append("human correction after success claim")
            assistant_claimed_success = False
    if saw_failed_test:
        categories.append("failing verification observed")
    return list(dict.fromkeys(categories))


def failure_summary(store: Store) -> tuple[Counter[str], List[Dict[str, Any]]]:
    counts: Counter[str] = Counter()
    details = []
    for path, trajectory in store.list_sessions():
        categories = classify_session(path, trajectory)
        if not categories:
            continue
        counts.update(categories)
        details.append(
            {
                "session_id": trajectory.get("session_id"),
                "agent": (trajectory.get("source") or {}).get("agent"),
                "categories": categories,
            }
        )
    return counts, details
