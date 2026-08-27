"""Trial directory -> one summary row. Schema-compatible with DSE-tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from roast_my_harness.telemetry.parser import final_event_metrics

# Column order matches DSE-tests collect.py so downstream notebooks keep
# working; new roastmyharness columns may only be appended.
COLUMNS = [
    "variant", "task", "resolved", "reward", "rewards",
    "exception_type", "input_tokens", "output_tokens", "cache_tokens",
    "cost_usd", "peak_context_tokens", "peak_input_cache_tokens",
    "avg_input_cache_tokens", "summarization_count",
    "agent_steps", "wall_sec",
    "llm_calls", "llm_ttft_sec", "turn_time_sec",
    "cache_write_tokens", "reasoning_tokens",
    "tool_calls", "read_calls", "read_rereads", "read_overlap_rereads",
    "distinct_read_files",
    "cm_llm_calls", "cm_input_tokens", "cm_output_tokens",
    "cm_attributions", "cm_errors", "cm_search_calls", "cm_rehydrate_calls",
]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_trial_dir(path: Path) -> bool:
    """Trial dirs contain the mounted /logs structure; job dirs do not."""
    return (path / "agent").is_dir() and (path / "verifier").is_dir()


def trial_row(result_path: Path, variant: str) -> dict[str, Any] | None:
    try:
        result = json.loads(result_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    trial_dir = result_path.parent
    rewards: dict = {}
    verifier = result.get("verifier_result") or {}
    if not isinstance(verifier, dict):
        verifier = {}
    reward_map = verifier.get("rewards")
    if isinstance(reward_map, dict):
        rewards = {str(k): v for k, v in reward_map.items()}
    reward_file = trial_dir / "verifier" / "reward.json"
    if reward_file.is_file():
        try:
            file_rewards = json.loads(reward_file.read_text())
            if isinstance(file_rewards, dict):
                rewards.update({str(k): v for k, v in file_rewards.items()})
        except (json.JSONDecodeError, OSError):
            pass

    exception_info = result.get("exception_info") or {}
    if not isinstance(exception_info, dict):
        exception_info = {}
    exception_type = exception_info.get("exception_type") or exception_info.get("type", "")
    try:
        reward = float(rewards.get("reward", 0) or 0)
    except (TypeError, ValueError):
        if not exception_type:
            return None
        reward = 0.0
    if exception_type:
        reward = 0.0
    resolved = not exception_type and reward >= 0.999

    agent = result.get("agent_result") or {}
    timing = result.get("agent_execution") or {}
    started, finished = timing.get("started_at"), timing.get("finished_at")
    wall_sec: Any = ""
    if started and finished:
        wall_sec = round((_parse_ts(finished) - _parse_ts(started)).total_seconds(), 1)

    steps: Any = ""
    trajectory = trial_dir / "agent" / "trajectory.json"
    if trajectory.is_file():
        try:
            fm = json.loads(trajectory.read_text()).get("final_metrics") or {}
            steps = fm.get("total_steps", "")
        except (json.JSONDecodeError, OSError):
            pass
    if steps == "":
        
        steps = result.get("n_agent_steps", "")

    row: dict[str, Any] = {
        "variant": variant,
        "task": result.get("task_name") or trial_dir.name,
        "resolved": int(resolved),
        "reward": reward,
        "rewards": json.dumps(rewards, sort_keys=True) if rewards else "",
        "exception_type": exception_type,
        "input_tokens": agent.get("n_input_tokens", ""),
        "output_tokens": agent.get("n_output_tokens", ""),
        "cache_tokens": agent.get("n_cache_tokens", ""),
        "cost_usd": agent.get("cost_usd", ""),
        "peak_context_tokens": agent.get("peak_context_tokens", ""),
        "summarization_count": agent.get("summarization_count", ""),
        "agent_steps": steps,
        "wall_sec": wall_sec,
        **final_event_metrics(trial_dir),
    }
    
    
    if not (trial_dir / "agent" / "pi-events.jsonl").is_file() and steps != "":
        row["llm_calls"] = steps
    return row
