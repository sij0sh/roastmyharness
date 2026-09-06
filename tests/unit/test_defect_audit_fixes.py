"""Repro + regression tests for defect-audit P0/P1 fixes (C1/C2/C3/C5)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from roast_my_harness.auth import staging
from roast_my_harness.runner import reconcile as rec


def _trial(jobs: Path, variant: str, stamp_dir: str, task_dir: str,
           task_name=None, reward=1.0):
    trial = jobs / variant / stamp_dir / task_dir
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    result: dict = {
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": {},
    }
    if task_name is not None:
        result["task_name"] = task_name
    (trial / "result.json").write_text(json.dumps(result))
    return trial


# C1 — dir-authoritative reconcile -------------------------------------

def test_c1_namespaced_collision_keys_under_dir(tmp_path: Path, caplog):
    jobs = tmp_path / "jobs"
    _trial(jobs, "v", "2026-01-01__00-00-00", "real-task__ABC123",
           task_name="ns/other", reward=1.0)
    with caplog.at_level(logging.WARNING, logger="roast_my_harness.runner.reconcile"):
        cells = rec.reconcile_variant("v", jobs / "v", {"real-task", "other"})
    assert set(cells) == {"real-task"}
    assert cells["real-task"].status == "pass"
    assert "real-task__ABC123" in cells["real-task"].job_path
    assert any("reconcile conflict" in r.message for r in caplog.records)


def test_c1_regressions(tmp_path: Path):
    jobs = tmp_path / "jobs"
    _trial(jobs, "v", "2026-01-01__00-00-00", "task-a__A",
           task_name="task-a", reward=1.0)
    cells = rec.reconcile_variant("v", jobs / "v", {"task-a", "task-b"})
    assert cells["task-a"].status == "pass"
    _trial(jobs, "v", "2026-01-01__00-00-00", "task-a__B",
           task_name="ns/task-a", reward=0.0)
    os.utime(jobs / "v" / "2026-01-01__00-00-00" / "task-a__B" / "result.json",
             (3000, 3000))
    os.utime(jobs / "v" / "2026-01-01__00-00-00" / "task-a__A" / "result.json",
             (1000, 1000))
    cells = rec.reconcile_variant("v", jobs / "v", {"task-a", "task-b"})
    assert cells["task-a"].status == "fail"
    _trial(jobs, "v", "2026-01-01__00-00-00", "weird__Z",
           task_name="ns/unknown-xyz", reward=1.0)
    cells = rec.reconcile_variant("v", jobs / "v", {"task-a"})
    assert "unknown-xyz" not in cells and "ns/unknown-xyz" not in cells


# C5 — deterministic newest-wins ----------------------------------------

def test_c5_tie_is_deterministic_not_sorted_last(tmp_path: Path):
    jobs = tmp_path / "jobs"
    older = _trial(jobs, "a", "2026-01-02__00-00-00", "t1__ZZZ", reward=0.0)
    newer = _trial(jobs, "a", "2026-01-01__00-00-00", "t1__AAA", reward=1.0)
    for p in (older / "result.json", newer / "result.json"):
        os.utime(p, (2000, 2000))
    first = rec.reconcile_variant("a", jobs / "a", {"t1"})
    second = rec.reconcile_variant("a", jobs / "a", {"t1"})
    assert first["t1"].job_path == second["t1"].job_path
    assert "t1__AAA" in first["t1"].job_path


def test_c5_distinct_mtime_newest_wins(tmp_path: Path):
    jobs = tmp_path / "jobs"
    old = _trial(jobs, "a", "2026-01-01__00-00-00", "t1__OLD", reward=0.0)
    new = _trial(jobs, "a", "2026-01-02__00-00-00", "t1__NEW", reward=1.0)
    os.utime(old / "result.json", (1000, 1000))
    os.utime(new / "result.json", (2000, 2000))
    cells = rec.reconcile_variant("a", jobs / "a", {"t1"})
    assert cells["t1"].status == "pass"


# C3 — stale staging sweep ------------------------------------------------

def test_c3_sweep_scans_then_deletes(tmp_path: Path):
    run_dir = tmp_path / "run"
    staged = run_dir / "staging" / "a"
    staged.mkdir(parents=True)
    (staged / "env.json").write_text(json.dumps({"K": "sk-live-abc"}) + "\n")
    os.chmod(staged / "env.json", 0o600)
    hits = staging.sweep_stale_staging(run_dir)
    assert hits and any("env.json" in h for h in hits)
    assert not (run_dir / "staging").exists()


def test_c3_clean_start_noop(tmp_path: Path):
    assert staging.sweep_stale_staging(tmp_path / "run") == []


def test_c3_controller_sweep_emits_record(tmp_path: Path):
    from roast_my_harness.runner.controller import ExperimentController

    run_dir = tmp_path / "run"
    staged = run_dir / "staging" / "a"
    staged.mkdir(parents=True)
    (staged / "env.json").write_text(json.dumps({"K": "sk-live-abc"}) + "\n")
    ctrl = object.__new__(ExperimentController)
    ctrl.run_dir = run_dir
    from roast_my_harness.observability import RunLogger

    ctrl._logger = RunLogger(run_dir / "logs" / "run.jsonl", "exp-test")
    ctrl._sweep_stale_staging()
    assert not (run_dir / "staging").exists()
    log = (run_dir / "logs" / "run.jsonl").read_text()
    assert "stale-staging-sweep" in log


# C2 — cancel guard -------------------------------------------------------

def test_c2_throw_if_cancelled(tmp_path: Path):
    from roast_my_harness.runner.controller import ExperimentController

    ctrl = object.__new__(ExperimentController)
    ctrl._cancel_event = asyncio.Event()
    ctrl._throw_if_cancelled()
    ctrl._cancel_event.set()
    try:
        ctrl._throw_if_cancelled()
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")


def test_c2_prelaunch_guard_cancels_without_launch(tmp_path: Path):
    import asyncio as _asyncio

    from roast_my_harness.runner.controller import ExperimentController

    ctrl = object.__new__(ExperimentController)
    ctrl._cancel_event = _asyncio.Event()
    ctrl._cancel_event.set()
    calls: list[str] = []

    async def fake_cancel(state: str):
        calls.append(state)
        ctrl.state = state

    async def fake_launch():
        calls.append("launch")
        raise AssertionError("must not launch when cancelled")

    ctrl._cancel = fake_cancel  # type: ignore[method-assign]
    ctrl._launch = fake_launch  # type: ignore[method-assign]
    ctrl.state = "READY"
    final = _asyncio.new_event_loop().run_until_complete(ExperimentController.run(ctrl))
    assert final == "CANCELLED"
    assert "launch" not in calls


def test_c2_sync_handler_sets_flag_synchronously():
    import signal as _signal

    from roast_my_harness.runner.signals import install_sync_cancel_handlers

    hits: list[str] = []
    cleanup = install_sync_cancel_handlers(lambda: hits.append("x"))
    try:
        handler = _signal.getsignal(_signal.SIGINT)
        assert hits == []
        handler(_signal.SIGINT, None)
        assert hits == ["x"]
    finally:
        cleanup()
