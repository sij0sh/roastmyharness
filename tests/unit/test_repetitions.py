"""Repetitions: independent rollouts, retries scoped to one replicate."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.report.collect import collect_rows
from roast_my_harness.report.statistics import paired_flips, task_rates
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.resolved import resolve_run_spec
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

SPEC = """
schema_version = 3
name = "reps"
[tasks]
path = "./dataset"
[execution]
repetitions = 2
[[variants]]
id = "a"
"""


def setup(tmp_path: Path, spec_text: str = SPEC) -> Path:
    for task_id in ("t1", "t2"):
        task = tmp_path / "dataset" / task_id
        task.mkdir(parents=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"task {task_id}\n")
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(spec_text)
    return spec_path


def _prepare(tmp_path: Path, spec_text: str = SPEC) -> ExperimentController:
    import os

    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    spec_path = setup(tmp_path, spec_text)
    spec = load_experiment(spec_path)
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    pairs = [(t.task_id, task_hash(t.path)) for t in tasks]
    resolved = resolve_run_spec(
        spec, pairs, repetitions=spec.execution.repetitions
    )
    repo = Repository(tmp_path / "db.sqlite")
    controller = ExperimentController(spec, resolved.run_id, tmp_path / "run", repo, None)
    controller.prepare(spec_path, resolved=resolved)
    return controller


def _seed(
    run: Path,
    variant: str,
    task: str,
    replicate: int | None,
    *,
    reward: float | None = 1.0,
    exception: str | None = None,
    tag: str = "X",
):
    base = run / "jobs" / variant
    if replicate is not None:
        base = base / f"replicate-{replicate}"
    trial = base / "2026" / f"{task}__{tag}"
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    result: dict = {"task_name": task, "exception_info": {}}
    if exception is not None:
        result["exception_info"] = {"exception_type": exception}
    else:
        result["verifier_result"] = {"rewards": {"reward": reward}}
    (trial / "result.json").write_text(json.dumps(result))


def _launches(controller: ExperimentController) -> dict[tuple[str, str], list[str]]:
    """(variant, jobs-dir-suffix) -> included tasks per launched proc."""
    out: dict[tuple[str, str], list[str]] = {}
    for job in controller.jobs.values():
        for proc in job.procs:
            argv = proc.argv
            jobs_dir = Path(argv[argv.index("--jobs-dir") + 1])
            includes = [
                argv[i + 1] for i, a in enumerate(argv) if a == "--include-task-name"
            ]
            out[(job.variant_id, jobs_dir.name)] = includes
    return out


def test_launch_creates_one_proc_per_replicate(tmp_path: Path):
    controller = _prepare(tmp_path)
    try:
        controller._launch()
        launches = _launches(controller)
        assert launches[("a", "replicate-1")] == ["t1", "t2"]
        assert launches[("a", "replicate-2")] == ["t1", "t2"]
        assert launches[("control", "replicate-1")] == ["t1", "t2"]
        assert launches[("control", "replicate-2")] == ["t1", "t2"]
    finally:
        controller.store.close()


def test_resume_fills_only_missing_replicates(tmp_path: Path):
    controller = _prepare(tmp_path)
    try:
        run = tmp_path / "run"
        _seed(run, "a", "t1", 1, reward=1.0)
        _seed(run, "a", "t2", 2, reward=0.0)
        controller._launch()
        launches = _launches(controller)
        assert launches[("a", "replicate-1")] == ["t2"]
        assert launches[("a", "replicate-2")] == ["t1"]
    finally:
        controller.store.close()


def test_retry_errors_stays_within_replicate(tmp_path: Path):
    controller = _prepare(tmp_path)
    try:
        run = tmp_path / "run"
        _seed(run, "a", "t1", 1, reward=None, exception="TimeoutError")
        _seed(run, "a", "t2", 1, reward=1.0)
        controller.set_rerun_filter(retry_errors=True)
        controller._launch()
        launches = _launches(controller)
        assert launches[("a", "replicate-1")] == ["t1"]
        assert launches[("a", "replicate-2")] == ["t1", "t2"]
    finally:
        controller.store.close()


def test_max_retries_zero_skips_error_relaunch(tmp_path: Path):
    spec_text = SPEC.replace("repetitions = 2", "repetitions = 1\nmax_retries = 0")
    controller = _prepare(tmp_path, spec_text)
    try:
        _seed(tmp_path / "run", "a", "t1", None, reward=None, exception="TimeoutError")
        _seed(tmp_path / "run", "a", "t2", None, reward=1.0)
        controller.set_rerun_filter(retry_errors=True)
        controller._launch()
        assert controller.jobs["a"].procs == []
    finally:
        controller.store.close()


def test_max_retries_caps_second_retry(tmp_path: Path):
    controller = _prepare(tmp_path)
    try:
        run = tmp_path / "run"
        _seed(run, "a", "t1", 1, reward=None, exception="TimeoutError", tag="X")
        _seed(run, "a", "t1", 1, reward=None, exception="TimeoutError", tag="Y")
        _seed(run, "a", "t2", 1, reward=1.0)
        _seed(run, "a", "t1", 2, reward=1.0)
        _seed(run, "a", "t2", 2, reward=1.0)
        controller.set_rerun_filter(retry_errors=True)
        controller._launch()
        assert ("a", "replicate-1") not in _launches(controller)
    finally:
        controller.store.close()


def test_collect_rows_carry_replicate(tmp_path: Path):
    run = tmp_path / "run"
    _seed(run, "a", "t1", 1, reward=1.0)
    _seed(run, "a", "t1", 2, reward=0.0)
    rows = collect_rows(run / "jobs")
    by_rep = {(r["task"], r["replicate"]): r for r in rows if r["variant"] == "a"}
    assert by_rep[("t1", 1)]["resolved"] == 1
    assert by_rep[("t1", 2)]["resolved"] == 0


def test_task_rates_average_replicates():
    rows = [
        {"variant": "a", "task": "t1", "replicate": 1, "resolved": 1},
        {"variant": "a", "task": "t1", "replicate": 2, "resolved": 0},
        {"variant": "a", "task": "t2", "replicate": 1, "resolved": 1},
        {"variant": "a", "task": "t2", "replicate": 2, "resolved": 1},
        {"variant": "a", "task": "t3", "replicate": 1, "resolved": 0,
         "exception_type": "TimeoutError"},
    ]
    rates = task_rates(rows)
    assert rates == {"a": {"t1": 0.5, "t2": 1.0}}


def test_paired_flips_pair_shared_replicates():
    rows = [
        {"variant": "a", "task": "t1", "replicate": 1, "resolved": 0},
        {"variant": "a", "task": "t1", "replicate": 2, "resolved": 0},
        {"variant": "b", "task": "t1", "replicate": 1, "resolved": 1},
        {"variant": "b", "task": "t1", "replicate": 2, "resolved": 0},
    ]
    (flip,) = paired_flips(rows)
    _a, _b, both, rescued, _broken, discordant = flip
    assert (both, rescued) == (0, 1)
    assert discordant == [("t1", "a fail -> b pass")]


def test_prepare_trials_multiply_repetitions(tmp_path: Path, monkeypatch):
    from roast_my_harness.agent import service as svc
    from roast_my_harness.runner import preflight as pf

    monkeypatch.setattr(pf, "run_checks", lambda spec, *, skip_docker=False: [])
    monkeypatch.setattr(
        "roast_my_harness.runner.pier.pier_version", lambda: "0.3.0"
    )
    spec_path = setup(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.ok is True
    assert result.experiment is not None
    assert result.experiment.trials == 2 * 2 * 2


def test_migration_v5_backfills_replicate_one(tmp_path: Path):
    import sqlite3

    from roast_my_harness.store.migrations import MIGRATIONS

    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    for version in (1, 2, 3, 4):
        conn.executescript(MIGRATIONS[version])
    conn.execute(
        "INSERT INTO experiments (id, name, spec_json, spec_hash, status,"
        " created_at, run_dir, tool_version, adapter_protocol)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("e1", "old", "{}", "hash", "COMPLETE", "2026-01-01", "/tmp", "0", 1),
    )
    conn.execute(
        "INSERT INTO trials (id, experiment_id, variant_id, task_id, attempt,"
        " status, job_path, reward, resolved) VALUES (?,?,?,?,?,?,?,?,?)",
        ("trial-1", "e1", "a", "t1", 1, "pass", "/jobs/a/t1__X", 1.0, 1),
    )
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()

    repo = Repository(db)
    try:
        row = repo.conn.execute("SELECT replicate FROM trials WHERE id='trial-1'").fetchone()
        assert row["replicate"] == 1
        trial_b = repo.upsert_reconciled_trial(
            experiment_id="e1", variant_id="a", task_id="t1", replicate=2,
            status="pass", job_path="/jobs/a/replicate-2/t1__X",
            reward=1.0, resolved=True, exception_type=None,
            metrics=None, finished_at=None,
        )
        assert trial_b != "trial-1"
        assert repo.next_attempt("e1", "a", "t1", 1) == 2
        assert repo.next_attempt("e1", "a", "t1", 2) == 2
    finally:
        repo.close()
