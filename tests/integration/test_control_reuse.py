"""Integration: two-wave control flow (sentinel hold -> reuse/release).

A fake pier writes trial results on launch so no containers are needed.
Wave 1 runs sentinel + uncovered + variant tasks; wave 2 runs released
held controls when the sentinel rejects (or skips them when accepted).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.paths import database_path
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.hashes import spec_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.normalize import experiment_id
from roast_my_harness.store.repository import Repository

SPEC = """
schema_version = 1
name = "reuse-test"
[tasks]
path = "./dataset"
[concurrency]
per_variant = 1
[control]
enabled = true
reuse = "require"
minimum_runs_per_task = 2
maximum_age_days = 30
sentinel_tasks = 2
[[variants]]
id = "a"
"""

def setup(tmp_path: Path) -> Path:
    for task_id in ("t1", "t2", "t3"):
        task = tmp_path / "dataset" / task_id
        task.mkdir(parents=True, exist_ok=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"task {task_id}\n")
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(SPEC)
    return spec_path

def seed_pool(repo: Repository, controller_ready: ExperimentController,
              tasks: list[str], resolved: bool, age_days: int = 0) -> None:
    """Insert eligible observations that satisfy minimum_runs."""
    from datetime import UTC, datetime, timedelta

    observed = (
        datetime.now(UTC) - timedelta(days=age_days)
    ).isoformat()
    for task_id in tasks:
        cohort = controller_ready._cohort_keys[task_id]
        task_hash = controller_ready._task_hashes[task_id]
        for i in range(3):
            repo.record_control_observation(
                cohort_key=cohort, task_hash=task_hash,
                trial_id=f"seed-{task_id}-{i}", resolved=resolved,
                reward=1.0 if resolved else 0.0, observed_at=observed,
            )

class FakePier:
    """Replace process launch: write a result for every requested task."""

    def __init__(self, controller: ExperimentController):
        self.controller = controller
        self.launches: list[list[str]] = []

    def patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from roast_my_harness.runner import process as process_mod

        class FakeProc:
            returncode = 0
            pid = 4242

            async def wait(self):
                return 0

        async def fake_start(self_proc, env=None):
            self.launches.append(list(self_proc.argv))
            self_proc.proc = FakeProc()
            argv = self_proc.argv
            includes = [
                argv[i + 1] for i, a in enumerate(argv) if a == "--include-task-name"
            ]
            jobs_dir = None
            for i, a in enumerate(argv):
                if a == "--jobs-dir":
                    jobs_dir = Path(argv[i + 1])
            for task_id in includes:
                trial = jobs_dir / "2026" / f"{task_id}__X"
                (trial / "agent").mkdir(parents=True, exist_ok=True)
                (trial / "verifier").mkdir(parents=True, exist_ok=True)
                (trial / "result.json").write_text(json.dumps({
                    "task_name": task_id,
                    "exception_info": None,
                    "verifier_result": {"rewards": {"reward": 0.0}},
                    "finished_at": "2026-08-26T00:00:00Z",
                }))

        monkeypatch.setattr(process_mod.VariantProcess, "start", fake_start)

        @property
        def fake_running(self_proc):
            return False

        monkeypatch.setattr(
            process_mod.VariantProcess, "running", fake_running
        )

@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_MY_HARNESS_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path

async def test_sentinel_reject_releases_held_controls(env, monkeypatch):
    spec_path = setup(env)
    spec = load_experiment(spec_path)
    repo = Repository(database_path())
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, env / "runs" / exp_id, repo, None
    )
    controller.prepare(spec_path)

    
    seed_pool(repo, controller, ["t1", "t2", "t3"], resolved=True)
    controller.enforce_reuse_policy(interactive=False)  
    assert controller._reuse_plan is not None
    assert sum(controller._reuse_plan.reuse_by_task.values()) >= 1

    fake = FakePier(controller)
    fake.patch(monkeypatch)
    final = await controller.run()
    assert final == "COMPLETE"

    
    
    assert controller._sentinel_verdict is not None
    assert controller._sentinel_verdict["reject"] is True
    assert controller._reuse_accepted is False

    snap = controller.snapshot()
    
    for variant in snap["matrix"]:
        assert "H" not in snap["matrix"][variant].values()
    assert all(
        c == "F" for c in snap["matrix"]["control"].values()
    ), snap["matrix"]["control"]

    
    assert len(fake.launches) >= 2  # control waves: sentinel first, released second

    
    pools_with_own = repo.conn.execute(
        "SELECT COUNT(*) FROM control_observations WHERE eligible=1 "
        "AND source LIKE 'experiment:%'"
    ).fetchone()[0]
    assert pools_with_own >= 2  
    # Rework check 1: rejection-path disclosure covers every control task
    # (released-after-rejection tasks must appear in fresh_control_tasks).
    summary = controller.reuse_summary()
    assert summary["fresh_control_tasks"] == ["t1", "t2", "t3"]
    assert summary["reused_tasks"] == []
    # Rework check 2: CLI `report` reproduces the finalize-path report
    # byte for byte (manifest.json is the provenance payload).
    from typer.testing import CliRunner

    from roast_my_harness.cli import app as cli_app

    run_dir = env / "runs" / exp_id
    before = (run_dir / "report.md").read_text()
    import os

    result = CliRunner().invoke(
        cli_app, ["report", exp_id],
        env={
            "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
            "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME", ""),
            "ROAST_MY_HARNESS_RUNS_DIR": os.environ.get(
                "ROAST_MY_HARNESS_RUNS_DIR", ""
            ),
        },
    )
    assert result.exit_code == 0, result.output
    after = (run_dir / "report.md").read_text()
    assert before == after, "CLI report regeneration is not byte-identical"
    repo.close()

async def test_policy_require_fails_without_history(env):
    spec_path = setup(env)
    spec = load_experiment(spec_path)
    repo = Repository(database_path())
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, env / "runs" / exp_id, repo, None
    )
    controller.prepare(spec_path)
    with pytest.raises(ValueError, match="require"):
        controller.enforce_reuse_policy(interactive=False)
    repo.close()

async def test_policy_never_runs_everything_fresh(env, monkeypatch):
    spec = load_experiment(setup(env))
    spec = spec.model_copy(update={
        "control": spec.control.model_copy(update={"reuse": "never"})
    })
    repo = Repository(database_path())
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, env / "runs" / exp_id, repo, None
    )
    controller.prepare()
    assert controller._reuse_plan is not None
    assert not any(controller._reuse_plan.reuse_by_task.values())
    repo.close()


async def test_cancel_removes_staging(env, monkeypatch):
    """Rework check 3: Ctrl-C/FAILED paths must not leave credentials."""
    spec = load_experiment(setup(env))
    repo = Repository(database_path())
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, env / "runs" / exp_id, repo, None
    )
    controller.prepare(setup(env))
    staging_root = env / "runs" / exp_id / "staging"
    staged_auths = list(staging_root.rglob("auth.json"))
    assert staged_auths, "fixture: staged credentials exist before cancel"
    await controller._cancel("CANCELLED")
    assert not staging_root.exists() or not any(
        staging_root.rglob("auth.json")
    ), "cancel left staged credentials on disk"
    repo.close()


async def test_launch_failure_cleans_staging(env, monkeypatch):
    """Rework regression: a failed _launch must not leave credentials either."""
    from roast_my_harness.runner import pier as pier_mod

    spec = load_experiment(setup(env))
    repo = Repository(database_path())
    exp_id = experiment_id(spec.name, spec_hash(spec))
    controller = ExperimentController(
        spec, exp_id, env / "runs" / exp_id, repo, None
    )
    controller.prepare(setup(env))
    staging_root = env / "runs" / exp_id / "staging"
    assert any(staging_root.rglob("auth.json"))

    def boom(**kwargs):
        raise RuntimeError("simulated pier arg failure")

    monkeypatch.setattr(pier_mod, "build_run_args", boom)
    from roast_my_harness.errors import PierError

    try:
        await controller.run()
    except PierError:
        pass
    assert not any(staging_root.rglob("auth.json")), (
        "staged credentials survived a _launch failure"
    )
    repo.close()
