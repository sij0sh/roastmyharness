"""Signal registration covers supervised and interactive cancellation."""

from __future__ import annotations

import asyncio
import signal

from roast_my_harness.runner.signals import install_cancel_handlers


def test_install_cancel_handlers_registers_and_cleans_up():
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    loop = asyncio.new_event_loop()
    try:
        called: list[bool] = []
        cleanup = install_cancel_handlers(loop, lambda: called.append(True))
        cleanup()
        assert signal.getsignal(signal.SIGINT) == previous_int
        assert signal.getsignal(signal.SIGTERM) == previous_term
    finally:
        loop.close()
