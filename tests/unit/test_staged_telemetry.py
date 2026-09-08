"""Staged (multi-step pier) telemetry: pairs, folds, trajectory merge."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.telemetry.parser import (
    event_log_pairs,
    final_event_metrics,
    fold_state_valid,
    fold_trial_incremental,
    new_fold_state,
)
from roast_my_harness.telemetry.trajectory import trajectory_tool_metrics


def _turn_end(n: int = 1) -> str:
    return "".join(
        json.dumps(
            {
                "type": "turn_end",
                "message": {"usage": {"input": 100, "cacheRead": 50}},
            }
        )
        + "\n"
        for _ in range(n)
    )


def make_stepped(root: Path, steps=("a", "b"), events_per_step=2) -> Path:
    trial = root / "t__X"
    for step in steps:
        agent = trial / "steps" / step / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "pi-events.jsonl").write_text(_turn_end(events_per_step))
    return trial


def test_event_log_pairs_root_layout(tmp_path: Path):
    trial = tmp_path / "t__X"
    (trial / "agent").mkdir(parents=True)
    pairs = event_log_pairs(trial)
    assert len(pairs) == 1
    assert pairs[0][0] == trial / "agent" / "pi-events.jsonl"


def test_event_log_pairs_stepped_skips_root(tmp_path: Path):
    trial = make_stepped(tmp_path)
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "agent" / "pi-events.jsonl").write_text(_turn_end(99))
    pairs = event_log_pairs(trial)
    assert [p[0].parent.parent.name for p in pairs] == ["a", "b"]


def test_fold_stepped_sums_across_steps(tmp_path: Path):
    trial = make_stepped(tmp_path, events_per_step=2)
    metrics, state, folded = fold_trial_incremental(trial, None)
    assert metrics["llm_calls"] == 4
    assert folded == 4
    assert state["mode"] == "steps"
    # Incremental second poll folds nothing new.
    metrics2, _, folded2 = fold_trial_incremental(trial, state)
    assert folded2 == 0
    assert metrics2["llm_calls"] == 4


def test_fold_mode_switch_restarts_without_double_count(tmp_path: Path):
    trial = tmp_path / "t__X"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "pi-events.jsonl").write_text(_turn_end(3))
    metrics, state, _ = fold_trial_incremental(trial, None)
    assert metrics["llm_calls"] == 3
    assert state["mode"] == "root"
    # First step relocates: same bytes now live under steps/a/.
    step_agent = trial / "steps" / "a" / "agent"
    step_agent.mkdir(parents=True)
    (trial / "agent" / "pi-events.jsonl").rename(step_agent / "pi-events.jsonl")
    metrics2, state2, _ = fold_trial_incremental(trial, state)
    assert state2["mode"] == "steps"
    assert metrics2["llm_calls"] == 3


def test_final_event_metrics_stepped(tmp_path: Path):
    trial = make_stepped(tmp_path, events_per_step=3)
    metrics = final_event_metrics(trial)
    assert metrics["llm_calls"] == 6


def test_trajectory_merge_sums_steps(tmp_path: Path):
    trial = tmp_path / "t__X"
    for step in ("a", "b"):
        agent = trial / "steps" / step / "agent"
        agent.mkdir(parents=True)
        (agent / "trajectory.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "tool_calls": [
                                {"function_name": "read", "tool_call_id": "1"}
                            ],
                            "observation": {"results": []},
                        }
                    ]
                }
            )
        )
    generic, _, stamp = trajectory_tool_metrics(trial)
    assert stamp > 0
    assert generic["tool_calls"] == 2


def test_old_fold_state_stays_valid():
    state = new_fold_state()
    del state["mode"]
    del state["step_files"]
    assert fold_state_valid(state)
