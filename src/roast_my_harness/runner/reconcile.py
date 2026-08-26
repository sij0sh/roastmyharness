"""Trial reconciliation from pier job directories, never from pier stdout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PASS_THRESHOLD = 0.999


@dataclass(frozen=True)
class Cell:
    variant_id: str
    task_id: str
    status: str  # pass | fail | error
    reward: float
    job_path: str
    finished_at: str | None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def reconcile_variant(
    variant_id: str, jobs_dir: Path, known_tasks: set[str]
) -> dict[str, Cell]:
    """Newest valid attempt per task from trial dirs under jobs/<variant>/.

    A trial directory must contain agent/ and verifier/ plus result.json.
    """
    cells: dict[str, tuple[float, Cell]] = {}
    if not jobs_dir.is_dir():
        return {}
    for result_path in sorted(jobs_dir.rglob("result.json")):
        trial_dir = result_path.parent
        if not (
            (trial_dir / "agent").is_dir() and (trial_dir / "verifier").is_dir()
        ):
            continue  # job-level summary, not a trial
        try:
            result = json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        task_id = str(result.get("task_name") or trial_dir.name)
        if known_tasks and task_id not in known_tasks:
            
            
            
            short = task_id.rsplit("/", 1)[-1]
            base = trial_dir.name.split("__", 1)[0]
            for candidate in (short, base):
                if candidate in known_tasks:
                    task_id = candidate
                    break
            else:
                continue
        exception = (result.get("exception_info") or {}).get("exception_type")
        if exception:
            status = "error"
            reward = 0.0
        else:
            verifier = result.get("verifier_result") or {}
            reward = verifier.get("rewards", {}).get("reward")
            if reward is None:
                reward_path = trial_dir / "verifier" / "reward.json"
                if reward_path.is_file():
                    try:
                        reward = json.loads(reward_path.read_text()).get("reward")
                    except (json.JSONDecodeError, OSError):
                        reward = None
            if reward is None:
                continue  # incomplete, not terminal
            reward = float(reward)
            status = "pass" if reward >= PASS_THRESHOLD else "fail"
        timing = (result.get("agent_execution") or {})
        finished = timing.get("finished_at")
        cell = Cell(
            variant_id=variant_id,
            task_id=task_id,
            status=status,
            reward=reward,
            job_path=str(trial_dir),
            finished_at=finished
            or datetime.fromtimestamp(
                _mtime(result_path), tz=UTC
            ).isoformat(),
        )
        stamp = _mtime(result_path)
        if task_id not in cells or stamp >= cells[task_id][0]:
            cells[task_id] = (stamp, cell)
    return {task: cell for task, (_, cell) in cells.items()}


def missing_tasks(cells: dict[str, Cell], all_tasks: list[str]) -> list[str]:
    return [t for t in all_tasks if t not in cells]
