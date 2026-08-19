from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .compiler import compile_session
from .errors import EvoTraceError
from .store import Store


@dataclass
class BuildResult:
    session_id: str
    status: str
    bundle: Optional[str] = None
    error: Optional[str] = None


def build_mined_candidates(
    store: Store,
    *,
    session_id: Optional[str] = None,
    limit: int = 10,
    extra_commands: Optional[List[str]] = None,
) -> List[BuildResult]:
    if limit < 1:
        raise EvoTraceError("--limit must be at least 1")
    if session_id:
        session_ids = [session_id]
    else:
        session_ids = [
            item["session_id"]
            for item in store.list_candidates()
            if "execution_verifiable" in item.get("labels", [])
        ][:limit]
        if not session_ids:
            raise EvoTraceError(
                "No execution-verifiable candidates found. Run `evotrace mine` first."
            )
    results = []
    for candidate_id in session_ids:
        existing = store.benchmarks / candidate_id
        if (existing / "task.json").exists():
            results.append(BuildResult(candidate_id, "already_built", str(existing)))
            continue
        try:
            bundle, _ = compile_session(
                candidate_id,
                extra_commands=extra_commands,
                store=store,
            )
            results.append(BuildResult(candidate_id, "built", str(bundle)))
        except EvoTraceError as exc:
            results.append(BuildResult(candidate_id, "skipped", error=str(exc)))
    return results
