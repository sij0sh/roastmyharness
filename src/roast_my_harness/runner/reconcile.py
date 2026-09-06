"""Trial reconciliation from pier job directories, never from pier stdout."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PASS_THRESHOLD = 0.999

_log = logging.getLogger(__name__)

_ATTEMPT_SEQ_RE = re.compile(r"(\d+)\s*$")


@dataclass(frozen=True)
class Cell:
    variant_id: str
    task_id: str
    status: str  # pass | fail | error
    reward: float
    job_path: str
    finished_at: str | None
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


def reconcile_variant(variant_id: str, jobs_dir: Path, known_tasks: set[str]) -> dict[str, Cell]:
    """Newest valid attempt per task from trial dirs under jobs/<variant>/.

    A trial directory must contain agent/ and verifier/ plus result.json.
    """
    cells: dict[str, tuple[float, int, str, Cell]] = {}
    if not jobs_dir.is_dir():
        return {}
    for result_path in sorted(jobs_dir.rglob("result.json")):
        trial_dir = result_path.parent
        if not ((trial_dir / "agent").is_dir() and (trial_dir / "verifier").is_dir()):
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
        timing = result.get("agent_execution") or {}
        finished = timing.get("finished_at")
        cell = Cell(
            variant_id=variant_id,
            task_id=task_id,
            status=status,
            reward=reward,
            job_path=str(trial_dir),
            finished_at=finished or datetime.fromtimestamp(_mtime(result_path), tz=UTC).isoformat(),
            exception_type=str(exception) if exception else None,
        )
        stamp = _mtime(result_path)
        key = (_attempt_seq(trial_dir), str(result_path))
        prev = cells.get(task_id)
        if prev is None or stamp > prev[0] or (stamp == prev[0] and key < (prev[1], prev[2])):
            cells[task_id] = (stamp, key[0], key[1], cell)
    return {task: cell for task, (_, _, _, cell) in cells.items()}


def missing_tasks(cells: dict[str, Cell], all_tasks: list[str]) -> list[str]:
    return [t for t in all_tasks if t not in cells]


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
    variant_id: str, trial_dir: Path, result: dict, task_id: str, stamp: float
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
    timing = result.get("agent_execution") or {}
    finished = timing.get("finished_at")
    return Cell(
        variant_id=variant_id,
        task_id=task_id,
        status=status,
        reward=reward,
        job_path=str(trial_dir),
        finished_at=finished or datetime.fromtimestamp(stamp, tz=UTC).isoformat(),
        exception_type=str(exception) if exception else None,
    )


def reconcile_variant_incremental(
    variant_id: str,
    jobs_dir: Path,
    known_tasks: set[str],
    file_state: dict[str, tuple[float, str, Cell | None]],
) -> tuple[dict[str, Cell], int]:
    """Delta reconcile: parse only new/changed result.json files.

    file_state maps result path -> (mtime, task_id or "", cell or None).
    Mutated in place. Returns (cells, parsed_count). Winner semantics match
    reconcile_variant exactly; unchanged files cost a stat, not a parse.
    """
    parsed = 0
    if not jobs_dir.is_dir():
        file_state.clear()
        return {}, 0
    seen: set[str] = set()
    for result_path in sorted(jobs_dir.rglob("result.json")):
        trial_dir = result_path.parent
        if not ((trial_dir / "agent").is_dir() and (trial_dir / "verifier").is_dir()):
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
        cell = _cell_from_result(variant_id, trial_dir, result, task_id, stamp)
        file_state[key] = (stamp, task_id, cell)
    for stale in [k for k in file_state if k not in seen]:
        del file_state[stale]
    winners: dict[str, tuple[float, int, str, Cell]] = {}
    for key, (stamp, task_id, cell) in file_state.items():
        if not task_id or cell is None:
            continue
        trial_dir = Path(key).parent
        seq_path = (_attempt_seq(trial_dir), key)
        prev = winners.get(task_id)
        if prev is None or stamp > prev[0] or (stamp == prev[0] and seq_path < (prev[1], prev[2])):
            winners[task_id] = (stamp, seq_path[0], seq_path[1], cell)
    return {task: cell for task, (_, _, _, cell) in winners.items()}, parsed
