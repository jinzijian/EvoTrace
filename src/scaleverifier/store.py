from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ScaleVerifierError
from .util import read_json, write_json


def find_git_root(start: Optional[Path] = None) -> Optional[Path]:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


class Store:
    def __init__(self, root: Optional[Path] = None) -> None:
        if root is None:
            configured = os.environ.get("SCALEVERIFIER_HOME")
            root = Path(configured).expanduser() if configured else Path.home() / ".scaleverifier"
        self.root = root.resolve()
        self.sessions = self.root / "sessions"
        self.benchmarks = self.root / "benchmarks"
        self.runs = self.root / "runs"
        self.candidates = self.root / "candidates"

    def initialize(self) -> None:
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.benchmarks.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)
        self.candidates.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        if session_id == "latest":
            sessions = self.list_sessions()
            if not sessions:
                raise ScaleVerifierError("No ScaleVerifier sessions found")
            return sessions[0][0]
        path = self.sessions / session_id
        if not path.is_dir():
            matches = [item for item in self.sessions.glob(f"{session_id}*") if item.is_dir()]
            if len(matches) == 1:
                return matches[0]
            raise ScaleVerifierError(f"Session not found: {session_id}")
        return path

    def load_session(self, session_id: str) -> tuple[Path, Dict[str, Any]]:
        path = self.session_dir(session_id)
        return path, read_json(path / "trajectory.json")

    def list_sessions(self) -> List[tuple[Path, Dict[str, Any]]]:
        if not self.sessions.exists():
            return []
        result = []
        for path in self.sessions.iterdir():
            metadata = path / "trajectory.json"
            if not path.is_dir() or not metadata.exists():
                continue
            try:
                result.append((path, read_json(metadata)))
            except ScaleVerifierError:
                continue
        return sorted(
            result,
            key=lambda item: item[1].get("created_at", ""),
            reverse=True,
        )

    def save_session(self, session_dir: Path, trajectory: Dict[str, Any]) -> None:
        write_json(session_dir / "trajectory.json", trajectory)

    def list_candidates(self) -> List[Dict[str, Any]]:
        if not self.candidates.exists():
            return []
        result = []
        for path in self.candidates.glob("*.json"):
            try:
                result.append(read_json(path))
            except ScaleVerifierError:
                continue
        return sorted(result, key=lambda item: item.get("score", 0), reverse=True)
