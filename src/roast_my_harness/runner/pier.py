"""Pier argument construction. Argument arrays only, never shell strings."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from roast_my_harness.errors import PierError

AGENT_IMPORT_PATH = "roast_my_harness.adapter.pi_agent:PiAgent"


def pier_executable() -> str:
    exe = shutil.which("pier")
    if not exe:
        raise PierError(
            "pier not on PATH (uv tool install datacurve-pier)"
        )
    return exe


def pier_version() -> str | None:
    try:
        result = subprocess.run(
            [pier_executable(), "--version"], capture_output=True, text=True,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if re.fullmatch(r"\d+\.\d+\.\d+.*", line):
            return line.split()[0]
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", value)[:3])


def version_satisfies(actual: str, constraint: str) -> bool:
    """Check '>=0.3,<0.4'-style constraints over dotted integer versions."""
    for clause in constraint.split(","):
        clause = clause.strip()
        match = re.fullmatch(r"(>=|<=|==|>|<) ?([\d.]+)", clause)
        if not match:
            raise PierError(f"unsupported version constraint: {clause!r}")
        op, ref = match.groups()
        a, r = _version_tuple(actual), _version_tuple(ref)
        if op == ">=":
            ok = a >= r
        elif op == "<=":
            ok = a <= r
        elif op == "==":
            ok = a[: len(r)] == r
        elif op == ">":
            ok = a > r
        else:
            ok = a < r
        if not ok:
            return False
    return True


def build_run_args(
    *,
    task_root: Path,
    jobs_dir: Path,
    job_name: str,
    manifest_path: Path,
    model_id: str,
    thinking: str,
    pi_version: str,
    n_concurrent: int,
    include_tasks: list[str] | None = None,
) -> list[str]:
    """Build the `pier run` argv for one variant job."""
    args = [
        pier_executable(), "run",
        "--path", str(task_root),
    ]
    for task in include_tasks or []:
        args += ["--include-task-name", task]
    args += [
        "--agent-import-path", AGENT_IMPORT_PATH,
        "--ak", f"variant_manifest={manifest_path}",
        "--ak", f"thinking={thinking}",
        "--ak", f"pi_version={pi_version}",
        "--model", model_id,
        "--n-concurrent", str(n_concurrent),
        "--jobs-dir", str(jobs_dir),
        "--job-name", job_name,
        "--yes",
    ]
    return args
