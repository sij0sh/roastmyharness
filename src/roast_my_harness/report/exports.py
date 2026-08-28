"""summary.csv (tool-owned schema) and summary.json exports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from roast_my_harness.files import atomic_write_text
from roast_my_harness.report.collect import collect_rows
from roast_my_harness.telemetry.result import COLUMNS


def write_summary_csv(run_dir: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    rows = rows if rows is not None else collect_rows(run_dir)
    out = run_dir / "summary.csv"
    extra = [k for k in rows[0] if k not in COLUMNS] if rows else []
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=COLUMNS + extra, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(out, buffer.getvalue())
    return out


def write_summary_json(
    run_dir: Path,
    rows: list[dict[str, Any]] | None,
    provenance: dict[str, Any],
) -> Path:
    rows = rows if rows is not None else collect_rows(run_dir)
    payload = {
        "provenance": provenance,
        "row_count": len(rows),
        "trials": rows,
    }
    out = run_dir / "summary.json"
    atomic_write_text(out, json.dumps(payload, indent=2, default=str) + "\n")
    return out
