"""Generic npm installer rejects unsafe pins."""

from __future__ import annotations

import pytest


def test_npm_install_rejects_non_exact():
    import asyncio
    from roast_my_harness.adapter import setup_handlers

    class FakeAgent:
        logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()

    async def run(pkg: str):
        await setup_handlers.npm_pi_install(FakeAgent(), object(), {"package": pkg})

    asyncio.run(run("context-mode@1.0.169"))
    with pytest.raises(ValueError):
        asyncio.run(run("context-mode"))
    with pytest.raises(ValueError):
        asyncio.run(run("context-mode@latest"))
    with pytest.raises(ValueError):
        asyncio.run(run("context-mode@1.0.0; echo leaked"))
