"""Six-step wizard: control exclusion, context payload, historic pool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roast_my_harness import cli as cli_mod
from roast_my_harness import wizard as wizard_mod
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.store.repository import Repository

MINIMAL = """
schema_version = 3
name = "demo"
[tasks]
path = "/tmp/does-not-need-to-exist"
[[variants]]
id = "bareish"
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def make_task_root(tmp_path: Path, *ids: str) -> Path:
    root = tmp_path / "tasks"
    for task_id in ids:
        task_dir = root / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text('schema_version = "1.3"\n')
    return root


def seed_control(repo: Repository, experiment_id: str, spec: dict,
                 task_id: str, resolved: bool | None, attempt: int = 1) -> None:
    repo.create_experiment(experiment_id=experiment_id, name="seed",
                           spec=spec, spec_hash="h" * 64,
                           run_dir="/tmp/seed", status="COMPLETE")
    repo.upsert_trial(experiment_id=experiment_id, variant_id="control",
                      task_id=task_id, attempt=attempt, status="pass",
                      job_path=None, reward=1.0, resolved=resolved,
                      exception_type=None, metrics=None)


def test_control_defaults_to_included(tmp_path: Path):
    spec = load_experiment(write(tmp_path / "experiment.toml", MINIMAL))
    assert spec.control is True
    assert [v.id for v in spec.arms()] == ["control", "bareish"]


def test_control_false_excludes_bare_arm(tmp_path: Path):
    text = MINIMAL.replace('name = "demo"', 'name = "demo"\ncontrol = false')
    spec = load_experiment(write(tmp_path / "experiment.toml", text))
    assert spec.control is False
    assert [v.id for v in spec.arms()] == ["bareish"]
    assert spec.peak_concurrency() < load_experiment(
        write(tmp_path / "other.toml", MINIMAL)).peak_concurrency()


def test_prepare_reports_excluded_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from roast_my_harness.agent import service as svc
    from roast_my_harness.runner import preflight as pf

    monkeypatch.setattr(pf, "run_checks", lambda spec, *, skip_docker=False: [])
    monkeypatch.setattr("roast_my_harness.runner.pier.pier_version", lambda: "0.3.0")
    root = make_task_root(tmp_path, "t1")
    spec_path = write(tmp_path / "exp.toml", MINIMAL.replace(
        "/tmp/does-not-need-to-exist", str(root)).replace(
        'name = "demo"', 'name = "demo"\ncontrol = false'))
    result = svc.AgentService(plans_dir=tmp_path / "plans",
                              db_path=tmp_path / "db.sqlite").prepare(spec_path)
    assert result.ok is True
    assert result.experiment.control == "excluded"
    assert result.experiment.arm_ids == ["bareish"]
    assert result.experiment.trials == 1


def test_historic_pool_matches_model_and_thinking(tmp_path: Path):
    repo = Repository(tmp_path / "db.sqlite")
    try:
        match = {"model": {"provider": "p", "id": "m"}, "thinking": "high"}
        other_model = {"model": {"provider": "p", "id": "other"}, "thinking": "high"}
        other_thinking = {"model": {"provider": "p", "id": "m"}, "thinking": "low"}
        seed_control(repo, "e1", match, "t1", True, attempt=1)
        seed_control(repo, "e1", match, "t1", False, attempt=2)
        seed_control(repo, "e2", match, "t2", None)
        seed_control(repo, "e3", other_model, "t1", True)
        seed_control(repo, "e4", other_thinking, "t1", True)
        assert repo.historic_control_runs("p/m", "high") == {"t1": 2}
    finally:
        repo.close()


def test_wizard_context_reports_tasks_suites_and_history(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "data"))
    root = make_task_root(tmp_path, "t1", "t2")
    (tmp_path / "tasks" / "suites.json").write_text(json.dumps({
        "suites": {"luna": {"signal": ["t1", "missing"],
                             "confirmation": ["t2"]},
                   "glm": {"signal": ["t2"], "confirmation": []}},
    }))
    repo = Repository(tmp_path / "data" / "roastmyharness.db")
    try:
        seed_control(repo, "e1", {"model": "p/m", "thinking": "high"}, "t1", True)
    finally:
        repo.close()
    result = wizard_mod.wizard_context(root, "p/m", "high")
    assert result["ok"] is True
    assert result["discovered"] == ["t1", "t2"]
    assert result["suites"]["luna_signal"] == ["t1"]
    assert result["suites"]["luna_confirmation"] == ["t2"]
    assert result["suites"]["glm_signal"] == ["t2"]
    assert result["historic"] == {"count": 1, "task_ids": ["t1"], "runs": {"t1": 1}}


def test_wizard_context_without_suites_or_database(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "empty"))
    root = make_task_root(tmp_path, "t1")
    result = wizard_mod.wizard_context(root, "p/m", "high")
    assert result["ok"] is True
    assert result["discovered"] == ["t1"]
    assert result["suites"] == {"luna_signal": [], "luna_confirmation": [],
                                "glm_signal": [], "glm_confirmation": []}
    assert result["historic"]["count"] == 0


def test_wizard_context_rejects_empty_root(tmp_path: Path):
    root = tmp_path / "vacant"
    root.mkdir()
    result = wizard_mod.wizard_context(root, "p/m", "high")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_tasks"


def test_bridge_wizard_context_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "data"))
    root = make_task_root(tmp_path, "t1")
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "wizard-context", str(root),
                                               "--model", "p/m", "--thinking", "high"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["discovered"] == ["t1"]
    assert payload["historic"]["count"] == 0


def test_bridge_wizard_context_rejects_bad_root(tmp_path: Path):
    from typer.testing import CliRunner

    from roast_my_harness import cli as cli_mod
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "wizard-context",
                                               str(tmp_path / "missing"),
                                               "--model", "p/m", "--thinking", "high"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_bridge_await_reaches_final_on_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(data))
    tasks = tmp_path / "tasks" / "t1"
    tasks.mkdir(parents=True)
    (tasks / "task.toml").write_text('schema_version = "1.3"\n')
    run_dir = tmp_path / "runs" / "await-exp"
    (run_dir / "jobs").mkdir(parents=True)
    repo = Repository(data / "roastmyharness.db")
    try:
        repo.create_experiment(experiment_id="await-exp", name="w",
                               spec={"schema_version": 3, "name": "w",
                                     "model": {"provider": "p", "id": "m"},
                                     "thinking": "high", "pi_version": "latest",
                                     "tasks": {"path": str(tmp_path / "tasks"),
                                               "include": ["*"], "exclude": []},
                                     "variants": [{"id": "bare"}]},
                               spec_hash="h" * 64, run_dir=str(run_dir), status="COMPLETE")
    finally:
        repo.close()
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "await", "await-exp",
                                               "--interval-sec", "0.01", "--grace-sec", "0"])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert [e["event"] for e in events] == ["snapshot", "final"]
    assert events[-1]["final"] is True
    assert events[-1]["state"] == "COMPLETE"


def test_bridge_await_rejects_unknown_experiment():
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "await", "missing-exp"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_bundled_suites_match_catalog_presets():
    from roast_my_harness.setup import repo_root
    base = repo_root()
    if base is None:
        pytest.skip("no repo checkout on disk")
    import tomllib
    suites = json.loads((base / "tasks" / "deepswe" / "suites.json").read_text())["suites"]
    catalog = tomllib.loads((base / "tasks" / "deepswe" / "tasks" / "catalog.toml").read_text())
    for family, preset_part in (("luna", "luna"), ("glm", "glm")):
        for part in ("signal", "confirmation"):
            assert sorted(suites[family][part]) == sorted(
                catalog["presets"][f"{preset_part}-{part}"]["tasks"])
