"""Experiment controller: states, typed events, resume, auto-report.

States: DRAFT -> VALIDATING -> BUILDING -> READY -> RUNNING ->
(FINALIZING -> COMPLETE | CANCELLING -> CANCELLED | FAILED), and
FAILED/CANCELLED -> RECONCILING -> READY on resume.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION, __version__
from roast_my_harness.auth import staging
from roast_my_harness.errors import PierError
from roast_my_harness.files import atomic_write_text
from roast_my_harness.homes.builder import build_home
from roast_my_harness.observability import RunLogger
from roast_my_harness.paths import homes_cache_dir
from roast_my_harness.report import collect as report_collect
from roast_my_harness.report import exports as report_exports
from roast_my_harness.report import markdown as report_markdown
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import process as process_mod
from roast_my_harness.runner.reconcile import Cell, missing_tasks, reconcile_variant
from roast_my_harness.spec.hashes import spec_hash as compute_spec_hash
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.store import controls as controls_mod
from roast_my_harness.store.controls import ReuseDecision
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash as compute_task_hash

POLL_INTERVAL_SEC = 2.0

ProgressCallback = Callable[[str], None]
AskCallback = Callable[[str], bool]


# ---------------------------------------------------------- controller ----

@dataclass
class VariantJob:
    variant_id: str
    home: Path
    staged: Path
    manifest_path: Path
    proc: process_mod.VariantProcess | None = None


class ExperimentController:
    def __init__(
        self,
        spec: ExperimentSpec,
        experiment_id: str,
        run_dir: Path,
        store: Repository,
        progress: ProgressCallback | None = None,
        ask: AskCallback | None = None,
    ):
        self.spec = spec
        self.experiment_id = experiment_id
        self.run_dir = run_dir
        self.store = store
        self.progress = progress
        self.ask = ask
        self.state = "DRAFT"
        self.jobs: dict[str, VariantJob] = {}
        self.cells: dict[str, dict[str, Cell]] = {}
        self._cancel_event = asyncio.Event()
        self._logger = RunLogger(self.run_dir / "logs" / "run.jsonl", experiment_id)

        self._control_hash: str | None = None
        self._cohort_keys: dict[str, str] = {}  
        self._task_hashes: dict[str, str] = {}  
        self._reuse_plan: ReuseDecision | None = None
        self._reuse_enabled = True  
        self._reuse_accepted: bool | None = None  
        self._sentinel_verdict: dict | None = None
        self._own_trial_ids: set[str] = set()
        self._observed_reused_tasks: set[str] = set()
        self._observed_task_ids: list[str] | None = None

    # ------------------------------------------------------------ events --

    def _progress(self, message: str) -> None:
        self._logger.emit("progress", state=self.state, message=message)
        if self.progress is not None:
            self.progress(message)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.store.set_status(self.experiment_id, state, started=state == "RUNNING")
        self._progress(f"state: {state}")

    # --------------------------------------------------------- prepare ----

    def prepare(self, spec_path: Path | None = None) -> None:
        """Idempotent: create run dir, records, homes, staged credentials."""
        self._set_state("VALIDATING")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        staging.force_remove(self.run_dir / "staging")
        if spec_path is not None:
            target = self.run_dir / "experiment.toml"
            if not target.exists():
                shutil.copy2(spec_path, target)

        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        s_hash = compute_spec_hash(self.spec)
        self.store.create_experiment(
            experiment_id=self.experiment_id,
            name=self.spec.name,
            spec=self.spec.model_dump(mode="json"),
            spec_hash=s_hash,
            run_dir=str(self.run_dir),
        )
        self.store.upsert_tasks(
            self.experiment_id,
            [(t.task_id, compute_task_hash(t.path), str(t.path)) for t in tasks],
        )

        self._set_state("BUILDING")
        homes_root = homes_cache_dir()
        for variant in self.spec.arms():
            build = build_home(variant, self.spec, homes_root)
            staged = staging.stage_home(
                build.path,
                self.run_dir / "staging" / variant.id,
                self.spec,
            )
            manifest_path = staged / "variant.json"
            self.jobs[variant.id] = VariantJob(
                variant_id=variant.id,
                home=build.path,
                staged=staged,
                manifest_path=manifest_path,
            )
            self.store.upsert_variant(
                self.experiment_id,
                variant.id,
                variant.name,
                build.variant_hash,
                variant.id == "control",
                build.manifest.model_dump(mode="json"),
            )

        self._write_manifest(tasks)
        self._plan_control_reuse(tasks)
        self._set_state("READY")

    def load_for_observation(self) -> None:
        """Load an existing run without rebuilding homes or changing state."""
        row = self.store.get_experiment(self.experiment_id)
        if row is None:
            raise PierError(f"unknown experiment {self.experiment_id}")
        self.state = str(row["status"])
        self.jobs = {
            variant.id: VariantJob(
                variant_id=variant.id,
                home=Path(),
                staged=self.run_dir / "staging" / variant.id,
                manifest_path=self.run_dir / "staging" / variant.id / "variant.json",
            )
            for variant in self.spec.arms()
        }
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text())
            except (json.JSONDecodeError, OSError):
                manifest = {}
            if not isinstance(manifest, dict):
                manifest = {}
            task_map = manifest.get("tasks") or {}
            if isinstance(task_map, dict):
                self._observed_task_ids = list(task_map)
            reuse = manifest.get("control_reuse") or {}
            if isinstance(reuse, dict) and reuse.get("accepted") is True:
                self._observed_reused_tasks = set(reuse.get("reused_tasks", []))

    def cleanup_staging(self) -> None:
        """Remove all staged homes, including partially prepared variants."""
        staging.force_remove(self.run_dir / "staging")

    def _task_ids(self) -> list[str]:
        if self._observed_task_ids is not None:
            return list(self._observed_task_ids)
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        return [task.task_id for task in tasks]

    def _plan_control_reuse(self, tasks) -> None:
        """Compute cohort keys and the reuse plan for an enabled control."""
        self._reuse_plan = None
        self._reuse_accepted = None
        self._sentinel_verdict = None
        control = self.spec.control
        if control is None or not control.enabled:
            return
        control_variant = next((v for v in self.spec.arms() if v.id == "control"), None)
        if control_variant is None:
            return
        build = build_home(control_variant, self.spec, homes_cache_dir())
        self._control_hash = build.variant_hash
        self._task_hashes = {
            t.task_id: compute_task_hash(t.path) for t in tasks
        }
        self._cohort_keys = {
            task_id: controls_mod.cohort_key_for_task(
                control_variant_hash=self._control_hash,
                spec=self.spec,
                task_hash=task_hash,
            )
            for task_id, task_hash in self._task_hashes.items()
        }
        pools_by_task: dict[str, list] = {}
        for task_id, task_hash in self._task_hashes.items():
            pools_by_task[task_id] = self.store.control_pool(
                self._cohort_keys[task_id], task_hash
            )
        seed = int(compute_spec_hash(self.spec)[:8], 16)
        sentinel_ids = controls_mod.sentinel_sample(
            list(self._task_hashes), control.sentinel_tasks, seed
        )
        self._sentinel_task_ids = sentinel_ids
        self._reuse_plan = controls_mod.plan_reuse(
            policy=control.reuse,
            pools=pools_by_task,
            minimum_runs=control.minimum_runs_per_task,
            maximum_age_days=control.maximum_age_days,
            sentinel_tasks=sentinel_ids,
        )

    def _write_manifest(self, tasks) -> None:
        manifest = {
            "experiment_id": self.experiment_id,
            "spec_hash": compute_spec_hash(self.spec),
            "tool_version": __version__,
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
            "pi_version": self.spec.pi_version,
            "pier_version": self.spec.pier_version,
            "model": self.spec.model.model_dump(mode="json"),
            "thinking": self.spec.thinking,
            "created_at": datetime.now(UTC).isoformat(),
            "tasks_path": str(self.spec.tasks.path),
            "tasks": {t.task_id: compute_task_hash(t.path) for t in tasks},
            "variants": {
                v.variant_id: {
                    "variant_hash": _hash_of(self, v.variant_id),
                    "manifest": str(v.manifest_path),
                }
                for v in self.jobs.values()
            },
        }
        atomic_write_text(
            self.run_dir / "manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )

    async def run(self) -> str:
        """Execute until COMPLETE, CANCELLED, or FAILED. Returns final state."""
        if self.state in ("CANCELLED", "FAILED", "COMPLETE"):
            self._set_state("RECONCILING")
            self._refresh_cells()
            self._set_state("READY")
        try:
            self._launch()
        except Exception as e:
            self._fail(e)
            return self.state
        self._set_state("RUNNING")
        try:
            await self._watch()
            if self._held_controls_pending():
                self._evaluate_sentinel()
                if self._reuse_accepted is not True:
                    self._launch()
                    await self._watch()
        except asyncio.CancelledError:
            await self._cancel("CANCELLED")
            raise
        except Exception as e:
            await self._cancel("FAILED")
            self._fail(e)
            return self.state
        if self._cancel_event.is_set():
            await self._cancel("CANCELLED")
        else:
            self._finalize()
        return self.state

    def enforce_reuse_policy(self, *, interactive: bool) -> bool:
        """Apply ControlSpec.reuse before launch (plan section 16).

        require: fail when nothing is reusable.
        ask: show the pool and confirm; non-interactive falls back to fresh.
        """
        if self._reuse_plan is None:
            return True
        
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        self._plan_control_reuse(tasks)
        reusable = [t for t, r in self._reuse_plan.reuse_by_task.items() if r]
        control = self.spec.control
        assert control is not None
        if control.reuse == "require" and not reusable:
            raise ValueError(
                "control reuse = require but no task meets "
                f"minimum_runs_per_task={control.minimum_runs_per_task} "
                f"within maximum_age_days={control.maximum_age_days}"
            )
        if control.reuse == "ask" and reusable:
            if not interactive:
                self._reuse_enabled = False
                return True
            
            
            
            
            
            counts = self._reuse_plan.pool_counts
            lines = [
                f"historic control pool: {len(reusable)} task(s) meet "
                f"minimum_runs={control.minimum_runs_per_task} "
                f"within {control.maximum_age_days}d"
            ]
            for task in sorted(reusable):
                lo, hi = self._reuse_plan.pool_date_ranges.get(task, ("", ""))
                span = f" {lo[:10]}..{hi[:10]}" if lo else ""
                lines.append(f"  {task}: {counts.get(task, 0)} observations{span}")
            lines.append("(a sentinel subset still runs fresh to detect drift)")
            message = "\n".join(lines)
            if self.ask is None:
                self._reuse_enabled = False
                self._progress(
                    "control reuse disabled (non-interactive); all control tasks run fresh"
                )
            elif self.ask(message):
                self._progress("control reuse accepted; sentinel subset runs fresh")
            else:
                self._reuse_enabled = False
                self._progress("control reuse disabled; all control tasks run fresh")
            return True
        return True

    def _held_controls_pending(self) -> bool:
        """True when reuse-planned control tasks are held for the sentinel."""
        return (
            self._reuse_plan is not None
            and self._reuse_enabled
            and self._reuse_accepted is None
            and any(self._reuse_plan.reuse_by_task.values())
        )

    def _evaluate_sentinel(self) -> None:
        """Gate reuse on fresh sentinel outcomes vs pooled history."""
        assert self._reuse_plan is not None
        fresh: list[tuple[str, bool]] = []
        for task_id in getattr(self, "_sentinel_task_ids", []):
            cell = self.cells.get("control", {}).get(task_id)
            if cell is not None and cell.status in ("pass", "fail"):
                fresh.append((task_id, cell.status == "pass"))
        historic: dict[str, list[bool]] = {}
        for task_id in self._task_hashes:
            rows = self.store.control_pool(
                self._cohort_keys[task_id], self._task_hashes[task_id]
            )
            terminal = [
                r for r in rows
                if r["resolved"] is not None
                and r["trial_id"] not in self._own_trial_ids
            ]
            historic[task_id] = [bool(r["resolved"]) for r in terminal]
        verdict = controls_mod.sentinel_verdict(fresh=fresh, historic=historic)
        self._sentinel_verdict = verdict
        if verdict["reject"]:
            self._reuse_accepted = False
        elif not verdict["informative"] and self.spec.control is not None \
                and self.spec.control.reuse != "never":
            
            self._reuse_accepted = False
        else:
            self._reuse_accepted = True

    def _launch(self) -> None:
        """Prepare process objects per variant with only missing tasks."""
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        self._refresh_cells()
        held = self._held_tasks() if self._held_controls_pending() else set()
        for job in self.jobs.values():
            missing = missing_tasks(self.cells.get(job.variant_id, {}), all_ids)
            if job.variant_id == "control" and self._reuse_plan is not None:
                missing = [t for t in missing if t not in held]
            if not missing:
                continue
            argv = pier_mod.build_run_args(
                task_root=self.spec.tasks.path,
                jobs_dir=self.run_dir / "jobs" / job.variant_id,
                job_name=f"{self.experiment_id}-{job.variant_id}",
                manifest_path=job.manifest_path,
                model_id=self.spec.model.full_id(),
                thinking=self.spec.thinking,
                pi_version=self.spec.pi_version,
                n_concurrent=self.spec.concurrency.per_variant,
                include_tasks=missing,
            )
            log = self.run_dir / "logs" / f"{job.variant_id}.log"
            job.proc = process_mod.VariantProcess(job.variant_id, argv, log)
            self._progress(f"launch {job.variant_id}: {len(missing)} task(s)")

    def _held_tasks(self) -> set[str]:
        """Control tasks whose history satisfies reuse while the sentinel gate
        is still undecided."""
        if self._reuse_plan is None:
            return set()
        return {
            task_id
            for task_id, reuse in self._reuse_plan.reuse_by_task.items()
            if reuse
        }

    async def _watch(self) -> None:
        env = self._pier_env()
        to_start = [j.proc for j in self.jobs.values() if j.proc is not None]
        if to_start:
            await asyncio.gather(*(proc.start(env) for proc in to_start))
        process_mod.require_all_started(
            [j.proc for j in self.jobs.values() if j.proc is not None]
        )
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        while True:
            if self._cancel_event.is_set():
                return
            procs = [j.proc for j in self.jobs.values() if j.proc is not None]
            if not procs or not any(p.running for p in procs):
                self._refresh_cells()
                return
            self._poll_once(all_ids)
            await asyncio.sleep(POLL_INTERVAL_SEC)

    def _poll_once(self, all_ids: list[str]) -> None:
        previous = {
            (v, t): c.status for v, cells in self.cells.items() for t, c in cells.items()
        }
        self._refresh_cells()
        for variant_id, cells in self.cells.items():
            for task_id, cell in cells.items():
                key = (variant_id, task_id)
                if previous.get(key) == cell.status:
                    continue
                trial_id = self.store.upsert_reconciled_trial(
                    experiment_id=self.experiment_id,
                    variant_id=variant_id,
                    task_id=task_id,
                    status=cell.status,
                    job_path=cell.job_path,
                    reward=cell.reward,
                    resolved=None if cell.status == "error" else cell.status == "pass",
                    exception_type=cell.exception_type,
                    metrics=None,
                    finished_at=cell.finished_at,
                )
                self._own_trial_ids.add(trial_id)
                self._record_control_observation(variant_id, task_id, cell, trial_id)
                self._logger.emit(
                    "trial",
                    variant=variant_id,
                    task=task_id,
                    status=cell.status,
                    reward=cell.reward,
                    exception_type=cell.exception_type,
                )
                self._progress(
                    f"{variant_id}/{task_id}: {cell.status}"
                    + (f" reward={cell.reward}" if cell.status != "error" else "")
                )

        for job in self.jobs.values():
            proc = job.proc
            if proc is not None and not proc.running and not getattr(
                proc, "_exit_emitted", False
            ):
                proc._exit_emitted = True  # type: ignore[attr-defined]
                code = proc.proc.returncode if proc.proc else None
                self._progress(f"{job.variant_id} exited rc={code}")

    def _refresh_cells(self) -> None:
        known = set(self._task_ids())
        for variant_id in self.jobs:
            self.cells[variant_id] = reconcile_variant(
                variant_id, self.run_dir / "jobs" / variant_id, known
            )

    def _record_control_observation(
        self, variant_id: str, task_id: str, cell: Cell, trial_id: str
    ) -> None:
        """Fresh terminal control outcomes feed the reusable pool."""
        if variant_id != "control" or cell.status not in ("pass", "fail"):
            return
        if self._reuse_plan is None or self._cohort_keys.get(task_id) is None:
            return
        self.store.record_control_observation(
            cohort_key=self._cohort_keys[task_id],
            task_hash=self._task_hashes[task_id],
            trial_id=trial_id,
            resolved=cell.status == "pass",
            reward=cell.reward if cell.reward is not None else 0.0,
            observed_at=cell.finished_at or datetime.now(UTC).isoformat(),
            eligible=True,
            source=f"experiment:{self.experiment_id}",
        )


    # ----------------------------------------------------------- cancel --

    def request_cancel(self) -> None:
        self._cancel_event.set()

    async def _cancel(self, final_state: str) -> None:
        self._set_state("CANCELLING")
        procs = [j.proc for j in self.jobs.values() if j.proc is not None]
        await process_mod.cancel_all(procs)
        self._refresh_cells()
        self._record_all_cells()
        self.cleanup_staging()
        leaks = staging.scan_for_secrets(self.run_dir)
        if leaks:
            self._logger.emit("secret_scan", hits=leaks)
        self._set_state(final_state)

    # --------------------------------------------------------- finalize --

    def _finalize(self) -> None:
        self._set_state("FINALIZING")
        self._refresh_cells()
        self._record_all_cells()
        self.cleanup_staging()
        rows = report_collect.collect_rows(self.run_dir / "jobs")
        provenance = self._provenance([])
        csv = report_exports.write_summary_csv(self.run_dir, rows)
        report_exports.write_summary_json(self.run_dir, rows, provenance)
        report = report_markdown.generate_report(
            self.run_dir,
            experiment_id=self.experiment_id,
            provenance=provenance,
            rows=rows,
        )
        leaks = staging.scan_for_secrets(self.run_dir)
        if leaks:
            self._logger.emit("secret_scan", hits=leaks)
            provenance = self._provenance(leaks)
            report_exports.write_summary_json(self.run_dir, rows, provenance)
            report = report_markdown.generate_report(
                self.run_dir,
                experiment_id=self.experiment_id,
                provenance=provenance,
                rows=rows,
            )
        self._set_state("COMPLETE")
        self._progress(f"reports written: {csv}, {report}")

    def _record_all_cells(self) -> None:
        for variant_id, cells in self.cells.items():
            for task_id, cell in cells.items():
                trial_id = self.store.upsert_reconciled_trial(
                    experiment_id=self.experiment_id,
                    variant_id=variant_id,
                    task_id=task_id,
                    status=cell.status,
                    job_path=cell.job_path,
                    reward=cell.reward,
                    resolved=None if cell.status == "error" else cell.status == "pass",
                    exception_type=cell.exception_type,
                    metrics=None,
                    finished_at=cell.finished_at,
                )
                self._own_trial_ids.add(trial_id)
                self._record_control_observation(variant_id, task_id, cell, trial_id)

    def _provenance(self, secret_hits: list[str]) -> dict[str, Any]:
        manifest: dict[str, Any] = {}
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text())
            except json.JSONDecodeError:
                manifest = {}
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest["secret_scan_scope"] = "all regular run artifacts after staging cleanup"
        manifest["secret_scan_hits"] = secret_hits
        manifest["control_reuse"] = self.reuse_summary()
        manifest["reused_control_observations"] = self.reuse_summary().get(
            "total_reused", 0
        )
        # Persist the merged manifest so `report <id>` reproduces this
        # provenance (and the control-reuse disclosure) byte for byte.
        atomic_write_text(
            self.run_dir / "manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        return manifest

    def reuse_summary(self) -> dict[str, Any]:
        """Disclosure payload for the report (plan section 16)."""
        if self._reuse_plan is None:
            return {"enabled": False, "total_reused": 0}
        reused = self._reused_tasks() if self._reuse_accepted else set()
        counts = {
            task: self._reuse_plan.pool_counts.get(task, 0)
            for task in reused
        }
        ranges = {
            task: list(self._reuse_plan.pool_date_ranges.get(task, ("", "")))
            for task in reused
        }
        fresh_control = sorted(set(self._reuse_plan.reuse_by_task) - reused)
        summary: dict[str, Any] = {
            "enabled": True,
            "policy": self.spec.control.reuse if self.spec.control else "never",
            "accepted": self._reuse_accepted,
            "reused_tasks": sorted(reused),
            "reused_counts": counts,
            "reused_date_ranges": ranges,
            "fresh_control_tasks": fresh_control,
            "total_reused": sum(counts.values()),
        }
        if self._sentinel_verdict is not None:
            summary["sentinel"] = self._sentinel_verdict
        return summary

    def fail_setup(self, error: Exception) -> None:
        """Record a preparation failure and remove partial staged homes."""
        self._logger.emit(
            "error",
            exception_type=type(error).__name__,
            message=str(error),
        )
        self.state = "FAILED"
        self.store.set_status(self.experiment_id, "FAILED", finished=True)
        self._progress("state: FAILED")
        self.cleanup_staging()

    def _fail(self, error: Exception) -> None:
        self.fail_setup(error)
        raise PierError(str(error)) from error

    # ------------------------------------------------------------- env ---

    def _pier_env(self) -> dict[str, str]:
        """Env for pier: PYTHONPATH must expose the stdlib-only adapter."""
        import os

        env = dict(os.environ)
        package_parent = str(Path(__file__).resolve().parent.parent)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            package_parent + (os.pathsep + existing if existing else "")
        )
        return env

    # --------------------------------------------------------- snapshot --

    def snapshot(self) -> dict[str, Any]:
        """Current matrix + aggregates for the CLI. Cheap to poll."""
        self._refresh_cells()
        all_ids = self._task_ids()
        matrix: dict[str, dict[str, str]] = {}
        held = self._held_tasks() if self._held_controls_pending() else set()
        reused = self._reused_tasks()
        for variant_id in self.jobs:
            cells = self.cells.get(variant_id, {})
            row: dict[str, str] = {}
            for task_id in all_ids:
                if task_id in cells:
                    row[task_id] = cells[task_id].status[0].upper()
                elif variant_id == "control" and task_id in reused:
                    row[task_id] = "H"
                elif variant_id == "control" and task_id in held:
                    row[task_id] = "."
                else:
                    row[task_id] = _running_or_pending(
                        self.run_dir / "jobs" / variant_id, task_id
                    )
            matrix[variant_id] = row
        return {"state": self.state, "matrix": matrix, "tasks": all_ids}

    def _reused_tasks(self) -> set[str]:
        """Control tasks covered by accepted history after the sentinel gate."""
        if self._reuse_plan is None:
            return self._observed_reused_tasks
        if self._reuse_accepted is not True:
            return set()
        return {
            task_id
            for task_id, reuse in self._reuse_plan.reuse_by_task.items()
            if reuse
        }


def _running_or_pending(jobs_variant_dir: Path, task_id: str) -> str:
    if not jobs_variant_dir.is_dir():
        return "."
    for trial_dir in jobs_variant_dir.rglob(f"{task_id}__*"):
        if trial_dir.is_dir() and not (trial_dir / "result.json").exists():
            return "~"
    return "."


def _hash_of(controller: ExperimentController, variant_id: str) -> str:
    row = controller.store.conn.execute(
        "SELECT variant_hash FROM variants WHERE experiment_id=? AND id=?",
        (controller.experiment_id, variant_id),
    ).fetchone()
    return row["variant_hash"] if row else ""
