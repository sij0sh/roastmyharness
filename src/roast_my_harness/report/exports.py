"""summary.csv (tool-owned schema) and summary.json exports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from roast_my_harness.files import atomic_write_text
from roast_my_harness.report.charts import chart_series
from roast_my_harness.report.collect import collect_rows
from roast_my_harness.report.dimensions import dimension_summary, has_dimensions
from roast_my_harness.report.markdown import task_labels
from roast_my_harness.report.statistics import deterministic_seed, stratify, variant_type
from roast_my_harness.telemetry.result import COLUMNS


def write_summary_csv(run_dir: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    rows = rows if rows is not None else collect_rows(run_dir)
    out = run_dir / "summary.csv"
    # custom_metrics is a nested per-trial dict for summary.json; it never
    # becomes a CSV column.
    extra = (
        [k for k in rows[0] if k not in COLUMNS and k != "custom_metrics"]
        if rows
        else []
    )
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
    spec_variants = (provenance.get("spec") or {}).get("variants", [])
    labels = task_labels(provenance)
    payload = {
        "provenance": provenance,
        "row_count": len(rows),
        "trials": rows,
        "charts": chart_series(rows, str(provenance.get("experiment_id") or "")),
        "variant_types": {
            variant: variant_type(variant, spec_variants)
            for variant in sorted({str(row.get("variant")) for row in rows})
        },
        "stratified": stratify(
            rows,
            labels,
            seed=deterministic_seed(f"{provenance.get('experiment_id')}\0strata"),
        ),
    }
    if has_dimensions(rows):
        payload["dimensions"] = dimension_summary(
            rows,
            seed=deterministic_seed(f"{provenance.get('experiment_id')}\0dimensions"),
        )
    out = run_dir / "summary.json"
    atomic_write_text(out, json.dumps(payload, indent=2, default=str) + "\n")
    return out
