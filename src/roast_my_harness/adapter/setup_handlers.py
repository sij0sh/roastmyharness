"""Typed setup: one generic npm extension installer. Pi-only."""

from __future__ import annotations

import shlex
from typing import Any

from roast_my_harness.adapter.command import REMOTE_HOME

REMOTE_TMP = "/tmp"
INSTALL_DOMAINS = ["registry.npmjs.org"]


def install_domains() -> list[str]:
    return list(INSTALL_DOMAINS)


async def npm_pi_install(agent, environment, step: dict[str, Any]) -> None:
    import re
    package = step.get("package") or ""
    name, sep, version = package.rpartition("@")
    if not (sep and re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", name)
            and re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)*", version)):
        raise ValueError(f"npm_pi_install requires an exact package pin, got {package!r}")
    agent.logger.info(f"installing pi package {package}")
    npm_root = f"{REMOTE_HOME}/npm"
    await agent.exec_as_root(
        environment,
        command=("set -e; " f"export PI_CODING_AGENT_DIR={REMOTE_HOME}; "
                 f"pi install npm:{shlex.quote(package)} " f"&& test -d {npm_root} " f"&& node --version"),
        timeout_sec=900,
    )


HANDLERS = {"npm_pi_install": npm_pi_install}


async def run_setup_step(agent, environment, step: dict[str, Any]) -> None:
    handler = step.get("handler") or ""
    fn = HANDLERS.get(handler)
    if fn is None:
        raise ValueError(f"unknown setup handler: {handler!r}")
    await fn(agent, environment, {**step, **(step.get("args") or {})})
