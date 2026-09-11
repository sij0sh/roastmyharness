"""Typer CLI. One front door is Pi; the binary exposes setup, doctor, and a private bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from roast_my_harness import __version__
from roast_my_harness.agent import service as agent_service
from roast_my_harness.errors import RoastMyHarnessError
from roast_my_harness.runner import preflight
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.tasks.discover import discover_tasks

app = typer.Typer(name="roastmyharness", help="Pi-native harness comparison engine.",
                  invoke_without_command=True, no_args_is_help=False, add_completion=False)


@app.command()
def setup(scope: str = typer.Option("user", help="Where to install: user or project.")) -> None:
    """Install/update the Pi extension."""
    from roast_my_harness import setup as setup_mod
    problems = False
    typer.echo(f"pi ({scope}):")
    for action in setup_mod.setup("pi", scope):
        mark = "!" if action.problem else ("*" if action.changed else " ")
        typer.echo(f"  {mark} {action.name}: {action.detail}")
        problems |= action.problem
    if problems:
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Report pi, pier, docker, auth, model, and extension health."""
    from roast_my_harness import setup as setup_mod
    results = setup_mod.run_doctor()
    typer.echo(preflight.format_table(results))
    if preflight.has_failures(results):
        raise typer.Exit(1)


bridge_app = typer.Typer(help="Private engine protocol for the Pi extension.", hidden=True)
app.add_typer(bridge_app, name="_bridge")


@bridge_app.command("version")
def bridge_version() -> None:
    from roast_my_harness import ADAPTER_PROTOCOL_VERSION, __version__
    print(json.dumps({"ok": True, "version": __version__,
                      "adapter_protocol": ADAPTER_PROTOCOL_VERSION}))


@bridge_app.command("inspect")
def bridge_inspect(spec_path: Path = typer.Argument(..., help="Experiment TOML file.")) -> None:
    try:
        spec = load_experiment(spec_path)
        tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    except RoastMyHarnessError as error:
        print(json.dumps({"ok": False, "error": {"code": "invalid_spec", "message": str(error)}}))
        raise typer.Exit(1) from None
    print(json.dumps({"ok": True, "name": spec.name, "model": spec.model.full_id(),
                      "thinking": spec.thinking, "pi_version": spec.pi_version,
                      "tasks": len(tasks), "task_ids": [t.task_id for t in tasks],
                      "arms": [a.id for a in spec.arms()],
                      "max_parallel": spec.peak_concurrency()}, indent=2))


@bridge_app.command("wizard-context")
def bridge_wizard_context(task_root: Path = typer.Argument(..., help="Task root directory."),
                           model: str = typer.Option(..., help="Model as provider/id."),
                           thinking: str = typer.Option(..., help="Thinking level.")) -> None:
    from roast_my_harness import wizard as wizard_mod
    result = wizard_mod.wizard_context(task_root, model, thinking)
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise typer.Exit(1)


@bridge_app.command("await")
def bridge_wait(
    experiment_id: str = typer.Argument(..., help="Experiment id, name, or prefix."),
    interval_sec: float = typer.Option(2.0, help="Poll interval in seconds."),
    grace_sec: float = typer.Option(10.0, help="Orphan grace period in seconds."),
) -> None:
    try:
        events = agent_service.AgentService().watch(experiment_id, interval_sec=interval_sec,
                                                     worker_grace_sec=grace_sec)
        for event in events:
            sys.stdout.write(json.dumps(event, default=str) + "\n")
            sys.stdout.flush()
    except agent_service.ServiceError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        raise typer.Exit(1) from None
    except Exception as error:
        print(json.dumps({"event": "final", "final": False,
                          "note": f"bridge error before a final event: {type(error).__name__}: {error}"}))
        sys.stdout.flush()
        raise typer.Exit(1) from None


@bridge_app.command("validate")
def bridge_validate(spec_path: Path = typer.Argument(..., help="Experiment TOML file."),
                    skip_docker: bool = typer.Option(False, help="Skip docker checks.")) -> None:
    result = agent_service.AgentService().prepare(spec_path, skip_docker=skip_docker)
    print(result.model_dump_json(exclude_none=True, indent=2))
    if not result.ok:
        raise typer.Exit(1)


@bridge_app.command("run")
def bridge_run(plan_id: str = typer.Argument(..., help="Plan id from validate."),
               skip_docker: bool = typer.Option(False, help="Skip docker checks.")) -> None:
    try:
        started = agent_service.AgentService().start(plan_id, skip_docker=skip_docker)
    except agent_service.ServiceError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        raise typer.Exit(1) from None
    print(json.dumps({"event": "started", "experiment_id": started.experiment_id,
                      "plan_id": plan_id}, default=str))
    sys.stdout.flush()
    try:
        for event in agent_service.AgentService().watch(started.experiment_id):
            sys.stdout.write(json.dumps(event, default=str) + "\n")
            sys.stdout.flush()
    except agent_service.UnknownExperimentError:
        print(json.dumps({"ok": False, "error": {"code": "unknown_experiment"}}))
        raise typer.Exit(1) from None
    except Exception as error:
        print(json.dumps({"event": "final", "final": False,
                          "note": f"bridge error before a final event: {type(error).__name__}: {error}"}))
        sys.stdout.flush()
        raise typer.Exit(1) from None


@bridge_app.command("status")
def bridge_status(experiment_id: str = typer.Argument(...)) -> None:
    try:
        result = agent_service.AgentService().status(experiment_id)
    except agent_service.ServiceError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        raise typer.Exit(1) from None
    print(result.model_dump_json(exclude_none=True, indent=2))


@bridge_app.command("cancel")
def bridge_cancel(experiment_id: str = typer.Argument(...)) -> None:
    try:
        result = agent_service.AgentService().cancel(experiment_id)
    except agent_service.ServiceError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
        raise typer.Exit(1) from None
    print(result.model_dump_json(exclude_none=True, indent=2))


@app.command("_worker", hidden=True)
def worker(spec_path: Path = typer.Argument(...),
            skip_docker: bool = typer.Option(False, help="Skip docker checks.")) -> None:
    raise typer.Exit(agent_service.run_experiment_worker(spec_path, skip_docker=skip_docker))


@app.callback()
def _default(ctx: typer.Context,
             version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
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
        typer.secho(f"error: unexpected {type(e).__name__}: {e}", fg=typer.colors.RED)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
