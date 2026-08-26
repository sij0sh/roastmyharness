"""Golden parity: scrubbed real DSE trial vs legacy summary.csv (backlog #22).

Fixture: bare/datacurve/abs-stepped-slices from DSE-tests
results/bare/2026-08-18__05-57-51, with prompts, patches, tokens, and
account IDs removed. Expected values copied verbatim from the legacy
summary.csv produced by DSE-tests collect.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.telemetry.result import trial_row

FIXTURE = (
    Path(__file__).parents[1] / "golden" / "fixtures" / "dse"
    / "bare-abs-stepped-slices.json"
)


def test_real_dse_trial_row_matches_legacy_csv():
    row = trial_row(FIXTURE, variant="bare")
    
    assert row["variant"] == "bare"
    assert row["task"] == "datacurve/abs-stepped-slices"
    assert row["resolved"] == 1
    assert row["reward"] == 1.0
    assert row["input_tokens"] == 189
    assert row["output_tokens"] == 25789
    assert row["cache_tokens"] == 3040437
    assert row["cost_usd"] == 0.0
    assert row["wall_sec"] == 591.8
    assert row["agent_steps"] == 63
    
    assert row["llm_calls"] == 63
    
    assert row["summarization_count"] == 0


def test_fixture_is_scrubbed():
    """No secrets, prompts, or account identifiers in the golden fixture."""
    text = FIXTURE.read_text()
    for banned in ("sk-", "Bearer ", "accountId", "access_token"):
        assert banned not in text, f"fixture leaks {banned!r}"
    data = json.loads(text)
    
    assert "step_results" not in data
    assert "agent_execution" not in data or set(
        data["agent_execution"].keys()
    ) <= {"started_at", "finished_at"}  
    agent_exec = data.get("agent_execution") or {}
    for field in ("stdout", "stderr", "logs", "commands"):
        assert field not in agent_exec
