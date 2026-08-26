"""Copy and hash extension/skill source trees with one sanitizer."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from roast_my_harness.homes.sanitize import iter_source_files


def source_tree_hash(src: Path, extra_excludes: list[str] | None = None) -> str:
    """Hash exactly the files the builder would copy (same iteration)."""
    sha = hashlib.sha256()
    for rel, path in iter_source_files(src, extra_excludes):
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


def copy_source_tree(
    src: Path, dst: Path, extra_excludes: list[str] | None = None
) -> list[str]:
    """Copy sanitized tree. Returns copied relative paths."""
    copied: list[str] = []
    for rel, path in iter_source_files(src, extra_excludes):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(path.readlink())
        else:
            shutil.copy2(path, target)
        copied.append(rel)
    return copied


def copy_runtime_packages(
    src: Path, dst: Path, packages: list[str]
) -> list[str]:
    """Copy declared runtime packages from the source node_modules."""
    copied: list[str] = []
    for name in packages:
        pkg_src = src / "node_modules" / name
        if not pkg_src.is_dir():
            continue
        pkg_dst = dst / "node_modules" / name
        if pkg_dst.exists():
            shutil.rmtree(pkg_dst)
        shutil.copytree(pkg_src, pkg_dst, ignore=shutil.ignore_patterns(".bin"))
        copied.append(name)
    return copied
