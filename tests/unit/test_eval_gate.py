"""Eval descriptor and fixture self-test gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.descriptor import EvalDescriptor, load_descriptor
from roast_my_harness.evals.selftest import evaluate_fixtures, run_selftests
from roast_my_harness.runner.preflight import _eval
from roast_my_harness.spec.models import EvalSpec, ExperimentSpec, TaskSelection, VariantSpec


def make_dataset(root: Path) -> Path:
    dataset = root / "dataset"
    task = dataset / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    (task / "instruction.md").write_text("do it\n")
    return dataset


def make_spec(tasks_path: Path, **kwargs) -> ExperimentSpec:
    return ExperimentSpec(
        name="gate",
        tasks=TaskSelection(path=tasks_path),
        control=None,
        variants=[VariantSpec(id="a")],
        pi_version="0.84.3",
        **kwargs,
    )


def write_descriptor(
    task_root: Path,
    *,
    eval_id: str = "my-eval",
    revision: str = "2026-09-01",
    threshold: float = 0.7,
    judge: str = "",
) -> Path:
    path = task_root / "eval.toml"
    path.write_text(
        f'id = "{eval_id}"\nrevision = "{revision}"\n'
        f"[scoring]\npass_threshold = {threshold}\n{judge}"
    )
    return path


def write_fixtures(task_root: Path, fixtures: list) -> Path:
    validation = task_root / "validation"
    validation.mkdir(parents=True, exist_ok=True)
    path = validation / "self-test.json"
    path.write_text(json.dumps({"fixtures": fixtures}))
    return path


def passing_pair() -> list:
    return [
        {"name": "good", "task": "t1",
         "rewards": {"reward": 1.0, "reward_deterministic": 1.0},
         "expect_resolved": True},
        {"name": "bad", "task": "t1",
         "rewards": {"reward": 0.0, "reward_deterministic": 0.0},
         "expect_resolved": False},
    ]


def test_descriptor_parses_and_hashes(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    path = write_descriptor(dataset)
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    assert isinstance(descriptor, EvalDescriptor)
    assert (descriptor.id, descriptor.revision, descriptor.pass_threshold) == (
        "my-eval",
        "2026-09-01",
        0.7,
    )
    assert not descriptor.judge_enabled
    assert len(descriptor.sha256) == 64
    assert load_descriptor(tmp_path / "nowhere") is None
    assert path.exists()


def test_descriptor_rejects_bad_contracts(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    path = dataset / "eval.toml"
    path.write_text('id = "x"\n[scoring]\npass_threshold = 0.0\n')
    with pytest.raises(SpecError, match="pass_threshold"):
        load_descriptor(dataset)
    path.write_text('id = "x"\n[scoring]\npass_threshold = 0.5\n[bogus]\n')
    with pytest.raises(SpecError, match="unknown fields"):
        load_descriptor(dataset)
    path.write_text(
        'id = "x"\n[scoring]\npass_threshold = 0.5\n'
        "[judge]\nenabled = true\n"
    )
    with pytest.raises(SpecError, match="judge.*model"):
        load_descriptor(dataset)
    path.write_text(
        'id = "x"\n[scoring]\npass_threshold = 0.5\n'
        '[judge]\nmodel = "m"\n'
    )
    with pytest.raises(SpecError, match="enabled = true"):
        load_descriptor(dataset)


def test_selftest_green_and_red(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_descriptor(dataset)
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    write_fixtures(dataset, passing_pair())
    result = run_selftests(dataset, descriptor)
    assert result.passed and result.evaluated == 2


def test_selftest_missing_file_raises(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_descriptor(dataset)
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    with pytest.raises(SpecError, match="self-test.*not found"):
        run_selftests(dataset, descriptor)


def test_selftest_enforces_threshold_and_discrimination(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_descriptor(dataset, threshold=0.7)
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    write_fixtures(
        dataset,
        [
            {"name": "wrong", "task": "t1", "rewards": {"reward": 0.5},
             "expect_resolved": True},
            {"name": "ok", "task": "t1", "rewards": {"reward": 0.0},
             "expect_resolved": False},
        ],
    )
    result = run_selftests(dataset, descriptor)
    assert not result.passed
    assert "resolves to False" in result.failures[0].message
    # Vacuous all-pass set refused even when each fixture is consistent.
    write_fixtures(
        dataset,
        [
            {"name": "a", "task": "t1", "rewards": {"reward": 1.0},
             "expect_resolved": True},
            {"name": "b", "task": "t1", "rewards": {"reward": 0.9},
             "expect_resolved": True},
        ],
    )
    result = run_selftests(dataset, descriptor)
    assert not result.passed
    assert "discriminate" in result.failures[0].message


def test_selftest_judge_rules(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_descriptor(dataset)  # judge disabled
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    write_fixtures(
        dataset,
        passing_pair()
        + [{"name": "j", "task": "t1",
            "rewards": {"reward": 1.0, "reward_judge": 0.8, "judge_model": "m"},
            "expect_resolved": True}],
    )
    result = run_selftests(dataset, descriptor)
    assert not result.passed
    assert "enabled" in result.failures[0].message
    # Enabled judge requires judge_model provenance on judge scores.
    write_descriptor(
        dataset, judge='[judge]\nenabled = true\nmodel = "m"\nrubric = "r-v1"\n'
    )
    descriptor = load_descriptor(dataset)
    assert descriptor is not None and descriptor.judge_enabled
    write_fixtures(
        dataset,
        passing_pair()
        + [{"name": "j", "task": "t1",
            "rewards": {"reward": 1.0, "reward_judge": 0.8},
            "expect_resolved": True}],
    )
    result = run_selftests(dataset, descriptor)
    assert not result.passed
    assert "judge_model" in result.failures[0].message


def test_selftest_rejects_unknown_task(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_descriptor(dataset)
    descriptor = load_descriptor(dataset)
    assert descriptor is not None
    write_fixtures(
        dataset,
        [{"name": "x", "task": "nope", "rewards": {"reward": 1.0},
          "expect_resolved": True}],
    )
    result = run_selftests(dataset, descriptor)
    assert not result.passed
    assert "not a task directory" in result.failures[0].message


def test_preflight_gate(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    legacy = make_spec(dataset)
    assert _eval(legacy).status == "pass"
    # Generated selection without a descriptor fails the gate.
    missing = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    assert _eval(missing).status == "fail"
    # Descriptor without fixtures fails the gate.
    write_descriptor(dataset)
    selected = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    assert _eval(selected).status == "fail"
    write_fixtures(dataset, passing_pair())
    passed = _eval(selected)
    assert passed.status == "pass"
    assert "2 fixtures" in passed.detail


def test_evaluate_fixtures_direct_shape():
    descriptor = EvalDescriptor(
        id="x", revision=None, title="", description="", pass_threshold=0.5,
        judge_enabled=False, judge_model=None, judge_rubric=None,
        judge_samples=1, sha256="",
    )
    result = evaluate_fixtures(descriptor, [], Path("."))
    assert not result.passed
