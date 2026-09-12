from __future__ import annotations

import statistics as st
from typing import Any

from roast_my_harness.telemetry.result import fnum_or_none

BASELINE_VARIANTS = {"control", "baseline"}
NORM_METRICS = ("partial", "output_tokens", "wall_sec")


def _group_norms(values: list[float]) -> dict[str, Any]:
    n = len(values)
    mean = st.mean(values)
    sigma = st.stdev(values) if n > 1 else None
    return {
        "n": n,
        "mean": mean,
        "sigma": sigma,
        "cv": (sigma / mean) if sigma is not None and mean else None,
    }


def _median(values: list[float]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2


def build_norms(trials: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for trial in trials:
        if trial.get("exception_type"):
            continue
        if str(trial.get("variant")) not in BASELINE_VARIANTS:
            continue
        key = (
            str(trial.get("model") or ""),
            str(trial.get("thinking") or ""),
            str(trial.get("task") or ""),
        )
        bucket = groups.setdefault(key, {m: [] for m in NORM_METRICS})
        for metric in NORM_METRICS:
            value = fnum_or_none(trial.get(metric))
            if value is not None:
                bucket[metric].append(value)
    cvs: dict[tuple[str, str, str], list[float]] = {}
    for (model, thinking, _task), bucket in groups.items():
        for metric in NORM_METRICS:
            if not bucket[metric]:
                continue
            norm = _group_norms(bucket[metric])
            if norm["cv"] is not None:
                cvs.setdefault((model, thinking, metric), []).append(norm["cv"])
    pooled_cv = {
        f"{model}\0{thinking}\0{metric}": _median(values)
        for (model, thinking, metric), values in cvs.items()
    }
    tasks = {}
    for (model, thinking, task), bucket in sorted(groups.items()):
        entry: dict[str, Any] = {}
        for metric in NORM_METRICS:
            if not bucket[metric]:
                continue
            norm = _group_norms(bucket[metric])
            if norm["sigma"] is None:
                fallback = pooled_cv.get(f"{model}\0{thinking}\0{metric}")
                norm["sigma_hat"] = (
                    fallback * norm["mean"] if fallback is not None else None
                )
            entry[metric] = norm
        tasks[f"{model}\0{thinking}\0{task}"] = entry
    return {"pooled_cv": pooled_cv, "tasks": tasks}


def flag_outliers(
    rows: list[dict[str, Any]], norms: dict[str, Any], *, k: float = 2.0
) -> list[dict[str, Any]]:
    tasks = norms.get("tasks", {})
    out = []
    for row in rows:
        if row.get("exception_type"):
            continue
        key = f"{row.get('model') or ''}\0{row.get('thinking') or ''}\0{row.get('task')}"
        entry = tasks.get(key)
        if not entry:
            continue
        for metric in NORM_METRICS:
            value = fnum_or_none(row.get(metric))
            norm = entry.get(metric)
            if value is None or not norm:
                continue
            sigma = norm.get("sigma") or norm.get("sigma_hat")
            if not sigma:
                continue
            z = (value - norm["mean"]) / sigma
            if abs(z) >= k:
                out.append(
                    {
                        "variant": str(row.get("variant")),
                        "task": str(row.get("task")),
                        "metric": metric,
                        "value": value,
                        "mean": norm["mean"],
                        "z": round(z, 2),
                    }
                )
    return sorted(out, key=lambda d: (d["variant"], d["task"], d["metric"]))
