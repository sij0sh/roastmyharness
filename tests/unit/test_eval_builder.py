"""EvalBuilder host validation and sandbox confinement."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.builder import (
    safe_join,
    validate_workspace,
    write_text_sandboxed,
)
from roast_my_harness.evals.scaffold import init_eval

EXAMPLE_TASKS = (
    Path(__file__).resolve().parents[2] / "examples" / "evals" / "structured-output" / "tasks"
)


def test_scaffold_layout_matches_example_discovery(tmp_path: Path):
    root = tmp_path / "my-eval"
    init_eval(root, eval_id="my-eval")
    assert (root / "eval.toml").is_file()
    assert not (root / "tasks").exists()
    report = validate_workspace(root)
    assert not report.ok  # untouched scaffold: TODOs, no tasks, empty gate


def test_example_workspace_validates_green():
    report = validate_workspace(EXAMPLE_TASKS)
    assert report.ok, report.errors
    assert report.eval_id == "structured-output"
    assert report.task_count == 3
    assert report.fixture_count == 6


def test_critic_must_pass_before_freeze(tmp_path: Path):
    root = tmp_path / "eval"
    shutil.copytree(EXAMPLE_TASKS, root)
    critic = root / "validation" / "critic.json"
    raw = json.loads(critic.read_text())
    raw["verdict"] = "todo"
    critic.write_text(json.dumps(raw))
    report = validate_workspace(root)
    assert not report.ok
    assert any("critic" in e for e in report.errors)


def test_capability_task_refs_must_exist(tmp_path: Path):
    root = tmp_path / "eval"
    shutil.copytree(EXAMPLE_TASKS, root)
    cap = root / "capability-map.json"
    raw = json.loads(cap.read_text())
    raw["capabilities"][0]["tasks"] = ["no-such-task"]
    cap.write_text(json.dumps(raw))
    report = validate_workspace(root)
    assert not report.ok
    assert any("unknown task" in e for e in report.errors)


def test_rationale_todo_blocks_freeze(tmp_path: Path):
    root = tmp_path / "eval"
    shutil.copytree(EXAMPLE_TASKS, root)
    (root / "rationale.md").write_text("# Rationale\n\nTODO: fill me\n")
    report = validate_workspace(root)
    assert not report.ok
    assert any("TODO" in e for e in report.errors)


def test_safe_join_refuses_escape(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    assert safe_join(root, "tasks/a/task.toml") == (root / "tasks/a/task.toml").resolve()
    with pytest.raises(SpecError, match="escapes"):
        safe_join(root, "../outside.txt")
    with pytest.raises(SpecError, match="escapes"):
        safe_join(root, "/absolute/path.txt")


def test_sandboxed_write_stays_inside(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    path = write_text_sandboxed(root, "tasks/a/note.md", "hello")
    assert path.read_text() == "hello"
    with pytest.raises(SpecError, match="escapes"):
        write_text_sandboxed(root, "../../evil.txt", "x")
    assert not (tmp_path / "evil.txt").exists()
