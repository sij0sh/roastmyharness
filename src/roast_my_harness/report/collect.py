"""Collect trial rows from a run directory's pier jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roast_my_harness.telemetry.result import is_trial_dir, trial_row


def collect_rows(jobs_root: Path) -> list[dict[str, Any]]:
    """One row per trial result.json under <variant>/... in jobs_root.

    jobs_root is `<run>/jobs` for roastmyharness runs, or a legacy DSE
    `results-*` directory (same <variant>/<timestamp>/<trial> layout).
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
