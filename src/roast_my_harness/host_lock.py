"""Cross-platform experiment lock. Pi-only engine."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from roast_my_harness.errors import RunBusyError

try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None


class ExperimentLock:
    def __init__(self, run_dir: Path):
        self.path = run_dir / ".experiment.lock"
        self._handle = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            self._acquire(handle)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                                     "started_at": datetime.now(UTC).isoformat()}) + "\n")
            handle.flush()
            try:
                os.fchmod(handle.fileno(), 0o600)
            except (AttributeError, OSError):
                pass
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def _acquire(self, handle) -> None:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, PermissionError) as exc:
                raise RunBusyError(self.path) from exc
            return
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RunBusyError(self.path) from exc

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            handle.close()


def lock_is_free(run_dir: Path) -> bool:
    """True when no process holds the experiment lock for run_dir.

    Never creates or mutates lock files; opens the existing file read-only
    and asks whether an exclusive lock would succeed. A missing lock file
    counts as free. Errors degrade to False so callers treat the runner as
    live rather than stopping the watch on a transient failure.
    """
    path = run_dir / ".experiment.lock"
    if not path.is_file():
        return True
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return False
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, PermissionError, OSError):
                return False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return True
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return True
    finally:
        handle.close()
