"""Experiment orchestration service: prepare/start/status/cancel/report.

One typed surface shared by the private bridge CLI and the Pi extension.
Plans are persisted under data_dir()/plans and bound to the exact
bytes that were approved; start rechecks those bindings, is idempotent per
plan_id, and launches the run in a detached worker process.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from roast_my_harness import ADAPTER_PROTOCOL_VERSION, __version__
from roast_my_harness.agent import models
from roast_my_harness.constants import EXIT_CODES
from roast_my_harness.errors import RoastMyHarnessError, SpecError
from roast_my_harness.evals.registry import eval_label, resolve_eval
from roast_my_harness.files import atomic_write_text
from roast_my_harness.homes.sources import source_file_hash, source_tree_hash
from roast_my_harness.paths import database_path, run_dir
from roast_my_harness.paths import plans_dir as default_plans_dir
from roast_my_harness.report.collect import (
    aggregate_by_variant,
    latest_result_path,
    newest_result_paths,
)
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import preflight
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.runner.lock_probe import lock_is_free
from roast_my_harness.runner.signals import install_cancel_handlers, install_sync_cancel_handlers
from roast_my_harness.spec.hashes import resolved_experiment_hash, sha256_canonical
from roast_my_harness.spec.hashes import spec_hash as compute_spec_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id
from roast_my_harness.spec.resolved import identity_payload, resolve_run_spec
from roast_my_harness.host_lock import ExperimentLock
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.catalog import catalog_info
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash as compute_task_hash
from roast_my_harness.telemetry.result import trial_row, trial_row_cached

PLAN_ID_RE = re.compile(r"^plan_[0-9a-f]{12}$")
FINAL_STATES = frozenset({"COMPLETE", "FAILED", "CANCELLED"})
WATCH_INTERVAL_SEC = 2.0
WATCH_HEARTBEAT_SEC = 30.0
WATCH_WORKER_GRACE_SEC = 10.0


class ServiceError(RoastMyHarnessError):
    """Machine-facing failure with a stable error code."""

    code = "error"


class UnknownPlanError(ServiceError):
    code = "unknown_plan"


class UnknownExperimentError(ServiceError):
    code = "unknown_experiment"


class AmbiguousExperimentError(ServiceError):
    code = "ambiguous_experiment"


class StalePlanError(ServiceError):
    code = "stale_plan"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _source_hashes(spec: ExperimentSpec) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for variant in spec.arms():
        for item in variant.extensions:
            if item.kind == "local":
                hashes[f"{variant.id}/ext/{item.name or item.path.name}"] = source_tree_hash(item.path)
        for item in variant.skills:
            hashes[f"{variant.id}/skill/{item.name or item.path.name}"] = source_tree_hash(item.path)
        if variant.agents_md is not None:
            try:
                hashes[f"{variant.id}/agents_md"] = source_file_hash(variant.agents_md)
            except OSError as error:
                raise SpecError(f"variant {variant.id!r} agents_md unreadable: {error}") from error
        if variant.settings is not None:
            try:
                hashes[f"{variant.id}/settings"] = source_file_hash(variant.settings)
            except OSError as error:
                raise SpecError(f"variant {variant.id!r} settings unreadable: {error}") from error
    return hashes


def plan_bindings(spec: ExperimentSpec, tasks: list[Any]) -> dict[str, Any]:
    """Everything a plan_id binds: config, task content, sources, versions.

    Pi versions resolve once here; the frozen ResolvedRunSpec travels
    in the bindings so start() rejects a plan whose `latest` moved since
    approval, and the run id derives from resolved content.
    """
    task_pairs = [(t.task_id, compute_task_hash(t.path)) for t in tasks]
    catalog_revision, catalog_hash = catalog_info(spec.tasks.path)
    eval_frozen = resolve_eval(
        spec,
        spec.tasks.path,
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
    )
    resolved = resolve_run_spec(
        spec,
        task_pairs,
        repetitions=spec.execution.repetitions,
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
        eval=eval_frozen,
    )
    return {
        "spec_hash": compute_spec_hash(spec),
        "experiment_hash": resolved_experiment_hash(identity_payload(resolved)),
        "task_hashes": [[task_id, h] for task_id, h in task_pairs],
        "source_hashes": _source_hashes(spec),
        "resolved": resolved.model_dump(mode="json"),
        "model_hash": sha256_canonical(
            {"model": spec.model.model_dump(mode="json"), "thinking": spec.thinking}
        ),
        "versions": {
            "pi_version": spec.pi_version,
            "resolved_pi_version": resolved.resolved_pi_versions.get(
                "pi", spec.pi_version
            ),
            "resolved_pi_versions": resolved.resolved_pi_versions,
            "pier_version_constraint": spec.pier_version,
            "pier_version": pier_mod.pier_version(),
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
            "roast_my_harness": __version__,
        },
    }


def _needs_input(field: str, message: str, choices: list[str] | None = None):
    return models.PrepareResult(
        ok=False,
        state="needs_input",
        questions=[models.Question(field=field, message=message, choices=choices or [])],
    )


WATCH_TRIAL_STAT_KEYS: tuple[tuple[str, str], ...] = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_tokens", "cache_tokens"),
    ("tool_calls", "tool_calls"),
    ("turns", "agent_steps"),
    ("wall_sec", "wall_sec"),
)


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _trial_stats(
    rd: Path,
    variant: str,
    task: str,
    newest_maps: dict[str, dict[tuple[str, int], Path]] | None = None,
    variant_tasks: list[str] | None = None,
    fold_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-trial stats for a just-completed cell, or {} when unmeasured.

    A cell only reaches P/F/E after its result.json parses, so trial_row
    can read the agent totals and event folds at flip time. Trials whose
    agent never finished (crash) carry no agent_result; they report no
    stats rather than zero defaults. When newest_maps is given, the caller's
    single per-variant enumeration serves the lookup instead of re-walking
    the variant tree per completed cell. With repetitions, stats come from
    the newest result across replicates. When fold_cache is given, event
    bytes fold incrementally instead of from zero.
    """
    if newest_maps is not None:
        by_trial = newest_maps.get(variant)
        if by_trial is None:
            by_trial = newest_result_paths(rd / "jobs" / variant, variant_tasks or [task])
            newest_maps[variant] = by_trial
        reps = sorted(
            (trial, path) for trial, path in by_trial.items() if trial[0] == task
        )
        if not reps:
            return {}
        from roast_my_harness.runner.reconcile import _attempt_seq, is_newer
        best: tuple[int, int, str, Path] | None = None
        for _trial, path in reps:
            seq = _attempt_seq(path.parent)
            cur = (best[0], best[1], best[2]) if best is not None else None
            if is_newer(_path_mtime_ns(path), seq, str(path), cur):
                best = (_path_mtime_ns(path), seq, str(path), path)
        result_path = best[3] if best is not None else None
    else:
        result_path = latest_result_path(rd / "jobs", variant, task)
    if not result_path:
        return {}
    if fold_cache is not None:
        key = str(result_path)
        cached = fold_cache.get(key)
        row, updated, _folded = trial_row_cached(result_path, variant, cached)
        fold_cache[key] = updated
    else:
        row = trial_row(result_path, variant)
    if not row or row.get("input_tokens") in ("", None):
        return {}
    stats: dict[str, Any] = {}
    for key, column in WATCH_TRIAL_STAT_KEYS:
        value = row.get(column)
        if value not in ("", None):
            stats[key] = value
    return stats


class AgentService:
    """Typed orchestration over the existing CLI machinery."""

    def __init__(
        self,
        *,
        plans_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.plans_dir = plans_dir or default_plans_dir()
        self.db_path = db_path or database_path()

    def prepare(self, spec_path: Path, *, skip_docker: bool = False) -> models.PrepareResult:
        """Validate, preflight, and persist a plan awaiting confirmation."""
        spec_path = spec_path.expanduser().resolve()
        try:
            spec = load_experiment(spec_path)
        except (SpecError, ValidationError) as error:
            return _needs_input("spec", str(error))
        try:
            tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
        except RoastMyHarnessError as error:
            return _needs_input("tasks.path", str(error))
        if not tasks:
            return _needs_input(
                "tasks.path",
                f"no tasks discovered under {spec.tasks.path}",
            )
        results = preflight.run_checks(spec, skip_docker=skip_docker)
        failures = [r for r in results if r.status == "fail"]
        warnings = [f"{r.name}: {r.detail}" for r in results if r.status == "warn"]
        if failures:
            first = failures[0]
            return _needs_input(f"preflight.{first.name}", first.detail, choices=None)

        try:
            bindings = plan_bindings(spec, tasks)
        except RuntimeError as error:
            return _needs_input("versions", str(error))
        except SpecError as error:
            return _needs_input("variants", str(error))
        plan_id = "plan_" + sha256_canonical(bindings)[:12]
        experiment_id = make_experiment_id(spec.name, bindings["experiment_hash"])
        self._write_plan(
            {
                "plan_id": plan_id,
                "spec_path": str(spec_path),
                "experiment_id": experiment_id,
                "bindings": bindings,
                "created_at": _utc_now(),
            }
        )
        arms = spec.arms()
        repetitions = bindings["resolved"]["repetitions"]
        resolved_pi_version = bindings["versions"].get("resolved_pi_version")
        return models.PrepareResult(
            ok=True,
            state="ready_for_confirmation",
            plan_id=plan_id,
            spec_path=str(spec_path),
            experiment=models.ExperimentSummary(
                tasks=len(tasks),
                arms=len(arms),
                trials=len(tasks) * len(arms) * repetitions,
                max_parallel=spec.peak_concurrency(),
                model=spec.model.full_id(),
                name=spec.name,
                pi_version=spec.pi_version,
                resolved_pi_version=resolved_pi_version,
                thinking=spec.thinking,
                repetitions=repetitions,
                evaluation=eval_label(spec),
                hypothesis=spec.hypothesis,
                control="fresh" if spec.control else "excluded",
                task_ids=[task.task_id for task in tasks],
                tasks_path=str(spec.tasks.path),
                arm_ids=[arm.id for arm in arms],
                variant_sources={
                    variant.id: [
                        *[
                            (
                                f"local:{extension.path}#{extension.entry}"
                                if extension.kind == "local"
                                else f"npm:{extension.package}"
                            )
                            for extension in variant.extensions
                        ],
                        *[f"skill:{skill.path}" for skill in variant.skills],
                    ]
                    for variant in spec.variants
                },
            ),
            warnings=warnings,
            next_action="start",
        )

    def start(self, plan_id: str, *, skip_docker: bool = False) -> models.StartResult:
        """Launch an approved plan. Idempotent per plan_id; rejects stale bytes."""
        if not PLAN_ID_RE.fullmatch(plan_id):
            raise UnknownPlanError(f"malformed plan id {plan_id!r}")
        plan = self._load_plan(plan_id)
        spec_path = Path(plan["spec_path"])
        try:
            spec = load_experiment(spec_path)
            tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
        except (RoastMyHarnessError, ValidationError) as error:
            raise StalePlanError(
                f"plan {plan_id} is stale; the spec no longer loads ({error}); run prepare again"
            ) from error
        current = plan_bindings(spec, tasks)
        if current != plan["bindings"]:
            changed = sorted(
                key for key in plan["bindings"] if plan["bindings"][key] != current.get(key)
            )
            raise StalePlanError(
                f"plan {plan_id} is stale; changed since approval: "
                f"{', '.join(changed)}; run prepare again"
            )
        experiment_id = plan["experiment_id"]

        repo = Repository(self.db_path)
        try:
            row = repo.get_experiment(experiment_id)
        finally:
            repo.close()
        if row is not None and str(row["status"]) not in (
            "DRAFT",
            "FAILED",
            "CANCELLED",
        ):
            return models.StartResult(
                ok=True,
                state="already_started",
                plan_id=plan_id,
                experiment_id=experiment_id,
                started=False,
            )

        marker = self.plans_dir / f"{plan_id}.started"
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return models.StartResult(
                ok=True,
                state="already_started",
                plan_id=plan_id,
                experiment_id=self._marker_experiment_id(marker, experiment_id),
                started=False,
            )
        try:
            pid = self._spawn_worker(spec_path, experiment_id, skip_docker)
        except BaseException:
            # Roll back the launch claim: an unstarted plan must stay
            # startable (the deterministic plan_id cannot be re-prepared)
            # and the marker fd must not leak. The size-0 guard fires only
            # while the marker is still the empty file just created.
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                if marker.stat().st_size == 0:
                    marker.unlink()
            except OSError:
                pass
            raise
        with os.fdopen(fd, "w") as handle:
            handle.write(
                json.dumps(
                    {
                        "experiment_id": experiment_id,
                        "pid": pid,
                        "started_at": _utc_now(),
                    }
                )
            )
        return models.StartResult(
            ok=True,
            state="running",
            plan_id=plan_id,
            experiment_id=experiment_id,
            started=True,
        )

    def _resolve_experiment(self, handle: str) -> str:
        """Resolve an experiment handle to a stored experiment id.

        Accepts an exact experiment id, an exact spec name (newest wins),
        or a unique id prefix. Ambiguous prefixes are rejected.
        """
        repo = Repository(self.db_path)
        try:
            row = repo.get_experiment(handle)
            if row is not None:
                return str(row["id"])
            rows = repo.list_experiments()
        finally:
            repo.close()
        by_name = [r for r in rows if str(r["name"]) == handle]
        if by_name:
            return str(by_name[0]["id"])
        prefix_matches = [r for r in rows if str(r["id"]).startswith(handle)]
        if len(prefix_matches) == 1:
            return str(prefix_matches[0]["id"])
        if len(prefix_matches) > 1:
            ids = ", ".join(sorted(str(r["id"]) for r in prefix_matches))
            raise AmbiguousExperimentError(
                f"experiment handle {handle!r} matches multiple experiments: {ids}"
            )
        if self._has_started_marker(handle):
            return handle
        raise UnknownExperimentError(f"unknown experiment {handle}")

    def _observe(self, handle: str) -> tuple[ExperimentController, Path]:
        experiment_id = self._resolve_experiment(handle)
        """Read-only observation of one experiment; caller closes no state.

        Opens the DB, validates the stored spec, and returns an observing
        controller plus the run dir. The DB connection is closed before
        returning; the controller only touches the filesystem afterwards.
        """
        repo = Repository(self.db_path)
        try:
            row = repo.get_experiment(experiment_id)
            if row is None:
                raise UnknownExperimentError(f"unknown experiment {experiment_id}")
            spec = ExperimentSpec.model_validate(json.loads(row["spec_json"]))
            run_dir = Path(row["run_dir"])
            controller = ExperimentController(spec, experiment_id, run_dir, repo, None)
            controller.load_for_observation()
        finally:
            repo.close()
        return controller, run_dir

    def status(self, experiment_id: str) -> models.StatusResult:
        """Current matrix and aggregates; cheap to poll."""
        controller, rd = self._observe(experiment_id)
        snap = controller.snapshot()
        state = str(snap["state"])
        totals = {
            variant: {s: sum(1 for c in cells.values() if c == s) for s in "PFEH"}
            for variant, cells in snap["matrix"].items()
        }
        from roast_my_harness.report import collect as report_collect

        fold_cache = report_collect.load_fold_cache(rd)
        rows, fold_cache, _folded, _reused = report_collect.collect_rows_incremental(
            rd / "jobs", fold_cache
        )
        report_collect.save_fold_cache(rd, fold_cache)
        aggregates = aggregate_by_variant(rows)
        report = (
            models.ReportPaths(markdown=str(rd / "report.md"), csv=str(rd / "summary.csv"))
            if (rd / "report.md").is_file()
            else None
        )
        return models.StatusResult(
            ok=True,
            experiment_id=experiment_id,
            state=state,
            final=state in FINAL_STATES,
            tasks=list(snap["tasks"]),
            matrix=snap["matrix"],
            totals=totals,
            aggregates=aggregates,
            report=report,
        )

    def watch(
        self,
        experiment_id: str,
        *,
        interval_sec: float = WATCH_INTERVAL_SEC,
        worker_grace_sec: float = WATCH_WORKER_GRACE_SEC,
    ) -> Iterator[dict[str, Any]]:
        """Yield NDJSON-ready dicts describing progress until a final state.

        Read-only: never takes the experiment lock and never mutates state.
        Emits a snapshot first, then trial/state events on change, a fresh
        snapshot whenever the matrix changes, a heartbeat when quiet, and a
        final event with aggregates and report paths. A non-final experiment
        with no lockable run dir and no live worker terminates with a note
        instead of hanging.
        """
        controller, rd = self._observe(experiment_id)
        experiment_id = controller.experiment_id
        first = self._watch_snapshot(controller)
        yield {"event": "snapshot", **first}
        last_emit = time.monotonic()
        started_at = last_emit
        state_prev = first["state"]
        matrix_prev = first["matrix"]
        from roast_my_harness.report import collect as report_collect

        fold_cache = report_collect.load_fold_cache(rd)
        try:
            while True:
                time.sleep(interval_sec)
                try:
                    controller, rd = self._observe(experiment_id)
                    snap = self._watch_snapshot(controller)
                except Exception as tick_error:
                    now = time.monotonic()
                    yield {
                        "event": "heartbeat",
                        "state": state_prev,
                        "note": f"transient observe error: {tick_error}",
                    }
                    last_emit = now
                    continue
                state, matrix = snap["state"], snap["matrix"]
                now = time.monotonic()
                if state != state_prev:
                    yield {"event": "state", "state": state}
                    state_prev = state
                    last_emit = now
                if matrix != matrix_prev:
                    newest_maps: dict[str, dict[tuple[str, int], Path]] = {}
                    for variant, cells in matrix.items():
                        old = matrix_prev.get(variant, {})
                        for task, status in cells.items():
                            if status in ("P", "F", "E") and old.get(task) != status:
                                event = {
                                    "event": "trial",
                                    "variant": variant,
                                    "task": task,
                                    "status": status,
                                    "reward": snap["rewards"].get(variant, {}).get(task),
                                }
                                try:
                                    stats = _trial_stats(
                                        rd, variant, task, newest_maps, list(cells), fold_cache
                                    )
                                except Exception:
                                    stats = {}
                                if stats:
                                    event["stats"] = stats
                                yield event
                    yield {"event": "snapshot", **snap}
                    matrix_prev = matrix
                    last_emit = now
                    continue
                if state in FINAL_STATES:
                    yield self._watch_final(experiment_id, rd, state, fold_cache=fold_cache)
                    return
                quiet = now - last_emit >= WATCH_HEARTBEAT_SEC
                orphan = (
                    now - started_at >= worker_grace_sec
                    and lock_is_free(rd)
                    and self._worker_pid(experiment_id) is None
                )
                if orphan:
                    yield self._watch_final(
                        experiment_id,
                        rd,
                        state,
                        note="worker not running; run resume or cancel to clean up",
                        fold_cache=fold_cache,
                    )
                    return
                if quiet:
                    yield {"event": "heartbeat", "state": state}
                    last_emit = now
        finally:
            report_collect.save_fold_cache(rd, fold_cache)

    @staticmethod
    def _watch_snapshot(controller: ExperimentController) -> dict[str, Any]:
        snap = controller.snapshot()
        totals = {
            variant: {s: sum(1 for c in cells.values() if c == s) for s in "PFEH"}
            for variant, cells in snap["matrix"].items()
        }
        running = [
            [variant, task]
            for variant, cells in snap["matrix"].items()
            for task, status in cells.items()
            if status == "~"
        ]
        return {
            "state": snap["state"],
            "totals": totals,
            "matrix": snap["matrix"],
            "rewards": snap["rewards"],
            "running": running,
        }

    def _watch_final(
        self,
        experiment_id: str,
        rd: Path,
        state: str,
        *,
        note: str | None = None,
        fold_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Final event: aggregates over completed trials plus report paths."""
        from roast_my_harness.report import collect as report_collect

        if fold_cache is None:
            fold_cache = report_collect.load_fold_cache(rd)
            rows, fold_cache, _folded, _reused = report_collect.collect_rows_incremental(
                rd / "jobs", fold_cache
            )
            report_collect.save_fold_cache(rd, fold_cache)
        else:
            rows, fold_cache, _folded, _reused = report_collect.collect_rows_incremental(
                rd / "jobs", fold_cache
            )
        aggregates = aggregate_by_variant(rows)
        report: dict[str, str] | None = None
        if (rd / "report.md").is_file():
            report = {
                "markdown": str(rd / "report.md"),
                "csv": str(rd / "summary.csv"),
            }
        event: dict[str, Any] = {
            "event": "final",
            "experiment_id": experiment_id,
            "state": state,
            "final": state in FINAL_STATES,
            "aggregates": aggregates,
            "report": report,
        }
        if note:
            event["note"] = note
        return event

    def cancel(self, experiment_id: str) -> models.CancelResult:
        """Ask a live worker to cancel gracefully."""
        experiment_id = self._resolve_experiment(experiment_id)
        repo = Repository(self.db_path)
        try:
            row = repo.get_experiment(experiment_id)
            state = str(row["status"]) if row is not None else "STARTING"
        finally:
            repo.close()
        if row is None and not self._has_started_marker(experiment_id):
            raise UnknownExperimentError(f"unknown experiment {experiment_id}")
        if state in FINAL_STATES:
            return models.CancelResult(
                ok=True,
                experiment_id=experiment_id,
                state=state,
                cancelled=False,
                note="experiment already reached a final state",
            )
        pid = self._worker_pid(experiment_id)
        if pid is None:
            return models.CancelResult(
                ok=True,
                experiment_id=experiment_id,
                state=state,
                cancelled=False,
                note="no live worker found; use roastmyharness resume to continue",
            )
        rd = Path(row["run_dir"]) if row is not None else run_dir(experiment_id)
        if lock_is_free(rd):
            # The worker holds the experiment lock for its whole lifetime,
            # so a free lock means the marker pid is OS pid reuse pointing
            # at an unrelated process; signal only a lock-holding worker.
            return models.CancelResult(
                ok=True,
                experiment_id=experiment_id,
                state=state,
                cancelled=False,
                note="no live worker found; use roastmyharness resume to continue",
            )
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return models.CancelResult(
                ok=True,
                experiment_id=experiment_id,
                state=state,
                cancelled=False,
                note="worker no longer running",
            )
        return models.CancelResult(
            ok=True, experiment_id=experiment_id, state="CANCELLING", cancelled=True
        )

    def report(self, experiment_id: str) -> models.ReportResult:
        """Regenerate summary.csv, summary.json, and report.md."""
        experiment_id = self._resolve_experiment(experiment_id)
        from roast_my_harness.auth import staging
        from roast_my_harness.report import collect as report_collect
        from roast_my_harness.report import exports as report_exports
        from roast_my_harness.report import markdown as report_markdown

        repo = Repository(self.db_path)
        try:
            row = repo.get_experiment(experiment_id)
            if row is None:
                raise UnknownExperimentError(f"unknown experiment {experiment_id}")
            rd = Path(row["run_dir"])
            with ExperimentLock(rd):
                rows, fold_cache, _folded, _reused = report_collect.collect_rows_incremental(
                    rd / "jobs", report_collect.load_fold_cache(rd)
                )
                report_collect.save_fold_cache(rd, fold_cache)
                if not rows:
                    raise ServiceError("no completed trials to report")
                provenance: dict[str, Any] = {
                    "experiment_id": experiment_id,
                    "spec_hash": row["spec_hash"],
                }
                manifest_path = rd / "manifest.json"
                if manifest_path.is_file():
                    try:
                        loaded = json.loads(manifest_path.read_text())
                    except json.JSONDecodeError:
                        loaded = None
                    if isinstance(loaded, dict):
                        provenance = loaded
                provenance["secret_scan_scope"] = "all regular run artifacts after staging cleanup"
                provenance["secret_scan_hits"] = staging.scan_for_secrets(rd)
                csv = report_exports.write_summary_csv(rd, rows)
                report_exports.write_summary_json(rd, rows, provenance)
                out = report_markdown.generate_report(
                    rd,
                    experiment_id=experiment_id,
                    provenance=provenance,
                    rows=rows,
                )
        finally:
            repo.close()
        return models.ReportResult(
            ok=True,
            experiment_id=experiment_id,
            csv_path=str(csv),
            markdown_path=str(out),
        )

    def _spawn_worker(self, spec_path: Path, experiment_id: str, skip_docker: bool) -> int:
        """Detached worker so the caller (extension/bridge) can poll and exit."""
        argv = [sys.executable, "-m", "roast_my_harness", "_worker", str(spec_path)]
        if skip_docker:
            argv.append("--skip-docker")
        log = run_dir(experiment_id) / "logs" / "worker.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as sink:
            proc = subprocess.Popen(
                argv,
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=(os.name == "posix"),
            )
        return proc.pid

    def _has_started_marker(self, experiment_id: str) -> bool:
        """True if any start marker references this experiment."""
        for marker in self.plans_dir.glob("*.started"):
            try:
                data = json.loads(marker.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("experiment_id") == experiment_id:
                return True
        return False

    def _worker_pid(self, experiment_id: str) -> int | None:
        for marker in self.plans_dir.glob("*.started"):
            try:
                data = json.loads(marker.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("experiment_id") != experiment_id:
                continue
            pid = data.get("pid")
            if not isinstance(pid, int):
                return None
            try:
                os.kill(pid, 0)
            except OSError:
                return None
            return pid
        return None

    @staticmethod
    def _marker_experiment_id(marker: Path, default: str) -> str:
        try:
            data = json.loads(marker.read_text())
        except (json.JSONDecodeError, OSError):
            return default
        return str(data.get("experiment_id", default))

    def _write_plan(self, plan: dict[str, Any]) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.plans_dir / f"{plan['plan_id']}.json",
            json.dumps(plan, indent=2) + "\n",
            mode=0o600,
        )

    def _load_plan(self, plan_id: str) -> dict[str, Any]:
        path = self.plans_dir / f"{plan_id}.json"
        if not path.is_file():
            raise UnknownPlanError(f"unknown plan {plan_id!r}; run prepare first")
        return json.loads(path.read_text())


def run_experiment(
    spec_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Run one experiment headless; return (experiment_id, final state).

    The single orchestration body shared by `roastmyharness run` and the
    detached worker. Callers own exit-code mapping and final-state reporting.
    """
    spec = load_experiment(spec_path)
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    catalog_revision, catalog_hash = catalog_info(spec.tasks.path)
    eval_frozen = resolve_eval(
        spec,
        spec.tasks.path,
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
    )
    resolved = resolve_run_spec(
        spec,
        [(t.task_id, compute_task_hash(t.path)) for t in tasks],
        repetitions=spec.execution.repetitions,
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
        eval=eval_frozen,
    )
    experiment_id = resolved.run_id
    from roast_my_harness.store import retention as retention_mod

    retention_mod.enforce_storage_policy(exclude=experiment_id, progress=progress)
    repo = Repository(database_path())
    controller = ExperimentController(
        spec,
        experiment_id,
        run_dir(experiment_id),
        repo,
        progress=progress,
    )
    with ExperimentLock(controller.run_dir):
        loop = asyncio.new_event_loop()
        cleanup = install_cancel_handlers(loop, controller.request_cancel)
        sync_cleanup = install_sync_cancel_handlers(controller.request_cancel)
        try:
            try:
                controller.prepare(spec_path, resolved=resolved)
                controller.enforce_reuse_policy()
            except (asyncio.CancelledError, KeyboardInterrupt):
                sync_cleanup()
                final = loop.run_until_complete(controller._cancel("CANCELLED"))
                return experiment_id, final
            except Exception as error:
                controller.fail_setup(error)
                raise
            except BaseException:
                controller.cleanup_staging()
                raise
            finally:
                try:
                    sync_cleanup()
                except Exception:
                    pass
            if controller._cancel_event.is_set():
                final = loop.run_until_complete(controller._cancel("CANCELLED"))
                return experiment_id, final
            final = loop.run_until_complete(controller.run())
        finally:
            cleanup()
            loop.close()
    return experiment_id, final


def run_experiment_worker(spec_path: Path, *, skip_docker: bool = False) -> int:
    """Headless run of one prepared experiment; returns a process exit code."""
    _experiment_id, final = run_experiment(spec_path)
    return EXIT_CODES.get(final, 0)
