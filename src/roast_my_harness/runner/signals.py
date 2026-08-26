"""Portable signal registration for graceful experiment cancellation."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from types import FrameType

CancelCallback = Callable[[], None]
Cleanup = Callable[[], None]


def install_cancel_handlers(loop: asyncio.AbstractEventLoop, callback: CancelCallback) -> Cleanup:
    """Register SIGINT and SIGTERM, with a Windows-compatible fallback."""
    installed: list[tuple[signal.Signals, object]] = []
    fallback: list[tuple[signal.Signals, object]] = []

    def _fallback(_signum: int, _frame: FrameType | None) -> None:
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError:
            pass

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(signum)
        try:
            loop.add_signal_handler(signum, callback)
            installed.append((signum, previous))
        except (AttributeError, NotImplementedError, RuntimeError):
            try:
                signal.signal(signum, _fallback)
            except (ValueError, OSError):
                continue
            fallback.append((signum, previous))

    def _cleanup() -> None:
        for signum, previous in installed:
            try:
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous)
            except (AttributeError, RuntimeError, ValueError, OSError):
                pass
        for signum, previous in fallback:
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass

    return _cleanup
