"""Discover task directories under a Pier task root.

A task root is either a single task directory (contains task.toml) or a
dataset directory whose immediate children are task directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from roast_my_harness.errors import SpecError


@dataclass(frozen=True)
class TaskInfo:
    task_id: str
    path: Path


def is_task_dir(path: Path) -> bool:
    return path.is_dir() and (path / "task.toml").is_file()


def uses_compose(task_path: Path) -> bool:
    """True when pier runs this task via docker-compose.

    Pier selects compose when environment/docker-compose.yaml exists;
    build-time agent install is unsupported for those tasks, so the
    runner must use runtime install instead.
    """
    env = task_path / "environment"
    return (env / "docker-compose.yaml").is_file() or (
        env / "docker-compose.yml"
    ).is_file()


def trial_dir_matches(task_id: str, dirname: str) -> bool:
    """True when a pier trial dir belongs to a task.

    Pier builds dir names as task[:32] plus a random suffix, so ids
    over 32 chars never match a plain prefix test.
    """
    if dirname == task_id or dirname.startswith(task_id + "__"):
        return True
    return dirname.split("__", 1)[0] == task_id[:32].rstrip("_-")


def discover_tasks(root: Path, include: list[str], exclude: list[str]) -> list[TaskInfo]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SpecError(f"task root is not a directory: {root}")
    if is_task_dir(root):
        candidates = [root]
    else:
        candidates = sorted(p for p in root.iterdir() if is_task_dir(p))
    if not candidates:
        raise SpecError(f"no task directories (task.toml) under {root}")

    tasks: list[TaskInfo] = []
    seen: set[str] = set()
    for path in candidates:
        task_id = path.name
        if task_id in seen:
            raise SpecError(f"duplicate task id {task_id!r} under {root}")
        seen.add(task_id)
        if not any(fnmatch(task_id, pat) for pat in include):
            continue
        if any(fnmatch(task_id, pat) for pat in exclude):
            continue
        tasks.append(TaskInfo(task_id=task_id, path=path))
    if not tasks:
        raise SpecError(f"task filters excluded every task under {root}")
    return tasks
