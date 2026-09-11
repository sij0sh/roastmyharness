"""Typer CLI. Headless commands emit plain text without a TTY."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

from roast_my_harness import __version__
from roast_my_harness.agent import service as agent_service
from roast_my_harness.auth import service as auth_service
from roast_my_harness.constants import EXIT_CODES
from roast_my_harness.errors import RoastMyHarnessError
from roast_my_harness.paths import database_path
from roast_my_harness.runner import preflight
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.runner.signals import install_cancel_handlers, install_sync_cancel_handlers
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.store.locking import ExperimentLock
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.profiles import ProfileView, load_profiles, rank_profiles

app = typer.Typer(
    name="roastmyharness",
    help="Compare Pi extensions and skills on identical Pier tasks.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
auth_app = typer.Typer(help="Credential inspection and login.")
app.add_typer(auth_app, name="auth")

STARTER_TOML = """
schema_version = 2
name = "my-comparison"
pi_version = "latest"   # resolved once at prepare; or pin x.y.z for reproducibility
thinking = "high"          # off | minimal | low | medium | high | xhigh | max

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[execution]
repetitions = 1   # independent scored rollouts per task
max_retries = 1   # relaunches per errored trial

[tasks]
path = "/path/to/task-dataset"   # dir of task dirs, each with task.toml
include = ["*"]
exclude = []
# preset = "luna-signal"  # named list from the benchmark catalog, if it has one

[concurrency]
per_variant = 2

[control]
enabled = true                  # bare-agent control arm
mode = "fresh"                  # fresh | historic
history_scope = "hybrid"        # hybrid | intersection (historic only)
minimum_runs_per_task = 4
maximum_age_days = 30
sentinel_tasks = 4
on_drift = "fresh"              # fresh | abort
on_inconclusive = "fresh"       # fresh | abort

# One [[variants]] block per arm. Local extension example:
[[variants]]
id = "my-ext"
name = "My extension"

[[variants.extensions]]
kind = "local"
path = "~/my-extensions/my-extension"
entry = "src/index.ts"

[variants.env]
MY_EXTENSION_SETTING = "2"

# Tool-restriction example (bash-only arm vs full-tool control):
# [[variants]]
# id = "bash-only"
# pi_flags = ["--no-builtin-tools", "--tools=bash"]

# Local skill example:
# [[variants]]
# id = "my-skill"
# [[variants.skills]]
# kind = "local"
# path = "~/my-skills/my-skill"  # must contain SKILL.md
"""


def _repo() -> Repository:
    return Repository(database_path())


def _print_progress(message: str) -> None:
    """Plain stderr progress line (states, launches, per-trial outcomes)."""
    print(f"[roast] {message}", file=sys.stderr)


def _exit_for_final_state(experiment_id: str, final: str) -> int:
    """Report the final state honestly: nonzero for FAILED/CANCELLED."""
    if final == "COMPLETE":
        typer.secho(f"experiment {experiment_id}: {final}", fg=typer.colors.GREEN)
        return 0
    code = EXIT_CODES.get(final)
    if code is None:
        typer.secho(f"experiment {experiment_id}: {final}", fg=typer.colors.YELLOW)
        return 0
    typer.secho(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": f"experiment_{final.lower()}",
                    "experiment_id": experiment_id,
                    "state": final,
                },
            }
        ),
        fg=typer.colors.RED,
    )
    typer.secho(f"experiment {experiment_id}: {final}", fg=typer.colors.RED)
    return code


@app.command()
def init(
    path: Path | None = typer.Argument(
        Path("experiment.toml"), help="Where to write the starter spec."
    ),
) -> None:
    """Write a commented starter experiment TOML."""
    path = path.expanduser()
    if path.exists():
        typer.secho(f"refusing to overwrite {path}", fg=typer.colors.RED)
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_TOML)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


# -------------------------------------------------------------- validate --


@app.command()
def validate(
    spec_path: Path = typer.Argument(..., help="Experiment TOML file."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable."),
) -> None:
    """Validate spec, discover tasks, check sources and environment."""
    spec = load_experiment(spec_path)
    results = preflight.run_checks(spec)
    if json_output:
        payload: dict[str, Any] = {
            "ok": not preflight.has_failures(results),
            "checks": [r.__dict__ for r in results],
            "peak_concurrency": spec.peak_concurrency(),
        }
        print(json.dumps(payload, indent=2))
    else:
        typer.echo(preflight.format_table(results))
        typer.echo(
            f"peak concurrency: {spec.peak_concurrency()} "
            f"({len(spec.arms())} arms x "
            f"{spec.concurrency.effective_per_variant(len(spec.arms()))} each)"
        )
        typer.echo(preflight.container_probe_note())
    if preflight.has_failures(results):
        raise typer.Exit(1)


_DEFAULT_TASKS_ROOT = Path("tasks/deepswe/tasks")


def _resolve_tasks_root(value: Path) -> Path:
    """CLI --tasks path, falling back to the wheel-bundled corpus.

    Only the conventional default falls back: an explicit path that does
    not exist keeps its error so typos never silently retarget.
    """
    if value.expanduser().exists() or value != _DEFAULT_TASKS_ROOT:
        return value
    from roast_my_harness import setup as setup_mod

    bundled = setup_mod.bundled_tasks_root()
    return bundled if bundled is not None else value


# -------------------------------------------------------------- profiles --


@app.command()
def profiles(
    tasks: Path = typer.Option(
        Path("tasks/deepswe/tasks"),
        "--tasks",
        help="Benchmark task root containing profiles.toml.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON for the wizard."
    ),
) -> None:
    """List benchmark model profiles ranked toward the 40-60% band.

    Rates are measured benchmark rates with a recorded basis; unmeasured
    profiles sort last and never show a rate.
    """
    import dataclasses

    tasks = _resolve_tasks_root(tasks)
    found = load_profiles(tasks.expanduser())
    if found is None:
        typer.secho(f"no profiles.toml under {tasks}", fg=typer.colors.RED)
        raise typer.Exit(1)
    ranked = rank_profiles(found)
    views = [
        ProfileView(
            id=item.profile.id,
            label=item.profile.label,
            full_id=item.profile.full_id(),
            thinking=item.profile.thinking,
            expected_rate=item.expected_rate,
            distance=item.distance,
            matched=item.matched,
            samples=item.profile.benchmark_samples,
            revision=item.profile.benchmark_revision,
            basis=item.profile.basis,
        )
        for item in ranked
    ]
    if json_output:
        print(
            json.dumps(
                {
                    "benchmark": found.benchmark,
                    "revision": found.revision,
                    "profiles": [dataclasses.asdict(view) for view in views],
                },
                indent=2,
            )
        )
        return
    typer.echo(f"benchmark {found.benchmark} revision {found.revision}")
    for view in views:
        if view.expected_rate is None:
            typer.echo(f"- {view.id} ({view.label}): unmeasured")
        else:
            typer.echo(
                f"- {view.id} ({view.label}): "
                f"{100 * view.expected_rate:.1f}% over {view.samples} "
                f"({view.revision})"
            )


# ------------------------------------------------------------------- run --


@app.command()
def run(
    spec_path: Path = typer.Argument(..., help="Experiment TOML file."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
    skip_docker: bool = typer.Option(False, help="Skip docker checks."),
) -> None:
    """Preflight, confirm, then run the experiment headless."""
    spec = load_experiment(spec_path)
    results = preflight.run_checks(spec, skip_docker=skip_docker)
    typer.echo(preflight.format_table(results))
    if preflight.has_failures(results):
        typer.secho("preflight failed; not launching", fg=typer.colors.RED)
        raise typer.Exit(1)
    if preflight.has_warnings(results) and not yes and sys.stdin.isatty():
        if not typer.confirm("Warnings present. Continue?"):
            raise typer.Exit(0)
    if not yes and sys.stdin.isatty():
        if not typer.confirm("Launch now?"):
            raise typer.Exit(0)
    experiment_id, final = agent_service.run_experiment(
        spec_path,
        progress=_print_progress,
    )
    raise typer.Exit(_exit_for_final_state(experiment_id, final))


# ---------------------------------------------------------------- resume --


@app.command()
def resume(
    experiment_id: str = typer.Argument(..., help="Experiment id from `list`."),
    task: list[str] = typer.Option(
        [],
        "--task",
        help="Rerun only this task cell (repeatable).",
    ),
    variant: list[str] = typer.Option(
        [],
        "--variant",
        help="Rerun only this variant arm (repeatable).",
    ),
    retry_errors: bool = typer.Option(
        False, "--retry-errors", help="Also re-run cells whose status is error."
    ),
) -> None:
    """Reconcile completed trials, then run only missing cells.

    With --task/--variant/--retry-errors, rerun individual cells instead:
    pier starts a new attempt per selected cell and reconciliation keeps the
    newest, so completed cells stay intact.
    """
    repo = _repo()
    row = repo.get_experiment(experiment_id)
    if row is None:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1)
    from roast_my_harness.spec.models import ExperimentSpec
    from roast_my_harness.store import retention as retention_mod

    retention_mod.enforce_storage_policy(exclude=experiment_id, progress=_print_progress)
    spec = ExperimentSpec.model_validate(json.loads(row["spec_json"]))
    controller = ExperimentController(
        spec,
        experiment_id,
        Path(row["run_dir"]),
        repo,
        _print_progress,
    )
    if task or variant or retry_errors:
        from roast_my_harness.errors import PierError

        try:
            controller.set_rerun_filter(
                tasks=list(task) or None,
                variants=list(variant) or None,
                retry_errors=retry_errors,
            )
        except PierError as error:
            typer.secho(str(error), fg=typer.colors.RED)
            raise typer.Exit(1) from None
    with ExperimentLock(controller.run_dir):
        final = asyncio.run(_run_with_cancel(controller, prepare=True))
    raise typer.Exit(_exit_for_final_state(experiment_id, final))


async def _run_with_cancel(controller: ExperimentController, *, prepare: bool = False) -> str:
    loop = asyncio.get_running_loop()
    cleanup = install_cancel_handlers(loop, controller.request_cancel)
    sync_cleanup = install_sync_cancel_handlers(controller.request_cancel)
    try:
        if prepare:
            try:
                controller.prepare()
                controller.enforce_reuse_policy()
            except (asyncio.CancelledError, KeyboardInterrupt):
                await controller._cancel("CANCELLED")
                return controller.state
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
                await controller._cancel("CANCELLED")
                return controller.state
        return await controller.run()
    finally:
        cleanup()


# ----------------------------------------------------------------- status --


@app.command()
def status(
    experiment_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print the current matrix and aggregates."""
    repo = _repo()
    row = repo.get_experiment(experiment_id)
    if row is None:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1)
    from roast_my_harness.spec.models import ExperimentSpec

    spec = ExperimentSpec.model_validate(json.loads(row["spec_json"]))
    controller = ExperimentController(spec, experiment_id, Path(row["run_dir"]), repo, None)
    controller.load_for_observation()
    snap = controller.snapshot()
    if json_output:
        print(json.dumps(snap, indent=2))
        return
    typer.echo(f"state: {snap['state']}")
    header = ["task"] + list(snap["matrix"])
    typer.echo("\t".join(header))
    for task in snap["tasks"]:
        cells = [snap["matrix"][v][task] for v in snap["matrix"]]
        typer.echo("\t".join([task] + cells))
    counts = {
        v: {s: sum(1 for c in row_.values() if c == s) for s in "PFE"}
        for v, row_ in snap["matrix"].items()
    }
    typer.echo(f"totals (pass/fail/error): {counts}")


# ----------------------------------------------------------------- report --


@app.command()
def report(experiment_id: str = typer.Argument(...)) -> None:
    """Regenerate summary.csv, summary.json, and report.md."""
    try:
        result = agent_service.AgentService().report(experiment_id)
    except agent_service.UnknownExperimentError:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1) from None
    except agent_service.ServiceError as error:
        typer.secho(str(error), fg=typer.colors.RED)
        raise typer.Exit(1) from None
    typer.secho(f"wrote {result.csv_path} and {result.markdown_path}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- list --


@app.command("list")
def list_experiments(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List known experiments."""
    repo = _repo()
    rows = repo.list_experiments()
    if json_output:
        print(
            json.dumps(
                [dict(r) for r in rows],
                indent=2,
                default=str,
            )
        )
        return
    for r in rows:
        typer.echo(f"{r['id']}\t{r['status']}\t{r['name']}")


# ------------------------------------------------------------------- auth --


@auth_app.command("status")
def auth_status() -> None:
    """Show credential status without printing token values."""
    cred = auth_service.codex_credential()
    if cred is None:
        typer.echo(
            f"no {auth_service.CODEX_PROVIDER} credential at "
            f"{auth_service.pi_auth_file()}; run pi /login codex"
        )
        raise typer.Exit(1)
    expiry = auth_service.credential_expiry(cred)
    state = "EXPIRED (host must re-login)" if auth_service.refresh_hint(cred) else "valid"
    typer.echo(f"{auth_service.CODEX_PROVIDER}: {state}{expiry}")
    typer.echo("account: " + str(cred.get("accountId", "unknown")))


@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument("codex"),
) -> None:
    """Integrated login is not in this build; reuse pi's own login."""
    if provider != "codex":
        typer.secho(f"unknown provider {provider}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("run `pi` then `/login codex`; roastmyharness reuses ~/.pi/agent/auth.json")


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Argument("codex"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Remove the selected credential from pi's auth file after confirmation."""
    if provider != "codex":
        typer.secho(f"unknown provider {provider}", fg=typer.colors.RED)
        raise typer.Exit(1)
    data = auth_service.load_auth_file()
    if auth_service.CODEX_PROVIDER not in data:
        typer.echo("nothing to remove")
        return
    if not yes and not typer.confirm("Remove codex credential from pi auth file?"):
        raise typer.Exit(0)
    del data[auth_service.CODEX_PROVIDER]
    auth_service.write_auth_file(data)
    typer.echo("removed")


@app.command()
def setup(
    agent: str | None = typer.Option(
        None, help="Client to configure: pi or claude. Default: detect."
    ),
    scope: str = typer.Option("user", help="Where to install: user or project."),
) -> None:
    """Install integrations for one agent (idempotent)."""
    from roast_my_harness import setup as setup_mod

    agents = [agent] if agent else setup_mod.detect_agents()
    if not agents:
        typer.secho(
            "no agent given and none detected; pass --agent pi or claude", fg=typer.colors.RED
        )
        raise typer.Exit(1)
    problems = False
    for one in agents:
        typer.echo(f"{one} ({scope}):")
        for action in setup_mod.setup(one, scope):
            mark = "!" if action.problem else ("*" if action.changed else " ")
            typer.echo(f"  {mark} {action.name}: {action.detail}")
            problems |= action.problem
    if problems:
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Report pi, pier, docker, auth, model, and integration health."""
    from roast_my_harness import setup as setup_mod

    results = setup_mod.run_doctor()
    typer.echo(preflight.format_table(results))
    if preflight.has_failures(results):
        raise typer.Exit(1)


tool_app = typer.Typer(help="Machine-facing JSON tool for agents.")
app.add_typer(tool_app, name="tool", hidden=True)

eval_app = typer.Typer(help="Author custom evaluations.")
app.add_typer(eval_app, name="eval")


@eval_app.command("init")
def eval_init(
    directory: Path = typer.Argument(..., help="Eval workspace root to create."),
    eval_id: str = typer.Option(..., "--id", help="Eval id for eval.toml."),
    title: str = typer.Option("", help="Human title for eval.toml."),
    threshold: float = typer.Option(
        0.7, help="Contract pass_threshold for validation/self-test.json."
    ),
) -> None:
    """Scaffold a custom-eval workspace (contract templates, empty gate)."""
    from roast_my_harness.errors import SpecError
    from roast_my_harness.evals.scaffold import init_eval

    try:
        root = init_eval(directory, eval_id=eval_id, title=title, threshold=threshold)
    except SpecError as error:
        typer.secho(str(error), fg=typer.colors.RED)
        raise typer.Exit(1) from None
    typer.secho(f"wrote eval scaffold to {root}", fg=typer.colors.GREEN)
    typer.echo(
        "next: fill capability-map.json, add one task dir per task under "
        f"{root}/, add fixtures to validation/self-test.json, then run: "
        f"roastmyharness eval validate {root}"
    )


@eval_app.command("validate")
def eval_validate(
    directory: Path = typer.Argument(..., help="Eval workspace root to check."),
) -> None:
    """Host-side validation for a custom-eval workspace (freeze gate)."""
    from roast_my_harness.evals.builder import validate_workspace

    report = validate_workspace(directory)
    if report.ok:
        typer.secho(
            f"eval {report.eval_id} valid: "
            f"{report.task_count} tasks, {report.fixture_count} fixtures green",
            fg=typer.colors.GREEN,
        )
        for warning in report.warnings:
            typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)
        return
    for error in report.errors:
        typer.secho(f"error: {error}", fg=typer.colors.RED)
    raise typer.Exit(1)


def _tool_call(fn, *args, **kwargs):
    """Run one service action; ServiceErrors come back as JSON."""
    try:
        return fn(*args, **kwargs)
    except agent_service.ServiceError as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                indent=2,
            )
        )
        raise typer.Exit(1) from None


@tool_app.command("prepare")
def tool_prepare(
    spec_path: Path = typer.Argument(..., help="Experiment TOML file."),
    skip_docker: bool = typer.Option(False, help="Skip docker checks."),
) -> None:
    """Validate and return a plan awaiting confirmation (JSON)."""
    result = agent_service.AgentService().prepare(spec_path, skip_docker=skip_docker)
    print(result.model_dump_json(exclude_none=True, indent=2))
    if not result.ok:
        raise typer.Exit(1)


@tool_app.command("start")
def tool_start(
    plan_id: str = typer.Argument(..., help="Plan id from prepare."),
    skip_docker: bool = typer.Option(False, help="Skip docker checks."),
) -> None:
    """Launch an approved plan (JSON). Idempotent per plan_id."""
    result = _tool_call(agent_service.AgentService().start, plan_id, skip_docker=skip_docker)
    print(result.model_dump_json(exclude_none=True, indent=2))


@tool_app.command("status")
def tool_status(
    experiment_id: str = typer.Argument(...),
) -> None:
    """Current experiment matrix and aggregates (JSON)."""
    result = _tool_call(agent_service.AgentService().status, experiment_id)
    print(result.model_dump_json(exclude_none=True, indent=2))


@tool_app.command("cancel")
def tool_cancel(
    experiment_id: str = typer.Argument(...),
) -> None:
    """Ask a live worker to cancel gracefully (JSON)."""
    result = _tool_call(agent_service.AgentService().cancel, experiment_id)
    print(result.model_dump_json(exclude_none=True, indent=2))


@tool_app.command("watch")
def tool_watch(
    experiment_id: str = typer.Argument(...),
    interval: float = typer.Option(
        agent_service.WATCH_INTERVAL_SEC, "--interval", help="Poll seconds."
    ),
) -> None:
    """Stream NDJSON progress until a final state (machine-facing)."""
    import sys

    try:
        for event in agent_service.AgentService().watch(
            experiment_id, interval_sec=max(interval, 0.2)
        ):
            sys.stdout.write(json.dumps(event, default=str) + "\n")
            sys.stdout.flush()
    except agent_service.UnknownExperimentError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "unknown_experiment",
                        "message": f"unknown experiment {experiment_id}",
                    },
                }
            )
        )
        raise typer.Exit(1) from None


@tool_app.command("catalog")
def tool_catalog(
    tasks: Path = typer.Option(
        Path("tasks/deepswe/tasks"),
        "--tasks",
        help="Benchmark task root containing catalog.toml/profiles.toml.",
    ),
) -> None:
    """Benchmark catalog for the Pi wizard: presets, profiles, labels (JSON)."""
    import dataclasses

    from roast_my_harness.tasks.catalog import load_catalog
    from roast_my_harness.tasks.discover import discover_tasks

    root = _resolve_tasks_root(tasks).expanduser()
    catalog = load_catalog(root)
    found = load_profiles(root)
    from roast_my_harness.evals.descriptor import load_descriptor as _load_eval_descriptor

    try:
        _early_descriptor = _load_eval_descriptor(root)
    except RoastMyHarnessError:
        _early_descriptor = None
    if catalog is None and found is None and _early_descriptor is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "no_catalog",
                        "message": f"no catalog.toml or profiles.toml under {tasks}",
                    },
                }
            )
        )
        raise typer.Exit(1)
    try:
        inventory = [task.task_id for task in discover_tasks(root, ["*"], [])]
    except RoastMyHarnessError:
        inventory = []
    ranked = (
        [
            ProfileView(
                id=item.profile.id,
                label=item.profile.label,
                full_id=item.profile.full_id(),
                thinking=item.profile.thinking,
                expected_rate=item.expected_rate,
                distance=item.distance,
                matched=item.matched,
                samples=item.profile.benchmark_samples,
                revision=item.profile.benchmark_revision,
                basis=item.profile.basis,
            )
            for item in rank_profiles(found)
        ]
        if found is not None
        else []
    )
    labels = (
        {
            task_id: {
                "difficulty": meta.difficulty,
                "duration": meta.duration,
                "estimated_minutes": meta.estimated_minutes,
            }
            for task_id, meta in catalog.tasks.items()
            if meta.difficulty is not None
            or meta.duration is not None
            or meta.estimated_minutes is not None
        }
        if catalog is not None
        else {}
    )
    benchmark = (
        catalog.benchmark
        if catalog is not None
        else (found.benchmark if found is not None else None)
    )
    revision = (
        catalog.revision if catalog is not None else (found.revision if found is not None else None)
    )
    descriptor = _early_descriptor
    eval_info = (
        {
            "id": descriptor.id,
            "revision": descriptor.revision,
            "title": descriptor.title,
        }
        if descriptor is not None
        else None
    )
    print(
        json.dumps(
            {
                "benchmark": benchmark,
                "revision": revision,
                "task_count": len(inventory),
                "eval": eval_info,
                "presets": [
                    {
                        "id": pid,
                        "label": preset.label,
                        "count": len(preset.tasks),
                        "tasks": list(preset.tasks),
                    }
                    for pid, preset in (catalog.presets.items() if catalog is not None else [])
                ],
                "profiles": [dataclasses.asdict(view) for view in ranked],
                "labels": labels,
            },
            indent=2,
        )
    )


@tool_app.command("history-availability")
def tool_history_availability(
    spec_path: Path = typer.Argument(..., help="Experiment TOML file."),
) -> None:
    """Historic-control availability for one spec, without preparing (JSON)."""
    from pydantic import ValidationError

    try:
        result = agent_service.AgentService().historic_availability(spec_path)
    except (RoastMyHarnessError, ValidationError) as error:
        result = {"available": False, "reason": str(error)}
    print(json.dumps(result, indent=2, default=str))


@app.command("watch")
def watch_human(
    experiment_id: str = typer.Argument(...),
    interval: float = typer.Option(
        agent_service.WATCH_INTERVAL_SEC, "--interval", help="Poll seconds."
    ),
) -> None:
    """Live matrix view for a running experiment (human-readable)."""
    try:
        for event in agent_service.AgentService().watch(
            experiment_id, interval_sec=max(interval, 0.2)
        ):
            kind = event.get("event")
            if kind in ("snapshot", "heartbeat"):
                _print_watch_frame(event)
            elif kind == "trial":
                mark = "!" if event["status"] == "E" else ("+" if event["status"] == "P" else "-")
                typer.secho(
                    f"{mark} {event['variant']}/{event['task']}: "
                    f"{event['status']}" + _reward_text(event.get("reward"))
                )
            elif kind == "final":
                _print_watch_frame(event, final=True)
    except agent_service.UnknownExperimentError:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


def _reward_text(reward: Any) -> str:
    if reward is None:
        return ""
    return f" reward={reward}"


def _print_watch_frame(event: dict[str, Any], *, final: bool = False) -> None:
    """Render one snapshot/final event as an ASCII frame."""
    matrix = event.get("matrix") or {}
    totals = event.get("totals") or {}
    state = event.get("state", "?")
    typer.echo()
    label = "final" if final else "state"
    typer.secho(f"{label}: {state}", bold=final)
    if matrix:
        variants = list(matrix)
        tasks = sorted({t for row in matrix.values() for t in row})
        typer.echo("\t".join(["task"] + variants))
        for task in tasks:
            typer.echo("\t".join([task] + [matrix[v].get(task, ".") for v in variants]))
    for variant, counts in totals.items():
        typer.echo(
            f"{variant}: P={counts.get('P', 0)} F={counts.get('F', 0)} E={counts.get('E', 0)}"
        )
    aggregates = event.get("aggregates") or {}
    for variant, agg in aggregates.items():
        n = int(agg.get("n", 0))
        resolved = int(agg.get("resolved", 0))
        typer.echo(
            f"{variant}: {resolved}/{n} resolved  "
            f"in {agg.get('input_tokens', 0) / 1000:.0f}k  "
            f"out {agg.get('output_tokens', 0) / 1000:.0f}k  "
            f"wall {agg.get('wall_sec', 0) / 60:.0f}m  "
            f"cost ${agg.get('cost_usd', 0):.2f}"
        )
    report = event.get("report") or {}
    if report.get("markdown"):
        typer.secho(f"report: {report['markdown']}", fg=typer.colors.GREEN)
    if final and event.get("note"):
        typer.secho(f"note: {event['note']}", fg=typer.colors.YELLOW)


@tool_app.command("report")
def tool_report(
    experiment_id: str = typer.Argument(...),
) -> None:
    """Regenerate summary and report artifacts (JSON)."""
    result = _tool_call(agent_service.AgentService().report, experiment_id)
    print(result.model_dump_json(exclude_none=True, indent=2))


@app.command("_worker", hidden=True)
def worker(
    spec_path: Path = typer.Argument(...),
    skip_docker: bool = typer.Option(False, help="Skip docker checks."),
) -> None:
    """Internal: run one prepared experiment headlessly."""
    raise typer.Exit(agent_service.run_experiment_worker(spec_path, skip_docker=skip_docker))


@app.callback()
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        typer.echo(f"roastmyharness {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


def main() -> None:
    try:
        app()
    except RoastMyHarnessError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED)
        raise SystemExit(1) from e
    except Exception as e:
        if os.environ.get("ROAST_MY_HARNESS_DEBUG"):
            raise
        typer.secho(
            f"error: unexpected {type(e).__name__}: {e}",
            fg=typer.colors.RED,
        )
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
