from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..gitops import capture_metadata
from ..privacy import redact
from ..store import Store, find_git_root
from ..util import append_jsonl, load_jsonl, read_json, short_id, utc_now


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def best_repository(
    cwd: Optional[str], git_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "root": cwd,
        "base_commit": None,
        "branch": None,
        "origin": None,
        "dirty": None,
        "status": [],
    }
    if git_data:
        metadata["base_commit"] = git_data.get("commit_hash") or git_data.get("commit")
        metadata["branch"] = git_data.get("branch")
        metadata["origin"] = git_data.get("repository_url") or git_data.get("origin")
    if cwd:
        repo = find_git_root(Path(cwd).expanduser())
        if repo and repo.exists():
            try:
                current = capture_metadata(repo)
                for key, value in current.items():
                    if metadata.get(key) in (None, "", []):
                        metadata[key] = value
                metadata["root"] = str(repo)
            except Exception:
                pass
    return metadata


def save_import(
    *,
    source: str,
    source_path: Path,
    source_session_id: Optional[str],
    cwd: Optional[str],
    git_data: Optional[Dict[str, Any]],
    task: str,
    events: Iterable[Dict[str, Any]],
    verification_commands: List[str],
    store: Store,
    created_at: Optional[str] = None,
) -> tuple[Path, Dict[str, Any]]:
    store.initialize()
    stable = source_session_id or source_path.stem
    session_id = short_id(source)
    if stable:
        suffix = "".join(
            character for character in stable if character.isalnum() or character in "-_"
        )[:24]
        if suffix:
            session_id = f"{source}-{suffix}"
            if (store.sessions / session_id).exists():
                existing = store.sessions / session_id
                return existing, read_json(existing / "trajectory.json")
    session_dir = store.sessions / session_id
    session_dir.mkdir(parents=True)
    event_count = 0
    for event in events:
        append_jsonl(session_dir / "events.jsonl", redact(event))
        event_count += 1
    trajectory = {
        "schema_version": "0.1",
        "session_id": session_id,
        "created_at": created_at or utc_now(),
        "source": {
            "kind": "history_import",
            "agent": source,
            "path": str(source_path),
            "source_session_id": source_session_id,
        },
        "task": {"text": task, "source": "history" if task else "missing"},
        "repository": best_repository(cwd, git_data),
        "snapshots": {"initial": {}, "final": {}},
        "verification": {"commands": verification_commands},
        "outcome": {"status": "imported", "exit_code": None},
        "privacy": {
            "storage": "local",
            "redaction": "best-effort",
            "raw_session_copied": False,
        },
        "import": {"normalized_events": event_count},
    }
    sanitized = redact(trajectory)
    store.save_session(session_dir, sanitized)
    return session_dir, sanitized


def raw_records(path: Path) -> Iterable[Dict[str, Any]]:
    return load_jsonl(path)
