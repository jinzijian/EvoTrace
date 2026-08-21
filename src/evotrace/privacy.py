from __future__ import annotations

import re
from pathlib import Path
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

_CLOUD_PATTERNS = [
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?<![A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9])"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)(\bssh\s+(?:-[A-Za-z]\s+\S+\s+)*)(?:[^\s@]+@)?[^\s'\"]+"),
    re.compile(r"(?<![A-Za-z0-9:])/(?!/)[^\s\"'<>]+"),
    re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>]+"),
]


def _home_variants() -> list[str]:
    home = str(Path.home())
    variants = [home]
    # Imported histories may have been produced on a different Unix account.
    # Strip the username while keeping the repository-relative suffix useful.
    variants.extend(re.findall(r"/(?:Users|home)/[^/\s\"']+", home))
    return sorted(set(variants), key=len, reverse=True)


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


def redact_for_cloud_text(text: str) -> str:
    """Apply stricter redaction before an explicitly authorized API review."""
    redacted = redact_text(text)
    for home in _home_variants():
        redacted = redacted.replace(home, "[HOME]")
    # Catch home paths from other machines without collecting their usernames.
    redacted = re.sub(r"/(?:Users|home)/[^/\s\"']+", "[HOME]", redacted)
    for pattern in _CLOUD_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_for_cloud(value: Any) -> Any:
    if isinstance(value, str):
        return redact_for_cloud_text(value)
    if isinstance(value, list):
        return [redact_for_cloud(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_for_cloud(item) for key, item in value.items()}
    return value
