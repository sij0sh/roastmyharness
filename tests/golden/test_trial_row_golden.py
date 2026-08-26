"""Golden: trial directory -> summary row (live/final parity included)."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.telemetry.result import trial_row

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_fixture_row_values():
    row = trial_row(FIXTURES / "trial" / "result.json", variant="bare")
    assert row["variant"] == "bare"
    assert row["task"] == "fixture-task"
    assert row["resolved"] == 1
    assert row["reward"] == 1.0
    assert row["input_tokens"] == 1800
    assert row["output_tokens"] == 80
    assert row["cache_tokens"] == 400
    assert row["cost_usd"] == 0.05
    assert row["wall_sec"] == 150.0
    assert row["agent_steps"] == 2
    # event-stream metrics
    assert row["llm_calls"] == 2
    assert row["cache_write_tokens"] == 10
    assert row["reasoning_tokens"] == 20
    assert row["peak_input_cache_tokens"] == 1400
    assert row["tool_calls"] == 3
    assert row["read_calls"] == 3
    assert row["read_rereads"] == 1
    assert row["read_overlap_rereads"] == 1
    assert row["distinct_read_files"] == 2
    assert row["summarization_count"] == 0  # from result.json
    # sidecar timings
    assert row["llm_ttft_sec"] == 0.9
    assert row["turn_time_sec"] == 17.0
    assert row["avg_input_cache_tokens"] == 1100.0
