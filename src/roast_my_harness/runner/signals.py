"""Signal registration for graceful experiment cancellation (POSIX)."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

CancelCallback = Callable[[], None]
Cleanup = Callable[[], None]


def install_sync_cancel_handlers(callback: CancelCallback) -> Cleanup:
    """Install synchronous SIGINT/SIGTERM handlers that fire during blocking code.

    loop.add_signal_handler defers callbacks until the event loop iterates,
    so sync prepare() would otherwise run uninterruptibly. signal.signal
    handlers run between bytecodes even while the loop is blocked or stopped.
    The callback (controller.request_cancel) only flips an event flag, so it
    is safe to run synchronously; callers poll it between prepare steps."""
    previous: list[tuple[signal.Signals, object]] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            prev = signal.getsignal(signum)
        except (ValueError, OSError):
            continue
        try:
            signal.signal(signum, lambda _n, _f: callback())
        except (ValueError, OSError, RuntimeError):
            continue
        previous.append((signum, prev))

    def _cleanup() -> None:
        for signum, prev in previous:
            try:
                signal.signal(signum, prev)
            except (ValueError, OSError, RuntimeError):
                pass

    return _cleanup


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
