"""Historic controls: availability API and baseline reporting."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.agent import service as svc
from roast_my_harness.report.markdown import generate_report

SPEC = """\
schema_version = 2
name = "hist"
pi_version = "0.84.3"
[tasks]
path = "./dataset"
[control]
enabled = true
mode = "historic"
minimum_runs_per_task = 2
sentinel_tasks = 1
[[variants]]
id = "a"
"""


def setup(tmp_path: Path, spec_text: str = SPEC) -> Path:
    for task_id in ("t1", "t2"):
        task = tmp_path / "dataset" / task_id
        task.mkdir(parents=True, exist_ok=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"{task_id}\n")
    spec_path = tmp_path / "exp.toml"
    spec_path.write_text(spec_text)
    return spec_path


def test_availability_without_control_arm(tmp_path: Path):
    spec_path = setup(tmp_path, SPEC.replace("enabled = true", "enabled = false"))
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    assert service.historic_availability(spec_path) == {
        "available": False,
        "reason": "no control arm",
    }


def test_availability_empty_history_is_unavailable(tmp_path: Path):
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.historic_availability(setup(tmp_path))
    assert result["available"] is True
    assert result["status"] == "unavailable"
    assert result["eligible"] == 0
    assert result["total"] == 2
    assert result["mode"] == "historic"
    assert result["agent"] == "pi"
    assert result["agent_version"] == "0.84.3"


def row(variant: str, task: str, resolved: int, replicate: int = 1) -> dict:
    return {
        "variant": variant,
        "task": task,
        "resolved": resolved,
        "replicate": replicate,
        "wall_time_sec": 60.0,
        "agent_time_sec": 50.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "context_tokens": 0,
        "tool_calls": 3,
        "tool_results": 3,
        "cost_usd": 0.01,
        "wall_sec": 60.0,
    }


def test_baseline_table_renders_historic_vs_extension(tmp_path: Path):
    rows = [
        row("control", "t1", 1),
        row("a", "t1", 1, replicate=1),
        row("a", "t1", 0, replicate=2),
        row("a", "t2", 1),
    ]
    provenance = {
        "experiment_id": "e",
        "control_reuse": {
            "enabled": True,
            "mode": "historic",
            "status": "accepted",
            "accepted": True,
            "reused_tasks": ["t1"],
            "reused_counts": {"t1": 8},
            "reused_date_ranges": {"t1": ["2026-08-01", "2026-08-10"]},
            "baseline": {"t1": {"pass": 6, "total": 8, "rate": 0.75}},
            "fresh_control_tasks": [],
            "out_of_scope_tasks": [],
            "sentinel": {
                "matches": 1,
                "total": 1,
                "p_value": 1.0,
                "reject": False,
                "informative": False,
            },
        },
        "reused_control_observations": 8,
    }
    report = generate_report(
        tmp_path, experiment_id="e", rows=rows, provenance=provenance
    )
    text = report.read_text()
    assert "- Historic control (historic, accepted): 8 observations reused across 1 tasks." in text
    assert "| t1 | 6/8 = 75.0% | a | 1/2 = 50.0% | -25.0pp |" in text
    assert "not paired evidence" in text
