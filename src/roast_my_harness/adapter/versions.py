"""Agent version pins: exact versions plus the ``latest`` sentinel.

Stdlib-only: the spec, runner, and pier adapters all import this module,
and adapters load inside pier's venv, so this module must never pull a
third-party dependency.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess

LATEST = "latest"
"""Spec pin meaning the newest npm release, resolved every time we run."""

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")


def is_latest(value: str | None) -> bool:
    """True when a pin asks for the newest release instead of an exact one."""
    return value == LATEST


def is_exact_pin(value: str) -> bool:
    """True when a pin is an exact version safe to pass as one argv token."""
    return bool(_VERSION_RE.fullmatch(value))


def validate_agent_pin(value: str, field: str) -> str:
    """A pi-family agent pin: ``latest`` or an exact version."""
    if value == LATEST or _VERSION_RE.fullmatch(value):
        return value
    raise ValueError(f"{field} must be 'latest' or an exact version, got {value!r}")


def validate_exact_pin(value: str, field: str) -> str:
    """An exact-only pin (omp, npm extensions): ``latest`` is rejected."""
    if _VERSION_RE.fullmatch(value):
        return value
    raise ValueError(f"{field} must be an exact version, got {value!r}")


@functools.cache
def resolve_package_version(package: str, pin: str, *, timeout: int = 60) -> str:
    """The exact version a pin installs.

    ``latest`` queries the npm registry, so every run picks up the newest
    release. Exact pins return unchanged without touching the network.
    Results cache per process so every arm of one run resolves identically.
    """
    if pin != LATEST:
        return validate_exact_pin(pin, "version")
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("cannot resolve 'latest': npm not on PATH; pin an exact version instead")
    try:
        proc = subprocess.run(
            [npm, "view", package, "version", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f"cannot resolve latest {package}: {error}; pin an exact version instead"
        ) from error
    if proc.returncode != 0:
        raise RuntimeError(
            f"latest {package} is not available from the npm registry; pin an exact version instead"
        )
    version: object = None
    text = proc.stdout.strip()
    if text:
        try:
            version = json.loads(text)
        except json.JSONDecodeError:
            version = text.splitlines()[-1].strip()
    if isinstance(version, list):
        version = version[-1] if version else None
    if isinstance(version, str) and _VERSION_RE.fullmatch(version):
        return version
    raise RuntimeError(
        f"npm returned an unusable version for latest {package}: {text[:80]!r}; "
        "pin an exact version instead"
    )
