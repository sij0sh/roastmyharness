"""Agent orchestration service: prepare/start/status/cancel/report contract."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from roast_my_harness.agent import service as svc
from roast_my_harness.spec.load import load_experiment

SPEC = """
schema_version = 3
name = "svc"
pi_version = "0.84.3"

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[tasks]
path = "{tasks}"

[[variants]]
id = "bare"
"""


def make_spec(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset" / "t1"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text('schema_version = "1.3"\n')
    path = tmp_path / "exp.toml"
    path.write_text(SPEC.format(tasks=tmp_path / "dataset"))
    return path


@pytest.fixture
def green_preflight(monkeypatch):
    """All preflight checks pass without touching pier/docker/auth/disk."""
    from roast_my_harness.runner import preflight as pf

    def fake_run_checks(spec, *, skip_docker=False):
        return [pf._ok("python", "stubbed")]

    monkeypatch.setattr(pf, "run_checks", fake_run_checks)
    monkeypatch.setattr(
        "roast_my_harness.runner.pier.pier_version", lambda: "0.3.0"
    )
    fresh = {"access": "x", "type": "oauth", "expires": (time.time() + 3600) * 1000}
    monkeypatch.setattr(
        "roast_my_harness.auth.service.codex_credential", lambda: fresh
    )




def test_prepare_ready_for_confirmation(tmp_path, green_preflight):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.ok is True
    assert result.state == "ready_for_confirmation"
    assert result.plan_id and result.plan_id.startswith("plan_")
    assert result.next_action == "start"
    assert result.experiment.tasks == 1
    assert result.experiment.arms == 2
    assert result.experiment.trials == 2
    assert result.experiment.model == "openai-codex/gpt-5.6-luna"
    assert result.experiment.name == "svc"
    assert result.experiment.pi_version == "0.84.3"
    assert result.experiment.resolved_pi_version == "0.84.3"
    assert result.experiment.repetitions == 1
    assert result.experiment.thinking == "high"
    assert result.experiment.control == "fresh"
    assert result.experiment.task_ids == ["t1"]
    assert result.experiment.tasks_path == str((tmp_path / "dataset").resolve())
    assert result.experiment.arm_ids == ["control", "bare"]
    assert result.experiment.variant_sources == {"bare": []}
    plan_file = tmp_path / "plans" / f"{result.plan_id}.json"
    assert plan_file.is_file()
    plan = json.loads(plan_file.read_text())
    assert plan["bindings"]["spec_hash"]
    assert plan["bindings"]["task_hashes"] == [["t1", plan["bindings"]["task_hashes"][0][1]]]
    assert plan["bindings"]["versions"]["pi_version"] == "0.84.3"


def test_prepare_reports_repetitions_in_trial_math(tmp_path, green_preflight):
    spec_path = make_spec(tmp_path)
    spec_path.write_text(
        SPEC.format(tasks=tmp_path / "dataset").replace(
            'name = "svc"', 'name = "svc"\nhypothesis = "bare is enough"'
        )
        + "\n[execution]\nrepetitions = 3\n"
    )
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.ok is True
    assert result.experiment.repetitions == 3
    assert result.experiment.trials == 1 * 2 * 3
    assert result.experiment.hypothesis == "bare is enough"


def test_prepare_needs_input_on_bad_spec(tmp_path):
    spec_path = make_spec(tmp_path)
    spec_path.write_text("bogus_field = 1\n")
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.ok is False
    assert result.state == "needs_input"
    assert result.questions and result.questions[0].field == "spec"


def test_prepare_needs_input_on_missing_tasks(tmp_path, green_preflight):
    spec_path = make_spec(tmp_path)
    import shutil

    shutil.rmtree(spec_path.parent / "dataset")
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.state == "needs_input"
    assert result.questions[0].field == "tasks.path"


def test_prepare_needs_input_on_preflight_failure(tmp_path, monkeypatch):
    from roast_my_harness.runner import preflight as pf

    spec_path = make_spec(tmp_path)

    def fake_run_checks(spec, *, skip_docker=False):
        return [pf._fail("docker", "daemon not running")]

    monkeypatch.setattr(pf, "run_checks", fake_run_checks)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(spec_path)
    assert result.state == "needs_input"
    assert result.questions[0].field == "preflight.docker"


def test_prepare_is_deterministic(tmp_path, green_preflight):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    first = service.prepare(spec_path)
    second = service.prepare(spec_path)
    assert first.plan_id == second.plan_id




def test_start_rejects_stale_plan(tmp_path, green_preflight, monkeypatch):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    
    task = tmp_path / "dataset" / "t1" / "task.toml"
    task.write_text('schema_version = "1.4"\n')
    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: 12345)
    with pytest.raises(svc.StalePlanError, match="stale"):
        service.start(prepared.plan_id)


def test_start_rejects_stale_spec_edit(tmp_path, green_preflight, monkeypatch):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    spec_path.write_text(
        SPEC.format(tasks=tmp_path / "dataset").replace(
            'id = "bare"', 'id = "renamed"'
        )
    )
    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: 12345)
    with pytest.raises(svc.StalePlanError, match="stale"):
        service.start(prepared.plan_id)


def test_start_idempotent_per_plan_id(tmp_path, green_preflight, monkeypatch):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    spawns: list[int] = []
    monkeypatch.setattr(
        service, "_spawn_worker", lambda *a, **k: (spawns.append(1) or 1)
    )
    first = service.start(prepared.plan_id)
    second = service.start(prepared.plan_id)
    assert first.ok and first.started is True
    assert second.ok and second.state == "already_started"
    assert second.experiment_id == first.experiment_id
    assert len(spawns) == 1


def test_start_rejects_unknown_and_malformed_plans(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    with pytest.raises(svc.UnknownPlanError):
        service.start("plan_000000000000")
    with pytest.raises(svc.UnknownPlanError, match="malformed"):
        service.start("../etc/passwd")


def test_start_already_started_via_db_row(tmp_path, green_preflight, monkeypatch):
    """An experiment in RUNNING state short-circuits the launch."""
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: 1)
    first = service.start(prepared.plan_id)
    
    from roast_my_harness.store.repository import Repository

    repo = Repository(tmp_path / "db.sqlite")
    repo.create_experiment(
        experiment_id=first.experiment_id,
        name="svc",
        spec=json.loads(json.dumps(load_experiment(spec_path).model_dump(mode="json"))),
        spec_hash="x",
        run_dir=str(tmp_path / "runs" / first.experiment_id),
    )
    repo.set_status(first.experiment_id, "RUNNING", started=True)
    repo.close()
    
    (tmp_path / "plans" / f"{prepared.plan_id}.started").unlink()
    again = service.start(prepared.plan_id)
    assert again.state == "already_started"
    assert again.started is False




def test_status_unknown_experiment(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    with pytest.raises(svc.UnknownExperimentError):
        service.status("nope")


def seed_experiment(tmp_path: Path, name: str, exp_id: str, spec_cache: dict) -> str:
    """Insert one experiment row built from the shared SPEC template."""
    from roast_my_harness.store.repository import Repository

    if "spec" not in spec_cache:
        spec_cache["spec"] = load_experiment(make_spec(tmp_path))
    spec = spec_cache["spec"]
    repo = Repository(tmp_path / "db.sqlite")
    repo.create_experiment(
        experiment_id=exp_id,
        name=name,
        spec=spec.model_dump(mode="json"),
        spec_hash="deadbeef",
        run_dir=str(tmp_path / "run"),
    )
    repo.close()
    return exp_id


def test_resolve_accepts_id_name_and_prefix(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    exp_id = seed_experiment(tmp_path, "svc", "svc-112233445566", {})
    assert service._resolve_experiment(exp_id) == exp_id
    assert service._resolve_experiment("svc") == exp_id
    assert service._resolve_experiment("svc-1122") == exp_id


def test_resolve_prefers_newest_on_duplicate_names(tmp_path):
    cache: dict = {}
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    seed_experiment(tmp_path, "svc", "svc-111111111111", cache)
    newest = seed_experiment(tmp_path, "svc", "svc-222222222222", cache)
    assert service._resolve_experiment("svc") == newest


def test_resolve_rejects_ambiguous_prefix(tmp_path):
    cache: dict = {}
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    seed_experiment(tmp_path, "one", "svc-112200000000", cache)
    seed_experiment(tmp_path, "two", "svc-112211111111", cache)
    with pytest.raises(svc.AmbiguousExperimentError):
        service._resolve_experiment("svc-1122")


def test_cancel_resolves_spec_name(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    seed_experiment(tmp_path, "svc", "svc-112233445566", {})
    result = service.cancel("svc")
    assert result.ok
    assert result.cancelled is False
    assert result.experiment_id == "svc-112233445566"


def test_cancel_reports_when_no_worker(tmp_path, green_preflight, monkeypatch):
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: 1)
    started = service.start(prepared.plan_id)
    
    result = service.cancel(started.experiment_id)
    assert result.ok
    assert result.cancelled is False
    assert result.note


def test_cancel_unknown_experiment(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    with pytest.raises(svc.UnknownExperimentError):
        service.cancel("nope")


# --- Fix A: start marker rollback (probe-start-brick.py) -------------------


def test_start_spawn_failure_rolls_back_marker(
    tmp_path, green_preflight, monkeypatch
):
    """A failed spawn leaves no marker, no fd leak, and start stays possible."""
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)

    def broken_spawn(*a, **k):
        raise OSError("worker log mkdir failed")

    monkeypatch.setattr(service, "_spawn_worker", broken_spawn)
    fds_before = len(os.listdir("/proc/self/fd")) if os.path.exists("/proc/self/fd") else None
    with pytest.raises(OSError):
        service.start(prepared.plan_id)
    if fds_before is not None:
        assert len(os.listdir("/proc/self/fd")) == fds_before
    assert not (tmp_path / "plans" / f"{prepared.plan_id}.started").exists()

    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: 4242)
    retry = service.start(prepared.plan_id)
    assert retry.ok
    assert retry.state == "running"
    assert retry.started is True


# --- Fix B: cancel lock probe (probe-cancel-pid-recycle.py) ----------------


def _sleepy_helper() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(120)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    return proc


def _seed_running_row(tmp_path: Path, experiment_id: str, run_dir: Path) -> None:
    from roast_my_harness.store.repository import Repository

    repo = Repository(tmp_path / "db.sqlite")
    repo.create_experiment(
        experiment_id=experiment_id,
        name="svc",
        spec=json.loads(
            json.dumps(load_experiment(tmp_path / "exp.toml").model_dump(mode="json"))
        ),
        spec_hash="deadbeef",
        run_dir=str(run_dir),
        status="RUNNING",
    )
    repo.close()


def test_cancel_refuses_when_marker_pid_belongs_to_another_process(
    tmp_path, green_preflight, monkeypatch
):
    """Stale marker + OS pid reuse must not signal an unrelated process."""
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    plan = json.loads((tmp_path / "plans" / f"{prepared.plan_id}.json").read_text())
    experiment_id = plan["experiment_id"]
    helper = _sleepy_helper()
    try:
        monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: helper.pid)
        service.start(prepared.plan_id)
        # Worker crashed mid-run: marker persists, row stuck at RUNNING,
        # lock never held.
        _seed_running_row(tmp_path, experiment_id, tmp_path / "run")

        result = service.cancel(experiment_id)
        assert result.ok
        assert result.cancelled is False
        assert "no live worker" in result.note
        assert helper.poll() is None  # the unrelated process survived
    finally:
        helper.terminate()
        helper.wait(timeout=10)


def test_cancel_still_signals_worker_holding_lock(
    tmp_path, green_preflight, monkeypatch
):
    """A genuine live worker (lock held) is still asked to cancel."""
    from roast_my_harness.host_lock import ExperimentLock

    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    plan = json.loads((tmp_path / "plans" / f"{prepared.plan_id}.json").read_text())
    experiment_id = plan["experiment_id"]
    rd = tmp_path / "run"
    helper = _sleepy_helper()
    try:
        with ExperimentLock(rd):
            monkeypatch.setattr(
                service, "_spawn_worker", lambda *a, **k: helper.pid
            )
            service.start(prepared.plan_id)
            _seed_running_row(tmp_path, experiment_id, rd)
            result = service.cancel(experiment_id)
            assert result.cancelled is True
            assert result.state == "CANCELLING"
        helper.wait(timeout=10)
        assert helper.returncode == -signal.SIGINT
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=10)


def test_cancel_pid_dead_marker_refuses(tmp_path, green_preflight, monkeypatch):
    """Control: a dead marker pid keeps the honest refusal."""
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=10)
    monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: dead.pid)
    service.start(prepared.plan_id)
    plan = json.loads((tmp_path / "plans" / f"{prepared.plan_id}.json").read_text())
    result = service.cancel(plan["experiment_id"])
    assert result.ok
    assert result.cancelled is False
    assert "no live worker" in result.note


def test_cancel_marker_only_starting_refuses_without_lock(
    tmp_path, green_preflight, monkeypatch
):
    """Worker died before its DB row: a live pid at the marker is not ours."""
    spec_path = make_spec(tmp_path)
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    prepared = service.prepare(spec_path)
    plan = json.loads((tmp_path / "plans" / f"{prepared.plan_id}.json").read_text())
    experiment_id = plan["experiment_id"]
    helper = _sleepy_helper()
    try:
        monkeypatch.setattr(service, "_spawn_worker", lambda *a, **k: helper.pid)
        service.start(prepared.plan_id)
        monkeypatch.setenv("ROAST_MY_HARNESS_RUNS_DIR", str(tmp_path / "runs"))

        result = service.cancel(experiment_id)
        assert result.ok
        assert result.state == "STARTING"
        assert result.cancelled is False
        assert helper.poll() is None
    finally:
        helper.terminate()
        helper.wait(timeout=10)




def test_service_start_unknown_plan_raises(tmp_path):
    from roast_my_harness.agent.service import AgentService, UnknownPlanError

    service = AgentService(plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite")
    with pytest.raises(UnknownPlanError):
        service.start("plan_ffffffffffff")


def test_service_prepare_needs_input(tmp_path):
    from roast_my_harness.agent.service import AgentService

    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = true\n")
    service = AgentService(plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite")
    result = service.prepare(bad)
    assert result.state == "needs_input"
