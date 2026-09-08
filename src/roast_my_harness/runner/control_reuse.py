"""Controller-facing state for historic control reuse."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from roast_my_harness.evals.registry import cohort_eval_id
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
        self.selected_task_ids: list[str] = []
        self.enabled = True
        self.accepted: bool | None = None
        self.status: str | None = None
        self.abort: bool = False
        self.verdict: dict[str, Any] | None = None
        self.observed_reused_tasks: set[str] = set()

    def plan_for(
        self,
        tasks: list[Any],
        control_hash: str,
        *,
        resolved_versions: dict[str, str] | None = None,
    ) -> None:
        self.plan = None
        self.accepted = None
        self.status = None
        self.abort = False
        self.verdict = None
        control = self.spec.control
        if control is None or not control.enabled:
            return
        control_agent = self.spec.resolved_agents()["control"]
        if resolved_versions is not None and control_agent in resolved_versions:
            agent_version = resolved_versions[control_agent]
        else:
            agent_version = self.spec.resolved_version_for(control_agent)
        self.task_hashes = {
            task.task_id: compute_task_hash(task.path) for task in tasks
        }
        eval_id = cohort_eval_id(self.spec)
        self.cohort_keys = {
            task_id: control_cohort_key(
                control_hash,
                self.spec.model,
                self.spec.thinking,
                task_hash,
                agent=control_agent,
                agent_version=agent_version,
                eval_id=eval_id,
            )
            for task_id, task_hash in self.task_hashes.items()
        }
        pools = self.store.control_pools(
            self.cohort_keys,
            self.task_hashes,
            exclude_experiment_id=self.experiment_id,
        )
        seed = int(spec_hash(self.spec)[:8], 16)
        self.selected_task_ids = [task.task_id for task in tasks]
        self.plan = controls.plan_reuse(
            mode=control.mode,
            scope=control.history_scope,
            selected=self.selected_task_ids,
            pools=pools,
            minimum_runs=control.minimum_runs_per_task,
            maximum_age_days=control.maximum_age_days,
            sentinel_count=control.sentinel_tasks,
            seed=seed,
        )
        self.sentinel_task_ids = list(self.plan.sentinel_tasks)
        self.status = self.plan.status

    def enforce(self, *, progress: Callable[[str], None]) -> None:
        """Validate the stored historic policy; fail when history is absent.

        The policy is fully determined by the spec: mode historic with no
        eligible task is a configuration error, not an interactive
        question. Fresh mode needs nothing.
        """
        if self.plan is None:
            return
        control = self.spec.control
        assert control is not None
        if control.mode == "fresh":
            return
        eligible = self.plan.eligible_tasks
        if not eligible:
            raise ValueError(
                "control mode = historic but no task has "
                f"minimum_runs_per_task={control.minimum_runs_per_task} "
                f"observations within maximum_age_days={control.maximum_age_days}"
            )
        progress(
            f"historic control ({self.plan.status}): {len(eligible)}/"
            f"{len(self.selected_task_ids)} tasks eligible, "
            f"scope={control.history_scope}, "
            f"sentinels={len(self.sentinel_task_ids)}"
        )

    def held_tasks(self) -> set[str]:
        if self.plan is None:
            return set()
        return {
            task_id
            for task_id, reuse in self.plan.reuse_by_task.items()
            if reuse
        }

    def out_of_scope_tasks(self) -> set[str]:
        """Control tasks with no trial in either wave (intersection scope).

        Hybrid and fresh scopes run the full selection, so only an
        intersection plan leaves tasks out.
        """
        if self.plan is None:
            return set()
        return set(self.selected_task_ids) - set(self.plan.control_tasks)

    def held_pending(self) -> bool:
        return (
            self.plan is not None
            and self.enabled
            and self.accepted is None
            and bool(self.held_tasks())
        )

    def evaluate(self, cells: dict[str, dict[tuple[str, int], Any]]) -> None:
        assert self.plan is not None
        control = self.spec.control
        if control is None or control.mode == "fresh":
            self.status = "accepted"
            self.accepted = True
            return
        fresh: list[tuple[str, bool]] = []
        control_cells = cells.get("control", {})
        for task_id in self.sentinel_task_ids:
            for (trial_task, _replicate), cell in sorted(
                control_cells.items(), key=lambda kv: kv[0]
            ):
                if trial_task != task_id:
                    continue
                if cell is not None and cell.status in ("pass", "fail"):
                    fresh.append((task_id, cell.status == "pass"))
        historic = {
            task_id: [
                bool(row["resolved"])
                for row in controls.observations_within_age(
                    self.store.control_pool(
                        self.cohort_keys[task_id],
                        self.task_hashes[task_id],
                        exclude_experiment_id=self.experiment_id,
                    ),
                    control.maximum_age_days,
                )
                if row["resolved"] is not None
            ]
            for task_id in self.task_hashes
        }
        self.verdict = controls.sentinel_verdict(fresh=fresh, historic=historic)
        self.status, self.accepted, self.abort = controls.acceptance_state(
            verdict=self.verdict,
            on_drift=control.on_drift,
            on_inconclusive=control.on_inconclusive,
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

    def baseline_rates(self) -> dict[str, dict[str, float | int]]:
        """Per-task historic pass/total for reused tasks, age-bounded.

        The report renders these as the labeled historical baseline next
        to fresh extension rates; they never mix into paired comparisons.
        """
        out: dict[str, dict[str, float | int]] = {}
        if self.plan is None or self.spec.control is None:
            return out
        maximum_age = self.spec.control.maximum_age_days
        for task_id in sorted(self.reused_tasks()):
            if task_id not in self.cohort_keys:
                continue
            rows = controls.observations_within_age(
                [
                    row
                    for row in self.store.control_pool(
                        self.cohort_keys[task_id],
                        self.task_hashes[task_id],
                        exclude_experiment_id=self.experiment_id,
                    )
                    if row["resolved"] is not None
                ],
                maximum_age,
            )
            passed = sum(1 for row in rows if row["resolved"])
            out[task_id] = {
                "pass": passed,
                "total": len(rows),
                "rate": passed / len(rows) if rows else 0.0,
            }
        return out

    def summary(self) -> dict[str, Any]:
        if self.plan is None:
            return {"enabled": False, "total_reused": 0}
        control = self.spec.control
        reused = self.reused_tasks()
        counts = {task: self.plan.pool_counts.get(task, 0) for task in reused}
        ranges = {
            task: list(self.plan.pool_date_ranges.get(task, ("", "")))
            for task in reused
        }
        summary: dict[str, Any] = {
            "enabled": True,
            "mode": control.mode if control else "fresh",
            "history_scope": control.history_scope if control else "hybrid",
            "policy": {
                "minimum_runs_per_task": control.minimum_runs_per_task if control else 0,
                "maximum_age_days": control.maximum_age_days if control else 0,
                "sentinel_tasks": control.sentinel_tasks if control else 0,
                "on_drift": control.on_drift if control else "fresh",
                "on_inconclusive": control.on_inconclusive if control else "fresh",
            },
            "status": self.status,
            "accepted": self.accepted,
            "abort": self.abort,
            "eligible_tasks": sorted(self.plan.eligible_tasks),
            "control_tasks": sorted(self.plan.control_tasks),
            "out_of_scope_tasks": sorted(self.out_of_scope_tasks()),
            "reused_tasks": sorted(reused),
            "reused_counts": counts,
            "reused_date_ranges": ranges,
            "baseline": self.baseline_rates(),
            "fresh_control_tasks": sorted(set(self.plan.control_tasks) - reused),
            "total_reused": sum(counts.values()),
        }
        if self.verdict is not None:
            summary["sentinel"] = self.verdict
        return summary
