"""Cross-process ownership for one experiment run at a time."""

from __future__ import annotations

import fcntl
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from roast_my_harness.errors import RunBusyError


class ExperimentLock:
    """Advisory, process-held lock for run, resume, and report operations."""

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
            os.fchmod(handle.fileno(), 0o600)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def _acquire(self, handle) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as exc:
            raise RunBusyError(self.path) from exc

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
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
