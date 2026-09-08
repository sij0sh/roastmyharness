"""Trial directory -> one summary row of the tool-owned CSV schema."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from roast_my_harness.runner.patch_guard import (
    classify_empty_patch,
    has_trial_logs,
    step_dirs,
)
from roast_my_harness.runner.reconcile import PASS_THRESHOLD
from roast_my_harness.telemetry.parser import (
    final_event_metrics,
    fold_state_valid,
    fold_trial_incremental,
)
from roast_my_harness.telemetry.trajectory import trajectory_tool_metrics

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
    "replicate",
    "tool_results",
    "tool_failures",
    "tool_failure_rate",
    "tool_missing_results",
    "reward_deterministic",
    "reward_judge",
    "judge_model",
]

CUSTOM_PREFIX = "cm_"
"""Pi-enrichment counters live under row["custom_metrics"], not in the CSV."""

DETERMINISTIC_KEY = "reward_deterministic"
"""Rewards-map key for the deterministic-check score (0..1)."""

JUDGE_KEY = "reward_judge"
"""Rewards-map key for the model-judge score (0..1)."""

JUDGE_MODEL_KEY = "judge_model"
"""Rewards-map key naming the judge model that produced reward_judge."""

DIMENSION_COLUMNS = ("reward_deterministic", "reward_judge", "judge_model")
"""Row columns carrying scoring dimensions; appended to the CSV schema."""


def fnum_or_none(value: Any) -> float | None:
    """Float value, or None for missing/unparsable input (never 0.0)."""
    try:
        return float(value) if value not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _dim_float(value: Any) -> float | str:
    """Dimension score for a row: float when reported, "" when absent.

    Zero is a reported score, not absence: only None survives as "".
    """
    parsed = fnum_or_none(value)
    return "" if parsed is None else parsed


def split_dimensions(rewards: dict[str, Any]) -> dict[str, Any]:
    """Dimension fragment for one trial row from a verifier rewards map."""
    return {
        DETERMINISTIC_KEY: _dim_float(rewards.get(DETERMINISTIC_KEY)),
        JUDGE_KEY: _dim_float(rewards.get(JUDGE_KEY)),
        JUDGE_MODEL_KEY: str(rewards.get(JUDGE_MODEL_KEY) or ""),
    }


def split_custom(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(flat CSV metrics, custom_metrics namespaced dict).

    cm_-prefixed pi counters move under custom_metrics with the prefix
    stripped; everything else stays a flat core column. Zero counters
    are dropped: custom_metrics records observed enrichment, so an empty
    dict means none fired.
    """
    flat: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    for key, value in metrics.items():
        if key.startswith(CUSTOM_PREFIX):
            if value:
                custom[key[len(CUSTOM_PREFIX):]] = value
        else:
            flat[key] = value
    return flat, custom


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_trial_dir(path: Path) -> bool:
    """Trial dirs contain the mounted /logs structure; job dirs do not."""
    return has_trial_logs(path)


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
    if not exception_type and reward == 0.0:
        # Same guard as reconcile: an empty patch beside mutation evidence
        # (or a failed artifact copy) is a collection failure. The row
        # becomes an infra error so quality stats exclude it instead of
        # averaging it in as a failure.
        guard = classify_empty_patch(trial_dir)
        if guard is not None:
            exception_type = guard
    resolved = not exception_type and reward >= PASS_THRESHOLD
    agent = result.get("agent_result") or {}
    timing = result.get("agent_execution") or {}
    started, finished = timing.get("started_at"), timing.get("finished_at")
    if not (started and finished):
        # Staged trials record timing per step; fall back to trial bounds.
        started, finished = result.get("started_at"), result.get("finished_at")
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
        **split_dimensions(rewards),
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


FAILURE_DEFAULTS_KEYS = frozenset(
    {"tool_results", "tool_failures", "tool_failure_rate", "tool_missing_results"}
)


def merge_telemetry(
    event_metrics: dict[str, Any],
    traj: tuple[dict[str, Any], dict[str, Any], int],
) -> dict[str, Any]:
    """One flat row fragment from pi-event metrics plus trajectory overlay.

    cm_-prefixed pi counters move under custom_metrics; generic tool
    failure/read counters overlay from the normalized ATIF trajectory
    when one exists (pi-event values stand otherwise, failures read
    zero).
    """
    flat, custom = split_custom(event_metrics)
    generic, traj_custom, traj_stamp = traj
    if traj_stamp:
        flat.update(generic)
        custom.update(traj_custom)
    else:
        flat.update(
            {k: v for k, v in generic.items() if k in FAILURE_DEFAULTS_KEYS}
        )
    flat["custom_metrics"] = custom
    return flat


def trial_row_cached(
    result_path: Path,
    variant: str,
    entry: dict[str, Any] | None = None,
    *,
    replicate: int = 1,
) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    """Row for one trial, folding only event bytes not seen before.

    entry caches (result.json mtime_ns, completion flag, fold offsets, row).
    Returns (row_or_None, updated entry, event lines folded this call).
    A completed trial with an unchanged result.json stamp reuses its row
    with zero folds: pier writes result.json at trial end and reconcile
    treats a parsed reward/exception as terminal, so those inputs are stable.
    A changed result.json stamp discards fold offsets and refolds from zero.
    The cached row also keys on the trajectory mtime: the adapter writes
    trajectory.json post-run, possibly after result.json, so a newly
    arrived trajectory refolds once instead of serving stale zeros.
    """
    trial_dir = result_path.parent
    try:
        stamp = result_path.stat().st_mtime_ns
    except OSError:
        return None, {"result_stamp": 0, "complete": False, "fold": None, "row": None}, 0
    if not _entry_valid(entry):
        entry = None
    traj = trajectory_tool_metrics(trial_dir)
    if (
        entry is not None
        and entry["complete"]
        and entry["result_stamp"] == stamp
        and entry.get("traj_stamp", -1) == traj[2]
        and entry["row"] is not None
    ):
        return entry["row"], entry, 0
    fold = entry["fold"] if entry is not None and entry["result_stamp"] == stamp else None
    metrics, fold, folded = fold_trial_incremental(trial_dir, fold)
    base = _row_base(result_path, variant)
    if base is None:
        new_entry = {"result_stamp": stamp, "complete": False, "fold": fold, "row": None}
        return None, new_entry, folded
    fragment = merge_telemetry(metrics, traj)
    traj_stamp = traj[2]
    row: dict[str, Any] = {**base, **fragment, "replicate": replicate}
    if not _has_event_logs(trial_dir) and row.get("agent_steps") != "":
        row["llm_calls"] = row["agent_steps"]
    return row, {
        "result_stamp": stamp,
        "complete": True,
        "fold": fold,
        "row": row,
        "traj_stamp": traj_stamp,
    }, folded


def trial_row(
    result_path: Path, variant: str, *, replicate: int = 1
) -> dict[str, Any] | None:
    base = _row_base(result_path, variant)
    if base is None:
        return None
    trial_dir = result_path.parent
    fragment = merge_telemetry(
        final_event_metrics(trial_dir), trajectory_tool_metrics(trial_dir)
    )
    row: dict[str, Any] = {**base, **fragment}
    row["replicate"] = replicate
    if not _has_event_logs(trial_dir) and base["agent_steps"] != "":
        row["llm_calls"] = base["agent_steps"]
    return row


def _has_event_logs(trial_dir: Path) -> bool:
    """Trial-root or any per-step pi-events file exists."""
    if (trial_dir / "agent" / "pi-events.jsonl").is_file():
        return True
    return any(
        (step / "agent" / "pi-events.jsonl").is_file()
        for step in step_dirs(trial_dir)
    )
