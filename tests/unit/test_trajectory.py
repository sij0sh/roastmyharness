"""Generic telemetry: failures from the ATIF trajectory, cm_* namespaced."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.report.exports import write_summary_csv, write_summary_json
from roast_my_harness.telemetry.result import COLUMNS, split_custom, trial_row
from roast_my_harness.telemetry.trajectory import (
    trajectory_metrics,
    trajectory_tool_metrics,
)

TRAJECTORY = {
    "steps": [
        {
            "step_id": 1,
            "timestamp": "2026-09-01T00:00:00+00:00",
            "tool_calls": [
                {"tool_call_id": "c1", "function_name": "read",
                 "arguments": {"path": "a.go"}},
                {"tool_call_id": "c2", "function_name": "read",
                 "arguments": {"path": "a.go", "offset": 1, "limit": 10}},
                {"tool_call_id": "c3", "function_name": "bash",
                 "arguments": {"command": "go test ./..."}},
                {"tool_call_id": "c4", "function_name": "edit",
                 "arguments": {"path": "a.go"}},
            ],
            "observation": {
                "results": [
                    {"source_call_id": "c1", "extra": {"tool": "read"}},
                    {"source_call_id": "c2", "extra": {"tool": "read"}},
                    {"source_call_id": "c3",
                     "extra": {"tool": "bash", "is_error": True}},
                ]
            },
        },
        {
            "step_id": 2,
            "timestamp": "2026-09-01T00:01:00+00:00",
            "tool_calls": [
                {"tool_call_id": "c5", "function_name": "search",
                 "arguments": {"query": "x"}},
            ],
            "observation": {"results": [{"extra": {"tool": "search"}}]},
        },
    ]
}


def test_trajectory_failures_and_missing_results():
    generic, custom = trajectory_metrics(TRAJECTORY)
    assert generic["tool_calls"] == 5
    assert generic["tool_results"] == 4
    assert generic["tool_failures"] == 1
    assert generic["tool_failure_rate"] == 0.25
    # c4 (edit) never got a result
    assert generic["tool_missing_results"] == 1
    assert custom["tool_calls_by_name"] == {
        "bash": 1, "edit": 1, "read": 2, "search": 1,
    }
    assert custom["tool_failures_by_name"] == {"bash": 1}


def test_trajectory_read_metrics_match_pi_fold():
    from roast_my_harness.telemetry.parser import fold_tool_event, new_tool_metrics

    generic, _ = trajectory_metrics(TRAJECTORY)
    assert generic["read_calls"] == 2
    assert generic["read_rereads"] == 1
    assert generic["distinct_read_files"] == 1
    # Same calls through the pi-event fold agree exactly.
    m = new_tool_metrics()
    for name, args, _ in (
        ("read", {"path": "a.go"}, None),
        ("read", {"path": "a.go", "offset": 1, "limit": 10}, None),
        ("bash", {"command": "x"}, None),
        ("edit", {"path": "a.go"}, None),
        ("search", {"query": "x"}, None),
    ):
        fold_tool_event(m, {"type": "tool_execution_start", "toolName": name, "args": args})
    for key in ("read_calls", "read_rereads", "read_overlap_rereads", "distinct_read_files"):
        assert generic[key] == m[key], key


def test_pi_trajectory_matches_pi_event_fold():
    from roast_my_harness.adapter.atif import convert_pi_events_to_atif
    from roast_my_harness.telemetry.parser import fold_tool_event, new_tool_metrics

    events = [
        {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "id": "c1", "name": "read",
                     "arguments": {"path": "a.go"}},
                    {"type": "toolCall", "id": "c2", "name": "bash",
                     "arguments": {"command": "go test ./..."}},
                ],
                "usage": {},
            },
            "toolResults": [
                {"toolName": "read", "isError": False, "content": "ok"},
                {"toolName": "bash", "isError": True, "content": "FAIL"},
            ],
        }
    ]
    trajectory = convert_pi_events_to_atif(
        events, instruction="", variant="v", agent_version="1"
    )
    generic, custom = trajectory_metrics(trajectory.to_json_dict())
    assert (generic["tool_calls"], generic["tool_results"], generic["tool_failures"]) == (2, 2, 1)
    assert generic["tool_missing_results"] == 0
    assert custom["tool_failures_by_name"] == {"bash": 1}
    m = new_tool_metrics()
    for name, args in (("read", {"path": "a.go"}), ("bash", {"command": "go test"})):
        fold_tool_event(
            m, {"type": "tool_execution_start", "toolName": name, "args": args}
        )
    assert generic["read_calls"] == m["read_calls"] == 1


def test_trajectory_absent_or_corrupt_reads_zero(tmp_path: Path):
    generic, custom, stamp = trajectory_tool_metrics(tmp_path / "missing")
    assert stamp == 0
    assert generic == {
        "tool_results": 0,
        "tool_failures": 0,
        "tool_failure_rate": 0.0,
        "tool_missing_results": 0,
    }
    assert custom == {}
    trial = tmp_path / "t"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "trajectory.json").write_text("{nope")
    generic, custom, stamp = trajectory_tool_metrics(trial)
    assert generic["tool_failures"] == 0 and custom == {}


def make_trial(root: Path, *, trajectory: dict | None) -> Path:
    trial = root / "t1__X"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({
        "task_name": "t1",
        "verifier_result": {"rewards": {"reward": 1.0}},
    }))
    if trajectory is not None:
        (trial / "agent" / "trajectory.json").write_text(json.dumps(trajectory))
    return trial / "result.json"


def test_trial_row_generic_failures_and_custom_namespace(tmp_path: Path):
    row = trial_row(make_trial(tmp_path, trajectory=TRAJECTORY), "a")
    assert row is not None
    assert row["tool_calls"] == 5
    assert row["tool_results"] == 4
    assert row["tool_failures"] == 1
    assert row["tool_failure_rate"] == 0.25
    assert row["tool_missing_results"] == 1
    assert row["read_rereads"] == 1
    assert "cm_errors" not in row
    assert row["custom_metrics"]["tool_failures_by_name"] == {"bash": 1}
    assert set(row) >= set(COLUMNS)


def test_trial_row_without_trajectory_keeps_pi_values(tmp_path: Path):
    agent = tmp_path / "t1__X" / "agent"
    agent.mkdir(parents=True)
    (tmp_path / "t1__X" / "verifier").mkdir(parents=True)
    (tmp_path / "t1__X" / "result.json").write_text(json.dumps({
        "task_name": "t1",
        "verifier_result": {"rewards": {"reward": 1.0}},
    }))
    agent.joinpath("pi-events.jsonl").write_text(
        '{"type": "tool_execution_start", "toolName": "read", "args": {"path": "a.go"}}\n'
        '{"type": "tool_execution_start", "toolName": "read", "args": {"path": "a.go"}}\n'
    )
    row = trial_row(tmp_path / "t1__X" / "result.json", "a")
    assert row is not None
    assert row["read_calls"] == 2
    assert row["read_rereads"] == 1
    assert row["tool_failures"] == 0
    assert row["custom_metrics"] == {}


def test_split_custom_strips_prefix():
    flat, custom = split_custom({"tool_calls": 3, "cm_errors": 2, "cm_search_calls": 1})
    assert flat == {"tool_calls": 3}
    assert custom == {"errors": 2, "search_calls": 1}


def test_columns_have_no_cm_but_generic_failures():
    assert not [c for c in COLUMNS if c.startswith("cm_")]
    for column in (
        "tool_results", "tool_failures", "tool_failure_rate", "tool_missing_results",
    ):
        assert column in COLUMNS


def test_csv_excludes_custom_metrics_but_json_keeps_them(tmp_path: Path):
    jobs = tmp_path / "jobs" / "a"
    jobs.mkdir(parents=True)
    result_path = make_trial(jobs, trajectory=TRAJECTORY)
    from roast_my_harness.report.collect import collect_rows

    rows = collect_rows(tmp_path / "jobs")
    assert len(rows) == 1
    csv_path = write_summary_csv(tmp_path, rows)
    header = csv_path.read_text().splitlines()[0]
    assert "custom_metrics" not in header
    assert "tool_failures" in header
    assert "cm_errors" not in header
    json_path = write_summary_json(
        tmp_path, rows, {"experiment_id": "e", "tool_version": "t"}
    )
    payload = json.loads(json_path.read_text())
    assert payload["trials"][0]["custom_metrics"]["tool_failures_by_name"] == {"bash": 1}
    assert result_path.exists()
