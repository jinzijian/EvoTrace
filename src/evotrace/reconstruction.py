from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .gitops import create_base_archive_binary, extract_archive, git, initialize_workspace
from .util import run

EDIT_TOOL_NAMES = {"edit", "multiedit", "str_replace_editor"}
WRITE_TOOL_NAMES = {"write", "write_file"}
PATCH_TOOL_NAMES = {"apply_patch"}


def _successful_calls(events: Iterable[Dict[str, Any]]) -> Dict[str, bool]:
    outcomes: Dict[str, bool] = {}
    for event in events:
        if event.get("kind") != "tool.result":
            continue
        data = event.get("data") or {}
        call_id = data.get("call_id")
        if isinstance(call_id, str) and call_id:
            outcomes[call_id] = not bool(data.get("is_error"))
    return outcomes


def _target_path(workspace: Path, repository: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value).expanduser()
    try:
        relative = raw.resolve().relative_to(repository.resolve()) if raw.is_absolute() else raw
    except (OSError, ValueError):
        return None
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = (workspace / relative).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return None
    return target


def _apply_write(workspace: Path, repository: Path, arguments: Dict[str, Any]) -> bool:
    target = _target_path(
        workspace,
        repository,
        arguments.get("file_path") or arguments.get("path"),
    )
    content = arguments.get("content")
    if target is None or not isinstance(content, str):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def _replace_text(
    target: Path,
    *,
    old: str,
    new: str,
    replace_all: bool,
) -> bool:
    if not target.is_file():
        return False
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if not old or count == 0 or (not replace_all and count != 1):
        return False
    target.write_text(text.replace(old, new, -1 if replace_all else 1), encoding="utf-8")
    return True


def _apply_edit(workspace: Path, repository: Path, arguments: Dict[str, Any]) -> bool:
    target = _target_path(
        workspace,
        repository,
        arguments.get("file_path") or arguments.get("path"),
    )
    if target is None:
        return False
    edits = arguments.get("edits")
    if isinstance(edits, list):
        applied = False
        for edit in edits:
            if not isinstance(edit, dict):
                return False
            old = edit.get("old_string") or edit.get("old_str")
            new = edit.get("new_string") or edit.get("new_str")
            if not isinstance(old, str) or not isinstance(new, str):
                return False
            if not _replace_text(
                target,
                old=old,
                new=new,
                replace_all=bool(edit.get("replace_all")),
            ):
                return False
            applied = True
        return applied
    old = arguments.get("old_string") or arguments.get("old_str")
    new = arguments.get("new_string") or arguments.get("new_str")
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    return _replace_text(
        target,
        old=old,
        new=new,
        replace_all=bool(arguments.get("replace_all")),
    )


def _apply_patch_changes(
    workspace: Path,
    repository: Path,
    arguments: Dict[str, Any],
) -> bool:
    changes = arguments.get("changes")
    if not isinstance(changes, dict) or not changes:
        return False
    applied = False
    for path_value, change in changes.items():
        if not isinstance(change, dict):
            return False
        target = _target_path(workspace, repository, path_value)
        if target is None:
            return False
        kind = change.get("type")
        if kind == "add":
            content = change.get("content")
            if not isinstance(content, str):
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        elif kind == "delete":
            if not target.is_file():
                return False
            target.unlink()
        elif kind == "update":
            diff = change.get("unified_diff")
            if not isinstance(diff, str) or not diff.startswith("@@"):
                return False
            relative = target.relative_to(workspace.resolve()).as_posix()
            move_value = change.get("move_path")
            destination = relative
            if isinstance(move_value, str) and move_value:
                moved = _target_path(workspace, repository, move_value)
                if moved is None:
                    return False
                destination = moved.relative_to(workspace.resolve()).as_posix()
            patch = (
                f"diff --git a/{relative} b/{destination}\n"
                f"--- a/{relative}\n"
                f"+++ b/{destination}\n"
                f"{diff}"
            )
            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=workspace,
                input=patch,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                return False
        else:
            return False
        applied = True
    return applied


def recover_reference_patch(
    session_dir: Path,
    repository: Dict[str, Any],
    events: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Replay supported local edit tools on an archived base commit.

    The source checkout is read-only. Reconstruction happens in a temporary
    repository, and only the resulting Git patch is persisted in the local
    EvoTrace session directory.
    """

    patches = session_dir / "patches"
    patches.mkdir(parents=True, exist_ok=True)
    initial_patch = patches / "initial.patch"
    reference_patch = patches / "final.patch"
    initial_patch.write_bytes(b"")
    reference_patch.write_bytes(b"")

    root_value = repository.get("root")
    commit = repository.get("base_commit")
    if not isinstance(root_value, str) or not isinstance(commit, str):
        return {"status": "unavailable", "reason": "repository base is missing"}
    repo = Path(root_value).expanduser()
    if not repo.is_dir():
        return {"status": "unavailable", "reason": "repository checkout is unavailable"}
    if not git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}", check=False).strip():
        return {"status": "unavailable", "reason": "base commit is unavailable"}
    # `git archive` on an empty tree exits 0 but writes a gzip that tarfile
    # cannot open, so an empty base has to be detected before archiving.
    if not git(repo, "ls-tree", "-r", "--name-only", commit).strip():
        return {"status": "unavailable", "reason": "base commit has no tracked files"}

    normalized_events = list(events)
    outcomes = _successful_calls(normalized_events)
    attempted = 0
    applied = 0
    failed = 0
    with tempfile.TemporaryDirectory(prefix="evotrace-reconstruct-") as temporary:
        root = Path(temporary)
        archive = root / "base.tar.gz"
        workspace = root / "workspace"
        create_base_archive_binary(repo, commit, archive)
        extract_archive(archive, workspace)
        initialize_workspace(workspace)
        for event in normalized_events:
            if event.get("kind") != "tool.call":
                continue
            data = event.get("data") or {}
            name = str(data.get("name") or "").lower().replace("-", "_")
            if name not in WRITE_TOOL_NAMES | EDIT_TOOL_NAMES | PATCH_TOOL_NAMES:
                continue
            call_id = data.get("call_id")
            if isinstance(call_id, str) and outcomes.get(call_id) is False:
                continue
            arguments = data.get("arguments")
            if not isinstance(arguments, dict):
                continue
            attempted += 1
            try:
                if name in WRITE_TOOL_NAMES:
                    changed = _apply_write(workspace, repo, arguments)
                elif name in EDIT_TOOL_NAMES:
                    changed = _apply_edit(workspace, repo, arguments)
                else:
                    changed = _apply_patch_changes(workspace, repo, arguments)
            except (OSError, UnicodeError):
                changed = False
            if changed:
                applied += 1
            else:
                failed += 1

        if applied:
            # Intent-to-add makes new files visible to `git diff` without
            # staging their contents into the reconstructed commit.
            run(["git", "add", "-N", "--", "."], cwd=workspace, check=False)
            diff = run(
                ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
                cwd=workspace,
                check=False,
            ).stdout
            reference_patch.write_bytes(diff.encode("utf-8", errors="surrogateescape"))
        changed_paths = git(workspace, "diff", "--name-only", "HEAD", "--").splitlines()

    recovered = reference_patch.stat().st_size > 0
    return {
        "status": "recovered" if recovered else "unavailable",
        "method": "edit_event_replay",
        "attempted_edits": attempted,
        "applied_edits": applied,
        "failed_edits": failed,
        "changed_paths": sorted(path for path in changed_paths if path),
        "patch_bytes": reference_patch.stat().st_size,
    }
