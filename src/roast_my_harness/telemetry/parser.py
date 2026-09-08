"""Final-event telemetry parser. Folds complete files from byte zero."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from roast_my_harness.runner.patch_guard import step_dirs

READ_TOOL_NAMES = {"read"}


# ---------------------------------------------------------------- ranges ---


def _read_range(args: dict) -> tuple[str, int, int | None]:
    """(path, start_line, end_line) of a read tool call; end None = open."""
    path = args.get("path") or args.get("file_path") or ""
    start = int(args.get("offset") or 1)
    limit = args.get("limit")
    end = None if limit is None else start + int(limit) - 1
    return posixpath.normpath(str(path)), start, end


def _ranges_overlap(a: tuple[int, int | None], b: tuple[int, int | None]) -> bool:
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
    return {
        k: 0
        for k in (
            "tool_calls",
            "read_calls",
            "read_rereads",
            "read_overlap_rereads",
            "distinct_read_files",
        )
    }


def new_event_metrics() -> dict[str, float]:
    return {
        k: 0
        for k in (
            "llm_calls",
            "llm_ttft_sec",
            "turn_time_sec",
            "cache_write_tokens",
            "reasoning_tokens",
            "cm_llm_calls",
            "cm_input_tokens",
            "cm_output_tokens",
            "cm_attributions",
            "cm_errors",
            "cm_search_calls",
            "cm_rehydrate_calls",
            "peak_input_cache_tokens",
            "avg_input_cache_tokens",
        )
    }


def _bisect_starts(merged: list, x: int, right: bool) -> int:
    lo, hi = 0, len(merged)
    while lo < hi:
        mid = (lo + hi) // 2
        if merged[mid][0] < x or (right and merged[mid][0] == x):
            lo = mid + 1
        else:
            hi = mid
    return lo


def _merge_find(merged: list, start: int, end: int | None) -> bool:
    """True when (start, end) overlaps any interval in merged.

    merged is sorted by start with pairwise-disjoint integer ranges. An open
    range overlaps every interval starting at or after start, so only the
    predecessor needs a comparison in that case. Each candidate reuses
    _ranges_overlap, keeping None-end-means-infinity semantics in one place.
    """
    i = _bisect_starts(merged, start, True)
    if end is None:
        if i < len(merged):
            return True
        i -= 1
        if i < 0:
            return False
        iv = merged[i]
        return _ranges_overlap((start, end), (iv[0], iv[1]))
    lo = max(0, i - 1)
    for iv in merged[lo:]:
        if iv[0] > end:
            break
        if _ranges_overlap((start, end), (iv[0], iv[1])):
            return True
    return False


def _merge_insert(merged: list, start: int, end: int | None) -> None:
    """Insert (start, end) into merged, fusing overlap and integer adjacency.

    Adjacent integer ranges fuse safely: overlap queries test for a shared
    integer point, and the union of adjacent integer ranges shares a point
    with a query exactly when one of the parts does.
    """
    new_start, new_end = start, end
    i = _bisect_starts(merged, start, False)
    lo = i
    if i > 0:
        prev_end = merged[i - 1][1]
        if prev_end is None or new_start <= prev_end + 1:  # type: ignore[operator]
            lo = i - 1
    hi = lo
    while hi < len(merged):
        iv = merged[hi]
        if new_end is not None and iv[0] > new_end + 1:
            break
        if iv[0] < new_start:
            new_start = iv[0]
        if new_end is None or iv[1] is None:
            new_end = None
        elif iv[1] > new_end:
            new_end = iv[1]
        hi += 1
    merged[lo:hi] = [[new_start, new_end]]


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
    seen: dict[str, dict] = m.setdefault("_seen", {})
    entry = seen.get(path)
    if entry is None:
        entry = seen[path] = {"n": 0, "merged": []}
        m["distinct_read_files"] += 1
    elif entry["n"]:
        m["read_rereads"] += 1
        if _merge_find(entry["merged"], start, end):
            m["read_overlap_rereads"] += 1
    else:
        m["distinct_read_files"] += 1
    entry["n"] += 1
    _merge_insert(entry["merged"], start, end)


def fold_event(m: dict[str, Any], event: dict[str, Any]) -> None:
    """Fold one pi event into event metrics (tool + turn + custom counters)."""
    t = event.get("type")
    if t == "turn_end":
        usage = (event.get("message") or {}).get("usage") or {}
        if usage:
            m["llm_calls"] += 1
            m["cache_write_tokens"] += int(usage.get("cacheWrite") or 0)
            m["reasoning_tokens"] += int(
                usage.get("reasoning") or usage.get("reasoningTokens") or 0
            )
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
        m["turn_time_sec"] += max(0.0, (ts - m["_turn_start_ms"]) / 1000.0)
        m["_turn_start_ms"] = None


def event_log_pairs(trial_dir: Path) -> list[tuple[Path, Path]]:
    """(events, sidecar) log pairs in fold order.

    Single-step trials read the trial-root agent/ pair (today's behavior).
    Once a staged trial relocates its first step, the per-step pairs under
    steps/<name>/agent/ are authoritative and the trial-root pair is
    skipped: it holds only the in-progress step's partial events, which
    would double-count after relocation into steps/.
    """
    steps = step_dirs(trial_dir)
    if steps:
        return [
            (
                step / "agent" / "pi-events.jsonl",
                step / "agent" / "pi-event-times.log",
            )
            for step in steps
        ]
    agent = trial_dir / "agent"
    return [(agent / "pi-events.jsonl", agent / "pi-event-times.log")]


def final_event_metrics(trial_dir: Path) -> dict[str, Any]:
    """Authoritative per-trial telemetry. Rereads from byte zero."""
    m: dict[str, Any] = {**new_event_metrics(), **new_tool_metrics()}
    for events, sidecar in event_log_pairs(trial_dir):
        if events.is_file():
            for line in _safe_lines(events):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fold_event(m, event)
                if event.get("type") == "tool_execution_start":
                    fold_tool_event(m, event)
        if sidecar.is_file():
            for line in _safe_lines(sidecar):
                fold_sidecar_line(m, line)
    return finalize_metrics(m)


def _new_file_part() -> dict[str, int]:
    return {"off": 0, "size": 0, "mtime_ns": 0}


def _file_part_valid(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and all(isinstance(part.get(k), int) for k in ("off", "size", "mtime_ns"))
        and part["off"] >= 0
        and part["size"] >= 0
    )


def new_fold_state() -> dict[str, Any]:
    """Empty incremental fold state for one trial dir (JSON-compatible)."""
    return {
        "work": {**new_event_metrics(), **new_tool_metrics()},
        "mode": "root",
        "events": _new_file_part(),
        "sidecar": _new_file_part(),
        "step_files": {},
    }


def fold_state_valid(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    work = state.get("work")
    if not isinstance(work, dict):
        return False
    if state.get("mode", "root") not in ("root", "steps"):
        return False
    for key in ("events", "sidecar"):
        if not _file_part_valid(state.get(key)):
            return False
    step_files = state.get("step_files", {})
    if not isinstance(step_files, dict):
        return False
    for sub in step_files.values():
        if not isinstance(sub, dict):
            return False
        if not _file_part_valid(sub.get("events")):
            return False
        if not _file_part_valid(sub.get("sidecar")):
            return False
    seen = work.get("_seen", {})
    if not isinstance(seen, dict):
        return False
    for entry in seen.values():
        if not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("n"), int):
            return False
        merged = entry.get("merged")
        if not isinstance(merged, list):
            return False
        for iv in merged:
            if not isinstance(iv, list) or len(iv) != 2:
                return False
            if not isinstance(iv[0], int):
                return False
            if iv[1] is not None and not isinstance(iv[1], int):
                return False
    return True


def _fold_new_bytes(m: dict[str, Any], path: Path, part: dict[str, int], is_events: bool) -> int:
    """Fold bytes appended since part["off"]; returns lines folded.

    Only newline-terminated lines fold; a trailing partial line stays
    unfolded until the writer completes it. A shrunk file means rewrite, so
    the caller refolds the trial from zero instead.
    """
    try:
        st = path.stat()
        size, mtime_ns = st.st_size, st.st_mtime_ns
    except OSError:
        part["off"], part["size"], part["mtime_ns"] = 0, 0, 0
        return 0
    if (mtime_ns, size) == (part["mtime_ns"], part["size"]):
        return 0
    off = part["off"]
    if off > size:
        raise _FileRewritten
    try:
        with path.open("rb") as f:
            f.seek(off)
            chunk = f.read()
    except OSError:
        return 0
    lines = chunk.splitlines(keepends=True)
    folded = 0
    pos = off
    for raw in lines:
        if not raw.endswith(b"\n"):
            break
        pos += len(raw)
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_events:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            fold_event(m, event)
            if event.get("type") == "tool_execution_start":
                fold_tool_event(m, event)
        else:
            fold_sidecar_line(m, line)
        folded += 1
    part["off"], part["size"], part["mtime_ns"] = pos, size, mtime_ns
    return folded


class _FileRewritten(Exception):
    pass


def _fold_pair(
    m: dict[str, Any],
    events_path: Path,
    events_part: dict[str, int],
    sidecar_path: Path,
    sidecar_part: dict[str, int],
) -> int:
    """Fold one (events, sidecar) pair; returns lines folded."""
    folded = _fold_new_bytes(m, events_path, events_part, True)
    folded += _fold_new_bytes(m, sidecar_path, sidecar_part, False)
    return folded


def fold_trial_incremental(
    trial_dir: Path, state: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Fold a trial's event logs, reusing state across polls.

    Returns (finalized metrics, updated state, lines folded this call). An
    invalid or missing state folds from zero. A shrunk event file means the
    trial was rewritten, so the fold restarts from zero rather than reuse
    stale counters. The returned metrics are a finalized copy; the state
    keeps the working counters for the next poll.

    Staged trials fold per-step pairs (see event_log_pairs) under a
    "steps" mode state; switching modes restarts from zero so relocated
    bytes are never double-counted.
    """
    import copy as _copy

    pairs = event_log_pairs(trial_dir)
    want_mode = "steps" if step_dirs(trial_dir) else "root"
    if not fold_state_valid(state) or state.get("mode", "root") != want_mode:  # type: ignore[union-attr]
        state = new_fold_state()
        state["mode"] = want_mode
    assert state is not None
    m = state["work"]
    try:
        folded = _fold_pairs(state, pairs, want_mode, m)
    except _FileRewritten:
        state = new_fold_state()
        state["mode"] = want_mode
        m = state["work"]
        folded = _fold_pairs(state, pairs, want_mode, m)
    return finalize_metrics(_copy.deepcopy(m)), state, folded


def _fold_pairs(
    state: dict[str, Any],
    pairs: list[tuple[Path, Path]],
    want_mode: str,
    m: dict[str, Any],
) -> int:
    """Fold every pair into m with per-pair offsets; returns lines folded."""
    folded = 0
    if want_mode == "steps":
        files = state.setdefault("step_files", {})
        for events_path, sidecar_path in pairs:
            key = events_path.parent.parent.name
            sub = files.get(key)
            if not isinstance(sub, dict):
                sub = {}
                files[key] = sub
            if not _file_part_valid(sub.get("events")):
                sub["events"] = _new_file_part()
            if not _file_part_valid(sub.get("sidecar")):
                sub["sidecar"] = _new_file_part()
            folded += _fold_pair(
                m, events_path, sub["events"], sidecar_path, sub["sidecar"]
            )
    else:
        events_path, sidecar_path = pairs[0]
        folded += _fold_pair(
            m, events_path, state["events"], sidecar_path, state["sidecar"]
        )
    return folded


def _safe_lines(path: Path) -> Iterator[str]:
    try:
        with path.open() as f:
            yield from f
    except OSError:
        return
