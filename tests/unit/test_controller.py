"""Controller prepare/snapshot/resume without launching pier."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.hashes import spec_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.normalize import experiment_id
from roast_my_harness.store.repository import Repository

SPEC = """
schema_version = 1
name = "ctrl-test"
[tasks]
path = "./dataset"
[control]
enabled = true
[[variants]]
id = "a"
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
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, tmp_path / "run", repo, None
    )
    controller.prepare(spec_path)
    assert controller.state == "READY"
    assert set(controller.jobs) == {"control", "a"}
    assert (tmp_path / "run" / "experiment.toml").is_file()
    assert (tmp_path / "run" / "manifest.json").is_file()
    assert (tmp_path / "run" / "staging" / "a" / "variant.json").is_file()
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
