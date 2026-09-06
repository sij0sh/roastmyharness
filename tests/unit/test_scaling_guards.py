"""Regression guards for the computational scaling audit repairs.

All guards are operation-count or structural; the single wall-clock budget at
the end is a generous secondary signal only. Audit ratio notes: the close
report wrote some guards as ops(4T)/ops(T); a linear repair gives 4 there, so
the doubling form ops(2T)/ops(T) is used (linear gives 2, quadratic gives 4).
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import roast_my_harness.telemetry.parser as parser_mod
from roast_my_harness.report.collect import (
    collect_rows,
    collect_rows_incremental,
    newest_result_paths,
    pending_statuses,
    scan_variant,
)
from roast_my_harness.telemetry.parser import (
    _ranges_overlap,
    fold_tool_event,
    new_tool_metrics,
)


def _write_trial(
    jobs: Path,
    variant: str,
    name: str,
    task: str,
    reward: float | None,
    lines: int,
    *,
    exception: str | None = None,
) -> Path:
    trial = jobs / variant / name
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    result: dict = {"task_name": task, "exception_info": {}}
    if reward is not None:
        result["verifier_result"] = {"rewards": {"reward": reward}}
    else:
        result["verifier_result"] = {"rewards": {}}
    if exception is not None:
        result["exception_info"] = {"exception_type": exception}
    (trial / "result.json").write_text(json.dumps(result))
    (trial / "agent" / "pi-events.jsonl").write_text(
        "".join(
            '{"type":"turn_end","message":{"usage":{"input":1,"cacheRead":0}}}\n'
            for _ in range(lines)
        )
    )
    return trial / "result.json"


def _reference_pending(variant_dir: Path, task_id: str) -> str:
    """Pre-repair per-task probe, kept as the equivalence oracle."""
    if not variant_dir.is_dir():
        return "."
    for trial_dir in variant_dir.rglob(f"{task_id}__*"):
        if trial_dir.is_dir() and not (trial_dir / "result.json").exists():
            return "~"
    return "."


class _ScandirCounter:
    entries = 0

    def __init__(self, iterator):
        self._it = iterator

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._it.close()
        return False

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self._it)
        _ScandirCounter.entries += 1
        return entry

    def close(self):
        self._it.close()


def _count_scandir_entries(fn):
    real = os.scandir
    _ScandirCounter.entries = 0

    def wrapper(path):
        return _ScandirCounter(real(path))

    os.scandir = wrapper
    try:
        fn()
    finally:
        os.scandir = real
    return _ScandirCounter.entries


def _count_rglob_calls(monkeypatch):
    calls: list = []
    real = Path.rglob

    def counting(self, pattern="*", case_sensitive=None):
        calls.append(1)
        if case_sensitive is None:
            return real(self, pattern)
        return real(self, pattern, case_sensitive=case_sensitive)

    monkeypatch.setattr(Path, "rglob", counting)
    return calls


def test_f1_single_enumeration_per_variant(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    _write_trial(jobs, "v0", "t1__1", "t1", 1.0, 2)
    calls = _count_rglob_calls(monkeypatch)
    scan_variant(jobs / "v0")
    assert calls == [1]


def test_f1_snapshot_equivalence(tmp_path):
    jobs = tmp_path / "jobs"
    variant = jobs / "v0"
    _write_trial(jobs, "v0", "t1__1", "t1", 1.0, 1)
    pending_dir = variant / "t2__1"
    (pending_dir / "agent").mkdir(parents=True)
    nested = variant / "nested" / "t3__7"
    nested.mkdir(parents=True)
    (variant / "we__ird__1").mkdir(parents=True)
    tasks = ["t1", "t2", "t3", "absent", "we__ird"]
    pending, _ = scan_variant(variant)
    got = pending_statuses(pending, tasks)
    for task in tasks:
        assert got[task] == _reference_pending(variant, task), task


def test_f1_scandir_ratio(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"

    def build(t):
        for i in range(t):
            _write_trial(jobs, "v0", f"t{i}__1", f"t{i}", 1.0, 1)

    build(16)
    real_rglob = Path.rglob
    monkeypatch.setattr(Path, "rglob", real_rglob)

    def work(t):
        pending, _ = scan_variant(jobs / "v0")
        pending_statuses(pending, [f"t{i}" for i in range(t)])

    small = _count_scandir_entries(lambda: work(8))
    big = _count_scandir_entries(lambda: work(16))
    assert big / small < 3


def _reference_counters(events):
    m = {
        k: 0
        for k in (
            "tool_calls",
            "read_calls",
            "read_rereads",
            "read_overlap_rereads",
            "distinct_read_files",
        )
    }
    seen: dict = {}
    for path, s, e in events:
        m["tool_calls"] += 1
        m["read_calls"] += 1
        prior = seen.setdefault(path, [])
        if prior:
            m["read_rereads"] += 1
            if any(_ranges_overlap((s, e), p) for p in prior):
                m["read_overlap_rereads"] += 1
        else:
            m["distinct_read_files"] += 1
        prior.append((s, e))
    return m


def _read_event(path, s, e):
    return {
        "type": "tool_execution_start",
        "toolName": "read",
        "args": {"path": path, "offset": s, **({} if e is None else {"limit": e - s + 1})},
    }


def test_f2_property_counters():
    rng = random.Random(20260906)
    for _ in range(100):
        events = []
        for _ in range(rng.randint(1, 30)):
            path = rng.choice(["a", "b"])
            s = rng.randint(1, 50)
            r = rng.random()
            e = None if r < 0.2 else s + rng.randint(0, 25)
            events.append((path, s, e))
        m = new_tool_metrics()
        for path, s, e in events:
            fold_tool_event(m, _read_event(path, s, e))
        m.pop("_seen", None)
        assert m == _reference_counters(events)


def test_f2_opcount_ratio(monkeypatch):
    counts = []

    def counting(a, b):
        counts.append(1)
        return _ranges_overlap(a, b)

    monkeypatch.setattr(parser_mod, "_ranges_overlap", counting)

    def fold_disjoint(size):
        del counts[:]
        m = new_tool_metrics()
        for i in range(size):
            fold_tool_event(m, _read_event("f", i * 10 + 1, i * 10 + 5))
        return len(counts)

    small = fold_disjoint(128)
    big = fold_disjoint(256)
    assert big / small < 2.5


def test_f3_batch_parses_once(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    tasks = [f"t{i}" for i in range(16)]
    for task in tasks:
        _write_trial(jobs, "v0", f"{task}__1", task, 1.0, 1)
    loads = []
    real_loads = json.loads

    def counting_loads(s, *a, **k):
        loads.append(1)
        return real_loads(s, *a, **k)

    monkeypatch.setattr(json, "loads", counting_loads)
    from roast_my_harness.report.collect import latest_result_path

    per_call = {t: latest_result_path(jobs, "v0", t) for t in tasks}
    per_call_loads = len(loads)
    del loads[:]
    batched = newest_result_paths(jobs / "v0", tasks)
    assert len(loads) == 16
    assert per_call_loads == 16 * 16
    assert batched == per_call


def test_f3_single_enumeration(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    _write_trial(jobs, "v0", "t1__1", "t1", 1.0, 1)
    calls = _count_rglob_calls(monkeypatch)
    newest_result_paths(jobs / "v0", ["t1"])
    assert calls == [1]


def _fold_counter(monkeypatch):
    total = [0]
    real_event = parser_mod.fold_event
    real_tool = parser_mod.fold_tool_event
    real_sidecar = parser_mod.fold_sidecar_line

    def event(m, e):
        total[0] += 1
        return real_event(m, e)

    def tool(m, e):
        total[0] += 1
        return real_tool(m, e)

    def sidecar(m, line):
        total[0] += 1
        return real_sidecar(m, line)

    monkeypatch.setattr(parser_mod, "fold_event", event)
    monkeypatch.setattr(parser_mod, "fold_tool_event", tool)
    monkeypatch.setattr(parser_mod, "fold_sidecar_line", sidecar)
    return total


def test_f4_repeat_poll_zero_folds(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    _write_trial(jobs, "v1", "t1__1", "t1", 1.0, 20)
    _write_trial(jobs, "v1", "t2__1", "t2", 0.0, 30)
    expected = collect_rows(jobs)
    total = _fold_counter(monkeypatch)
    cache: dict = {}
    rows1, cache, folded1, reused1 = collect_rows_incremental(jobs, cache)
    assert rows1 == expected
    assert (folded1, reused1) == (2, 0)
    first_total = total[0]
    assert first_total == 50
    rows2, cache, folded2, reused2 = collect_rows_incremental(jobs, cache)
    assert rows2 == rows1
    assert (folded2, reused2) == (0, 2)
    assert total[0] == first_total


def test_f4_appended_bytes_only(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    _write_trial(jobs, "v1", "run__1", "run", None, 10)
    total = _fold_counter(monkeypatch)
    cache: dict = {}
    _, cache, _, _ = collect_rows_incremental(jobs, cache)
    assert total[0] == 10
    events = jobs / "v1" / "run__1" / "agent" / "pi-events.jsonl"
    with events.open("a") as f:
        for _ in range(5):
            f.write('{"type":"turn_end","message":{"usage":{"input":1,"cacheRead":0}}}\n')
    _, cache, folded, _ = collect_rows_incremental(jobs, cache)
    assert folded == 1
    assert total[0] == 15


def test_f4_corrupt_cache_refolds(tmp_path):
    jobs = tmp_path / "jobs"
    _write_trial(jobs, "v1", "t1__1", "t1", 1.0, 5)
    cache = {"bogus": "entry", str(jobs / "v1" / "t1__1" / "result.json"): {"fold": {"work": {}}}}
    rows, cache, folded, _ = collect_rows_incremental(jobs, cache)
    assert rows == collect_rows(jobs)
    assert folded == 1


def test_poll_budget_wall_clock(tmp_path):
    jobs = tmp_path / "jobs"
    tasks = [f"task{t:03d}" for t in range(64)]
    for v in range(2):
        for t in tasks:
            _write_trial(jobs, f"v{v}", f"{t}__1", t, 1.0, 200)
    cache: dict = {}
    collect_rows_incremental(jobs, cache)
    start = time.monotonic()
    for v in range(2):
        pending, _ = scan_variant(jobs / f"v{v}")
        pending_statuses(pending, tasks)
    collect_rows_incremental(jobs, cache)
    assert time.monotonic() - start < 10
