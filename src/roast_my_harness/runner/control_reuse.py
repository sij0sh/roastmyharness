"""Controller-facing state for historic control reuse."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from roast_my_harness.spec.hashes import control_cohort_key, spec_hash
from roast_my_harness.store import controls
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.hashes import task_hash as compute_task_hash


class ControlReuse:
    def __init__(self, spec: Any, experiment_id: str, store: Repository):
        self.spec = spec
        self.experiment_id = experiment_id
        self.store = store
        self.plan: controls.ReuseDecision | None = None
        self.task_hashes: dict[str, str] = {}
        self.cohort_keys: dict[str, str] = {}
        self.sentinel_task_ids: list[str] = []
        self.enabled = True
        self.accepted: bool | None = None
        self.verdict: dict[str, Any] | None = None
        self.observed_reused_tasks: set[str] = set()

    def plan_for(self, tasks: list[Any], control_hash: str) -> None:
        self.plan = None
        self.accepted = None
        self.verdict = None
        control = self.spec.control
        if control is None or not control.enabled:
            return
        control_agent = self.spec.resolved_agents()["control"]
        agent_version = self.spec.agent_version_for(control_agent)
        self.task_hashes = {
            task.task_id: compute_task_hash(task.path) for task in tasks
        }
        self.cohort_keys = {
            task_id: control_cohort_key(
                control_hash,
                self.spec.model,
                self.spec.thinking,
                task_hash,
                agent=control_agent,
                agent_version=agent_version,
            )
            for task_id, task_hash in self.task_hashes.items()
        }
        pools = self.store.control_pools(
            self.cohort_keys,
            self.task_hashes,
            exclude_experiment_id=self.experiment_id,
        )
        seed = int(spec_hash(self.spec)[:8], 16)
        self.sentinel_task_ids = controls.sentinel_sample(
            list(self.task_hashes), control.sentinel_tasks, seed
        )
        self.plan = controls.plan_reuse(
            policy=control.reuse,
            pools=pools,
            minimum_runs=control.minimum_runs_per_task,
            maximum_age_days=control.maximum_age_days,
            sentinel_tasks=self.sentinel_task_ids,
        )

    def enforce(
        self,
        *,
        interactive: bool,
        ask: Callable[[str], bool] | None,
        progress: Callable[[str], None],
    ) -> None:
        if self.plan is None:
            return
        control = self.spec.control
        assert control is not None
        reusable = [task for task, reuse in self.plan.reuse_by_task.items() if reuse]
        if control.reuse == "require" and not reusable:
            raise ValueError(
                "control reuse = require but no task meets "
                f"minimum_runs_per_task={control.minimum_runs_per_task} "
                f"within maximum_age_days={control.maximum_age_days}"
            )
        if control.reuse != "ask" or not reusable:
            return
        if not interactive or ask is None:
            self.enabled = False
            progress("control reuse disabled (non-interactive); all controls run fresh")
            return
        lines = [
            f"historic control pool: {len(reusable)} task(s) meet "
            f"minimum_runs={control.minimum_runs_per_task} "
            f"within {control.maximum_age_days}d"
        ]
        for task_id in sorted(reusable):
            lo, hi = self.plan.pool_date_ranges.get(task_id, ("", ""))
            span = f" {lo[:10]}..{hi[:10]}" if lo else ""
            lines.append(
                f"  {task_id}: {self.plan.pool_counts.get(task_id, 0)} observations{span}"
            )
        lines.append("(a sentinel subset still runs fresh to detect drift)")
        if ask("\n".join(lines)):
            progress("control reuse accepted; sentinel subset runs fresh")
        else:
            self.enabled = False
            progress("control reuse disabled; all controls run fresh")

    def held_tasks(self) -> set[str]:
        if self.plan is None:
            return set()
        return {
            task_id
            for task_id, reuse in self.plan.reuse_by_task.items()
            if reuse
        }

    def held_pending(self) -> bool:
        return (
            self.plan is not None
            and self.enabled
            and self.accepted is None
            and bool(self.held_tasks())
        )

    def evaluate(self, cells: dict[str, dict[str, Any]]) -> None:
        assert self.plan is not None
        fresh: list[tuple[str, bool]] = []
        for task_id in self.sentinel_task_ids:
            cell = cells.get("control", {}).get(task_id)
            if cell is not None and cell.status in ("pass", "fail"):
                fresh.append((task_id, cell.status == "pass"))
        maximum_age = self.spec.control.maximum_age_days
        historic = {
            task_id: [
                bool(row["resolved"])
                for row in controls.observations_within_age(
                    self.store.control_pool(
                        self.cohort_keys[task_id],
                        self.task_hashes[task_id],
                        exclude_experiment_id=self.experiment_id,
                    ),
                    maximum_age,
                )
                if row["resolved"] is not None
            ]
            for task_id in self.task_hashes
        }
        self.verdict = controls.sentinel_verdict(fresh=fresh, historic=historic)
        control = self.spec.control
        self.accepted = not self.verdict["reject"] and (
            self.verdict["informative"]
            or control is None
            or control.reuse == "never"
        )

    def record(self, variant_id: str, task_id: str, cell: Any, trial_id: str) -> None:
        if variant_id != "control" or cell.status not in ("pass", "fail"):
            return
        if self.plan is None or task_id not in self.cohort_keys:
            return
        self.store.record_control_observation(
            self.cohort_keys[task_id],
            self.task_hashes[task_id],
            trial_id,
            cell.status == "pass",
            cell.reward if cell.reward is not None else 0.0,
            cell.finished_at or datetime.now(UTC).isoformat(),
            source=f"experiment:{self.experiment_id}",
        )

    def reused_tasks(self) -> set[str]:
        if self.plan is None:
            return self.observed_reused_tasks
        return self.held_tasks() if self.accepted is True else set()

    def load_manifest(self, manifest: dict[str, Any]) -> None:
        reuse = manifest.get("control_reuse") or {}
        if isinstance(reuse, dict) and reuse.get("accepted") is True:
            self.observed_reused_tasks = set(reuse.get("reused_tasks", []))

    def summary(self) -> dict[str, Any]:
        if self.plan is None:
            return {"enabled": False, "total_reused": 0}
        reused = self.reused_tasks()
        counts = {task: self.plan.pool_counts.get(task, 0) for task in reused}
        ranges = {
            task: list(self.plan.pool_date_ranges.get(task, ("", "")))
            for task in reused
        }
        summary: dict[str, Any] = {
            "enabled": True,
            "policy": self.spec.control.reuse if self.spec.control else "never",
            "accepted": self.accepted,
            "reused_tasks": sorted(reused),
            "reused_counts": counts,
            "reused_date_ranges": ranges,
            "fresh_control_tasks": sorted(set(self.plan.reuse_by_task) - reused),
            "total_reused": sum(counts.values()),
        }
        if self.verdict is not None:
            summary["sentinel"] = self.verdict
        return summary
