"""AgentService.watch: read-only NDJSON progress events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roast_my_harness.agent import service as svc
from roast_my_harness.report.collect import aggregate_by_variant
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.store.repository import Repository

SPEC = """
schema_version = 1
name = "watch"
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

EXPERIMENT_ID = "watch-exp"


def _make_db(tmp_path: Path) -> tuple[Path, Path]:
    """Create the DB row (valid stored spec) and run-dir skeleton."""
    dataset = tmp_path / "dataset"
    for task in ("t1", "t2"):
        (dataset / task).mkdir(parents=True)
        (dataset / task / "task.toml").write_text('schema_version = "1.3"\n')
    spec_path = tmp_path / "exp.toml"
    spec_path.write_text(SPEC.format(tasks=dataset))
    spec = json.loads(json.dumps(load_experiment(spec_path).model_dump(mode="json")))
    db_path = tmp_path / "db.sqlite"
    run_dir = tmp_path / "runs" / EXPERIMENT_ID
    (run_dir / "jobs").mkdir(parents=True)
    repo = Repository(db_path)
    repo.create_experiment(
        experiment_id=EXPERIMENT_ID,
        name="watch",
        spec=spec,
        spec_hash="hash",
        run_dir=str(run_dir),
    )
    repo.close()
    return db_path, run_dir


def _write_result(run_dir: Path, variant: str, task: str, reward: float) -> None:
    trial = run_dir / "jobs" / variant / "attempt-1" / f"{task}__1"
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": task,
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": {},
                "agent_execution": {
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": "2026-01-01T00:01:00+00:00",
                },
                "agent_result": {
                    "n_input_tokens": 1000,
                    "n_output_tokens": 500,
                    "cost_usd": 0.01,
                },
            }
        )
    )


@pytest.fixture
def environment(tmp_path: Path, monkeypatch):
    """DB, run dir, and runs-root override for one watch test."""
    db_path, run_dir = _make_db(tmp_path)
    monkeypatch.setenv("ROAST_MY_HARNESS_RUNS_DIR", str(tmp_path / "runs"))
    return db_path, run_dir


def _patch_observe_states(
    monkeypatch, service, run_dir: Path, script: list[dict[str, Any]]
) -> None:
    """Replace _observe with a scripted state/result sequence.

    Each script entry is {"state": str, "results": [(variant, task, reward)]}.
    The last entry repeats for every poll beyond the script.
    """
    original = svc.AgentService._observe
    last = [script[0]]

    def observe(self, experiment_id: str):
        step = script.pop(0) if script else last[0]
        last[0] = step
        for variant, task, reward in step["results"]:
            _write_result(run_dir, variant, task, reward)
        repo = Repository(self.db_path)
        repo.set_status(experiment_id, step["state"], started=True)
        repo.close()
        return original(self, experiment_id)

    monkeypatch.setattr(svc.AgentService, "_observe", observe)


def test_watch_emits_snapshot_then_final_on_complete(environment, monkeypatch):
    db_path, run_dir = environment
    _write_result(run_dir, "control", "t1", 1.0)
    _write_result(run_dir, "bare", "t1", 0.0)
    service = svc.AgentService(plans_dir=run_dir.parent / "plans", db_path=db_path)
    _patch_observe_states(
        monkeypatch,
        service,
        run_dir,
        [
            {"state": "RUNNING", "results": []},
            {"state": "COMPLETE", "results": []},
        ],
    )

    events = list(service.watch(EXPERIMENT_ID, interval_sec=0.01))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "snapshot"
    assert kinds[-1] == "final"
    snapshot = events[0]
    assert snapshot["matrix"]["control"]["t1"] == "P"
    assert snapshot["matrix"]["control"]["t2"] == "."
    assert snapshot["totals"]["bare"]["P"] == 0
    final = events[-1]
    assert final["state"] == "COMPLETE"
    assert final["final"] is True
    assert final["aggregates"]["control"]["n"] == 1
    assert final["aggregates"]["control"]["resolved"] == 1
    assert final["aggregates"]["control"]["cost_usd"] == pytest.approx(0.01)
    assert final["report"] is None


def test_watch_reports_trial_transitions(environment, monkeypatch):
    db_path, run_dir = environment
    service = svc.AgentService(plans_dir=run_dir.parent / "plans", db_path=db_path)
    _patch_observe_states(
        monkeypatch,
        service,
        run_dir,
        [
            {"state": "RUNNING", "results": []},
            {"state": "RUNNING", "results": [("bare", "t1", 1.0)]},
            {"state": "COMPLETE", "results": [("bare", "t2", 0.5)]},
        ],
    )

    events = list(service.watch(EXPERIMENT_ID, interval_sec=0.01))
    trials = [e for e in events if e["event"] == "trial"]
    assert [(t["variant"], t["task"], t["status"]) for t in trials] == [
        ("bare", "t1", "P"),
        ("bare", "t2", "F"),
    ]
    assert trials[0]["reward"] == 1.0
    finals = [e for e in events if e["event"] == "final"]
    assert len(finals) == 1
    assert finals[0]["aggregates"]["bare"]["n"] == 2


def test_watch_terminates_when_worker_gone(environment, monkeypatch):
    db_path, run_dir = environment
    (run_dir / ".experiment.lock").write_text("{}\n")
    service = svc.AgentService(plans_dir=run_dir.parent / "plans", db_path=db_path)
    monkeypatch.setattr(service, "_worker_pid", lambda experiment_id: None)
    events = list(
        service.watch(EXPERIMENT_ID, interval_sec=0.01, worker_grace_sec=0.0)
    )
    final = events[-1]
    assert final["event"] == "final"
    assert final["state"] == "DRAFT"
    assert final["final"] is False
    assert "resume" in final["note"]


def test_watch_unknown_experiment_raises(tmp_path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    with pytest.raises(svc.UnknownExperimentError):
        list(service.watch("nope", interval_sec=0.01))


def test_aggregate_by_variant_sums_completed_rows():
    rows = [
        {
            "variant": "a",
            "resolved": 1,
            "input_tokens": 1000,
            "output_tokens": 10,
            "wall_sec": 1.5,
            "cost_usd": 0.1,
        },
        {"variant": "a", "resolved": 0, "input_tokens": "", "wall_sec": None},
        {"variant": "b", "resolved": 1, "cost_usd": 0.25},
    ]
    agg = aggregate_by_variant(rows)
    assert agg["a"]["n"] == 2
    assert agg["a"]["resolved"] == 1
    assert agg["a"]["input_tokens"] == 1000.0
    assert agg["a"]["output_tokens"] == 10.0
    assert agg["a"]["wall_sec"] == 1.5
    assert agg["b"]["n"] == 1
    assert agg["b"]["cost_usd"] == pytest.approx(0.25)


def test_status_includes_aggregates(environment):
    db_path, run_dir = environment
    _write_result(run_dir, "control", "t1", 1.0)
    service = svc.AgentService(plans_dir=run_dir.parent / "plans", db_path=db_path)
    result = service.status(EXPERIMENT_ID)
    assert result.aggregates["control"]["n"] == 1
    assert result.aggregates["control"]["resolved"] == 1


def test_tool_watch_streams_ndjson(environment, monkeypatch):
    """The CLI watch command prints one JSON object per line."""
    from typer.testing import CliRunner

    from roast_my_harness import cli as cli_mod

    db_path, run_dir = environment
    repo = Repository(db_path)
    repo.set_status(EXPERIMENT_ID, "COMPLETE", started=True, finished=True)
    repo.close()
    (run_dir / "report.md").write_text("# report\n")
    (run_dir / "summary.csv").write_text("variant\n")
    service = svc.AgentService(plans_dir=run_dir.parent / "plans", db_path=db_path)
    monkeypatch.setattr(cli_mod.agent_service, "AgentService", lambda: service)

    runner = CliRunner()
    result = runner.invoke(cli_mod.tool_app, ["watch", EXPERIMENT_ID, "--interval", "0.01"])
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert lines[0]["event"] == "snapshot"
    assert lines[-1]["event"] == "final"
    assert lines[-1]["state"] == "COMPLETE"
    assert lines[-1]["report"]["markdown"].endswith("report.md")
