"""Shipped example eval: contract, tasks, and spec stay valid together."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from roast_my_harness.evals.descriptor import load_descriptor
from roast_my_harness.evals.selftest import run_selftests
from roast_my_harness.runner.preflight import _eval
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.tasks.discover import discover_tasks

ROOT = Path(__file__).resolve().parents[2] / "examples" / "evals" / "structured-output"
TASKS = ROOT / "tasks"
EXPECTED_TASKS = ("edit-preserve", "headed-notes", "json-record")


def test_example_contract_selftest_green():
    descriptor = load_descriptor(TASKS)
    assert descriptor is not None
    assert descriptor.id == "structured-output"
    assert not descriptor.judge_enabled
    result = run_selftests(TASKS, descriptor)
    assert result.passed, result.failures
    assert result.evaluated == 6


def test_example_tasks_discover_and_parse():
    tasks = discover_tasks(TASKS, ["*"], [])
    assert sorted(t.task_id for t in tasks) == sorted(EXPECTED_TASKS)
    from pier.models.task.task import Task

    for task in tasks:
        loaded = Task(task.path)
        assert loaded.task_dir == task.path


def test_example_verifiers_grade_good_and_bad(tmp_path: Path):
    cases = {
        "json-record": (
            "record.json",
            '{"name": "atlas", "version": "2.4.1", '
            '"tags": ["red", "green", "blue"], "enabled": true}',
            '{"name": "atlas"}',
        ),
        "edit-preserve": (
            "notes.txt",
            "# Project notes\n\nowner: ada\nstatus: final\nbudget: 1200\n\n"
            "## Reminders\n- water the plants\n- rotate the logs\n",
            "status: final\n",
        ),
        "headed-notes": (
            "summary.md",
            "# Summary\nDone.\n## Findings\nTwo.\n## Next steps\nShip.\n",
            "no headings here\n",
        ),
    }
    for task_id, (filename, good, bad) in cases.items():
        for label, content, want in (("good", good, 1.0), ("bad", bad, 0.0)):
            app = tmp_path / f"{task_id}-{label}-app"
            logs = tmp_path / f"{task_id}-{label}-logs"
            app.mkdir(parents=True)
            logs.mkdir(parents=True)
            (app / filename).write_text(content)
            env = {"APP_DIR": str(app), "LOGS_DIR": str(logs), "PATH": "/usr/bin:/bin"}
            proc = subprocess.run(
                ["bash", str(TASKS / task_id / "tests" / "test.sh")],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert proc.returncode == 0, proc.stderr
            rewards = json.loads((logs / "reward.json").read_text())
            assert rewards["reward"] == want, (task_id, label, rewards)
            if want == 1.0:
                assert rewards["reward_deterministic"] == 1.0
            else:
                assert rewards["reward_deterministic"] < 0.7


def test_example_spec_loads_and_gates_green():
    spec = load_experiment(ROOT.parents[1] / "structured-output-eval.toml")
    assert spec.evaluation is not None
    assert spec.evaluation.id == "structured-output"
    assert spec.evaluation.type == "generated"
    assert [t.task_id for t in discover_tasks(
        spec.tasks.path, spec.tasks.include, spec.tasks.exclude
    )] == sorted(EXPECTED_TASKS)
    assert _eval(spec).status == "pass"
