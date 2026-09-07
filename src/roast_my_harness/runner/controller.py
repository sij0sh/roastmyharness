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
from roast_my_harness.adapter.registry import get_agent
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
from roast_my_harness.runner import probe as probe_mod
from roast_my_harness.runner import process as process_mod
from roast_my_harness.runner.control_reuse import ControlReuse
from roast_my_harness.runner.patch_guard import (
    INFRA_ARTIFACT_COPY,
    INVALID_EMPTY_PATCH,
)
from roast_my_harness.runner.reconcile import (
    Cell,
    is_throttle_error,
    is_timeout_error,
    missing_tasks,
    reconcile_variant,
    reconcile_variant_incremental,
)
from roast_my_harness.spec.hashes import experiment_hash as compute_experiment_hash
from roast_my_harness.spec.hashes import spec_hash as compute_spec_hash
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash as compute_task_hash

POLL_INTERVAL_SEC = 2.0
POLL_MAX_INTERVAL_SEC = 10.0

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
        self.control_reuse = ControlReuse(spec, experiment_id, store)
        self.state = "DRAFT"
        self.jobs: dict[str, VariantJob] = {}
        self.cells: dict[str, dict[str, Cell]] = {}
        self._cancel_event = asyncio.Event()
        self._logger = RunLogger(self.run_dir / "logs" / "run.jsonl", experiment_id)

        self._observed_task_ids: list[str] | None = None
        self._rerun_tasks: set[str] | None = None
        self._rerun_variants: set[str] | None = None
        self._retry_errors: bool = False
        self.smoke_result: probe_mod.ProbeResult | None = None
        self._reconcile_state: dict[str, dict[str, tuple[float, str, Cell | None]]] = {}
        self._last_parse_count = 0
        self._last_tick_sec = 0.0
        self._row_cache: dict[str, Any] = {}
        self._secret_scan_state: dict[str, tuple[float, int, bool]] = {}
        self._finalize_stats: dict[str, float | int | str] = {}

    # ------------------------------------------------------------ events --

    def _progress(self, message: str) -> None:
        self._logger.emit("progress", state=self.state, message=message)
        if self.progress is not None:
            self.progress(message)

    TERMINAL_STATES = frozenset({"COMPLETE", "FAILED", "CANCELLED"})

    def _set_state(self, state: str) -> None:
        self.state = state
        self.store.set_status(
            self.experiment_id,
            state,
            started=state == "RUNNING",
            finished=state in self.TERMINAL_STATES,
        )
        self._progress(f"state: {state}")

    # --------------------------------------------------------- prepare ----

    def prepare(self, spec_path: Path | None = None) -> None:
        """Idempotent: create run dir, records, homes, staged credentials."""
        self._set_state("VALIDATING")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._sweep_stale_staging()
        if spec_path is not None:
            target = self.run_dir / "experiment.toml"
            if not target.exists():
                shutil.copy2(spec_path, target)

        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        pairs = [(t.task_id, compute_task_hash(t.path)) for t in tasks]
        self._assert_identity_current(pairs)
        s_hash = compute_spec_hash(self.spec)
        self.store.create_experiment(
            experiment_id=self.experiment_id,
            name=self.spec.name,
            spec=self.spec.model_dump(mode="json"),
            spec_hash=s_hash,
            run_dir=str(self.run_dir),
        )
        task_rows = [
            (task_id, task_hash, str(task.path))
            for (task_id, task_hash), task in zip(pairs, tasks, strict=True)
        ]
        self.store.upsert_tasks(self.experiment_id, task_rows)

        self._set_state("BUILDING")
        self._throw_if_cancelled()
        homes_root = homes_cache_dir()
        for variant in self.spec.arms():
            self._throw_if_cancelled()
            build = build_home(variant, self.spec, homes_root)
            staged = staging.stage_home(
                build.path,
                self.run_dir / "staging" / variant.id,
                self.spec,
                agent_id=build.manifest.agent,
            )
            self._stage_env(staged, variant)
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
        if "control" in self.jobs:
            self.control_reuse.plan_for(tasks, _hash_of(self, "control"))
        self._set_state("READY")

        if probe_mod.should_probe(self.spec):
            self._throw_if_cancelled()
            self._progress("smoke probe: one task on an extension arm")
            import time as _time

            _probe_start = _time.monotonic()
            try:
                result = probe_mod.run_probe_sync(
                    spec=self.spec,
                    jobs=self.jobs,
                    run_dir=self.run_dir,
                    env=self._pier_env(),
                )
            except probe_mod.ProbeTimeoutError as e:
                self._logger.emit("error", exception_type="ProbeTimeoutError", message=str(e))
                self._fail(PierError(str(e)))
                return
            _probe_sec = _time.monotonic() - _probe_start
            msg = f"smoke probe took {_probe_sec:.1f}s"
            self._logger.emit("progress", state=self.state, message=msg)
            self._throw_if_cancelled()
            self.smoke_result = result
            if not result.ok:
                self._fail(
                    PierError(
                        f"smoke probe failed on variant {result.variant_id} "
                        f"(task {result.task_id}, exit {result.returncode}); "
                        f"see {result.log_path}"
                    )
                )
                return
            self._progress(f"smoke probe passed ({result.task_id} on {result.variant_id})")
        else:
            self.smoke_result = None

    def _assert_identity_current(self, pairs: list[tuple[str, str]]) -> None:
        """Refuse to touch a stored experiment whose task content drifted.

        Identity binds the ordered task id/hash map, so a fresh run with
        changed content gets a new id. Reaching an existing row with a
        different map means the dataset changed mid-flight: refuse instead
        of silently overwriting the stored hashes and reusing old cells.
        """
        stored = self.store.get_tasks(self.experiment_id)
        if not stored:
            return
        stored_pairs = [(row["task_id"], row["task_hash"]) for row in stored]
        if stored_pairs != pairs:
            expected = make_experiment_id(self.spec.name, compute_experiment_hash(self.spec, pairs))
            raise PierError(
                f"task content changed since experiment {self.experiment_id} "
                f"was created; expected identity is now {expected}. Create a "
                "new experiment instead of resuming this one."
            )

    @staticmethod
    def _stage_env(staged: Path, variant) -> None:
        """Write literal env values into the run-only staging dir (0600).

        Cached homes carry names only; this per-run file is deleted by
        cleanup_staging so values stay out of manifests, hashes, and
        reports.
        """
        if not variant.env:
            return
        atomic_write_text(
            staged / "env.json",
            json.dumps(dict(variant.env)) + "\n",
            mode=0o600,
        )

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
            self.control_reuse.load_manifest(manifest)

    def _sweep_stale_staging(self) -> None:
        """Scan crash-leftover staging creds, record the finding, then delete."""
        hits = staging.sweep_stale_staging(self.run_dir)
        if hits:
            self._logger.emit("secret_scan", hits=hits, context="stale-staging-sweep")

    def _throw_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise asyncio.CancelledError

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

    def _write_manifest(self, tasks) -> None:
        agents = self.spec.resolved_agents()
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
            "agents": {
                agent_id: {
                    "family": get_agent(agent_id).family,
                    "import_path": get_agent(agent_id).import_path,
                    "agent_version": self.spec.resolved_version_for(agent_id),
                }
                for agent_id in sorted(set(agents.values()))
            },
            "variants": {
                v.variant_id: {
                    "variant_hash": _hash_of(self, v.variant_id),
                    "manifest": str(v.manifest_path),
                    "agent": agents[v.variant_id],
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
        if self._cancel_event.is_set():
            await self._cancel("CANCELLED")
            return self.state
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
            if self.control_reuse.held_pending():
                self.control_reuse.evaluate(self.cells)
                if self.control_reuse.accepted is not True:
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

    def enforce_reuse_policy(self, *, interactive: bool) -> None:
        if "control" in self.jobs:
            tasks = discover_tasks(
                self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
            )
            self.control_reuse.plan_for(tasks, _hash_of(self, "control"))
        self.control_reuse.enforce(
            interactive=interactive,
            ask=self.ask,
            progress=self._progress,
        )

    def set_rerun_filter(
        self,
        *,
        tasks: list[str] | None = None,
        variants: list[str] | None = None,
        retry_errors: bool = False,
    ) -> None:
        """Restrict launches to individual cells (resume reruns).

        tasks/variants select cells; retry_errors additionally re-runs cells
        whose reconciled status is error (timeouts, invalid patches, infra
        failures). Without any filter, resume keeps its default behavior of
        running only missing cells. Unknown ids raise PierError before
        anything launches.
        """
        known_tasks = {
            t.task_id
            for t in discover_tasks(
                self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
            )
        }
        if tasks is not None:
            unknown = [t for t in tasks if t not in known_tasks]
            if unknown:
                raise PierError(
                    f"unknown task(s) for this experiment: {', '.join(unknown)} "
                    f"(known: {', '.join(sorted(known_tasks))})"
                )
            self._rerun_tasks = set(tasks)
        known_variants = {v.id for v in self.spec.arms()}
        if variants is not None:
            unknown = [v for v in variants if v not in known_variants]
            if unknown:
                raise PierError(
                    f"unknown variant(s) for this experiment: {', '.join(unknown)} "
                    f"(known: {', '.join(sorted(known_variants))})"
                )
            self._rerun_variants = set(variants)
        self._retry_errors = retry_errors

    def _launch(self) -> None:
        """Prepare process objects per variant with only missing tasks."""
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        self._refresh_cells()
        agents = self.spec.resolved_agents()
        held = self.control_reuse.held_tasks() if self.control_reuse.held_pending() else set()
        scope_tasks = self._rerun_tasks
        scope_variants = self._rerun_variants
        if scope_tasks is not None or scope_variants is not None or self._retry_errors:
            task_scope = ",".join(sorted(scope_tasks)) if scope_tasks is not None else "*"
            variant_scope = ",".join(sorted(scope_variants)) if scope_variants is not None else "*"
            retry = " retry-errors" if self._retry_errors else ""
            self._progress(f"rerun filter: tasks={task_scope} variants={variant_scope}{retry}")
        missing_by_job: dict[str, list[str]] = {}
        order = {task_id: idx for idx, task_id in enumerate(all_ids)}
        for job in self.jobs.values():
            job.proc = None
            if self._rerun_variants is not None and job.variant_id not in self._rerun_variants:
                continue
            missing = missing_tasks(self.cells.get(job.variant_id, {}), all_ids)
            if self._retry_errors:
                cells = self.cells.get(job.variant_id, {})
                retried = [t for t, c in cells.items() if c.status == "error" and t in order]
                missing = missing + [t for t in retried if t not in missing]
            if self._rerun_tasks is not None:
                missing = [task_id for task_id in missing if task_id in self._rerun_tasks]
            if job.variant_id == "control":
                missing = [task_id for task_id in missing if task_id not in held]
            missing = sorted(set(missing), key=order.__getitem__)
            if missing:
                missing_by_job[job.variant_id] = missing
        if not missing_by_job and (
            scope_tasks is not None or scope_variants is not None or self._retry_errors
        ):
            self._progress("rerun filter matched no runnable cells")
        n_concurrent = self.spec.concurrency.effective_per_variant(len(missing_by_job))
        for job in self.jobs.values():
            missing = missing_by_job.get(job.variant_id)
            if not missing:
                continue
            agent_id = agents[job.variant_id]
            argv = pier_mod.build_run_args(
                task_root=self.spec.tasks.path,
                jobs_dir=self.run_dir / "jobs" / job.variant_id,
                job_name=f"{self.experiment_id}-{job.variant_id}",
                manifest_path=job.manifest_path,
                model_id=self.spec.model.full_id(),
                thinking=self.spec.thinking,
                pi_version=self.spec.resolved_version_for(agent_id),
                n_concurrent=n_concurrent,
                include_tasks=missing,
                agent=agent_id,
            )
            log = self.run_dir / "logs" / f"{job.variant_id}.log"
            job.proc = process_mod.VariantProcess(job.variant_id, argv, log)
            self._progress(
                f"launch {job.variant_id}: {len(missing)} task(s), {n_concurrent} concurrent"
            )

    async def _start_gated(self, env: dict[str, str]) -> None:
        """Start arms through a bounded admission gate with stagger.

        Decision cx-pier-fanout: both ceiling and stagger (conservative
        defaults from ConcurrencySpec, tunable per experiment). Bounds the
        correlated-failure blast radius; retries must land only after this gate.
        """
        to_start = [j.proc for j in self.jobs.values() if j.proc is not None]
        if not to_start:
            return
        cap = max(1, self.spec.concurrency.launch_max_in_flight)
        stagger = max(0.0, self.spec.concurrency.launch_stagger_sec)
        sem = asyncio.Semaphore(cap)
        in_flight = 0

        async def _one(proc: process_mod.VariantProcess) -> None:
            nonlocal in_flight
            async with sem:
                in_flight += 1
                msg = f"launch gate {in_flight}/{cap} {proc.variant_id}"
                self._logger.emit("progress", state=self.state, message=msg)
                try:
                    await proc.start(env)
                finally:
                    in_flight -= 1
            if stagger:
                await asyncio.sleep(stagger)

        await asyncio.gather(*(_one(proc) for proc in to_start))

    async def _watch(self) -> None:
        env = self._pier_env()
        await self._start_gated(env)
        process_mod.require_all_started([j.proc for j in self.jobs.values() if j.proc is not None])
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        interval = POLL_INTERVAL_SEC
        while True:
            if self._cancel_event.is_set():
                return
            procs = [j.proc for j in self.jobs.values() if j.proc is not None]
            if not procs or not any(p.running for p in procs):
                self._refresh_cells()
                return
            import time as _time

            tick_start = _time.monotonic()
            self._poll_once(all_ids)
            tick_sec = _time.monotonic() - tick_start
            if tick_sec > POLL_INTERVAL_SEC:
                interval = min(POLL_MAX_INTERVAL_SEC, max(POLL_INTERVAL_SEC, tick_sec * 1.5))
                msg = f"poll overrun {tick_sec:.2f}s, backoff {interval:.1f}s"
                self._logger.emit("progress", state=self.state, message=msg)
            else:
                interval = POLL_INTERVAL_SEC
            await asyncio.sleep(interval)

    def _poll_once(self, all_ids: list[str]) -> None:
        import time as _time

        _tick_start = _time.monotonic()
        previous = {(v, t): c.status for v, cells in self.cells.items() for t, c in cells.items()}
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
                self.control_reuse.record(variant_id, task_id, cell, trial_id)
                self._logger.emit(
                    "trial",
                    variant=variant_id,
                    task=task_id,
                    status=cell.status,
                    reward=cell.reward,
                    exception_type=cell.exception_type,
                )
                label = ""
                if cell.status == "error" and is_throttle_error(cell.exception_type):
                    label = " [throttled]"
                elif cell.status == "error" and is_timeout_error(cell.exception_type):
                    label = " [infra-timeout]"
                elif cell.exception_type == INVALID_EMPTY_PATCH:
                    label = " [invalid-patch]"
                elif cell.exception_type == INFRA_ARTIFACT_COPY:
                    label = " [infra-artifact]"
                self._progress(
                    f"{variant_id}/{task_id}: {cell.status}{label}"
                    + (f" reward={cell.reward}" if cell.status != "error" else "")
                )

        for job in self.jobs.values():
            proc = job.proc
            if proc is not None and not proc.running and not getattr(proc, "_exit_emitted", False):
                proc._exit_emitted = True  # type: ignore[attr-defined]
                code = proc.proc.returncode if proc.proc else None
                self._progress(f"{job.variant_id} exited rc={code}")
        import time as _time2

        self._last_tick_sec = _time2.monotonic() - _tick_start
        self._logger.emit(
            "progress",
            state=self.state,
            message=f"tick {self._last_tick_sec:.3f}s parsed={self._last_parse_count}",
        )

    def _refresh_cells(self) -> None:
        known = set(self._task_ids())
        total_parsed = 0
        for variant_id in self.jobs:
            state = self._reconcile_state.setdefault(variant_id, {})
            cells, parsed = reconcile_variant_incremental(
                variant_id, self.run_dir / "jobs" / variant_id, known, state
            )
            # First call with empty state but existing files parses everything;
            # later ticks parse only deltas. Fall back to full scan only when
            # the jobs dir appeared between ticks (state empty, cells empty).
            if not state and not cells:
                self.cells[variant_id] = reconcile_variant(
                    variant_id, self.run_dir / "jobs" / variant_id, known
                )
            else:
                self.cells[variant_id] = cells
            total_parsed += parsed
        self._last_parse_count = total_parsed

    # ----------------------------------------------------------- cancel --

    def request_cancel(self) -> None:
        self._cancel_event.set()

    async def _cancel(self, final_state: str) -> None:
        # Cancel must release, not add work: no secret scan on this path.
        # Coverage relies on the last incremental scan plus staging cleanup.
        self._set_state("CANCELLING")
        procs = [j.proc for j in self.jobs.values() if j.proc is not None]
        await process_mod.cancel_all(procs)
        self._refresh_cells()
        self._record_all_cells()
        self.cleanup_staging()
        self._set_state(final_state)

    # --------------------------------------------------------- finalize --

    def _finalize(self) -> None:
        import time as _time

        self._set_state("FINALIZING")
        _t0 = _time.monotonic()
        self._refresh_cells()
        _t1 = _time.monotonic()
        self._record_all_cells()
        _t2 = _time.monotonic()
        self.cleanup_staging()
        rows, self._row_cache, _parsed, _reused = report_collect.collect_rows_incremental(
            self.run_dir / "jobs", self._row_cache
        )
        report_collect.save_fold_cache(self.run_dir, self._row_cache)
        _t3 = _time.monotonic()
        provenance = self._provenance([])
        csv = report_exports.write_summary_csv(self.run_dir, rows)
        report_exports.write_summary_json(self.run_dir, rows, provenance)
        report = report_markdown.generate_report(
            self.run_dir,
            experiment_id=self.experiment_id,
            provenance=provenance,
            rows=rows,
        )
        leaks, self._secret_scan_state, _scanned, _skipped = staging.scan_for_secrets_incremental(
            self.run_dir, self._secret_scan_state
        )
        _t4 = _time.monotonic()
        self._finalize_stats = {
            "refresh_sec": round(_t1 - _t0, 3),
            "record_sec": round(_t2 - _t1, 3),
            "collect_sec": round(_t3 - _t2, 3),
            "scan_sec": round(_t4 - _t3, 3),
            "rows_parsed": _parsed,
            "rows_reused": _reused,
            "scan_scanned": _scanned,
            "scan_skipped": _skipped,
        }
        self._logger.emit("progress", state=self.state, message=f"finalize {self._finalize_stats}")
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
                self.control_reuse.record(variant_id, task_id, cell, trial_id)

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
        manifest["reused_control_observations"] = manifest["control_reuse"].get("total_reused", 0)
        atomic_write_text(
            self.run_dir / "manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        return manifest

    def reuse_summary(self) -> dict[str, Any]:
        return self.control_reuse.summary()

    def fail_setup(self, error: Exception) -> None:
        """Record a preparation failure and remove partial staged homes."""
        self._logger.emit(
            "error",
            exception_type=type(error).__name__,
            message=str(error),
        )
        self._set_state("FAILED")
        self.cleanup_staging()

    def _fail(self, error: Exception) -> None:
        self.fail_setup(error)
        raise PierError(str(error)) from error

    # ------------------------------------------------------------- env ---

    def _pier_env(self) -> dict[str, str]:
        """Env for pier: PYTHONPATH must expose the stdlib-only adapter."""
        import os

        env = dict(os.environ)
        package_parent = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = package_parent + (os.pathsep + existing if existing else "")
        return env

    # --------------------------------------------------------- snapshot --

    def snapshot(self) -> dict[str, Any]:
        """Current matrix + aggregates for the CLI. Cheap to poll."""
        self._refresh_cells()
        all_ids = self._task_ids()
        matrix: dict[str, dict[str, str]] = {}
        matrix_rewards: dict[str, dict[str, float]] = {}
        held = self.control_reuse.held_tasks() if self.control_reuse.held_pending() else set()
        reused = self.control_reuse.reused_tasks()
        for variant_id in self.jobs:
            cells = self.cells.get(variant_id, {})
            row: dict[str, str] = {}
            rewards: dict[str, float] = {}
            pending, _ = report_collect.scan_variant(self.run_dir / "jobs" / variant_id)
            statuses = report_collect.pending_statuses(pending, all_ids)
            for task_id in all_ids:
                if task_id in cells:
                    row[task_id] = cells[task_id].status[0].upper()
                    rewards[task_id] = cells[task_id].reward
                elif variant_id == "control" and task_id in reused:
                    row[task_id] = "H"
                elif variant_id == "control" and task_id in held:
                    row[task_id] = "."
                else:
                    row[task_id] = statuses[task_id]
            matrix[variant_id] = row
            matrix_rewards[variant_id] = rewards
        return {
            "state": self.state,
            "matrix": matrix,
            "rewards": matrix_rewards,
            "tasks": all_ids,
        }


def _hash_of(controller: ExperimentController, variant_id: str) -> str:
    row = controller.store.conn.execute(
        "SELECT variant_hash FROM variants WHERE experiment_id=? AND id=?",
        (controller.experiment_id, variant_id),
    ).fetchone()
    return row["variant_hash"] if row else ""
