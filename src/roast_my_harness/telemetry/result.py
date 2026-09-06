"""Trial directory -> one summary row of the tool-owned CSV schema."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from roast_my_harness.runner.reconcile import PASS_THRESHOLD
from roast_my_harness.telemetry.parser import (
    final_event_metrics,
    fold_state_valid,
    fold_trial_incremental,
)

# Column order matches DSE-tests collect.py so downstream notebooks keep
# working; new roastmyharness columns may only be appended.
COLUMNS = [
    "variant",
    "task",
    "resolved",
    "reward",
    "rewards",
    "exception_type",
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "cost_usd",
    "peak_context_tokens",
    "peak_input_cache_tokens",
    "avg_input_cache_tokens",
    "summarization_count",
    "agent_steps",
    "wall_sec",
    "llm_calls",
    "llm_ttft_sec",
    "turn_time_sec",
    "cache_write_tokens",
    "reasoning_tokens",
    "tool_calls",
    "read_calls",
    "read_rereads",
    "read_overlap_rereads",
    "distinct_read_files",
    "cm_llm_calls",
    "cm_input_tokens",
    "cm_output_tokens",
    "cm_attributions",
    "cm_errors",
    "cm_search_calls",
    "cm_rehydrate_calls",
]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_trial_dir(path: Path) -> bool:
    """Trial dirs contain the mounted /logs structure; job dirs do not."""
    return (path / "agent").is_dir() and (path / "verifier").is_dir()


def _row_base(result_path: Path, variant: str) -> dict[str, Any] | None:
    """Row fields from result/reward/trajectory files, without event folds.

    Returns None for artifacts reconcile also skips: unparsable result.json,
    or reward-less trials without an exception (incomplete, not terminal).
    A returned dict means the trial is terminal: pier writes result.json at
    trial end, so a stable result.json mtime implies immutable inputs.
    """
    try:
        result = json.loads(result_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(result, dict):
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
        reward = float(rewards.get("reward"))
    except (TypeError, ValueError):
        if not exception_type:
            return None
        reward = 0.0
    if exception_type:
        reward = 0.0
    resolved = not exception_type and reward >= PASS_THRESHOLD
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
    return {
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
    }


def _entry_valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("result_stamp"), int):
        return False
    if not isinstance(entry.get("complete"), bool):
        return False
    fold = entry.get("fold")
    if fold is not None and not fold_state_valid(fold):
        return False
    row = entry.get("row")
    if row is not None and not isinstance(row, dict):
        return False
    return True


def trial_row_cached(
    result_path: Path, variant: str, entry: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    """Row for one trial, folding only event bytes not seen before.

    entry caches (result.json mtime_ns, completion flag, fold offsets, row).
    Returns (row_or_None, updated entry, event lines folded this call).
    A completed trial with an unchanged result.json stamp reuses its row
    with zero folds: pier writes result.json at trial end and reconcile
    treats a parsed reward/exception as terminal, so those inputs are stable.
    A changed result.json stamp discards fold offsets and refolds from zero.
    """
    trial_dir = result_path.parent
    try:
        stamp = result_path.stat().st_mtime_ns
    except OSError:
        return None, {"result_stamp": 0, "complete": False, "fold": None, "row": None}, 0
    if not _entry_valid(entry):
        entry = None
    if (
        entry is not None
        and entry["complete"]
        and entry["result_stamp"] == stamp
        and entry["row"] is not None
    ):
        return entry["row"], entry, 0
    fold = entry["fold"] if entry is not None and entry["result_stamp"] == stamp else None
    metrics, fold, folded = fold_trial_incremental(trial_dir, fold)
    base = _row_base(result_path, variant)
    if base is None:
        new_entry = {"result_stamp": stamp, "complete": False, "fold": fold, "row": None}
        return None, new_entry, folded
    row: dict[str, Any] = {**base, **metrics}
    if not (trial_dir / "agent" / "pi-events.jsonl").is_file() and row.get("agent_steps") != "":
        row["llm_calls"] = row["agent_steps"]
    return row, {"result_stamp": stamp, "complete": True, "fold": fold, "row": row}, folded


def trial_row(result_path: Path, variant: str) -> dict[str, Any] | None:
    base = _row_base(result_path, variant)
    if base is None:
        return None
    trial_dir = result_path.parent
    row: dict[str, Any] = {**base, **final_event_metrics(trial_dir)}
    if not (trial_dir / "agent" / "pi-events.jsonl").is_file() and base["agent_steps"] != "":
        row["llm_calls"] = base["agent_steps"]
    return row
