"""Trial reconciliation from pier job directories, never from pier stdout."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from roast_my_harness.runner.patch_guard import (
    classify_empty_patch,
    has_trial_logs,
)

PASS_THRESHOLD = 0.999

_log = logging.getLogger(__name__)

_ATTEMPT_SEQ_RE = re.compile(r"(\d+)\s*$")


REPLICATE_DIR_PREFIX = "replicate-"
"""Jobs subdir segment per rollout: jobs/<variant>/replicate-N/... .

Single-repetition runs keep the legacy flat layout (jobs/<variant>/...),
which always reads as replicate 1, so old runs resume unchanged.
"""


def replicate_of(variant_dir: Path, trial_dir: Path) -> int:
    """Rollout number from the trial path; 1 when no replicate segment."""
    try:
        rel = trial_dir.relative_to(variant_dir)
    except ValueError:
        return 1
    for part in rel.parts[:-1]:
        if part.startswith(REPLICATE_DIR_PREFIX):
            try:
                return max(1, int(part[len(REPLICATE_DIR_PREFIX):]))
            except ValueError:
                continue
    return 1


@dataclass(frozen=True)
class Cell:
    variant_id: str
    task_id: str
    status: str  # pass | fail | error
    reward: float
    job_path: str
    finished_at: str | None
    replicate: int = 1
    exception_type: str | None = None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _attempt_seq(trial_dir: Path) -> int:
    """Best-effort attempt order from the trial dir name, else -1.

    True filesystem recency is unknowable when result.json mtimes tie,
    so ties fall back to this sequence proxy (then path) for a stable winner.
    """
    match = _ATTEMPT_SEQ_RE.search(trial_dir.name)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return -1
    return -1


def reconcile_variant(
    variant_id: str, jobs_dir: Path, known_tasks: set[str]
) -> dict[tuple[str, int], Cell]:
    """Newest valid attempt per (task, replicate) under jobs/<variant>/.

    A trial directory must contain agent/ and verifier/ plus result.json.
    """
    cells: dict[tuple[str, int], tuple[float, int, str, Cell]] = {}
    if not jobs_dir.is_dir():
        return {}
    for result_path in sorted(jobs_dir.rglob("result.json")):
        trial_dir = result_path.parent
        if not has_trial_logs(trial_dir):
            continue  # job-level summary, not a trial
        try:
            result = json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        raw_task = str(result.get("task_name") or trial_dir.name)
        task_id = raw_task
        if known_tasks and task_id not in known_tasks:
            short = raw_task.rsplit("/", 1)[-1]
            base = trial_dir.name.split("__", 1)[0]
            short_hit = short in known_tasks
            base_hit = base in known_tasks
            if base_hit and short_hit:
                if short == base:
                    task_id = base
                else:
                    _log.warning(
                        "reconcile conflict: dir %s implies task %r but pier task_name %r "
                        "implies %r; keeping dir task",
                        trial_dir,
                        base,
                        raw_task,
                        short,
                    )
                    task_id = base
            elif base_hit:
                task_id = base
            elif short_hit:
                task_id = short
            else:
                continue
        exception_info = result.get("exception_info") or {}
        if not isinstance(exception_info, dict):
            exception_info = {}
        exception = exception_info.get("exception_type") or exception_info.get("type")
        if exception:
            status = "error"
            reward = 0.0
        else:
            verifier = result.get("verifier_result") or {}
            if not isinstance(verifier, dict):
                verifier = {}
            reward_map = verifier.get("rewards")
            if not isinstance(reward_map, dict):
                reward_map = {}
            reward = reward_map.get("reward")
            if reward is None:
                reward_path = trial_dir / "verifier" / "reward.json"
                if reward_path.is_file():
                    try:
                        reward_data = json.loads(reward_path.read_text())
                        reward = (
                            reward_data.get("reward") if isinstance(reward_data, dict) else None
                        )
                    except (json.JSONDecodeError, OSError):
                        reward = None
            if reward is None:
                continue  # incomplete, not terminal
            try:
                reward = float(reward)
            except (TypeError, ValueError):
                continue
            status = "pass" if reward >= PASS_THRESHOLD else "fail"
            if status == "fail" and reward == 0.0:
                # Same guard as _cell_from_result: an empty patch beside
                # mutation evidence (or a failed artifact copy) is a
                # collection failure, never a quality 0.
                guard = classify_empty_patch(trial_dir)
                if guard is not None:
                    status = "error"
                    exception = guard
        timing = result.get("agent_execution") or {}
        finished = timing.get("finished_at")
        replicate = replicate_of(jobs_dir, trial_dir)
        cell = Cell(
            variant_id=variant_id,
            task_id=task_id,
            status=status,
            reward=reward,
            job_path=str(trial_dir),
            finished_at=finished or datetime.fromtimestamp(_mtime(result_path), tz=UTC).isoformat(),
            replicate=replicate,
            exception_type=str(exception) if exception else None,
        )
        stamp = _mtime(result_path)
        key = (_attempt_seq(trial_dir), str(result_path))
        trial = (task_id, replicate)
        prev = cells.get(trial)
        if prev is None or stamp > prev[0] or (stamp == prev[0] and key < (prev[1], prev[2])):
            cells[trial] = (stamp, key[0], key[1], cell)
    return {trial: cell for trial, (_, _, _, cell) in cells.items()}


def missing_replicates(
    cells: dict[tuple[str, int], Cell], all_tasks: list[str], repetitions: int
) -> list[tuple[str, int]]:
    """(task, replicate) trials with no reconciled cell, in task order."""
    return [
        (task_id, replicate)
        for task_id in all_tasks
        for replicate in range(1, repetitions + 1)
        if (task_id, replicate) not in cells
    ]


_THROTTLE_MARKERS = (
    "429",
    "throttl",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota",
    "overloaded",
    "capacity",
)


def is_throttle_error(exception_type: str | None) -> bool:
    """True when an error label looks like provider throttling, not a real failure."""
    if not exception_type:
        return False
    lowered = str(exception_type).lower()
    return any(m in lowered for m in _THROTTLE_MARKERS)


_TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "timedout",
    "deadline exceeded",
)


def is_timeout_error(exception_type: str | None) -> bool:
    """True when an error label looks like a harness timeout, not agent output.

    Covers pier's hard verifier/agent bounds (asyncio TimeoutError via
    wait_for) and the smoke probe's ProbeTimeoutError. Timeout trials are
    infrastructure outcomes: the trial never produced a gradable result, so
    they must read as infra errors rather than quality signal. Pair with
    is_throttle_error when grouping report errors.
    """
    if not exception_type:
        return False
    lowered = str(exception_type).lower()
    return any(m in lowered for m in _TIMEOUT_MARKERS)


def _resolve_task_id(raw_task: str, trial_dir: Path, known_tasks: set[str]) -> str | None:
    task_id = raw_task
    if known_tasks and task_id not in known_tasks:
        short = raw_task.rsplit("/", 1)[-1]
        base = trial_dir.name.split("__", 1)[0]
        short_hit = short in known_tasks
        base_hit = base in known_tasks
        if base_hit and short_hit:
            if short == base:
                task_id = base
            else:
                _log.warning(
                    "reconcile conflict: dir %s implies task %r but pier task_name %r "
                    "implies %r; keeping dir task",
                    trial_dir,
                    base,
                    raw_task,
                    short,
                )
                task_id = base
        elif base_hit:
            task_id = base
        elif short_hit:
            task_id = short
        else:
            return None
    return task_id


def _cell_from_result(
    variant_id: str,
    trial_dir: Path,
    result: dict,
    task_id: str,
    stamp: float,
    *,
    replicate: int = 1,
) -> Cell | None:
    exception_info = result.get("exception_info") or {}
    if not isinstance(exception_info, dict):
        exception_info = {}
    exception = exception_info.get("exception_type") or exception_info.get("type")
    if exception:
        status = "error"
        reward = 0.0
    else:
        verifier = result.get("verifier_result") or {}
        if not isinstance(verifier, dict):
            verifier = {}
        reward_map = verifier.get("rewards")
        if not isinstance(reward_map, dict):
            reward_map = {}
        reward = reward_map.get("reward")
        if reward is None:
            reward_path = trial_dir / "verifier" / "reward.json"
            if reward_path.is_file():
                try:
                    reward_data = json.loads(reward_path.read_text())
                    reward = reward_data.get("reward") if isinstance(reward_data, dict) else None
                except (json.JSONDecodeError, OSError):
                    reward = None
        if reward is None:
            return None
        try:
            reward = float(reward)
        except (TypeError, ValueError):
            return None
        status = "pass" if reward >= PASS_THRESHOLD else "fail"
        if status == "fail" and reward == 0.0:
            # Zero-byte patch beside evidence of agent mutations (or a
            # failed artifact copy) is a collection failure, not a quality
            # signal: mark it so reports exclude it instead of scoring 0.
            guard = classify_empty_patch(trial_dir)
            if guard is not None:
                status = "error"
                exception = guard
    timing = result.get("agent_execution") or {}
    finished = timing.get("finished_at")
    return Cell(
        variant_id=variant_id,
        task_id=task_id,
        status=status,
        reward=reward,
        job_path=str(trial_dir),
        finished_at=finished or datetime.fromtimestamp(stamp, tz=UTC).isoformat(),
        replicate=replicate,
        exception_type=str(exception) if exception else None,
    )


def reconcile_variant_incremental(
    variant_id: str,
    jobs_dir: Path,
    known_tasks: set[str],
    file_state: dict[str, tuple[float, str, Cell | None]],
) -> tuple[dict[tuple[str, int], Cell], int]:
    """Delta reconcile: parse only new/changed result.json files.

    file_state maps result path -> (mtime, task_id or "", cell or None).
    Mutated in place. Returns (cells, parsed_count). Winner semantics match
    reconcile_variant exactly (newest valid attempt per (task, replicate));
    unchanged files cost a stat, not a parse.
    """
    parsed = 0
    if not jobs_dir.is_dir():
        file_state.clear()
        return {}, 0
    seen: set[str] = set()
    for result_path in sorted(jobs_dir.rglob("result.json")):
        trial_dir = result_path.parent
        if not has_trial_logs(trial_dir):
            continue
        key = str(result_path)
        seen.add(key)
        try:
            stamp = result_path.stat().st_mtime
        except OSError:
            continue
        cached = file_state.get(key)
        if cached is not None and cached[0] == stamp:
            continue
        parsed += 1
        try:
            result = json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            file_state[key] = (stamp, "", None)
            continue
        if not isinstance(result, dict):
            file_state[key] = (stamp, "", None)
            continue
        raw_task = str(result.get("task_name") or trial_dir.name)
        task_id = _resolve_task_id(raw_task, trial_dir, known_tasks)
        if task_id is None:
            file_state[key] = (stamp, "", None)
            continue
        cell = _cell_from_result(
            variant_id,
            trial_dir,
            result,
            task_id,
            stamp,
            replicate=replicate_of(jobs_dir, trial_dir),
        )
        file_state[key] = (stamp, task_id, cell)
    for stale in [k for k in file_state if k not in seen]:
        del file_state[stale]
    winners: dict[tuple[str, int], tuple[float, int, str, Cell]] = {}
    for key, (stamp, task_id, cell) in file_state.items():
        if not task_id or cell is None:
            continue
        trial_dir = Path(key).parent
        trial = (task_id, cell.replicate)
        seq_path = (_attempt_seq(trial_dir), key)
        prev = winners.get(trial)
        if prev is None or stamp > prev[0] or (stamp == prev[0] and seq_path < (prev[1], prev[2])):
            winners[trial] = (stamp, seq_path[0], seq_path[1], cell)
    return {trial: cell for trial, (_, _, _, cell) in winners.items()}, parsed
