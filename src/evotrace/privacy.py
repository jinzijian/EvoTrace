from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)"
        r"([^\s,;\"']{8,})"
    ),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value
