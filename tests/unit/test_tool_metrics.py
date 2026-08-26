"""Ported from DSE-tests tests/test_collect_metrics.py (same semantics)."""

from __future__ import annotations

from roast_my_harness.telemetry.parser import (
    _ranges_overlap,
    _read_range,
    fold_tool_event,
    new_tool_metrics,
)


def read_call(path, offset=None, limit=None):
    args = {"path": path}
    if offset is not None:
        args["offset"] = offset
    if limit is not None:
        args["limit"] = limit
    return {"type": "tool_execution_start", "toolName": "read", "args": args}


def tool_metrics_from(events):
    m = new_tool_metrics()
    for e in events:
        fold_tool_event(m, e)
    return m


def test_read_range():
    assert _read_range({"path": "a.go", "offset": 1, "limit": 260}) == ("a.go", 1, 260)
    assert _read_range({"path": "a.go"}) == ("a.go", 1, None)
    assert _read_range({"path": "a.go", "offset": 100}) == ("a.go", 100, None)
    assert _read_range({"path": "./a.go", "offset": 10, "limit": 5}) == ("a.go", 10, 14)


def test_overlap():
    assert _ranges_overlap((1, 100), (50, 150))
    assert _ranges_overlap((1, 100), (100, 200))
    assert not _ranges_overlap((1, 99), (100, 200))
    assert _ranges_overlap((10, None), (5000, 6000))
    assert _ranges_overlap((1, 10), (5, None))
    assert not _ranges_overlap((1, 10), (11, None))


def test_tool_metrics_counts():
    m = tool_metrics_from([
        read_call("a.go", 1, 100),
        read_call("b.go", 1, 100),
        read_call("a.go", 50, 100),
        read_call("a.go", 500, 100),
        read_call("a.go", 1, 100),
        {"type": "tool_execution_start", "toolName": "bash",
         "args": {"command": "ls"}},
        {"type": "tool_execution_start", "toolName": "edit", "args": {"path": "a.go"}},
        {"type": "tool_execution_end"},
    ])
    assert m["tool_calls"] == 8
    assert m["read_calls"] == 5
    assert m["read_rereads"] == 3
    assert m["read_overlap_rereads"] == 2
    assert m["distinct_read_files"] == 2
