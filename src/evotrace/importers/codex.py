from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..quality import task_is_meaningful
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


EXEC_COMMAND_ARGUMENT = re.compile(r"\bcmd\s*:\s*(\"(?:\\.|[^\"\\])*\")", re.DOTALL)


def _commands_from_exec_input(value: Any) -> List[str]:
    if not isinstance(value, str):
        return []
    commands = []
    for match in EXEC_COMMAND_ARGUMENT.finditer(value):
        try:
            command = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(command, str):
            commands.append(command)
    return commands


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
        elif (
            outer_type == "event_msg"
            and payload_type == "patch_apply_end"
            and payload.get("success") is True
            and isinstance(payload.get("changes"), dict)
        ):
            yield {
                "timestamp": timestamp,
                "kind": "tool.call",
                "data": {
                    "name": "apply_patch",
                    "arguments": {"changes": payload["changes"]},
                    "call_id": payload.get("call_id"),
                },
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
        elif outer_type == "response_item" and payload_type == "custom_tool_call":
            for index, command in enumerate(_commands_from_exec_input(payload.get("input"))):
                yield {
                    "timestamp": timestamp,
                    "kind": "tool.call",
                    "data": {
                        "name": "exec_command",
                        "arguments": {"cmd": command},
                        "call_id": f"{payload.get('call_id') or 'custom'}:{index}",
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
    task = next((message for message in user_messages if task_is_meaningful(message)), "")
    raw_source = metadata.get("source")
    source_details = raw_source if isinstance(raw_source, dict) else {}
    subagent = ((source_details.get("subagent") or {}).get("thread_spawn") or {})
    parent_session_id = (
        metadata.get("parent_thread_id")
        or metadata.get("forked_from_id")
        or subagent.get("parent_thread_id")
    )
    source_metadata = {
        key: value
        for key, value in {
            "thread_source": metadata.get("thread_source"),
            "parent_session_id": parent_session_id,
            "agent_path": metadata.get("agent_path") or subagent.get("agent_path"),
        }.items()
        if value
    }
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
        source_metadata=source_metadata,
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
