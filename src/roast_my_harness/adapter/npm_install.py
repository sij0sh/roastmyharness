"""npm extension install for Pi trial containers. Stdlib-only."""

from __future__ import annotations

import re
import shlex

from roast_my_harness.adapter.command import REMOTE_HOME

INSTALL_DOMAINS = ["registry.npmjs.org"]


def install_domains() -> list[str]:
    return list(INSTALL_DOMAINS)


async def npm_pi_install(agent, environment, package: str) -> None:
    name, sep, version = package.rpartition("@")
    if not (sep and re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", name)
            and re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)*", version)):
        raise ValueError(f"npm extension install requires an exact package pin, got {package!r}")
    agent.logger.info(f"installing pi package {package}")
    npm_root = f"{REMOTE_HOME}/npm"
    await agent.exec_as_root(
        environment,
        command=("set -e; " f"export PI_CODING_AGENT_DIR={REMOTE_HOME}; "
                 f"pi install npm:{shlex.quote(package)} "
                 f"&& test -d {npm_root} " f"&& node --version"),
        timeout_sec=900,
    )
