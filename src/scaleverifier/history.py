from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ScaleVerifierError
from .importers import import_claude, import_codex, import_codex_prompt_history
from .store import Store
from .util import read_json, utc_now, write_json


@dataclass(frozen=True)
class HistoryFile:
    source: str
    kind: str
    path: Path
    modified_ns: int


@dataclass
class ImportSummary:
    discovered_files: int = 0
    processed_files: int = 0
    skipped_unchanged_files: int = 0
    created_sessions: int = 0
    refreshed_sessions: int = 0
    kept_richer_sessions: int = 0
    session_ids: List[str] = field(default_factory=list)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()


def discover_history(source: str = "all", last: Optional[int] = None) -> List[HistoryFile]:
    if source not in {"all", "codex", "claude"}:
        raise ScaleVerifierError(f"Unsupported history source: {source}")
    if last is not None and last < 1:
        raise ScaleVerifierError("--last must be at least 1")
    discovered: List[HistoryFile] = []
    if source in {"all", "codex"}:
        codex_home = _codex_home()
        for path in (codex_home / "sessions").glob("**/*.jsonl"):
            if path.is_file():
                discovered.append(HistoryFile("codex", "session", path, path.stat().st_mtime_ns))
        prompt_history = codex_home / "history.jsonl"
        if last is None and prompt_history.is_file():
            discovered.append(
                HistoryFile(
                    "codex", "prompt_history", prompt_history, prompt_history.stat().st_mtime_ns
                )
            )
    if source in {"all", "claude"}:
        for path in (_claude_home() / "projects").glob("**/*.jsonl"):
            if path.is_file() and "subagents" not in path.parts:
                discovered.append(HistoryFile("claude", "session", path, path.stat().st_mtime_ns))
    discovered.sort(key=lambda item: item.modified_ns, reverse=True)
    return discovered[:last] if last is not None else discovered


def _fingerprint(item: HistoryFile) -> str:
    stat = item.path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _load_index(store: Store) -> Dict[str, Any]:
    path = store.root / "import-index.json"
    if not path.exists():
        return {"schema_version": "0.1", "files": {}}
    return read_json(path)


def import_discovered_history(
    store: Store,
    *,
    source: str = "all",
    last: Optional[int] = None,
    incremental: bool = True,
) -> ImportSummary:
    store.initialize()
    files = discover_history(source, last)
    if not files:
        raise ScaleVerifierError(f"No local {source} session history found")
    summary = ImportSummary(discovered_files=len(files))
    index = _load_index(store)
    file_index = index.setdefault("files", {})
    seen = set()
    for item in files:
        key = str(item.path.resolve())
        fingerprint = _fingerprint(item)
        previous = file_index.get(key) or {}
        if incremental and previous.get("fingerprint") == fingerprint:
            summary.skipped_unchanged_files += 1
            summary.session_ids.extend(previous.get("session_ids", []))
            continue
        if item.source == "claude":
            results = [import_claude(item.path, store)]
        elif item.kind == "prompt_history":
            results = import_codex_prompt_history(item.path, store)
        else:
            results = [import_codex(item.path, store)]
        imported_ids = []
        for _, trajectory in results:
            session_id = trajectory["session_id"]
            imported_ids.append(session_id)
            if session_id in seen:
                continue
            seen.add(session_id)
            status = (trajectory.get("import") or {}).get("status")
            if trajectory.get("import_status") == "kept_richer_existing_session":
                summary.kept_richer_sessions += 1
            elif status == "refreshed":
                summary.refreshed_sessions += 1
            else:
                summary.created_sessions += 1
            summary.session_ids.append(session_id)
        file_index[key] = {
            "source": item.source,
            "kind": item.kind,
            "fingerprint": fingerprint,
            "session_ids": imported_ids,
            "indexed_at": utc_now(),
        }
        summary.processed_files += 1
    index["updated_at"] = utc_now()
    write_json(store.root / "import-index.json", index)
    summary.session_ids = list(dict.fromkeys(summary.session_ids))
    return summary
