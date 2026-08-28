"""Collect trial rows from a run directory's pier jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roast_my_harness.telemetry.result import is_trial_dir, trial_row


def collect_rows(jobs_root: Path) -> list[dict[str, Any]]:
    """One row per trial result.json under <variant>/... in jobs_root.

    jobs_root is `<run>/jobs` for a roastmyharness run.
    """
    selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    jobs = jobs_root
    if not jobs.is_dir():
        return []
    for variant_dir in sorted(jobs.iterdir()):
        if not variant_dir.is_dir():
            continue
        for result_path in sorted(variant_dir.rglob("result.json")):
            if not is_trial_dir(result_path.parent):
                continue
            row = trial_row(result_path, variant_dir.name)
            if not row:
                continue
            task_id = str(row.get("task") or result_path.parent.name)
            try:
                stamp = result_path.stat().st_mtime_ns
            except OSError:
                stamp = 0
            key = (variant_dir.name, task_id)
            if key not in selected or stamp >= selected[key][0]:
                selected[key] = (stamp, row)
    return [
        row
        for _, row in sorted(selected.values(), key=lambda item: (
            item[1]["variant"], item[1]["task"]
        ))
    ]


def aggregate_by_variant(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per-variant totals over collected trial rows (completed trials only).

    Keys per variant: n, resolved, input_tokens, output_tokens, wall_sec,
    cost_usd. Missing or unparsable fields contribute zero, so aggregates
    stay available mid-run.
    """
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        variant = str(row.get("variant", ""))
        agg = out.setdefault(
            variant,
            {
                "n": 0,
                "resolved": 0,
                "input_tokens": 0.0,
                "output_tokens": 0.0,
                "wall_sec": 0.0,
                "cost_usd": 0.0,
            },
        )
        agg["n"] += 1
        try:
            agg["resolved"] += int(row.get("resolved") or 0)
        except (TypeError, ValueError):
            pass
        for key in ("input_tokens", "output_tokens", "wall_sec", "cost_usd"):
            try:
                agg[key] += float(row.get(key) or 0)
            except (TypeError, ValueError):
                pass
    for agg in out.values():
        agg["wall_sec"] = round(agg["wall_sec"], 1)
        agg["cost_usd"] = round(agg["cost_usd"], 4)
    return out
