"""Agent-portable tool metrics from the normalized ATIF trajectory.

Pi-specific event patterns (result-preview markers, context-manager entry
types) stay in telemetry.parser as enrichment. The generic columns here
derive from the ATIF trajectory every adapter writes post-run, so a new
agent gets failure metrics without new patterns:

- tool_calls / tool_results / tool_failures / tool_missing_results from
  step tool_calls[] and observation results[] (extra.is_error).
- read/reread/overlap reuse the parser's read analysis over the normalized
  calls, so read metrics mean the same thing for every agent whose tools
  expose file reads.

Absent or corrupt trajectory: the generic failure counters read zero and
pi-event values stand. Zero here means "no normalized trajectory", not
"no failures"; the pi-pattern attribution survives under custom_metrics
for runs that have pi events.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from roast_my_harness.runner.patch_guard import step_dirs
from roast_my_harness.telemetry.parser import fold_tool_event, new_tool_metrics

TRAJECTORY_FILENAME = "trajectory.json"

FAILURE_DEFAULTS: dict[str, Any] = {
    "tool_results": 0,
    "tool_failures": 0,
    "tool_failure_rate": 0.0,
    "tool_missing_results": 0,
}


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def iter_trajectory_calls(data: dict[str, Any]) -> list[tuple[str, dict, float | None]]:
    """(tool name, arguments, epoch seconds) across every step."""
    calls: list[tuple[str, dict, float | None]] = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        stamp = _epoch(step.get("timestamp"))
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            args = call.get("arguments") or {}
            calls.append(
                (
                    str(call.get("function_name") or "unknown"),
                    args if isinstance(args, dict) else {},
                    stamp,
                )
            )
    return calls


def iter_trajectory_results(data: dict[str, Any]) -> list[tuple[str, bool]]:
    """(tool name, is_error) across every step observation.

    Names resolve through source_call_id first, then the adapter-recorded
    tool name, then "unknown" — pi trajectories predate source_call_id and
    resolve through the second leg.
    """
    results: list[tuple[str, bool]] = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        id_to_name = {
            call.get("tool_call_id"): str(call.get("function_name") or "unknown")
            for call in step.get("tool_calls") or []
            if isinstance(call, dict) and call.get("tool_call_id")
        }
        observation = step.get("observation") or {}
        for result in observation.get("results") or []:
            if not isinstance(result, dict):
                continue
            extra = result.get("extra") or {}
            name = (
                id_to_name.get(result.get("source_call_id"))
                or extra.get("tool")
                or "unknown"
            )
            results.append((str(name), bool(extra.get("is_error"))))
    return results


def trajectory_metrics(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(generic scalars, custom by-name maps) from parsed trajectory data."""
    calls = iter_trajectory_calls(data)
    results = iter_trajectory_results(data)
    failures = [(name) for name, is_error in results if is_error]
    read_state = new_tool_metrics()
    for name, args, _stamp in calls:
        fold_tool_event(
            read_state,
            {"type": "tool_execution_start", "toolName": name, "args": args},
        )
    generic: dict[str, Any] = {
        "tool_calls": len(calls),
        "tool_results": len(results),
        "tool_failures": len(failures),
        "tool_failure_rate": round(len(failures) / len(results), 4) if results else 0.0,
        "tool_missing_results": max(0, len(calls) - len(results)),
        "read_calls": read_state["read_calls"],
        "read_rereads": read_state["read_rereads"],
        "read_overlap_rereads": read_state["read_overlap_rereads"],
        "distinct_read_files": read_state["distinct_read_files"],
    }
    custom = {
        "tool_calls_by_name": dict(sorted(Counter(name for name, _, _ in calls).items())),
        "tool_failures_by_name": dict(sorted(Counter(failures).items())),
    }
    return generic, custom


def _trajectory_paths(trial_dir: Path) -> list[Path]:
    """trajectory.json files in merge order.

    Single-step trials read the trial-root file (today's behavior). Once a
    staged trial relocates its first step, per-step files under
    steps/<name>/agent/ are authoritative and the trial-root file is
    skipped (same no-double-count rule as event_log_pairs).
    """
    steps = step_dirs(trial_dir)
    if steps:
        return [step / "agent" / TRAJECTORY_FILENAME for step in steps]
    return [trial_dir / "agent" / TRAJECTORY_FILENAME]


def trajectory_tool_metrics(
    trial_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Generic + custom metrics for one trial dir, plus trajectory mtime.

    Returns (generic, custom, traj_stamp_ns); traj_stamp_ns is 0 when the
    trajectory is absent. Callers overlay generic onto pi-event metrics
    only when a trajectory is present (traj_stamp_ns nonzero); otherwise
    generic is FAILURE_DEFAULTS and custom is empty. Staged trials merge
    per-step trajectories by concatenating their step observations, so
    rates and reread tracking span the whole session.
    """
    merged_steps: list[Any] = []
    stamp = 0
    for path in _trajectory_paths(trial_dir):
        try:
            file_stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        if isinstance(steps, list):
            merged_steps.extend(steps)
        stamp = max(stamp, file_stamp)
    if not merged_steps:
        return dict(FAILURE_DEFAULTS), {}, stamp
    generic, custom = trajectory_metrics({"steps": merged_steps})
    return generic, custom, stamp
