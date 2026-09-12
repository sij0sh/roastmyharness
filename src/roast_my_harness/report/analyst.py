"""Post-run analyst: a deterministic reading of summary.json.

The analyst never calls a model and never fails a run. It restates what
the frozen evidence says (arm scores, best arm, per-stratum deltas with
CI-overlap separation, the pre-run hypothesis for the human reader) and
writes analysis.json + analysis.md next to the other reports. Anything it
cannot compute degrades to status "unavailable" with a reason; the
controller additionally guards the call so analyst failure is fail-open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roast_my_harness.files import atomic_write_text
from roast_my_harness.report.charts import outcome_label
from roast_my_harness.report.statistics import (
    resolved_rows,
    task_rates,
    variant_type,
)
from roast_my_harness.telemetry.result import fnum_or_none


def _arm_scores(rows: list[dict]) -> dict[str, dict[str, float]]:
    """resolved/total plus the mean of per-task rates, per arm."""
    totals: dict[str, dict[str, float]] = {}
    rates = task_rates(rows)
    partials: dict[str, dict[str, list[float]]] = {}
    near: dict[str, float] = {}
    for row in resolved_rows(rows):
        variant = str(row["variant"])
        arm = totals.setdefault(variant, {"resolved": 0.0, "total": 0.0})
        arm["resolved"] += int(row["resolved"])
        arm["total"] += 1
        if outcome_label(row) == "near-miss":
            near[variant] = near.get(variant, 0.0) + 1.0
        value = fnum_or_none(row.get("partial"))
        if value is not None:
            task_partials = partials.setdefault(variant, {})
            task_partials.setdefault(str(row["task"]), []).append(value)
    for variant, arm in totals.items():
        per_task = list(rates.get(variant, {}).values())
        arm["score"] = sum(per_task) / len(per_task) if per_task else 0.0
        task_means = [sum(v) / len(v) for v in partials.get(variant, {}).values()]
        arm["mean_partial"] = sum(task_means) / len(task_means) if task_means else 0.0
        arm["near_miss"] = near.get(variant, 0.0)
    return totals


def _separated(entry: dict, control_entry: dict | None) -> bool | None:
    """True when the arm's rate CI does not overlap the control CI."""
    if control_entry is None:
        return None
    try:
        lo, hi = float(entry["lo"]), float(entry["hi"])
        clo, chi = float(control_entry["lo"]), float(control_entry["hi"])
    except (KeyError, TypeError, ValueError):
        return None
    return hi < clo or chi < lo


def analyze_run(run_dir: Path) -> dict[str, Any]:
    """Deterministic findings for a finished run. Never raises."""
    try:
        return _analyze(run_dir)
    except Exception as error:
        return {
            "status": "unavailable",
            "reason": f"{type(error).__name__}: {error}",
        }


def _analyze(run_dir: Path) -> dict[str, Any]:
    summary_path = Path(run_dir) / "summary.json"
    summary = json.loads(summary_path.read_text())
    rows = summary.get("trials", [])
    provenance = summary.get("provenance", {})
    spec = provenance.get("spec", {})
    spec_variants = spec.get("variants", [])
    hypothesis = spec.get("hypothesis", "") or ""
    arms = _arm_scores(rows)
    types = {
        variant: variant_type(variant, spec_variants) for variant in arms
    }
    control = arms.get("control", {})
    control_score = control.get("score", 0.0)
    contenders = {
        variant: arm for variant, arm in arms.items() if variant != "control"
    }
    best_name = max(contenders, key=lambda v: contenders[v]["score"], default=None)
    best = None
    if best_name is not None:
        arm = contenders[best_name]
        best = {
            "variant": best_name,
            "type": types.get(best_name, "unknown"),
            "score": arm["score"],
            "delta_pp_vs_control": 100 * (arm["score"] - control_score),
        }
    by_stratum: dict[str, dict[str, dict]] = {}
    for entry in summary.get("stratified", []):
        by_stratum.setdefault(str(entry["stratum"]), {})[str(entry["variant"])] = entry
    strata = []
    for stratum, entries in by_stratum.items():
        control_entry = entries.get("control")
        leaders = sorted(
            (v for v in entries if v != "control"),
            key=lambda v: entries[v].get("rate", 0.0),
            reverse=True,
        )
        strata.append(
            {
                "stratum": stratum,
                "leader": leaders[0] if leaders else None,
                "arms": {
                    variant: {
                        "passed": entry.get("passed"),
                        "total": entry.get("total"),
                        "rate": entry.get("rate"),
                        "delta_pp_vs_control": entry.get("delta_pp_vs_control"),
                        "separated_from_control": _separated(entry, control_entry),
                    }
                    for variant, entry in sorted(entries.items())
                },
            }
        )
    return {
        "status": "ok",
        "experiment_id": provenance.get("experiment_id"),
        "hypothesis": hypothesis,
        "hypothesis_present": bool(hypothesis.strip()),
        "arms": {
            variant: {
                "type": types.get(variant, "unknown"),
                "resolved": int(arm["resolved"]),
                "total": int(arm["total"]),
                "score": arm["score"],
                "mean_partial": arm["mean_partial"],
                "near_miss": int(arm["near_miss"]),
            }
            for variant, arm in sorted(arms.items())
        },
        "best_arm": best,
        "strata": strata,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    """Human-readable analysis.md. Pure rendering, no model judgment."""
    if payload.get("status") != "ok":
        return (
            "# Analysis\n\n"
            f"Analysis unavailable: {payload.get('reason', 'unknown')}.\n"
            "The run reports above stand on their own.\n"
        )
    lines = ["# Analysis", ""]
    lines.append("Deterministic summary of summary.json; no model judgment.")
    lines.append("")
    hypothesis = payload.get("hypothesis") or ""
    if hypothesis.strip():
        lines.append("## Frozen hypothesis")
        lines.append("")
        lines.append(f"> {hypothesis.strip()}")
        lines.append("")
        lines.append("Compare the deltas below against it by hand; the")
        lines.append("analyst does not parse prose.")
        lines.append("")
    else:
        lines.append("No hypothesis was frozen at authoring time.")
        lines.append("")
    lines.append("## Arms")
    lines.append("")
    lines.append("| arm | type | resolved | score | mean partial | near misses |")
    lines.append("|---|---|---|---|---|---|")
    for variant, arm in payload.get("arms", {}).items():
        lines.append(
            f"| {variant} | {arm['type']} | "
            f"{arm['resolved']}/{arm['total']} | {100 * arm['score']:.1f}% | "
            f"{100 * arm.get('mean_partial', 0.0):.1f}% | {arm.get('near_miss', 0)} |"
        )
    lines.append("")
    best = payload.get("best_arm")
    if best:
        lines.append(
            f"Best non-control arm: {best['variant']} "
            f"({100 * best['score']:.1f}%, "
            f"{best['delta_pp_vs_control']:+.1f}pp vs control)."
        )
        lines.append("")
    for stratum in payload.get("strata", []):
        lines.append(f"## Stratum {stratum['stratum']}")
        lines.append("")
        if stratum["leader"]:
            lines.append(f"Leader: {stratum['leader']}.")
        for variant, arm in stratum["arms"].items():
            if variant == "control":
                continue
            delta = arm["delta_pp_vs_control"]
            delta_text = f"{delta:+.1f}pp" if delta is not None else "n/a"
            sep = arm["separated_from_control"]
            sep_text = (
                "separated"
                if sep is True
                else ("overlapping" if sep is False else "no control baseline")
            )
            lines.append(f"- {variant}: {delta_text} vs control ({sep_text}).")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_analysis(run_dir: Path) -> Path:
    """Write analysis.json + analysis.md. Returns the JSON path."""
    payload = analyze_run(Path(run_dir))
    out = Path(run_dir) / "analysis.json"
    atomic_write_text(out, json.dumps(payload, indent=2) + "\n")
    atomic_write_text(Path(run_dir) / "analysis.md", render_markdown(payload))
    return out
