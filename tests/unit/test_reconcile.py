"""Reconciliation over synthetic pier job trees."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.runner.reconcile import (
    missing_replicates,
    reconcile_variant,
    replicate_of,
)


def make_trial(jobs: Path, variant: str, task: str, reward=1.0, exception=None,
               replicate: int | None = None):
    base = jobs / variant
    if replicate is not None:
        base = base / f"replicate-{replicate}"
    trial = base / "2026-01-01__00-00-00" / f"{task}__ABC123"
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
    assert cells[("t1", 1)].status == "pass"
    assert cells[("t2", 1)].status == "fail"
    assert cells[("t3", 1)].status == "error"
    assert cells[("t3", 1)].exception_type == "AgentTimeoutError"


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
    assert cells[("t1", 1)].status == "pass"


def test_missing_replicates(tmp_path: Path):
    make_trial(tmp_path / "jobs", "a", "t1")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1", "t2", "t3"})
    assert missing_replicates(cells, ["t1", "t2", "t3"], 1) == [("t2", 1), ("t3", 1)]
    assert missing_replicates(cells, ["t1", "t2"], 2) == [
        ("t1", 2), ("t2", 1), ("t2", 2),
    ]


def test_reward_json_fallback_preserved(tmp_path: Path):
    """Regression (RP2): reward.json reward must survive into the cell.

    result.json carries no verifier reward; the fallback file does. The
    old code recomputed reward from result.json afterwards and recorded 0.0.
    """
    trial = make_trial(tmp_path / "jobs", "a", "t1", reward=None)
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 0.75}))
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1"})
    assert cells[("t1", 1)].status == "fail"
    assert cells[("t1", 1)].reward == 0.75


def test_error_cell_reward_zero(tmp_path: Path):
    make_trial(tmp_path / "jobs", "a", "t9", exception="AgentTimeoutError")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t9"})
    assert cells[("t9", 1)].status == "error"
    assert cells[("t9", 1)].reward == 0.0


def test_empty_patch_with_dirty_worktree_is_invalid_not_fail(tmp_path: Path):
    """Zero-byte patch beside a dirty collect-time tree must not score 0."""
    trial = make_trial(tmp_path / "jobs", "a", "t1", reward=0.0)
    (trial / "artifacts").mkdir(exist_ok=True)
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text(" M src/app.py\n")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1"})
    assert cells[("t1", 1)].status == "error"
    assert cells[("t1", 1)].exception_type == "INVALID_EMPTY_PATCH"
    assert cells[("t1", 1)].reward == 0.0


def test_empty_patch_with_failed_copy_is_infra(tmp_path: Path):
    trial = make_trial(tmp_path / "jobs", "a", "t2", reward=0.0)
    (trial / "artifacts").mkdir(exist_ok=True)
    (trial / "artifacts" / "manifest.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "source": "/logs/artifacts/model.patch",
                        "destination": "artifacts/model.patch",
                        "type": "file",
                        "status": "failed",
                    }
                ]
            }
        )
    )
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t2"})
    assert cells[("t2", 1)].status == "error"
    assert cells[("t2", 1)].exception_type == "INFRA_ARTIFACT_COPY"


def test_empty_patch_without_evidence_stays_fail(tmp_path: Path):
    """A genuinely idle agent (clean tree, no writes) keeps its fail/0."""
    trial = make_trial(tmp_path / "jobs", "a", "t3", reward=0.0)
    (trial / "artifacts").mkdir(exist_ok=True)
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text("")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t3"})
    assert cells[("t3", 1)].status == "fail"
    assert cells[("t3", 1)].exception_type is None


def test_nonempty_patch_with_dirty_worktree_stays_graded(tmp_path: Path):
    trial = make_trial(tmp_path / "jobs", "a", "t4", reward=0.0)
    (trial / "artifacts").mkdir(exist_ok=True)
    (trial / "artifacts" / "model.patch").write_text("diff --git a/f b/f\n")
    (trial / "artifacts" / "worktree-status.txt").write_text(" M src/app.py\n")
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t4"})
    assert cells[("t4", 1)].status == "fail"
    assert cells[("t4", 1)].exception_type is None


def test_incremental_reconcile_applies_patch_guard(tmp_path: Path):
    from roast_my_harness.runner.reconcile import reconcile_variant_incremental

    trial = make_trial(tmp_path / "jobs", "a", "t1", reward=0.0)
    (trial / "artifacts").mkdir(exist_ok=True)
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text("?? new.py\n")
    state: dict = {}
    cells, _ = reconcile_variant_incremental("a", tmp_path / "jobs" / "a", {"t1"}, state)
    assert cells[("t1", 1)].status == "error"
    assert cells[("t1", 1)].exception_type == "INVALID_EMPTY_PATCH"


def test_timeout_errors_classified_as_infra(tmp_path: Path):
    from roast_my_harness.runner.reconcile import is_timeout_error

    assert is_timeout_error("TimeoutError")
    assert is_timeout_error("AgentTimeoutError")
    assert is_timeout_error("ProbeTimeoutError")
    assert is_timeout_error("asyncio.TimeoutError")
    assert is_timeout_error("verifier timed out after 1800s")
    assert not is_timeout_error(None)
    assert not is_timeout_error("")
    assert not is_timeout_error("AgentError")


def test_replicates_are_independent_cells(tmp_path: Path):
    make_trial(tmp_path / "jobs", "a", "t1", reward=1.0, replicate=1)
    make_trial(tmp_path / "jobs", "a", "t1", reward=0.0, replicate=2)
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1"})
    assert cells[("t1", 1)].status == "pass"
    assert cells[("t1", 1)].replicate == 1
    assert cells[("t1", 2)].status == "fail"
    assert cells[("t1", 2)].replicate == 2


def test_newest_attempt_wins_within_replicate_only(tmp_path: Path):
    import os

    old = make_trial(tmp_path / "jobs", "a", "t1", reward=0.0, replicate=1)
    os.utime(old / "result.json", (1000, 1000))
    make_trial(tmp_path / "jobs", "a", "t1", reward=1.0, replicate=1)
    make_trial(tmp_path / "jobs", "a", "t1", reward=0.0, replicate=2)
    cells = reconcile_variant("a", tmp_path / "jobs" / "a", {"t1"})
    assert cells[("t1", 1)].status == "pass"
    assert cells[("t1", 2)].status == "fail"


def test_replicate_of_defaults_to_one(tmp_path: Path):
    variant = tmp_path / "jobs" / "a"
    assert replicate_of(variant, variant / "2026" / "t1__X") == 1
    assert replicate_of(variant, variant / "replicate-2" / "2026" / "t1__X") == 2
    assert replicate_of(variant, variant / "replicate-x" / "t1__X") == 1
