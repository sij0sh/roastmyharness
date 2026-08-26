"""Task content hashing: names, bytes, and symlink targets.

Ignores VCS metadata and caches so rebuilding a checkout does not change
the hash of identical task content.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

IGNORED_DIRS = {".git", ".pi-files", "__pycache__", ".pytest_cache", ".ruff_cache"}
IGNORED_FILES = {".DS_Store"}


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            yield path
            continue
        if path.is_file():
            yield path


def task_hash(task_dir: Path) -> str:
    task_dir = task_dir.resolve()
    sha = hashlib.sha256()
    for path in _iter_files(task_dir):
        rel = path.relative_to(task_dir).as_posix()
        parts = set(rel.split("/")[:-1])
        if parts & IGNORED_DIRS:
            continue
        if path.name in IGNORED_FILES:
            continue
        sha.update(rel.encode("utf-8"))
        sha.update(b"\0")
        if path.is_symlink():
            sha.update(b"L")
            sha.update(path.readlink().as_posix().encode("utf-8"))
        else:
            sha.update(b"F")
            sha.update(hashlib.sha256(path.read_bytes()).digest())
        sha.update(b"\0")
    return sha.hexdigest()
