"""Report collection selects one newest attempt per variant and task."""

from __future__ import annotations

import json
import os
from pathlib import Path

from roast_my_harness.report.collect import collect_rows


def _write_trial(root: Path, job: str, reward: float) -> Path:
    trial = root / "a" / job
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    result = trial / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_name": "t1",
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": {},
            }
        )
    )
    return result


def test_collect_rows_uses_newest_attempt(tmp_path: Path):
    older = _write_trial(tmp_path, "old", 0.0)
    newer = _write_trial(tmp_path, "new", 1.0)
    os.utime(older, (10, 10))
    os.utime(newer, (20, 20))
    rows = collect_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["reward"] == 1.0


# --- Fix C: telemetry mirrors reconcile on incomplete trials ---------------


def _write_incomplete_trial(root: Path, job: str) -> Path:
    trial = root / "a" / job
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    result = trial / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_name": "t1",
                "verifier_result": {"rewards": {}},
                "exception_info": {},
            }
        )
    )
    return result


def test_rewardless_trial_emits_no_row(tmp_path: Path):
    """Reconcile skips reward-less artifacts; telemetry must mirror it."""
    from roast_my_harness.runner.reconcile import reconcile_variant
    from roast_my_harness.telemetry.result import trial_row

    result = _write_incomplete_trial(tmp_path, "old")
    assert trial_row(result, "a") is None
    assert collect_rows(tmp_path) == []
    assert reconcile_variant("a", tmp_path / "a", {"t1"}) == {}


def test_explicit_zero_reward_still_counts_as_fail(tmp_path: Path):
    _write_trial(tmp_path, "old", 0.0)
    rows = collect_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["resolved"] == 0
    assert rows[0]["reward"] == 0.0


def test_exception_trial_still_counts_as_error(tmp_path: Path):
    trial = tmp_path / "a" / "old"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "t1",
                "verifier_result": {"rewards": {}},
                "exception_info": {"exception_type": "AgentCrash"},
            }
        )
    )
    rows = collect_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["exception_type"] == "AgentCrash"
    assert rows[0]["reward"] == 0.0


def test_empty_patch_with_mutations_is_invalid_row(tmp_path: Path):
    """Report rows mirror reconcile: contaminated zeros become infra errors."""
    from roast_my_harness.report.statistics import resolved_rows

    result = _write_trial(tmp_path, "old", 0.0)
    trial = result.parent
    (trial / "artifacts").mkdir()
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text(" M src/app.py\n")
    rows = collect_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["exception_type"] == "INVALID_EMPTY_PATCH"
    assert rows[0]["resolved"] == 0
    assert resolved_rows(rows) == []
