"""Non-destructive liveness probe for an experiment's run lock.

The watch path must never create or mutate lock files; it only opens the
existing lock file read-only and asks flock whether an exclusive lock would
succeed. The run/resume worker holds that lock for the whole experiment.
"""

from __future__ import annotations

import fcntl
from pathlib import Path


def lock_is_free(run_dir: Path) -> bool:
    """True when no process holds the experiment lock for run_dir.

    A missing lock file counts as free. Errors opening or locking degrade
    to False so the caller treats the runner as live rather than stopping
    the watch on a transient failure.
    """
    path = run_dir / ".experiment.lock"
    if not path.is_file():
        return True
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError, OSError):
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True
    finally:
        handle.close()
