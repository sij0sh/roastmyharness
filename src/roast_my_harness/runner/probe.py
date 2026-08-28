"""One-task smoke probe run during prepare before a large experiment.

Validates that the staged home + manifest actually load a Pi extension
inside the pier container, without burning a full experiment on it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import process as process_mod
from roast_my_harness.tasks.discover import discover_tasks

SMOKE_MIN_TRIALS = 20


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
) -> list[str]:
    """Pier argv for a one-task, one-concurrent probe run."""
    job = jobs[variant_id]
    agent_id = spec.resolved_agents()[variant_id]
    return pier_mod.build_run_args(
        task_root=spec.tasks.path,
        jobs_dir=job.staged.parent / "probe-jobs",
        job_name=f"smoke-{variant_id}",
        manifest_path=job.manifest_path,
        model_id=spec.model.full_id(),
        thinking=spec.thinking,
        pi_version=spec.agent_version_for(agent_id),
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


async def run_probe(
    *,
    spec: Any,
    jobs: dict[str, Any],
    run_dir: Path,
    env: dict[str, str] | None = None,
) -> ProbeResult:
    """Launch one smoke task on an extension-bearing arm; fail fast on crash.

    Raises PierError when the process cannot start. A nonzero exit marks the
    probe failed; the caller decides whether to abort the experiment.
    """
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    task_id = tasks[0].task_id
    variant_id = select_variant(spec, jobs)
    argv = probe_argv(spec=spec, jobs=jobs, task_id=task_id, variant_id=variant_id)
    log_path = run_dir / "logs" / f"smoke-{variant_id}.log"
    proc = process_mod.VariantProcess(f"smoke-{variant_id}", argv, log_path)
    await proc.start(env)
    assert proc.proc is not None
    returncode = await proc.proc.wait()
    return ProbeResult(
        state="passed" if returncode == 0 else "failed",
        variant_id=variant_id,
        task_id=task_id,
        returncode=returncode,
        log_path=log_path,
    )


def run_probe_sync(**kwargs: Any) -> ProbeResult:
    return asyncio.run(run_probe(**kwargs))
