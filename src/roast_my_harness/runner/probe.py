"""One-task smoke probe run during prepare before a large experiment.

Validates that the staged home + manifest actually load a Pi extension
inside the pier container, without burning a full experiment on it.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import process as process_mod
from roast_my_harness.tasks.discover import discover_tasks

SMOKE_MIN_TRIALS = 20
PROBE_TIMEOUT_SEC = 600.0
PROBE_KILL_GRACE_SEC = 10.0


@dataclass
class ProbeResult:
    state: str
    variant_id: str
    task_id: str
    returncode: int
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.state == "passed"


class ProbeTimeoutError(Exception):
    pass


def should_probe(spec: Any) -> bool:
    """True when the experiment is large enough to warrant a smoke probe."""
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    return len(tasks) * len(spec.arms()) >= SMOKE_MIN_TRIALS


def probe_argv(
    *,
    spec: Any,
    jobs: dict[str, Any],
    task_id: str,
    variant_id: str,
    agent_version: str | None = None,
) -> list[str]:
    """Pier argv for a one-task, one-concurrent probe run.

    agent_version pins the install (the prepare-time frozen value); when
    None the pin resolves live, which callers must only use outside a
    frozen run.
    """
    job = jobs[variant_id]
    agent_id = spec.resolved_agents()[variant_id]
    if agent_version is None:
        agent_version = spec.resolved_version_for(agent_id)
    return pier_mod.build_run_args(
        task_root=spec.tasks.path,
        jobs_dir=job.staged.parent / "probe-jobs",
        job_name=f"smoke-{variant_id}",
        manifest_path=job.manifest_path,
        model_id=spec.model.full_id(),
        thinking=spec.thinking,
        pi_version=agent_version,
        n_concurrent=1,
        include_tasks=[task_id],
        agent=agent_id,
    )


def select_variant(spec: Any, jobs: dict[str, Any]) -> str:
    """Prefer an arm carrying a local extension; else the first arm."""
    ext_arms = {v.id for v in spec.variants if any(e.kind == "local" for e in v.extensions)}
    for variant_id in jobs:
        if variant_id in ext_arms:
            return variant_id
    return next(iter(jobs))


def select_probe_task(tasks: list[Any], catalog: Any | None) -> str:
    """Deterministic smoke task: tagged smoke first, else first discovered.

    Smoke candidates prefer fast+easy; ties break by task id. Without a
    catalog (or without smoke tags, which need calibration data), the
    probe covers the first discovered task — which is the preset head
    when tasks.preset scoped discovery.
    """
    ids = {t.task_id for t in tasks}
    if catalog is not None:
        smoked = sorted(
            task_id
            for task_id, meta in catalog.tasks.items()
            if meta.smoke and task_id in ids
        )
        if smoked:
            fast_easy = [
                task_id
                for task_id in smoked
                if catalog.tasks[task_id].duration == "fast"
                and catalog.tasks[task_id].difficulty == "easy"
            ]
            return fast_easy[0] if fast_easy else smoked[0]
    return tasks[0].task_id


async def run_probe(
    *,
    spec: Any,
    jobs: dict[str, Any],
    run_dir: Path,
    env: dict[str, str] | None = None,
    timeout_sec: float | None = PROBE_TIMEOUT_SEC,
    resolved_versions: dict[str, str] | None = None,
    catalog: Any | None = None,
) -> ProbeResult:
    """Launch one smoke task on an extension-bearing arm; fail fast on crash.

    Raises PierError when the process cannot start. A nonzero exit marks the
    probe failed; the caller decides whether to abort the experiment.
    Exceeding timeout_sec kills the probe and raises ProbeTimeoutError.
    resolved_versions pins installs to the frozen run; without it the pin
    resolves live. catalog scopes smoke-tag selection; without it the
    first discovered task probes.
    """
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    task_id = select_probe_task(tasks, catalog)
    variant_id = select_variant(spec, jobs)
    agent_id = spec.resolved_agents()[variant_id]
    frozen = (resolved_versions or {}).get(agent_id)
    argv = probe_argv(
        spec=spec, jobs=jobs, task_id=task_id, variant_id=variant_id,
        agent_version=frozen,
    )
    log_path = run_dir / "logs" / f"smoke-{variant_id}.log"
    proc = process_mod.VariantProcess(f"smoke-{variant_id}", argv, log_path)
    await proc.start(env)
    assert proc.proc is not None
    start = time.monotonic()
    try:
        if timeout_sec is None:
            returncode = await proc.proc.wait()
        else:
            returncode = await asyncio.wait_for(proc.proc.wait(), timeout=timeout_sec)
    except TimeoutError:
        elapsed = time.monotonic() - start
        await _kill_probe(proc)
        raise ProbeTimeoutError(
            f"smoke probe timed out on variant {variant_id} "
            f"(task {task_id}, {elapsed:.1f}s > {timeout_sec:.0f}s deadline); "
            f"see {log_path}"
        ) from None
    return ProbeResult(
        state="passed" if returncode == 0 else "failed",
        variant_id=variant_id,
        task_id=task_id,
        returncode=returncode,
        log_path=log_path,
    )


async def _kill_probe(proc: process_mod.VariantProcess) -> None:
    if proc.proc is None or proc.proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(proc.proc.wait(), timeout=PROBE_KILL_GRACE_SEC)
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        await proc.proc.wait()


def run_probe_sync(**kwargs: Any) -> ProbeResult:
    return asyncio.run(run_probe(**kwargs))
