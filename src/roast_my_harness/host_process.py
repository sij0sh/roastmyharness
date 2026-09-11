"""Cross-platform process control. POSIX groups stay inside this module."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from roast_my_harness.errors import PierError


@dataclass
class VariantProcess:
    variant_id: str
    argv: list[str]
    log_path: Path
    proc: asyncio.subprocess.Process | None = field(default=None, init=False)

    async def start(self, env: dict[str, str] | None = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self.log_path.open("ab")
        try:
            try:
                os.chmod(self.log_path, 0o600)
            except OSError:
                pass
            kwargs: dict = dict(stdout=log_file, stderr=asyncio.subprocess.STDOUT,
                                stdin=asyncio.subprocess.DEVNULL, env=env)
            if os.name == "posix":
                kwargs["start_new_session"] = True
            else:
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.proc = await asyncio.create_subprocess_exec(*self.argv, **kwargs)
        finally:
            log_file.close()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


async def _terminate(proc: VariantProcess) -> None:
    assert proc.proc is not None
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.proc.pid), signal.SIGTERM)
        else:
            proc.proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass


async def _kill(proc: VariantProcess) -> None:
    assert proc.proc is not None
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.proc.pid), signal.SIGKILL)
        else:
            proc.proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


async def cancel_all(processes: list[VariantProcess], grace_sec: float = 10.0) -> None:
    targets = [p for p in processes if p.running and p.proc is not None]
    for proc in targets:
        await _terminate(proc)
    if not targets:
        return
    _, pending = await asyncio.wait([asyncio.create_task(_wait(p)) for p in targets], timeout=grace_sec)
    for task in pending:
        task.cancel()
    for proc in targets:
        if proc.running and proc.proc is not None:
            await _kill(proc)
            await _wait(proc)


async def _wait(process: VariantProcess) -> None:
    if process.proc is not None:
        await process.proc.wait()


def require_all_started(processes: list[VariantProcess]) -> None:
    failed = [p.variant_id for p in processes if p.proc is None]
    if failed:
        raise PierError(f"failed to launch pier jobs for: {', '.join(failed)}")
