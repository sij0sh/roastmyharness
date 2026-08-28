"""Agent orchestration service: prepare/start/status/cancel/report contract."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from roast_my_harness.agent import service as svc
from roast_my_harness.spec.load import load_experiment

SPEC = """
schema_version = 1
name = "svc"
pi_version = "0.84.3"

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[tasks]
path = "{tasks}"

[control]
enabled = true

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




def test_tool_start_unknown_plan_prints_json_error(tmp_path, capsys):
    from typer.testing import CliRunner

    from roast_my_harness.cli import tool_app

    runner = CliRunner()
    result = runner.invoke(
        tool_app, ["start", "plan_ffffffffffff"], env={"HOME": str(tmp_path)}
    )
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unknown_plan"
    assert result.exit_code == 1


def test_tool_prepare_needs_input_exits_nonzero(tmp_path, capsys):
    from typer.testing import CliRunner

    from roast_my_harness.cli import tool_app

    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = true\n")
    runner = CliRunner()
    result = runner.invoke(tool_app, ["prepare", str(bad)])
    payload = json.loads(result.output)
    assert payload["state"] == "needs_input"
    assert result.exit_code == 1
