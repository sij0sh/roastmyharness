from __future__ import annotations

import random
from typing import Any

from roast_my_harness.report.statistics import (
    by_variant,
    deterministic_seed,
    fnum,
    paired_flips,
    rate_ci,
    resolved_rows,
    task_rates,
)
from roast_my_harness.telemetry.result import fnum_or_none

NEAR_MISS_THRESHOLD = 0.8

PARTIAL_BUCKETS = ("<0.5", "0.5-0.8", "0.8-1.0")


def outcome_label(row: dict[str, Any]) -> str:
    if row.get("exception_type"):
        return "error"
    if int(row.get("resolved") or 0) == 1:
        return "pass"
    partial = fnum_or_none(row.get("partial"))
    if partial is not None and partial >= NEAR_MISS_THRESHOLD:
        return "near-miss"
    return "fail"


def _mean_partial(rows: list[dict[str, Any]]) -> float | None:
    per_task: dict[str, list[float]] = {}
    for row in resolved_rows(rows):
        value = fnum_or_none(row.get("partial"))
        if value is None:
            continue
        per_task.setdefault(str(row["task"]), []).append(value)
    if not per_task:
        return None
    means = [sum(v) / len(v) for v in per_task.values()]
    return sum(means) / len(means)


def resolve_rate_series(
    rows: list[dict[str, Any]], *, seed: int
) -> list[dict[str, Any]]:
    rates = task_rates(rows)
    out = []
    for variant in sorted(rates):
        per_task = sorted(rates[variant].values())
        mean, lo, hi = rate_ci(
            per_task,
            random.Random(seed + deterministic_seed(f"charts\0rate\0{variant}")),
        )
        out.append(
            {
                "variant": variant,
                "tasks": len(per_task),
                "rate": mean,
                "lo": lo,
                "hi": hi,
            }
        )
    return out


def flip_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "a": a,
            "b": b,
            "both_pass": both,
            "a_fail_b_pass": a_fail_b,
            "b_fail_a_pass": b_fail_a,
        }
        for a, b, both, a_fail_b, b_fail_a, _discordant in paired_flips(rows)
    ]


def near_miss_series(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = by_variant(resolved_rows(rows))
    arms: dict[str, dict[str, int]] = {}
    for variant in sorted(grouped):
        buckets = {name: 0 for name in PARTIAL_BUCKETS}
        for row in grouped[variant].values():
            if int(row.get("resolved") or 0) == 1:
                continue
            partial = fnum_or_none(row.get("partial"))
            if partial is None:
                continue
            if partial < 0.5:
                buckets["<0.5"] += 1
            elif partial < NEAR_MISS_THRESHOLD:
                buckets["0.5-0.8"] += 1
            else:
                buckets["0.8-1.0"] += 1
        arms[variant] = buckets
    return {"threshold": NEAR_MISS_THRESHOLD, "arms": arms}


def _task_partials(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    per_task: dict[str, dict[str, list[float]]] = {}
    for row in resolved_rows(rows):
        value = fnum_or_none(row.get("partial"))
        if value is None:
            continue
        per_task.setdefault(str(row["variant"]), {}).setdefault(
            str(row["task"]), []
        ).append(value)
    return {
        variant: {task: sum(v) / len(v) for task, v in tasks.items()}
        for variant, tasks in per_task.items()
    }


def partial_delta_series(
    rows: list[dict[str, Any]], *, control: str = "control", limit: int = 10
) -> list[dict[str, Any]]:
    partials = _task_partials(rows)
    baseline = partials.get(control, {})
    deltas = []
    for variant in sorted(partials):
        if variant == control:
            continue
        for task in sorted(set(partials[variant]) & set(baseline)):
            delta = partials[variant][task] - baseline[task]
            if abs(delta) >= 0.1:
                deltas.append(
                    {
                        "variant": variant,
                        "task": task,
                        "delta": delta,
                        "control": baseline[task],
                        "value": partials[variant][task],
                    }
                )
    deltas.sort(key=lambda d: abs(d["delta"]), reverse=True)
    return deltas[:limit]


def cost_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = by_variant(rows)
    means: dict[str, dict[str, float]] = {}
    for variant in sorted(grouped):
        tasks = list(grouped[variant].values())
        valid = resolved_rows(tasks)
        n = len(valid) or 1
        resolved = sum(int(t["resolved"]) for t in valid)
        out = sum(fnum(t.get("output_tokens", "")) for t in valid) / n
        wall = sum(fnum(t.get("wall_sec", "")) for t in valid) / n
        cost = sum(fnum(t.get("cost_usd", "")) for t in valid) / n
        means[variant] = {
            "mean_output_tokens": out,
            "mean_wall_sec": wall,
            "mean_cost_usd": cost,
            "cost_per_resolve": (cost * n / resolved) if resolved else None,
        }
    base = means.get("control", {})
    out = []
    for variant in sorted(means):
        entry: dict[str, Any] = {"variant": variant, **means[variant]}
        for key in ("mean_output_tokens", "mean_wall_sec", "mean_cost_usd"):
            b = base.get(key)
            v = means[variant][key]
            entry[f"{key}_pct_vs_control"] = (
                None if variant == "control" or not b else 100 * (v - b) / b
            )
        out.append(entry)
    return out


def p2p_regressions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in resolved_rows(rows):
        if int(row.get("resolved") or 0) != 1:
            continue
        try:
            total = int(row.get("p2p_total") or 0)
            passed = int(row.get("p2p_passed") or 0)
        except (TypeError, ValueError):
            continue
        if total and passed < total:
            out.append(
                {
                    "variant": str(row.get("variant")),
                    "task": str(row.get("task")),
                    "p2p_passed": passed,
                    "p2p_total": total,
                }
            )
    return sorted(out, key=lambda d: (d["variant"], d["task"]))


def chart_series(
    rows: list[dict[str, Any]],
    experiment_id: str,
    spec_variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del spec_variants
    seed = deterministic_seed(f"{experiment_id}\0charts")
    grouped = by_variant(rows)
    arms = {}
    for variant in sorted(grouped):
        tasks = list(grouped[variant].values())
        valid = resolved_rows(tasks)
        labels = [outcome_label(t) for t in valid]
        arms[variant] = {
            "resolved": sum(int(t["resolved"]) for t in valid),
            "total": len(valid),
            "near_miss": sum(1 for label in labels if label == "near-miss"),
            "mean_partial": _mean_partial(tasks),
        }
    return {
        "resolve_rates": resolve_rate_series(rows, seed=seed),
        "flips": flip_series(rows),
        "near_miss": near_miss_series(rows),
        "partial_deltas": partial_delta_series(rows),
        "cost": cost_series(rows),
        "p2p_regressions": p2p_regressions(rows),
        "arms": arms,
    }
