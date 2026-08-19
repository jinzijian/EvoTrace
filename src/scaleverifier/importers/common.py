from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..gitops import capture_metadata, git
from ..privacy import redact
from ..store import Store, find_git_root
from ..util import load_jsonl, read_json, short_id, utc_now, write_jsonl


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
    cwd: Optional[str],
    git_data: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "root": cwd,
        "base_commit": None,
        "branch": None,
        "origin": None,
        "dirty": None,
        "status": [],
        "base_commit_source": None,
        "reconstruction_confidence": "none",
    }
    if git_data:
        metadata["base_commit"] = git_data.get("commit_hash") or git_data.get("commit")
        metadata["branch"] = git_data.get("branch")
        metadata["origin"] = git_data.get("repository_url") or git_data.get("origin")
        if metadata["base_commit"]:
            metadata["base_commit_source"] = "session"
            metadata["reconstruction_confidence"] = "high"
    if cwd:
        repo = find_git_root(Path(cwd).expanduser())
        if repo and repo.exists():
            try:
                current = capture_metadata(repo)
                for key, value in current.items():
                    if metadata.get(key) in (None, "", []):
                        metadata[key] = value
                metadata["root"] = str(repo)
                if metadata["base_commit_source"] is None and created_at:
                    inferred = git(
                        repo,
                        "rev-list",
                        "-1",
                        f"--before={created_at}",
                        "--all",
                        check=False,
                    ).strip()
                    if inferred:
                        metadata["base_commit"] = inferred
                        metadata["base_commit_source"] = "git_time_inference"
                        metadata["reconstruction_confidence"] = "medium"
                if metadata["base_commit_source"] is None and metadata["base_commit"]:
                    metadata["base_commit_source"] = "current_checkout"
                    metadata["reconstruction_confidence"] = "low"
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
    indexed_at = utc_now()
    stable = source_session_id or source_path.stem
    session_id = short_id(source)
    if stable:
        suffix = "".join(
            character for character in stable if character.isalnum() or character in "-_"
        )[:24]
        if suffix:
            session_id = f"{source}-{suffix}"
    session_dir = store.sessions / session_id
    existing = None
    if (session_dir / "trajectory.json").exists():
        existing = read_json(session_dir / "trajectory.json")
    normalized_events = [redact(event) for event in events]
    existing_count = ((existing or {}).get("import") or {}).get("normalized_events", 0)
    existing_path = ((existing or {}).get("source") or {}).get("path")
    incoming_rank = 0 if source == "codex" and source_path.name == "history.jsonl" else 1
    existing_rank = (
        0
        if source == "codex" and existing_path and Path(existing_path).name == "history.jsonl"
        else 1
    )
    keep_existing = bool(
        existing
        and existing_path != str(source_path)
        and (
            existing_rank > incoming_rank
            or (existing_rank == incoming_rank and existing_count > len(normalized_events))
        )
    )
    if existing and keep_existing:
        existing["import_status"] = "kept_richer_existing_session"
        return session_dir, existing
    session_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(session_dir / "events.jsonl", normalized_events)
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
        "repository": best_repository(cwd, git_data, created_at),
        "snapshots": {"initial": {}, "final": {}},
        "verification": {"commands": verification_commands},
        "outcome": {"status": "imported", "exit_code": None},
        "privacy": {
            "storage": "local",
            "redaction": "best-effort",
            "raw_session_copied": False,
        },
        "import": {
            "normalized_events": len(normalized_events),
            "first_indexed_at": ((existing or {}).get("import") or {}).get(
                "first_indexed_at", indexed_at
            ),
            "last_indexed_at": indexed_at,
            "status": "refreshed" if existing else "created",
        },
    }
    sanitized = redact(trajectory)
    store.save_session(session_dir, sanitized)
    return session_dir, sanitized


def raw_records(path: Path) -> Iterable[Dict[str, Any]]:
    return load_jsonl(path)
