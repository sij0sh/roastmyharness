"""Reconciliation over synthetic pier job trees."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.runner.reconcile import missing_tasks, reconcile_variant


def make_trial(jobs: Path, variant: str, task: str, reward=1.0, exception=None):
    trial = jobs / variant / "2026-01-01__00-00-00" / f"{task}__ABC123"
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    result = {
        "task_name": task,
        "verifier_result": {} if exception else {"rewards": {"reward": reward}},
        "exception_info": {"exception_type": exception} if exception else {},
    }
    (trial / "result.json").write_text(json.dumps(result))
    return trial


def test_pass_fail_error(tmp_path: Path):
    jobs = tmp_path / "jobs"
    make_trial(jobs, "a", "t1", reward=1.0)
    make_trial(jobs, "a", "t2", reward=0.0)
    make_trial(jobs, "a", "t3", exception="AgentTimeoutError")
    cells = reconcile_variant("a", jobs / "a", {"t1", "t2", "t3"})
    assert cells["t1"].status == "pass"
    assert cells["t2"].status == "fail"
    assert cells["t3"].status == "error"


def test_job_level_result_ignored(tmp_path: Path):
    jobs = tmp_path / "jobs" / "a" / "2026-01-01__00-00-00"
    jobs.mkdir(parents=True)
    (jobs / "result.json").write_text(json.dumps({"job": True}))
    assert reconcile_variant("a", jobs.parent, set()) == {}


def test_newest_attempt_wins(tmp_path: Path):
    trial = make_trial(tmp_path / "jobs", "a", "t1", reward=0.0)
    import os
    import time

    os.utime(trial / "result.json", (1000, 1000))
    make_trial(tmp_path / "jobs", "a", "t1", reward=1.0)
    time.sleep(0.01)
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1"})
    assert cells["t1"].status == "pass"


def test_missing_tasks(tmp_path: Path):
    make_trial(tmp_path / "jobs", "a", "t1")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1", "t2", "t3"})
    assert missing_tasks(cells, ["t1", "t2", "t3"]) == ["t2", "t3"]
