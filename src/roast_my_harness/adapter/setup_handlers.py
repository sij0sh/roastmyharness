"""Typed setup handlers. Stdlib-only: importable inside pier's venv.

Named handlers replace arbitrary shell so untrusted experiment files can
never execute host-side commands. Handlers validate fields before any
task starts.
"""

from __future__ import annotations

import shutil
from typing import Any

from roast_my_harness.adapter.command import REMOTE_HOME

REMOTE_TMP = "/tmp"

INSTALL_DOMAINS = ["registry.npmjs.org", "deb.debian.org"]


def install_domains() -> list[str]:
    return list(INSTALL_DOMAINS)


async def npm_pi_install(agent, environment, step: dict[str, Any]) -> None:
    """Install a pinned npm pi package into the uploaded home (setup phase).

    Install latency must not count against agent time. Fails loudly when
    the package entry file is missing: a silently degraded extension would
    invalidate the variant.
    """
    package = step.get("package") or ""
    if not package or "@" not in package:
        raise ValueError(f"npm_pi_install requires an exact package pin, got {package!r}")
    agent.logger.info(f"installing pi package {package}")
    npm_root = f"{REMOTE_HOME}/npm"
    await agent.exec_as_root(
        environment,
        command=(
            "set -e; "
            f"export PI_CODING_AGENT_DIR={REMOTE_HOME}; "
            f"pi install npm:{package} "
            f"&& test -d {npm_root} "
            f"&& node --version"
        ),
        timeout_sec=900,
    )


async def install_binary(agent, environment, step: dict[str, Any]) -> None:
    """Upload a host binary and install it world-executable."""
    source = step.get("source") or ""
    destination = step.get("destination") or "/usr/local/bin"
    if not source:
        raise ValueError("install_binary requires source")
    remote_tmp = f"{REMOTE_TMP}/roast-my-harness-binary"
    await environment.upload_file(source, remote_tmp)
    name = destination.rsplit("/", 1)[-1]
    verify = step.get("verify") or f"{destination}/{name}".replace(
        f"{destination}/{name}", name
    )
    await agent.exec_as_root(
        environment,
        command=(
            "set -e; "
            f"install -m 0755 {remote_tmp} {destination}/{name} "
            f"&& rm -f {remote_tmp} "
            f"&& {verify} --version"
        ),
    )


async def run_rtk_init(agent, environment, step: dict[str, Any]) -> None:
    """Run `rtk init -g --agent pi` against the uploaded home.

    Matches the documented install flow and verifies binary and extension
    agree; the home build pre-generates extensions/rtk.ts from the same
    vendored binary.
    """
    agent.logger.info("running rtk init -g --agent pi")
    await agent.exec_as_root(
        environment,
        command=(
            "set -e; "
            f"export PI_CODING_AGENT_DIR={REMOTE_HOME}; "
            "rtk init -g --agent pi "
            f"&& test -f {REMOTE_HOME}/extensions/rtk.ts "
            "&& rtk --version"
        ),
    )


async def codegraph_index(agent, environment, step: dict[str, Any]) -> None:
    """Install the CodeGraph CLI from a vendored bundle and index /app."""
    bundle = step.get("bundle") or ""
    if not bundle:
        raise ValueError("codegraph_index requires bundle")
    if not shutil.which(bundle) and not _is_file(bundle):
        raise FileNotFoundError(f"codegraph bundle missing: {bundle}")
    agent.logger.info("installing CodeGraph CLI from vendored bundle")
    root = "/opt/codegraph"
    tarball = f"{REMOTE_TMP}/codegraph.tar.gz"
    await environment.upload_file(bundle, tarball)
    await agent.exec_as_root(
        environment,
        command=(
            "set -e; "
            f"mkdir -p {root} "
            f"&& tar -xzf {tarball} -C {root} --strip-components=1 "
            f"&& ln -sf {root}/bin/codegraph /usr/local/bin/codegraph "
            f"&& chmod -R a+rX {root} "
            f"&& rm -f {tarball} "
            "&& codegraph --version"
        ),
    )
    agent.logger.info("building CodeGraph index for /app")
    await agent.exec_as_agent(
        environment,
        command="set -e; codegraph init",
        cwd="/app",
        env={"CODEGRAPH_TELEMETRY": "0", "CODEGRAPH_NO_WATCH": "1"},
        timeout_sec=1800,
    )
    await agent.exec_as_agent(
        environment,
        command=(
            "set -e; mkdir -p /app/.git/info "
            "&& echo '.codegraph/' >> /app/.git/info/exclude"
        ),
        cwd="/app",
    )


def _is_file(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_file()


HANDLERS = {
    "npm_pi_install": npm_pi_install,
    "install_binary": install_binary,
    "run_rtk_init": run_rtk_init,
    "codegraph_index": codegraph_index,
}


async def run_setup_step(agent, environment, step: dict[str, Any]) -> None:
    handler = step.get("handler") or ""
    fn = HANDLERS.get(handler)
    if fn is None:
        raise ValueError(f"unknown setup handler: {handler!r}")
    await fn(agent, environment, step)
