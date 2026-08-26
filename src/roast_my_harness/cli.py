"""Typer CLI. Headless commands emit plain text without a TTY."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer

from roast_my_harness import __version__
from roast_my_harness.auth import service as auth_service
from roast_my_harness.errors import RoastMyHarnessError
from roast_my_harness.paths import database_path, run_dir
from roast_my_harness.runner import preflight
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.hashes import spec_hash as compute_spec_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id
from roast_my_harness.store.repository import Repository

app = typer.Typer(
    name="roast-my-harness",
    help="Compare Pi extensions and skills on identical Pier tasks.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
auth_app = typer.Typer(help="Credential inspection and login.")
app.add_typer(auth_app, name="auth")

STARTER_TOML = '''
schema_version = 1
name = "my-comparison"
pi_version = "0.84.3"
thinking = "high"          # off | minimal | low | medium | high | xhigh | max

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"  # any host pi models.json provider, or "custom" with provider_id + models_json

[tasks]
path = "/path/to/task-dataset"   # dir of task dirs, each with task.toml
include = ["*"]
exclude = []

[concurrency]
per_variant = 2

[control]
enabled = true                  # bare Pi control arm
reuse = "never"                 
minimum_runs_per_task = 10
maximum_age_days = 30
sentinel_tasks = 6              

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

# Local skill example:
# [[variants]]
# id = "my-skill"
# [[variants.skills]]
# kind = "local"
# path = "~/my-skills/my-skill"  # must contain SKILL.md
'''


def _repo() -> Repository:
    return Repository(database_path())


def _print_progress(message: str) -> None:
    """Plain stderr progress line (states, launches, per-trial outcomes)."""
    print(f"[roast] {message}", file=sys.stderr)


def _ask_reuse(message: str) -> bool:
    """Interactive ask-mode prompt for control reuse (plan section 16)."""
    typer.echo(message)
    return typer.confirm("Reuse this historic control pool?")


# ------------------------------------------------------------------ init --

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
    allow_unsafe_source: bool = typer.Option(
        False, "--allow-unsafe-source", help="Allow world-writable sources."
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable."),
) -> None:
    """Validate spec, discover tasks, check sources and environment."""
    spec = load_experiment(spec_path)
    results = preflight.run_checks(spec)
    if json_output:
        payload: dict[str, Any] = {
            "ok": not preflight.has_failures(results),
            "checks": [r.__dict__ for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        typer.echo(preflight.format_table(results))
        typer.echo(preflight.container_probe_note())
    if preflight.has_failures(results):
        raise typer.Exit(1)


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
    experiment_id = make_experiment_id(spec.name, compute_spec_hash(spec))
    repo = _repo()
    controller = ExperimentController(
        spec, experiment_id, run_dir(experiment_id), repo,
        progress=_print_progress,
        ask=_ask_reuse if (sys.stdin.isatty() and not yes) else None,
    )
    controller.prepare(spec_path)
    controller.enforce_reuse_policy(
        interactive=sys.stdin.isatty() and not yes
    )

    loop = asyncio.new_event_loop()
    main_task = loop.create_task(controller.run())

    def _sigint(*_args: Any) -> None:
        controller.request_cancel()

    import signal

    loop.add_signal_handler(signal.SIGINT, _sigint)
    try:
        final = loop.run_until_complete(main_task)
    finally:
        loop.close()
    typer.secho(f"experiment {experiment_id}: {final}", fg=typer.colors.GREEN)


# ---------------------------------------------------------------- resume --

@app.command()
def resume(
    experiment_id: str = typer.Argument(..., help="Experiment id from `list`."),
) -> None:
    """Reconcile completed trials, then run only missing cells."""
    repo = _repo()
    row = repo.get_experiment(experiment_id)
    if row is None:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1)
    from roast_my_harness.spec.models import ExperimentSpec

    spec = ExperimentSpec.model_validate(json.loads(row["spec_json"]))
    controller = ExperimentController(
        spec, experiment_id, Path(row["run_dir"]), repo, _print_progress
    )
    controller.prepare()
    asyncio.run(_run_with_cancel(controller))


async def _run_with_cancel(controller: ExperimentController) -> None:
    import signal

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, controller.request_cancel)
    await controller.run()


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
    controller = ExperimentController(
        spec, experiment_id, Path(row["run_dir"]), repo, None
    )
    controller.prepare()
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
    repo = _repo()
    row = repo.get_experiment(experiment_id)
    if row is None:
        typer.secho(f"unknown experiment {experiment_id}", fg=typer.colors.RED)
        raise typer.Exit(1)
    from roast_my_harness.report import collect as report_collect
    from roast_my_harness.report import exports as report_exports
    from roast_my_harness.report import markdown as report_markdown

    rd = Path(row["run_dir"])
    rows = report_collect.collect_rows(rd / "jobs")
    if not rows:
        typer.secho("no completed trials to report", fg=typer.colors.RED)
        raise typer.Exit(1)
    provenance: dict[str, Any] = {
        "experiment_id": experiment_id, "spec_hash": row["spec_hash"]
    }
    manifest_path = rd / "manifest.json"
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            provenance = loaded  # full provenance incl. control_reuse disclosure
    csv = report_exports.write_summary_csv(rd, rows)
    report_exports.write_summary_json(rd, rows, provenance)
    out = report_markdown.generate_report(
        rd, experiment_id=experiment_id, provenance=provenance, rows=rows
    )
    typer.secho(f"wrote {csv} and {out}", fg=typer.colors.GREEN)


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
                [dict(r) for r in rows], indent=2, default=str,
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
        typer.echo(f"no {auth_service.CODEX_PROVIDER} credential at "
                   f"{auth_service.pi_auth_file()}; run pi /login codex")
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
    typer.echo("run `pi` then `/login codex`; roast-my-harness reuses ~/.pi/agent/auth.json")


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
    auth_service.pi_auth_file().write_text(json.dumps(data, indent=2) + "\n")
    typer.echo("removed")


# ---------------------------------------------------------------- default --

@app.command("import-dse")
def import_dse(
    results_root: Path = typer.Argument(
        ..., help="Legacy DSE-tests results directory (results-* layout)."
    ),
    variant: list[str] = typer.Option(
        ["bare"], help="Variant directory names to import (repeatable)."
    ),
) -> None:
    """Import legacy DSE control observations (reference-only, not reused)."""
    from roast_my_harness.report.importer import import_dse_results

    report = import_dse_results(_repo(), results_root, variants=variant)
    typer.echo(
        f"scanned={report.scanned} imported={report.imported} "
        f"skipped_error={report.skipped_error} skipped_layout={report.skipped_variant}"
    )
    typer.echo(
        "imported observations are ineligible for automatic reuse "
        "(unknown pi_version/adapter provenance)"
    )
    for detail in report.details[:10]:
        typer.echo(f"  {detail}")


@app.callback()
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Show version and exit."
    ),
) -> None:
    if version:
        typer.echo(f"roast-my-harness {__version__}")
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


if __name__ == "__main__":
    main()
