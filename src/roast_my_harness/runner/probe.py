"""One-task smoke probe run during prepare before a large experiment.

Validates that the staged home + manifest actually load a Pi extension
inside the pier container, without burning a full experiment on it.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness import host_process as process_mod
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.tasks.discover import discover_tasks

SMOKE_MIN_TRIALS = 20
# Deadline must clear the slowest healthy trial, not just catch load
# failures (those error in seconds). Luna High trials run 400-1500s.
PROBE_TIMEOUT_SEC = float(os.environ.get("ROAST_PROBE_TIMEOUT", "1800"))
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
    pi_version: str | None = None,
    agent_version: str | None = None,
) -> list[str]:
    """Pier argv for a one-task, one-concurrent probe run.

    pi_version pins the install (the prepare-time frozen value); when
    None the pin resolves live, which callers must only use outside a
    frozen run. agent_version stays as a deprecated alias of pi_version.
    """
    job = jobs[variant_id]
    arm = next(a for a in spec.arms() if a.id == variant_id)
    pin = pi_version if pi_version is not None else agent_version
    if pin is None:
        pin = spec.resolved_pi_version_for(arm if arm.id != "control" else None)
    return pier_mod.build_run_args(
        task_root=spec.tasks.path,
        jobs_dir=job.staged.parent / "probe-jobs",
        job_name=f"smoke-{variant_id}",
        manifest_path=job.manifest_path,
        model_id=spec.model.full_id(),
        thinking=spec.thinking,
        pi_version=pin,
        n_concurrent=1,
        include_tasks=[task_id],
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
            task_id for task_id, meta in catalog.tasks.items() if meta.smoke and task_id in ids
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
    frozen = (resolved_versions or {}).get(f"pi:{variant_id}", (resolved_versions or {}).get("pi"))
    argv = probe_argv(
        spec=spec,
        jobs=jobs,
        task_id=task_id,
        variant_id=variant_id,
        pi_version=frozen,
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
    await process_mod.kill_after_grace(proc, PROBE_KILL_GRACE_SEC)


def run_probe_sync(**kwargs: Any) -> ProbeResult:
    return asyncio.run(run_probe(**kwargs))
