from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..store import Store
from ..util import utc_now
from .common import raw_records, save_import


def _timestamp(record: Dict[str, Any]) -> str:
    value = record.get("timestamp")
    return value if isinstance(value, str) else utc_now()


def _parse_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, str):
        return arguments
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def _normalize(records: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for record in records:
        outer_type = record.get("type")
        payload = record.get("payload") or {}
        payload_type = payload.get("type")
        timestamp = _timestamp(record)
        if outer_type == "event_msg" and payload_type == "user_message":
            text = payload.get("message")
            if isinstance(text, str) and text.strip():
                yield {"timestamp": timestamp, "kind": "message.user", "data": {"text": text}}
        elif outer_type == "event_msg" and payload_type == "agent_message":
            text = payload.get("message")
            if isinstance(text, str) and text.strip():
                yield {
                    "timestamp": timestamp,
                    "kind": "message.assistant",
                    "data": {"text": text, "phase": payload.get("phase")},
                }
        elif outer_type == "response_item" and payload_type == "function_call":
            yield {
                "timestamp": timestamp,
                "kind": "tool.call",
                "data": {
                    "name": payload.get("name"),
                    "arguments": _parse_arguments(payload.get("arguments")),
                    "call_id": payload.get("call_id"),
                },
            }
        elif outer_type == "response_item" and payload_type == "function_call_output":
            yield {
                "timestamp": timestamp,
                "kind": "tool.result",
                "data": {
                    "call_id": payload.get("call_id"),
                    "output": payload.get("output"),
                },
            }


def import_codex(path: Path, store: Store) -> tuple[Path, Dict[str, Any]]:
    records = list(raw_records(path))
    session_meta = next((item for item in records if item.get("type") == "session_meta"), {})
    metadata = session_meta.get("payload") or {}
    user_messages = []
    for item in records:
        payload = item.get("payload") or {}
        if item.get("type") == "event_msg" and payload.get("type") == "user_message":
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                user_messages.append(message.strip())
    task = user_messages[0] if user_messages else ""
    return save_import(
        source="codex",
        source_path=path,
        source_session_id=metadata.get("id"),
        cwd=metadata.get("cwd"),
        git_data=metadata.get("git") if isinstance(metadata.get("git"), dict) else None,
        task=task,
        events=_normalize(records),
        verification_commands=[],
        store=store,
        created_at=session_meta.get("timestamp") or metadata.get("timestamp"),
    )


def _history_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    return utc_now()


def import_codex_prompt_history(path: Path, store: Store) -> List[tuple[Path, Dict[str, Any]]]:
    """Import prompt-only history entries that have no richer rollout file."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in raw_records(path):
        session_id = record.get("session_id")
        if isinstance(session_id, str) and session_id:
            grouped[session_id].append(record)
    imported = []
    for session_id, records in grouped.items():
        records.sort(key=lambda item: item.get("ts", 0))
        messages = [
            item.get("text", "").strip()
            for item in records
            if isinstance(item.get("text"), str) and item.get("text", "").strip()
        ]
        created_at = _history_timestamp(records[0].get("ts"))
        events = [
            {
                "timestamp": _history_timestamp(item.get("ts")),
                "kind": "message.user",
                "data": {"text": item.get("text", "")},
            }
            for item in records
            if isinstance(item.get("text"), str)
        ]
        imported.append(
            save_import(
                source="codex",
                source_path=path,
                source_session_id=session_id,
                cwd=None,
                git_data=None,
                task=messages[0] if messages else "",
                events=events,
                verification_commands=[],
                store=store,
                created_at=created_at,
            )
        )
    return imported
