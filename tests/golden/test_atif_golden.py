"""Golden: pi event stream -> ATIF trajectory (real pier models)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pier = pytest.importorskip("pier")

from roast_my_harness.adapter.atif import convert_pi_events_to_atif  # noqa: E402

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_events(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fixture_matches_golden():
    events = load_events(FIXTURES / "trial" / "agent" / "pi-events.jsonl")
    trajectory = convert_pi_events_to_atif(
        events, instruction="fix the bug", variant="bare", agent_version="0.84.3"
    )
    dumped = trajectory.to_json_dict()
    golden = json.loads(
        (FIXTURES / "trial" / "agent" / "trajectory.json").read_text()
    )
    # golden file is exactly what our converter must reproduce
    assert dumped == golden


def test_empty_stream():
    trajectory = convert_pi_events_to_atif(
        [], instruction="", variant="bare", agent_version="x"
    )
    assert [s["source"] for s in trajectory.to_json_dict()["steps"]] == ["system"]
