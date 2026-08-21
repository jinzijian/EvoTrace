from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import List, Optional

from .compiler import compile_session
from .errors import EvoTraceError
from .quality import execution_build_gaps
from .store import Store
from .util import utc_now


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
    rebuild: bool = False,
) -> List[BuildResult]:
    if limit < 1:
        raise EvoTraceError("--limit must be at least 1")
    if session_id:
        session_ids = [session_id]
    else:
        session_ids = [
            item["session_id"]
            for item in store.list_candidates()
            if "training_ready_execution" in item.get("labels", [])
        ][:limit]
        if not session_ids:
            raise EvoTraceError(
                "No training-ready execution candidates passed the difficulty gate. "
                "Run `evotrace mine`, or explicitly build a seed and use `/harden`."
            )
    results = []
    for candidate_id in session_ids:
        candidate = next(
            (
                item
                for item in store.list_candidates()
                if item.get("session_id") == candidate_id
            ),
            None,
        )
        if candidate is None:
            results.append(
                BuildResult(candidate_id, "skipped", error="Mined candidate metadata is missing")
            )
            continue
        try:
            _, trajectory = store.load_session(candidate_id)
        except EvoTraceError as exc:
            results.append(BuildResult(candidate_id, "skipped", error=str(exc)))
            continue
        gaps = execution_build_gaps(candidate, trajectory)
        if gaps:
            results.append(
                BuildResult(
                    candidate_id,
                    "skipped",
                    error="Build gate rejected candidate: " + "; ".join(gaps),
                )
            )
            continue
        existing = store.benchmarks / candidate_id
        if (existing / "task.json").exists():
            if not rebuild:
                results.append(BuildResult(candidate_id, "already_built", str(existing)))
                continue
            stamp = utc_now().replace(":", "").replace("-", "")
            archive = store.root / "archives" / "benchmarks" / f"{candidate_id}-{stamp}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(existing), str(archive))
        else:
            archive = None
        try:
            bundle, _ = compile_session(
                candidate_id,
                extra_commands=extra_commands,
                store=store,
            )
            results.append(
                BuildResult(candidate_id, "rebuilt" if archive else "built", str(bundle))
            )
        except EvoTraceError as exc:
            if archive and archive.exists() and not existing.exists():
                shutil.move(str(archive), str(existing))
            results.append(BuildResult(candidate_id, "skipped", error=str(exc)))
    return results
