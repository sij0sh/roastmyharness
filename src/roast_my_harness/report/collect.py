"""Collect trial rows from a run directory's pier jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roast_my_harness.telemetry.result import is_trial_dir, trial_row


def collect_rows(jobs_root: Path) -> list[dict[str, Any]]:
    """One row per trial result.json under <variant>/... in jobs_root.

    jobs_root is `<run>/jobs` for roast-my-harness runs, or a legacy DSE
    `results-*` directory (same <variant>/<timestamp>/<trial> layout).
    """
    rows: list[dict[str, Any]] = []
    jobs = jobs_root
    if not jobs.is_dir():
        return rows
    for variant_dir in sorted(jobs.iterdir()):
        if not variant_dir.is_dir():
            continue
        for result_path in sorted(variant_dir.rglob("result.json")):
            if not is_trial_dir(result_path.parent):
                continue
            row = trial_row(result_path, variant_dir.name)
            if row:
                rows.append(row)
    return rows
