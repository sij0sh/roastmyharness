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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION, __version__
from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.auth import staging
from roast_my_harness.errors import PierError
from roast_my_harness.evals.registry import resolve_eval
from roast_my_harness.files import atomic_write_text
from roast_my_harness.homes.builder import build_home
from roast_my_harness.observability import RunLogger
from roast_my_harness.paths import homes_cache_dir
from roast_my_harness.report import analyst as report_analyst
from roast_my_harness.report import collect as report_collect
from roast_my_harness.report import exports as report_exports
from roast_my_harness.report import markdown as report_markdown
from roast_my_harness.report.collect import pending_replicates
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import probe as probe_mod
from roast_my_harness.runner import process as process_mod
from roast_my_harness.runner.control_reuse import ControlReuse
from roast_my_harness.runner.patch_guard import (
    INFRA_ARTIFACT_COPY,
    INVALID_EMPTY_PATCH,
)
from roast_my_harness.runner.reconcile import (
    REPLICATE_DIR_PREFIX,
    Cell,
    is_throttle_error,
    is_timeout_error,
    missing_replicates,
    reconcile_variant,
    reconcile_variant_incremental,
)
from roast_my_harness.spec.hashes import resolved_experiment_hash
from roast_my_harness.spec.hashes import spec_hash as compute_spec_hash
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id
from roast_my_harness.spec.resolved import ResolvedRunSpec, resolve_run_spec
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.catalog import catalog_info, load_catalog
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash as compute_task_hash

POLL_INTERVAL_SEC = 2.0
POLL_MAX_INTERVAL_SEC = 10.0

ProgressCallback = Callable[[str], None]


# ---------------------------------------------------------- controller ----


@dataclass
class VariantJob:
    variant_id: str
    home: Path
    staged: Path
    manifest_path: Path
    procs: list[process_mod.VariantProcess] = field(default_factory=list)


class ExperimentController:
    def __init__(
        self,
        spec: ExperimentSpec,
        experiment_id: str,
        run_dir: Path,
        store: Repository,
        progress: ProgressCallback | None = None,
    ):
        self.spec = spec
        self.experiment_id = experiment_id
        self.run_dir = run_dir
        self.store = store
        self.resolved: ResolvedRunSpec | None = None
        self.progress = progress
        self.control_reuse = ControlReuse(spec, experiment_id, store)
        self.state = "DRAFT"
        self.jobs: dict[str, VariantJob] = {}
        self.cells: dict[str, dict[tuple[str, int], Cell]] = {}
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

    RESOLVED_NAME = "resolved.json"

    def version_for(self, agent_id: str) -> str:
        """Exact version one agent installs in this run.

        Frozen at prepare; only a controller that never prepared (tests,
        one-off observation) resolves live.
        """
        if self.resolved is not None:
            try:
                return self.resolved.resolved_agent_versions[agent_id]
            except KeyError:
                pass
        return self.spec.resolved_version_for(agent_id)

    def _frozen_versions(self) -> dict[str, str] | None:
        """Frozen agent versions for callees taking a versions map."""
        if self.resolved is None:
            return None
        return dict(self.resolved.resolved_agent_versions)

    def _resolved_path(self) -> Path:
        return self.run_dir / self.RESOLVED_NAME

    def _load_persisted_resolved(self) -> ResolvedRunSpec | None:
        """Frozen identity from an earlier prepare; None when absent/broken.

        v1 runs predate the freeze file; those fall back to fresh
        resolution with its live-`latest` semantics.
        """
        try:
            raw = json.loads(self._resolved_path().read_text())
        except (json.JSONDecodeError, OSError):
            return None
        try:
            resolved = ResolvedRunSpec.model_validate(raw)
        except ValueError:
            return None
        return resolved

    def _freeze_identity(
        self,
        pairs: list[tuple[str, str]],
        resolved: ResolvedRunSpec | None,
    ) -> ResolvedRunSpec:
        """Single freeze point: explicit, persisted, or fresh resolution.

        An explicitly passed spec (headless run) must match this run's id
        and task content. Otherwise a persisted freeze wins (resume never
        re-resolves a moved `latest`); only a first prepare resolves fresh
        and then must explain this run's id.
        """
        if resolved is not None:
            if resolved.run_id != self.experiment_id:
                raise PierError(
                    f"resolved run {resolved.run_id} does not match experiment "
                    f"{self.experiment_id}; prepare the spec that produced "
                    "this run instead"
                )
            if list(resolved.tasks) != [tuple(p) for p in pairs]:
                raise PierError(
                    f"task content changed since run {self.experiment_id} "
                    "was frozen; create a new experiment instead of "
                    "resuming this one"
                )
            atomic_write_text(
                self._resolved_path(),
                resolved.model_dump_json(indent=2) + "\n",
            )
            return resolved
        persisted = self._load_persisted_resolved()
        if persisted is not None:
            if persisted.run_id != self.experiment_id:
                raise PierError(
                    f"run dir holds frozen run {persisted.run_id}, not "
                    f"experiment {self.experiment_id}"
                )
            if list(persisted.tasks) != [tuple(p) for p in pairs]:
                self._assert_identity_current(pairs, persisted)
            return persisted
        catalog_revision, catalog_hash = catalog_info(self.spec.tasks.path)
        eval_frozen = resolve_eval(
            self.spec,
            self.spec.tasks.path,
            catalog_revision=catalog_revision,
            catalog_hash=catalog_hash,
        )
        fresh = resolve_run_spec(
            self.spec,
            pairs,
            repetitions=self.spec.execution.repetitions,
            catalog_revision=catalog_revision,
            catalog_hash=catalog_hash,
            eval=eval_frozen,
        )
        if fresh.run_id != self.experiment_id:
            raise PierError(
                f"spec resolves to run {fresh.run_id}, not experiment "
                f"{self.experiment_id}; old `latest` pins may have moved. "
                "Create a new experiment instead of resuming this one"
            )
        atomic_write_text(
            self._resolved_path(), fresh.model_dump_json(indent=2) + "\n"
        )
        return fresh

    def prepare(
        self, spec_path: Path | None = None, *, resolved: ResolvedRunSpec | None = None
    ) -> None:
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
        self.resolved = self._freeze_identity(pairs, resolved)
        self._assert_identity_current(pairs, self.resolved)
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
            build = build_home(
                variant,
                self.spec,
                homes_root,
                agent_version=self.version_for(variant.agent or self.spec.agent),
            )
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
            self.control_reuse.plan_for(
                tasks,
                _hash_of(self, "control"),
                resolved_versions=self._frozen_versions(),
            )
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
                    resolved_versions=self._frozen_versions(),
                    catalog=load_catalog(self.spec.tasks.path),
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

    def _assert_identity_current(
        self, pairs: list[tuple[str, str]], resolved: ResolvedRunSpec
    ) -> None:
        """Refuse to touch a stored experiment whose task content drifted.

        Identity binds the ordered task id/hash map, so a fresh run with
        changed content gets a new id. Reaching an existing row with a
        different map means the dataset changed mid-flight: refuse instead
        of silently overwriting the stored hashes and reusing old cells.
        The expected-id hint reuses the frozen versions, so it never
        re-resolves a moved `latest` just to format an error.
        """
        stored = self.store.get_tasks(self.experiment_id)
        if not stored:
            return
        stored_pairs = [(row["task_id"], row["task_hash"]) for row in stored]
        if stored_pairs != pairs:
            hint = resolved.model_copy(update={"tasks": list(pairs), "run_id": ""})
            expected = make_experiment_id(
                self.spec.name,
                resolved_experiment_hash(hint.model_dump(mode="json", exclude={"run_id"})),
            )
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
        self.resolved = self._load_persisted_resolved()
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
        frozen = self._frozen_versions() or {}
        manifest = {
            "experiment_id": self.experiment_id,
            "spec_hash": compute_spec_hash(self.spec),
            "tool_version": __version__,
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
            # pi_version is the exact version that ran (frozen at
            # prepare); requested_pi_version is the pin as written.
            "pi_version": self.version_for("pi")
            if "pi" in set(agents.values())
            else self.spec.pi_version,
            "requested_pi_version": self.spec.pi_version,
            "preset": self.spec.tasks.preset,
            "catalog_revision": self.resolved.catalog_revision
            if self.resolved is not None
            else None,
            "catalog_hash": self.resolved.catalog_hash
            if self.resolved is not None
            else None,
            "evaluation": {
                "type": self.resolved.eval_type,
                "id": self.resolved.eval_id,
                "revision": self.resolved.eval_revision,
                "hash": self.resolved.eval_hash,
            }
            if self.resolved is not None
            else None,
            "requested_agent_versions": dict(
                self.resolved.requested_agent_versions
            )
            if self.resolved is not None
            else {},
            "resolved_agent_versions": frozen,
            "pier_version": self.spec.pier_version,
            "model": self.spec.model.model_dump(mode="json"),
            "thinking": self.spec.thinking,
            "spec": self.spec.model_dump(mode="json", exclude={"tasks": {"path"}}),
            "created_at": datetime.now(UTC).isoformat(),
            "tasks_path": str(self.spec.tasks.path),
            "tasks": {t.task_id: compute_task_hash(t.path) for t in tasks},
            "agents": {
                agent_id: {
                    "family": get_agent(agent_id).family,
                    "import_path": get_agent(agent_id).import_path,
                    "agent_version": self.version_for(agent_id),
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
                if self.control_reuse.abort:
                    self._fail(
                        PierError(
                            f"historic control {self.control_reuse.status}; "
                            "aborting per control policy"
                        )
                    )
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

    def enforce_reuse_policy(self) -> None:
        if "control" in self.jobs:
            tasks = discover_tasks(
                self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
            )
            self.control_reuse.plan_for(
                tasks,
                _hash_of(self, "control"),
                resolved_versions=self._frozen_versions(),
            )
        self.control_reuse.enforce(progress=self._progress)

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

    def _repetitions(self) -> int:
        """Rollouts per task: frozen at prepare, else the live spec value."""
        if self.resolved is not None:
            return self.resolved.repetitions
        return self.spec.execution.repetitions

    def _replicate_jobs_dir(self, variant_id: str, replicate: int) -> Path:
        """Jobs dir for one rollout; flat legacy layout when repetitions == 1."""
        base = self.run_dir / "jobs" / variant_id
        if self._repetitions() == 1:
            return base
        return base / f"{REPLICATE_DIR_PREFIX}{replicate}"

    def _attempts_used(self, variant_id: str, task_id: str, replicate: int) -> int:
        """Terminal result files already recorded for one (task, replicate).

        Same dir-name heuristic as the pending snapshot: a trial dir
        counts when its name equals the task or starts with task + "__".
        """
        scope = self._replicate_jobs_dir(variant_id, replicate)
        if not scope.is_dir():
            return 0
        prefix = task_id + "__"
        count = 0
        for result_path in scope.rglob("result.json"):
            trial_dir = result_path.parent
            if not ((trial_dir / "agent").is_dir() and (trial_dir / "verifier").is_dir()):
                continue
            name = trial_dir.name
            if name == task_id or name.startswith(prefix):
                count += 1
        return count

    def _launch(self) -> None:
        """Prepare process objects per variant with only missing trials.

        One pier process per (variant, replicate) with missing trials;
        each replicate lands in its own jobs subdir so reconciliation can
        tell independent rollouts apart from retries of one trial.
        """
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        repetitions = self._repetitions()
        self._refresh_cells()
        agents = self.spec.resolved_agents()
        held = self.control_reuse.held_tasks() if self.control_reuse.held_pending() else set()
        # Intersection-scope tasks outside the history-backed set never
        # run as controls, in either wave; pending-held tasks wait out
        # the sentinel verdict.
        held = held | self.control_reuse.out_of_scope_tasks()
        scope_tasks = self._rerun_tasks
        scope_variants = self._rerun_variants
        if scope_tasks is not None or scope_variants is not None or self._retry_errors:
            task_scope = ",".join(sorted(scope_tasks)) if scope_tasks is not None else "*"
            variant_scope = ",".join(sorted(scope_variants)) if scope_variants is not None else "*"
            retry = " retry-errors" if self._retry_errors else ""
            self._progress(f"rerun filter: tasks={task_scope} variants={variant_scope}{retry}")
        missing_by_launch: dict[tuple[str, int], list[str]] = {}
        order = {task_id: idx for idx, task_id in enumerate(all_ids)}
        for job in self.jobs.values():
            job.procs = []
            if self._rerun_variants is not None and job.variant_id not in self._rerun_variants:
                continue
            cells = self.cells.get(job.variant_id, {})
            missing = missing_replicates(cells, all_ids, repetitions)
            if self._retry_errors:
                max_retries = self.spec.execution.max_retries
                for (task_id, replicate), cell in cells.items():
                    if cell.status != "error" or task_id not in order:
                        continue
                    if (task_id, replicate) in missing:
                        continue
                    if self._attempts_used(job.variant_id, task_id, replicate) <= max_retries:
                        missing.append((task_id, replicate))
            if self._rerun_tasks is not None:
                missing = [(t, r) for (t, r) in missing if t in self._rerun_tasks]
            if job.variant_id == "control":
                missing = [(t, r) for (t, r) in missing if t not in held]
            by_rep: dict[int, list[str]] = {}
            for task_id, replicate in missing:
                by_rep.setdefault(replicate, []).append(task_id)
            for replicate, rep_tasks in by_rep.items():
                ordered = sorted(set(rep_tasks), key=order.__getitem__)
                if ordered:
                    missing_by_launch[(job.variant_id, replicate)] = ordered
        if not missing_by_launch and (
            scope_tasks is not None or scope_variants is not None or self._retry_errors
        ):
            self._progress("rerun filter matched no runnable cells")
        n_concurrent = self.spec.concurrency.effective_per_variant(len(missing_by_launch))
        for job in self.jobs.values():
            agent_id = agents[job.variant_id]
            for replicate in range(1, repetitions + 1):
                missing = missing_by_launch.get((job.variant_id, replicate))
                if not missing:
                    continue
                multi = repetitions > 1
                argv = pier_mod.build_run_args(
                    task_root=self.spec.tasks.path,
                    jobs_dir=self._replicate_jobs_dir(job.variant_id, replicate),
                    job_name=(
                        f"{self.experiment_id}-{job.variant_id}"
                        + (f"-r{replicate}" if multi else "")
                    ),
                    manifest_path=job.manifest_path,
                    model_id=self.spec.model.full_id(),
                    thinking=self.spec.thinking,
                    pi_version=self.version_for(agent_id),
                    n_concurrent=n_concurrent,
                    include_tasks=missing,
                    agent=agent_id,
                )
                log_name = (
                    f"{job.variant_id}-r{replicate}.log" if multi
                    else f"{job.variant_id}.log"
                )
                log = self.run_dir / "logs" / log_name
                job.procs.append(process_mod.VariantProcess(job.variant_id, argv, log))
                scope = f" rep {replicate}/{repetitions}" if multi else ""
                self._progress(
                    f"launch {job.variant_id}{scope}: {len(missing)} task(s), "
                    f"{n_concurrent} concurrent"
                )

    async def _start_gated(self, env: dict[str, str]) -> None:
        """Start arms through a bounded admission gate with stagger.

        Decision cx-pier-fanout: both ceiling and stagger (conservative
        defaults from ConcurrencySpec, tunable per experiment). Bounds the
        correlated-failure blast radius; retries must land only after this gate.
        """
        to_start = [proc for j in self.jobs.values() for proc in j.procs]
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
        process_mod.require_all_started(
            [proc for j in self.jobs.values() for proc in j.procs]
        )
        tasks = discover_tasks(
            self.spec.tasks.path, self.spec.tasks.include, self.spec.tasks.exclude
        )
        all_ids = [t.task_id for t in tasks]
        interval = POLL_INTERVAL_SEC
        while True:
            if self._cancel_event.is_set():
                return
            procs = [proc for j in self.jobs.values() for proc in j.procs]
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
        multi = self._repetitions() > 1
        previous = {
            (v, t, r): c.status
            for v, cells in self.cells.items()
            for (t, r), c in cells.items()
        }
        self._refresh_cells()
        for variant_id, cells in self.cells.items():
            for (task_id, replicate), cell in cells.items():
                key = (variant_id, task_id, replicate)
                if previous.get(key) == cell.status:
                    continue
                trial_id = self.store.upsert_reconciled_trial(
                    experiment_id=self.experiment_id,
                    variant_id=variant_id,
                    task_id=task_id,
                    replicate=cell.replicate,
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
                    replicate=cell.replicate,
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
                rep = f" rep {cell.replicate}" if multi else ""
                self._progress(
                    f"{variant_id}/{task_id}{rep}: {cell.status}{label}"
                    + (f" reward={cell.reward}" if cell.status != "error" else "")
                )

        for job in self.jobs.values():
            for proc in job.procs:
                if proc.running or getattr(proc, "_exit_emitted", False):
                    continue
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
        procs = [proc for j in self.jobs.values() for proc in j.procs]
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
        # Fail-open: analyst output is advisory; it must never fail the run.
        analysis = None
        try:
            analysis = report_analyst.write_analysis(self.run_dir)
        except Exception as error:
            self._logger.emit(
                "progress", state=self.state, message=f"analyst unavailable: {error}"
            )
        self._set_state("COMPLETE")
        self._progress(f"reports written: {csv}, {report}" + (f", {analysis}" if analysis else ""))

    def _record_all_cells(self) -> None:
        for variant_id, cells in self.cells.items():
            for (task_id, _replicate), cell in cells.items():
                trial_id = self.store.upsert_reconciled_trial(
                    experiment_id=self.experiment_id,
                    variant_id=variant_id,
                    task_id=task_id,
                    replicate=cell.replicate,
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

    @staticmethod
    def _aggregate_trial_states(chars: list[str]) -> str:
        """One matrix char per task across replicates.

        P only when every rollout passed; E beats F beats ~ beats idle,
        so a glance still surfaces the worst outcome first.
        """
        if chars and all(c == "P" for c in chars):
            return "P"
        if "E" in chars:
            return "E"
        if "F" in chars:
            return "F"
        if "~" in chars:
            return "~"
        return "."

    def snapshot(self) -> dict[str, Any]:
        """Current matrix + aggregates for the CLI. Cheap to poll."""
        self._refresh_cells()
        all_ids = self._task_ids()
        repetitions = self._repetitions()
        matrix: dict[str, dict[str, str]] = {}
        matrix_rewards: dict[str, dict[str, float]] = {}
        held = self.control_reuse.held_tasks() if self.control_reuse.held_pending() else set()
        held = held | self.control_reuse.out_of_scope_tasks()
        reused = self.control_reuse.reused_tasks()
        for variant_id in self.jobs:
            cells = self.cells.get(variant_id, {})
            row: dict[str, str] = {}
            rewards: dict[str, float] = {}
            pending, _ = report_collect.scan_variant(self.run_dir / "jobs" / variant_id)
            pend = pending_replicates(pending, all_ids, repetitions)
            for task_id in all_ids:
                chars: list[str] = []
                rep_rewards: list[float] = []
                for replicate in range(1, repetitions + 1):
                    cell = cells.get((task_id, replicate))
                    if cell is not None:
                        chars.append(cell.status[0].upper())
                        rep_rewards.append(cell.reward)
                    else:
                        chars.append(pend[(task_id, replicate)])
                if rep_rewards:
                    row[task_id] = self._aggregate_trial_states(chars)
                    rewards[task_id] = sum(rep_rewards) / len(rep_rewards)
                elif variant_id == "control" and task_id in reused:
                    row[task_id] = "H"
                elif variant_id == "control" and task_id in held:
                    row[task_id] = "."
                else:
                    row[task_id] = self._aggregate_trial_states(chars)
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
