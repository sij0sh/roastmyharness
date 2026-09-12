"""Copy-based Pi extension install. Works on Linux, macOS, and Windows."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ActionResult:
    name: str
    detail: str
    changed: bool = False
    problem: bool = False


def _copy_file(source: Path, dest: Path) -> ActionResult:
    label = f"install {dest}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.is_file():
            if not dest.is_symlink() and dest.read_bytes() == source.read_bytes():
                return ActionResult(label, "already installed")
            dest.unlink()
        elif dest.exists():
            return ActionResult(label, f"{dest} is a directory; remove it manually", problem=True)
        shutil.copy2(source, dest)
    except OSError as e:
        return ActionResult(label, str(e), problem=True)
    return ActionResult(label, f"{source} -> {dest}", changed=True)


def _copy_tree(source: Path, dest: Path) -> ActionResult:
    label = f"install {dest}"
    try:
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)
    except OSError as e:
        return ActionResult(label, str(e), problem=True)
    return ActionResult(label, f"{source} -> {dest}", changed=True)


def install_pi_extension(*, source_file: Path, source_dir: Path,
                         dest_dir: Path) -> list[ActionResult]:
    results: list[ActionResult] = []
    results.append(_copy_file(source_file, dest_dir / source_file.name))
    results.append(_copy_tree(source_dir, dest_dir / source_dir.name))
    return results
