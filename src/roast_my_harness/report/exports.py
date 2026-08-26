"""summary.csv (DSE-compatible schema) and summary.json exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from roast_my_harness.report.collect import collect_rows
from roast_my_harness.telemetry.result import COLUMNS


def write_summary_csv(run_dir: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    rows = rows if rows is not None else collect_rows(run_dir)
    out = run_dir / "summary.csv"
    extra = [k for k in rows[0] if k not in COLUMNS] if rows else []
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS + extra, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
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
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    return out
