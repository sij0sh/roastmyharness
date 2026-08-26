"""Async process control: launch with log capture, cancel via process group."""

from __future__ import annotations

import asyncio
import os
import signal
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
            os.chmod(self.log_path, 0o600)
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        finally:
            log_file.close()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


async def cancel_all(
    processes: list[VariantProcess], grace_sec: float = 10.0
) -> None:
    """SIGTERM every process group, wait the grace period, then SIGKILL."""
    targets = [p for p in processes if p.running and p.proc is not None]
    for proc in targets:
        try:
            os.killpg(os.getpgid(proc.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    if not targets:
        return
    _, pending = await asyncio.wait(
        [asyncio.create_task(_wait(p)) for p in targets], timeout=grace_sec
    )
    for task in pending:
        task.cancel()
    for proc in targets:
        if proc.running and proc.proc is not None:
            try:
                os.killpg(os.getpgid(proc.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await _wait(proc)


async def _wait(process: VariantProcess) -> None:
    if process.proc is not None:
        await process.proc.wait()


def require_all_started(processes: list[VariantProcess]) -> None:
    failed = [p.variant_id for p in processes if p.proc is None]
    if failed:
        raise PierError(f"failed to launch pier jobs for: {', '.join(failed)}")
