"""Signal registration for graceful experiment cancellation (POSIX)."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

CancelCallback = Callable[[], None]
Cleanup = Callable[[], None]


def install_cancel_handlers(loop: asyncio.AbstractEventLoop, callback: CancelCallback) -> Cleanup:
    """Register SIGINT and SIGTERM on the loop (main thread only)."""
    installed: list[tuple[signal.Signals, object]] = []

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(signum)
        try:
            loop.add_signal_handler(signum, callback)
        except RuntimeError:
            continue  # not in the main thread; nothing to install
        installed.append((signum, previous))

    def _cleanup() -> None:
        for signum, previous in installed:
            try:
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous)
            except (RuntimeError, ValueError, OSError):
                pass

    return _cleanup
