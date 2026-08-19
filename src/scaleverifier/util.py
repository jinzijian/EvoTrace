from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .errors import ScaleVerifierError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compact_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str, fallback: str = "session") -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return value[:64] or fallback


def short_id(prefix: str = "session") -> str:
    raw = f"{utc_now()}:{os.getpid()}:{os.urandom(8).hex()}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:10]
    return f"{prefix}-{compact_timestamp()}-{digest}"


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScaleVerifierError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScaleVerifierError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ScaleVerifierError(
                        f"Invalid JSONL at {path}:{line_number}: {exc}"
                    ) from exc
                if isinstance(value, dict):
                    yield value
    except FileNotFoundError as exc:
        raise ScaleVerifierError(f"File not found: {path}") from exc


def run(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
    timeout: Optional[int] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ScaleVerifierError(f"Command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScaleVerifierError(
            f"Command timed out after {timeout}s: {shlex.join(command)}"
        ) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ScaleVerifierError(
            f"Command failed ({result.returncode}): {shlex.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def shell_join(command: Sequence[str]) -> str:
    return shlex.join(list(command))


def console(message: str = "", *, error: bool = False) -> None:
    print(message, file=sys.stderr if error else sys.stdout)


def parse_name_value(value: str, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise ScaleVerifierError(f"{option} expects NAME=VALUE, got: {value}")
    name, item = value.split("=", 1)
    if not name.strip() or not item.strip():
        raise ScaleVerifierError(f"{option} expects non-empty NAME=VALUE")
    return slugify(name), item.strip()


def unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
