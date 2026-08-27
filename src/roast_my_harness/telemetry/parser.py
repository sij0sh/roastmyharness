"""Final-event telemetry parser. Folds complete files from byte zero."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterator
from pathlib import Path
from typing import Any

READ_TOOL_NAMES = {"read"}


# ---------------------------------------------------------------- ranges ---

def _read_range(args: dict) -> tuple[str, int, int | None]:
    """(path, start_line, end_line) of a read tool call; end None = open."""
    path = args.get("path") or args.get("file_path") or ""
    start = int(args.get("offset") or 1)
    limit = args.get("limit")
    end = None if limit is None else start + int(limit) - 1
    return posixpath.normpath(str(path)), start, end


def _ranges_overlap(
    a: tuple[int, int | None], b: tuple[int, int | None]
) -> bool:
    """Line-range overlap; None end is +infinity."""
    a1, a2 = a
    b1, b2 = b
    if a2 is None:
        a2 = float("inf")  # type: ignore[assignment]
    if b2 is None:
        b2 = float("inf")  # type: ignore[assignment]
    return a1 <= b2 and b1 <= a2  # type: ignore[operator]


# ------------------------------------------------------------- metrics -----

def new_tool_metrics() -> dict[str, int]:
    return {k: 0 for k in (
        "tool_calls", "read_calls", "read_rereads",
        "read_overlap_rereads", "distinct_read_files",
    )}


def new_event_metrics() -> dict[str, float]:
    return {k: 0 for k in (
        "llm_calls", "llm_ttft_sec", "turn_time_sec",
        "cache_write_tokens", "reasoning_tokens", "cm_llm_calls",
        "cm_input_tokens", "cm_output_tokens", "cm_attributions",
        "cm_errors", "cm_search_calls", "cm_rehydrate_calls",
        "peak_input_cache_tokens", "avg_input_cache_tokens",
    )}


def fold_tool_event(m: dict[str, Any], event: dict[str, Any]) -> None:
    """Fold one tool_execution_start event into tool metrics."""
    m["tool_calls"] += 1
    name = event.get("toolName") or ""
    if name not in READ_TOOL_NAMES:
        return
    m["read_calls"] += 1
    path, start, end = _read_range(event.get("args") or {})
    if not path:
        return
    seen: dict[str, list[tuple[int, int | None]]] = m.setdefault("_seen", {})
    prior = seen.setdefault(path, [])
    if prior:
        m["read_rereads"] += 1
        if any(_ranges_overlap((start, end), p) for p in prior):
            m["read_overlap_rereads"] += 1
    else:
        m["distinct_read_files"] += 1
    prior.append((start, end))


def fold_event(m: dict[str, Any], event: dict[str, Any]) -> None:
    """Fold one pi event into event metrics (tool + turn + custom counters)."""
    t = event.get("type")
    if t == "turn_end":
        usage = (event.get("message") or {}).get("usage") or {}
        if usage:
            m["llm_calls"] += 1
            m["cache_write_tokens"] += int(usage.get("cacheWrite") or 0)
            m["reasoning_tokens"] += int(usage.get("reasoning") or 0)
            peak = int(usage.get("input") or 0) + int(usage.get("cacheRead") or 0)
            m["_ctx_sum"] = m.get("_ctx_sum", 0) + peak
            m["_ctx_n"] = m.get("_ctx_n", 0) + 1
            m["peak_input_cache_tokens"] = max(m["peak_input_cache_tokens"], peak)
    elif t == "entry_appended":
        entry = event.get("entry") or {}
        ct = entry.get("customType", "")
        if ct == "agentic-context-manager-summary":
            m["cm_attributions"] += 1
            u = (entry.get("data") or {}).get("usage") or {}
            if u:
                m["cm_llm_calls"] += 1
                m["cm_input_tokens"] += int(u.get("input") or 0)
                m["cm_output_tokens"] += int(u.get("output") or 0)
        elif ct == "agentic-context-manager-error":
            m["cm_errors"] += 1
    elif t == "tool_execution_start":
        name = event.get("toolName") or ""
        if name == "search_context":
            m["cm_search_calls"] += 1
        elif name == "rehydrate_context":
            m["cm_rehydrate_calls"] += 1


def finalize_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """Strip scratch state and round derived values."""
    ctx_sum, ctx_n = m.pop("_ctx_sum", 0), m.pop("_ctx_n", 0)
    m.pop("_seen", None)
    if ctx_n:
        m["avg_input_cache_tokens"] = round(ctx_sum / ctx_n, 1)
    m["llm_ttft_sec"] = round(float(m.get("llm_ttft_sec", 0)), 1)
    m["turn_time_sec"] = round(float(m.get("turn_time_sec", 0)), 1)
    return m


def fold_sidecar_line(m: dict[str, Any], line: str) -> None:
    """Fold one '<epoch_ms> <event json>' arrival-time line into timings.

    ttft = turn_start -> first streamed token; turn_time = turn_start ->
    turn_end (LLM call plus tool execution).
    """
    try:
        ts_str, payload = line.split(" ", 1)
        ts = int(ts_str)
        event = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return
    t = event.get("type")
    if t == "turn_start":
        m["_turn_start_ms"], m["_first_update_ms"] = ts, None
    elif (
        t == "message_update"
        and m.get("_first_update_ms") is None
        and m.get("_turn_start_ms") is not None
    ):
        m["_first_update_ms"] = ts
        m["llm_ttft_sec"] += max(0.0, (ts - m["_turn_start_ms"]) / 1000.0)
    elif t == "turn_end" and m.get("_turn_start_ms") is not None:
        m["turn_time_sec"] += max(
            0.0, (ts - m["_turn_start_ms"]) / 1000.0
        )
        m["_turn_start_ms"] = None


def final_event_metrics(trial_dir: Path) -> dict[str, Any]:
    """Authoritative per-trial telemetry. Rereads from byte zero."""
    m: dict[str, Any] = {**new_event_metrics(), **new_tool_metrics()}
    events = trial_dir / "agent" / "pi-events.jsonl"
    if events.is_file():
        for line in _safe_lines(events):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            fold_event(m, event)
            if event.get("type") == "tool_execution_start":
                fold_tool_event(m, event)
    sidecar = trial_dir / "agent" / "pi-event-times.log"
    if sidecar.is_file():
        for line in _safe_lines(sidecar):
            fold_sidecar_line(m, line)
    return finalize_metrics(m)


def _safe_lines(path: Path) -> Iterator[str]:
    try:
        with path.open() as f:
            yield from f
    except OSError:
        return

