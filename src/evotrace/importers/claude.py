from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..store import Store
from ..util import utc_now
from .common import raw_records, save_import, text_from_content


def _timestamp(record: Dict[str, Any]) -> str:
    value = record.get("timestamp")
    return value if isinstance(value, str) else utc_now()


def _normalize_content(content: Any, role: str, timestamp: str) -> Iterable[Dict[str, Any]]:
    text = text_from_content(content)
    if text:
        yield {"timestamp": timestamp, "kind": f"message.{role}", "data": {"text": text}}
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        content_type = item.get("type")
        if content_type == "tool_use":
            yield {
                "timestamp": timestamp,
                "kind": "tool.call",
                "data": {
                    "name": item.get("name"),
                    "arguments": item.get("input"),
                    "call_id": item.get("id"),
                },
            }
        elif content_type == "tool_result":
            yield {
                "timestamp": timestamp,
                "kind": "tool.result",
                "data": {
                    "call_id": item.get("tool_use_id"),
                    "output": item.get("content"),
                    "is_error": item.get("is_error", False),
                },
            }


def _normalize(records: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for record in records:
        record_type = record.get("type")
        if record_type not in {"user", "assistant"} or record.get("isSidechain"):
            continue
        message = record.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        yield from _normalize_content(content, record_type, _timestamp(record))


def import_claude(path: Path, store: Store) -> tuple[Path, Dict[str, Any]]:
    records = list(raw_records(path))
    primary = [item for item in records if not item.get("isSidechain")]
    first = next((item for item in primary if item.get("sessionId")), {})
    cwd = next((item.get("cwd") for item in primary if item.get("cwd")), None)
    task = ""
    for item in primary:
        if item.get("type") != "user" or item.get("toolUseResult"):
            continue
        message = item.get("message") or {}
        candidate = text_from_content(message.get("content") if isinstance(message, dict) else None)
        if candidate:
            task = candidate
            break
    return save_import(
        source="claude",
        source_path=path,
        source_session_id=first.get("sessionId"),
        cwd=cwd,
        git_data={"branch": first.get("gitBranch")},
        task=task,
        events=_normalize(records),
        verification_commands=[],
        store=store,
        created_at=first.get("timestamp"),
    )
