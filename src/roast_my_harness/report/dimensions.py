"""Named scoring dimension aggregates: deterministic vs judge breakdowns.

The scalar ``reward`` stays the primary trial outcome (pass/fail against
the threshold). Dimension keys are standardized in telemetry.result;
this module aggregates them per variant for reports. Single-dimension
verifiers (DeepSWE today) report no dimension keys and every consumer
below degrades to absent.
"""

from __future__ import annotations

import random
from typing import Any

from roast_my_harness.report.statistics import deterministic_seed, rate_ci, resolved_rows
from roast_my_harness.telemetry.result import (
    DETERMINISTIC_KEY,
    JUDGE_KEY,
    fnum_or_none,
)


def has_dimensions(rows: list[dict[str, Any]]) -> bool:
    """True when any row reports a deterministic or judge score."""
    return any(
        fnum_or_none(row.get(DETERMINISTIC_KEY)) is not None
        or fnum_or_none(row.get(JUDGE_KEY)) is not None
        for row in rows
    )


def dimension_summary(
    rows: list[dict[str, Any]], *, seed: int = 0
) -> dict[str, dict[str, Any]]:
    """Per-variant dimension aggregates, mirroring task_rates semantics.

    Each task contributes one mean over the replicates that report the
    dimension; infra-error trials are excluded like everywhere else.
    The variant mean is the mean of per-task means with a
    task-bootstrapped CI. Tasks with no dimension data never enter.
    """
    per_task: dict[str, dict[str, dict[str, list[float]]]] = {}
    judges: dict[str, set[str]] = {}
    for row in resolved_rows(rows):
        variant = str(row.get("variant", ""))
        task = str(row.get("task", ""))
        for key in (DETERMINISTIC_KEY, JUDGE_KEY):
            value = fnum_or_none(row.get(key))
            if value is None:
                continue
            per_task.setdefault(variant, {}).setdefault(key, {}).setdefault(
                task, []
            ).append(value)
        judge = str(row.get("judge_model") or "")
        if judge:
            judges.setdefault(variant, set()).add(judge)
    summary: dict[str, dict[str, Any]] = {}
    for variant in sorted(per_task):
        entry: dict[str, Any] = {"judge_models": sorted(judges.get(variant, ()))}
        for key in (DETERMINISTIC_KEY, JUDGE_KEY):
            task_means = sorted(
                sum(values) / len(values)
                for values in per_task[variant].get(key, {}).values()
            )
            if not task_means:
                continue
            mean, lo, hi = rate_ci(
                task_means,
                random.Random(seed + deterministic_seed(f"dimension\0{key}\0{variant}")),
            )
            entry[key] = {
                "mean": mean,
                "lo": lo,
                "hi": hi,
                "tasks": len(task_means),
            }
        summary[variant] = entry
    return summary
