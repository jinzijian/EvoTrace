from __future__ import annotations

import os
import pty
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from .errors import EvoTraceError
from .gitops import (
    capture_metadata,
    changed_paths,
    require_repo,
    snapshot_untracked,
    write_patch,
)
from .privacy import redact, redact_text
from .store import Store
from .util import append_jsonl, shell_join, short_id, utc_now


def _event(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"timestamp": utc_now(), "kind": kind, "data": redact(data)}


def _stream_pipe(
    pipe: TextIO,
    stream_name: str,
    output: TextIO,
    sink: "queue.Queue[tuple[str, str]]",
) -> None:
    for line in iter(pipe.readline, ""):
        output.write(line)
        output.flush()
        sink.put((stream_name, line))
    pipe.close()


def _record_piped_process(command: Sequence[str], repo: Path, events_path: Path) -> int:
    process = subprocess.Popen(
        list(command),
        cwd=repo,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        errors="replace",
    )
    assert process.stdout is not None
    assert process.stderr is not None
    sink: "queue.Queue[tuple[str, str]]" = queue.Queue()
    threads = [
        threading.Thread(
            target=_stream_pipe,
            args=(process.stdout, "stdout", sys.stdout, sink),
            daemon=True,
        ),
        threading.Thread(
            target=_stream_pipe,
            args=(process.stderr, "stderr", sys.stderr, sink),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    while process.poll() is None or any(thread.is_alive() for thread in threads):
        try:
            stream, text = sink.get(timeout=0.05)
        except queue.Empty:
            continue
        append_jsonl(events_path, _event("process.output", {"stream": stream, "text": text}))
    while not sink.empty():
        stream, text = sink.get_nowait()
        append_jsonl(events_path, _event("process.output", {"stream": stream, "text": text}))
    return process.wait()


def _record_pty_process(command: Sequence[str], repo: Path, events_path: Path) -> int:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        list(command), cwd=repo, stdin=None, stdout=slave, stderr=slave, close_fds=True
    )
    os.close(slave)
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            os.write(sys.stdout.fileno(), chunk)
            append_jsonl(
                events_path,
                _event(
                    "process.output",
                    {"stream": "pty", "text": chunk.decode("utf-8", errors="replace")},
                ),
            )
    except KeyboardInterrupt:
        process.send_signal(2)
    finally:
        os.close(master)
    return process.wait()


def record_command(
    command: Sequence[str],
    *,
    task: Optional[str] = None,
    agent: Optional[str] = None,
    verification_commands: Optional[List[str]] = None,
    store: Optional[Store] = None,
    use_pty: bool = True,
) -> tuple[Path, Dict[str, Any]]:
    if not command:
        raise EvoTraceError("No command provided after --")
    repo = require_repo()
    target_store = store or Store()
    before = capture_metadata(repo)
    target_store.initialize()
    session_id = short_id("recording")
    session_dir = target_store.sessions / session_id
    session_dir.mkdir(parents=True)
    events_path = session_dir / "events.jsonl"

    write_patch(repo, session_dir / "patches" / "initial.patch")
    initial_untracked = snapshot_untracked(
        repo, session_dir / "environment" / "untracked-initial.tar.gz"
    )
    started = time.monotonic()
    append_jsonl(
        events_path,
        _event(
            "process.started",
            {"command": shell_join(command), "cwd": str(repo), "agent": agent},
        ),
    )
    try:
        if use_pty and sys.stdout.isatty() and os.name == "posix":
            exit_code = _record_pty_process(command, repo, events_path)
        else:
            exit_code = _record_piped_process(command, repo, events_path)
    except FileNotFoundError as exc:
        raise EvoTraceError(f"Command not found: {command[0]}") from exc

    duration = round(time.monotonic() - started, 3)
    append_jsonl(
        events_path,
        _event("process.completed", {"exit_code": exit_code, "duration_seconds": duration}),
    )
    write_patch(repo, session_dir / "patches" / "final.patch")
    final_untracked = snapshot_untracked(
        repo, session_dir / "environment" / "untracked-final.tar.gz"
    )
    after = capture_metadata(repo)
    trajectory = {
        "schema_version": "0.1",
        "session_id": session_id,
        "created_at": utc_now(),
        "source": {
            "kind": "command",
            "agent": agent or Path(command[0]).name,
            "command": redact_text(shell_join(command)),
        },
        "task": {
            "text": task or "",
            "source": "explicit" if task else "missing",
        },
        "repository": before,
        "snapshots": {
            "initial": {
                "patch": "patches/initial.patch",
                "untracked": initial_untracked,
            },
            "final": {
                "patch": "patches/final.patch",
                "untracked": final_untracked,
                "changed_paths": changed_paths(repo),
            },
        },
        "verification": {"commands": verification_commands or []},
        "outcome": {
            "status": "completed" if exit_code == 0 else "process_failed",
            "exit_code": exit_code,
            "duration_seconds": duration,
        },
        "privacy": {
            "storage": "local",
            "redaction": "best-effort",
            "raw_session_copied": False,
        },
        "repository_after": after,
    }
    sanitized = redact(trajectory)
    target_store.save_session(session_dir, sanitized)
    return session_dir, sanitized
