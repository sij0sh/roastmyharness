"""Cross-process ownership for one experiment run at a time."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from roast_my_harness.errors import RunBusyError

try:
    import fcntl
except ImportError:
    fcntl = None


class ExperimentLock:
    """Advisory, process-held lock for run, resume, and report operations."""

    def __init__(self, run_dir: Path):
        self.path = run_dir / ".experiment.lock"
        self._handle = None
        self._windows_lock = False

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            self._acquire(handle)
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "started_at": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n"
            )
            handle.flush()
            try:
                os.fchmod(handle.fileno(), 0o600)
            except AttributeError:
                os.chmod(self.path, 0o600)
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

        handle.seek(0)
        handle.write(" ")
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RunBusyError(self.path) from exc
        self._windows_lock = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif self._windows_lock:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
        self._windows_lock = False
