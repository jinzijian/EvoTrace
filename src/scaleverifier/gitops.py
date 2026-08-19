from __future__ import annotations

import shutil
import sys
import tarfile
from os.path import normpath
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .errors import ScaleVerifierError
from .store import find_git_root
from .util import run

DEFAULT_IGNORED_PARTS = {
    ".scaleverifier",
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
}
SENSITIVE_UNTRACKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service-account.json",
}
SENSITIVE_UNTRACKED_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
MAX_UNTRACKED_FILE_BYTES = 10 * 1024 * 1024
MAX_UNTRACKED_TOTAL_BYTES = 50 * 1024 * 1024


def require_repo(path: Optional[Path] = None) -> Path:
    repo = find_git_root(path)
    if repo is None:
        raise ScaleVerifierError("This command must run inside a Git repository")
    return repo


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = run(["git", *args], cwd=repo, check=check)
    return result.stdout


def head_commit(repo: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], cwd=repo, check=False)
    if result.returncode != 0:
        raise ScaleVerifierError(
            "The repository has no commits. Create an initial commit before recording."
        )
    return result.stdout.strip()


def capture_metadata(repo: Path) -> Dict[str, Any]:
    branch = git(repo, "branch", "--show-current", check=False).strip()
    remote = git(repo, "remote", "get-url", "origin", check=False).strip()
    status_lines = git(repo, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    status_lines = [
        line
        for line in status_lines
        if not any(part in DEFAULT_IGNORED_PARTS for part in Path(line[3:]).parts)
    ]
    return {
        "root": str(repo),
        "base_commit": head_commit(repo),
        "branch": branch or None,
        "origin": remote or None,
        "dirty": bool(status_lines),
        "status": status_lines,
    }


def write_patch(repo: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
        cwd=repo,
        check=False,
    )
    destination.write_bytes(result.stdout.encode("utf-8", errors="surrogateescape"))


def changed_paths(repo: Path) -> List[str]:
    tracked = git(repo, "diff", "--name-only", "HEAD", "--").splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(
        {
            item
            for item in [*tracked, *untracked]
            if item and not any(part in DEFAULT_IGNORED_PARTS for part in Path(item).parts)
        }
    )


def _untracked_paths(repo: Path) -> Iterable[Path]:
    output = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    for raw in output.split("\0"):
        if not raw:
            continue
        path = Path(raw)
        if any(part in DEFAULT_IGNORED_PARTS for part in path.parts):
            continue
        if (
            path.name in SENSITIVE_UNTRACKED_NAMES
            or path.suffix.lower() in SENSITIVE_UNTRACKED_SUFFIXES
        ):
            continue
        absolute = repo / path
        if absolute.is_file() and not absolute.is_symlink():
            yield path


def snapshot_untracked(repo: Path, destination: Path) -> Dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    included: List[str] = []
    skipped: List[Dict[str, Any]] = []
    total = 0
    with tarfile.open(destination, "w:gz") as archive:
        for relative in _untracked_paths(repo):
            absolute = repo / relative
            size = absolute.stat().st_size
            if size > MAX_UNTRACKED_FILE_BYTES or total + size > MAX_UNTRACKED_TOTAL_BYTES:
                skipped.append({"path": str(relative), "bytes": size})
                continue
            archive.add(absolute, arcname=str(relative), recursive=False)
            included.append(str(relative))
            total += size
    return {"included": included, "skipped": skipped, "total_bytes": total}


def create_base_archive_binary(repo: Path, commit: str, destination: Path) -> None:
    """Create an archive without routing binary bytes through text mode."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    import subprocess

    result = subprocess.run(
        ["git", "archive", "--format=tar.gz", "-o", str(destination), commit],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ScaleVerifierError(f"Could not archive base commit {commit}: {result.stderr.strip()}")


def _safe_members(archive: tarfile.TarFile, destination: Path):
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ScaleVerifierError(f"Unsafe path in archive: {member.name}") from exc
        if member.issym():
            link_target = normpath(str(Path(member.name).parent / member.linkname))
            if (
                Path(member.linkname).is_absolute()
                or link_target == ".."
                or link_target.startswith("../")
            ):
                raise ScaleVerifierError(f"Unsafe link in archive: {member.name}")
        elif member.islnk():
            link_target = normpath(member.linkname)
            if (
                Path(member.linkname).is_absolute()
                or link_target == ".."
                or link_target.startswith("../")
            ):
                raise ScaleVerifierError(f"Unsafe link in archive: {member.name}")
        elif not (member.isfile() or member.isdir()):
            raise ScaleVerifierError(
                f"Special files are not allowed in task archives: {member.name}"
            )
        yield member


def extract_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        members = list(_safe_members(archive, destination))
        if sys.version_info >= (3, 12):
            archive.extractall(destination, members=members, filter="data")
        else:
            archive.extractall(destination, members=members)


def initialize_workspace(destination: Path) -> None:
    run(["git", "init", "-q"], cwd=destination)
    run(["git", "config", "user.email", "scaleverifier@local"], cwd=destination)
    run(["git", "config", "user.name", "ScaleVerifier"], cwd=destination)
    run(["git", "add", "-A"], cwd=destination)
    run(
        ["git", "commit", "-q", "--allow-empty", "-m", "ScaleVerifier base state"],
        cwd=destination,
    )


def apply_patch(repo: Path, patch: Path) -> None:
    if not patch.exists() or patch.stat().st_size == 0:
        return
    result = run(
        ["git", "apply", "--whitespace=nowarn", str(patch)],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        raise ScaleVerifierError(
            f"Could not restore initial patch {patch}: {result.stderr.strip()}"
        )


def copy_snapshot(source: Path, destination: Path) -> None:
    if source.exists() and source.stat().st_size:
        extract_archive(source, destination)


def setup_from_bundle(bundle: Path, destination: Path) -> Path:
    base = bundle / "environment" / "base.tar.gz"
    if not base.exists():
        raise ScaleVerifierError(f"Bundle is missing {base.relative_to(bundle)}")
    if destination.exists() and any(destination.iterdir()):
        raise ScaleVerifierError(f"Destination is not empty: {destination}")
    extract_archive(base, destination)
    initialize_workspace(destination)
    apply_patch(destination, bundle / "patches" / "initial.patch")
    copy_snapshot(bundle / "environment" / "untracked-initial.tar.gz", destination)
    return destination


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copy2(source, destination)
    else:
        destination.write_bytes(b"")
