"""Controller prepare/snapshot/resume without launching pier."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.resolved import resolve_run_spec
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

SPEC = """
schema_version = 2
name = "ctrl-test"
[tasks]
path = "./dataset"
[control]
enabled = true
[[variants]]
id = "a"
[variants.env]
AIRHEAD_KEEP = "2"
[[variants.extensions]]
kind = "local"
path = "./ext"
entry = "src/index.ts"
"""


def setup(tmp_path: Path) -> Path:
    for task_id in ("t1", "t2"):
        task = tmp_path / "dataset" / task_id
        task.mkdir(parents=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"task {task_id}\n")
    ext = tmp_path / "ext"
    (ext / "src").mkdir(parents=True)
    (ext / "src" / "index.ts").write_text("1")
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(SPEC)
    return spec_path


def test_prepare_and_snapshot(tmp_path: Path):
    import os

    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    spec_path = setup(tmp_path)
    spec = load_experiment(spec_path)
    repo = Repository(tmp_path / "db.sqlite")
    exp_id = resolve_run_spec(spec, _pairs(spec)).run_id
    controller = ExperimentController(
        spec, exp_id, tmp_path / "run", repo, None
    )
    controller.prepare(spec_path)
    assert controller.state == "READY"
    assert set(controller.jobs) == {"control", "a"}
    assert (tmp_path / "run" / "experiment.toml").is_file()
    assert (tmp_path / "run" / "manifest.json").is_file()
    assert (tmp_path / "run" / "staging" / "a" / "variant.json").is_file()
    staged_env = tmp_path / "run" / "staging" / "a" / "env.json"
    assert staged_env.is_file()
    import json as _json
    import stat as _stat
    assert _json.loads(staged_env.read_text()) == {"AIRHEAD_KEEP": "2"}
    assert not _stat.S_IMODE(staged_env.stat().st_mode) & 0o077
    staged_manifest = _json.loads(
        (tmp_path / "run" / "staging" / "a" / "variant.json").read_text()
    )
    assert staged_manifest["env"] == {}
    staged_auth = tmp_path / "run" / "staging" / "a" / "auth.json"
    assert staged_auth.is_file()  # codex default staged per job

    observer = ExperimentController(
        spec, exp_id, tmp_path / "run", repo, None
    )
    observer.load_for_observation()
    assert observer.state == "READY"
    assert set(observer.jobs) == {"control", "a"}
    extra = tmp_path / "dataset" / "t3"
    extra.mkdir()
    (extra / "task.toml").write_text('schema_version = "1.3"\n')
    (extra / "instruction.md").write_text("task t3\n")
    assert observer.snapshot()["tasks"] == ["t1", "t2"]
    manifest_path = tmp_path / "run" / "manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    manifest["control_reuse"] = {
        "accepted": True,
        "reused_tasks": ["t2"],
    }
    manifest_path.write_text(_json.dumps(manifest))
    historic_observer = ExperimentController(
        spec, exp_id, tmp_path / "run", repo, None
    )
    historic_observer.load_for_observation()
    assert historic_observer.snapshot()["matrix"]["control"]["t2"] == "H"


    (extra / "task.toml").unlink()
    (extra / "instruction.md").unlink()
    extra.rmdir()

    snap = controller.snapshot()
    assert snap["tasks"] == ["t1", "t2"]
    assert snap["matrix"]["a"] == {"t1": ".", "t2": "."}

    # A completed control trial makes resume skip that cell.
    trial = tmp_path / "run" / "jobs" / "control" / "2026" / "t1__X"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(
        '{"task_name":"t1","verifier_result":{"rewards":{"reward":1.0}},'
        '"exception_info":{}}'
    )
    snap = controller.snapshot()
    assert snap["matrix"]["control"]["t1"] == "P"
    assert snap["matrix"]["control"]["t2"] == "."
    repo.close()


def test_terminal_state_sets_finished_at(tmp_path: Path):
    import os

    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    spec_path = setup(tmp_path)
    spec = load_experiment(spec_path)
    repo = Repository(tmp_path / "db.sqlite")
    exp_id = resolve_run_spec(spec, _pairs(spec)).run_id
    controller = ExperimentController(spec, exp_id, tmp_path / "run", repo, None)
    controller.prepare(spec_path)
    controller._set_state("RUNNING")
    controller._set_state("COMPLETE")
    row = repo.get_experiment(exp_id)
    assert row["status"] == "COMPLETE"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    repo.close()


def _pairs(spec) -> list[tuple[str, str]]:
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    return [(t.task_id, task_hash(t.path)) for t in tasks]


def _prepared_controller(tmp_path: Path) -> ExperimentController:
    import os

    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    spec_path = setup(tmp_path)
    spec = load_experiment(spec_path)
    repo = Repository(tmp_path / "db.sqlite")
    exp_id = resolve_run_spec(spec, _pairs(spec)).run_id
    controller = ExperimentController(spec, exp_id, tmp_path / "run", repo, None)
    controller.prepare(spec_path)
    return controller


def _seed_trial(run: Path, variant: str, task: str, *, reward=None, exception=None):
    trial = run / "jobs" / variant / "2026" / f"{task}__X"
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    import json as _json

    result: dict = {"task_name": task, "exception_info": {}}
    if exception is not None:
        result["exception_info"] = {"exception_type": exception}
    else:
        result["verifier_result"] = {"rewards": {"reward": reward}}
    (trial / "result.json").write_text(_json.dumps(result))


def _includes(controller: ExperimentController, variant: str) -> list[str] | None:
    procs = controller.jobs[variant].procs
    if not procs:
        return None
    out: list[str] = []
    for proc in procs:
        argv = proc.argv
        out += [argv[i + 1] for i, a in enumerate(argv) if a == "--include-task-name"]
    return out


def test_rerun_filter_rejects_unknown_ids(tmp_path: Path):
    import pytest

    from roast_my_harness.errors import PierError

    controller = _prepared_controller(tmp_path)
    with pytest.raises(PierError):
        controller.set_rerun_filter(tasks=["no-such-task"])
    with pytest.raises(PierError):
        controller.set_rerun_filter(variants=["no-such-arm"])


def test_launch_without_filter_runs_all_missing(tmp_path: Path):
    controller = _prepared_controller(tmp_path)
    _seed_trial(tmp_path / "run", "control", "t1", reward=1.0)
    controller._launch()
    assert _includes(controller, "control") == ["t2"]
    assert _includes(controller, "a") == ["t1", "t2"]


def test_launch_with_task_filter_runs_only_selected(tmp_path: Path):
    controller = _prepared_controller(tmp_path)
    controller.set_rerun_filter(tasks=["t2"])
    controller._launch()
    assert _includes(controller, "control") == ["t2"]
    assert _includes(controller, "a") == ["t2"]


def test_launch_with_variant_filter_skips_other_arms(tmp_path: Path):
    controller = _prepared_controller(tmp_path)
    controller.set_rerun_filter(variants=["a"])
    controller._launch()
    assert _includes(controller, "control") is None
    assert _includes(controller, "a") == ["t1", "t2"]


def test_launch_retry_errors_reruns_error_cells_only(tmp_path: Path):
    controller = _prepared_controller(tmp_path)
    _seed_trial(tmp_path / "run", "a", "t1", exception="TimeoutError")
    _seed_trial(tmp_path / "run", "a", "t2", reward=1.0)
    controller.set_rerun_filter(retry_errors=True)
    controller._launch()
    assert _includes(controller, "a") == ["t1"]
    # Control has no completed cells, so both of its cells are still missing.
    assert _includes(controller, "control") == ["t1", "t2"]


def test_pier_env_pythonpath_exposes_package_parent(monkeypatch):
    """PYTHONPATH must point at the *parent* of roast_my_harness so pier can
    `import roast_my_harness.adapter.pi_agent` (regression: off-by-one)."""

    import roast_my_harness.runner.controller as controller_mod

    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = ExperimentController._pier_env(object.__new__(ExperimentController))
    expected = str(
        Path(controller_mod.__file__).resolve().parents[2]
    )
    assert env["PYTHONPATH"] == expected

    # The path must make the adapter importable from a clean interpreter.
    import subprocess
    import sys

    code = "import roast_my_harness.adapter.pi_agent as m; print(m.__name__)"
    proc = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "roast_my_harness.adapter.pi_agent"


def test_retry_moves_recorded_trials_aside_but_keeps_budget(tmp_path: Path):
    controller = _prepared_controller(tmp_path)
    run = tmp_path / "run"
    _seed_trial(run, "a", "t1", exception="ValueError")
    assert controller._attempts_used("a", "t1", 1) == 1
    _seed_trial(run, "a", "deepswe_pwntools-tube-multiplexi", exception="ValueError")
    long_id = "deepswe_pwntools-tube-multiplexing"
    assert controller._attempts_used("a", long_id, 1) == 1
    controller._clear_retry_trials("a", long_id, 1)
    assert controller._attempts_used("a", long_id, 1) == 1
    assert controller._attempts_used("a", "t1", 1) == 1
    controller._clear_retry_trials("a", "t1", 1)
    assert list((run / "jobs" / "a").rglob("result.json")) == []
    backup = run / "logs" / "retries" / "a" / "replicate-1" / "t1__X"
    assert (backup / "result.json").is_file()
    assert controller._attempts_used("a", "t1", 1) == 1
