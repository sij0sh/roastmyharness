"""Private bridge CLI: inspect/validate/run/status/cancel contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from roast_my_harness import cli as cli_mod

SPEC = """
schema_version = 3
name = "bridge"
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
    (dataset / "instruction.md").write_text("do it\n")
    path = tmp_path / "exp.toml"
    path.write_text(SPEC.format(tasks=tmp_path / "dataset"))
    return path


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def green_preflight(monkeypatch):
    import time

    from roast_my_harness.runner import preflight as pf

    monkeypatch.setattr(
        pf, "run_checks", lambda spec, *, skip_docker=False: [pf._ok("python", "stubbed")]
    )
    monkeypatch.setattr("roast_my_harness.runner.pier.pier_version", lambda: "0.3.0")
    fresh = {"access": "x", "type": "oauth", "expires": (time.time() + 3600) * 1000}
    monkeypatch.setattr("roast_my_harness.auth.service.codex_credential", lambda: fresh)


def test_bridge_inspect_reports_arms(tmp_path: Path):
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "inspect", str(make_spec(tmp_path))])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["arms"] == ["control", "bare"]
    assert payload["model"] == "openai-codex/gpt-5.6-luna"


def test_bridge_inspect_rejects_bad_spec(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = true\n")
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "inspect", str(bad)])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_spec"


def test_bridge_validate_bad_spec_needs_input(tmp_path: Path, data_dir: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = true\n")
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "validate", str(bad)])
    assert result.exit_code == 1
    assert json.loads(result.output)["state"] == "needs_input"


def test_bridge_validate_ready(tmp_path: Path, data_dir: Path, green_preflight):
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "validate", str(make_spec(tmp_path))])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state"] == "ready_for_confirmation"
    assert payload["plan_id"].startswith("plan_")


def test_bridge_status_unknown_experiment(tmp_path: Path, data_dir: Path):
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "status", "no-such-exp"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_experiment"


def test_bridge_cancel_unknown_experiment(tmp_path: Path, data_dir: Path):
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "cancel", "no-such-exp"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_experiment"


def test_bridge_run_streams_started_then_events(tmp_path: Path, data_dir: Path, monkeypatch):
    events = [
        {"event": "snapshot", "state": "RUNNING", "matrix": {}, "rewards": {}, "running": []},
        {"event": "final", "experiment_id": "e1", "state": "COMPLETE", "final": True,
         "aggregates": {}, "report": None},
    ]
    service = SimpleNamespace(
        start=lambda plan_id, skip_docker=False: SimpleNamespace(experiment_id="e1"),
        watch=lambda experiment_id, **kwargs: iter(events),
    )
    monkeypatch.setattr(cli_mod.agent_service, "AgentService", lambda: service)
    result = CliRunner().invoke(cli_mod.app, ["_bridge", "run", "plan_ffffffffffff"])
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert lines[0]["event"] == "started"
    assert lines[0]["experiment_id"] == "e1"
    assert lines[-1]["event"] == "final"
    assert lines[-1]["state"] == "COMPLETE"
