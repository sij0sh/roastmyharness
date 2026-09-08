"""Eval abstraction: identity, registry, and legacy compatibility."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.registry import cohort_eval_id, resolve_eval
from roast_my_harness.spec.hashes import (
    control_cohort_key,
    is_default_evaluation_dump,
    spec_hash,
)
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import EvalSpec, ExperimentSpec, TaskSelection, VariantSpec
from roast_my_harness.spec.resolved import ResolvedRunSpec, identity_payload, resolve_run_spec
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

PINNED = "0.84.3"


def make_dataset(root: Path) -> Path:
    dataset = root / "dataset"
    task = dataset / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    (task / "instruction.md").write_text("do it\n")
    return dataset


def make_spec(tasks_path: Path, **kwargs) -> ExperimentSpec:
    return ExperimentSpec(
        name="evals",
        tasks=TaskSelection(path=tasks_path),
        control=None,
        variants=[VariantSpec(id="a")],
        pi_version=PINNED,
        **kwargs,
    )


def pairs_of(spec: ExperimentSpec) -> list[tuple[str, str]]:
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    return [(t.task_id, task_hash(t.path)) for t in tasks]


def test_legacy_spec_keeps_identity_shape(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    assert spec.evaluation is None
    payload = identity_payload(resolve_run_spec(spec, pairs_of(spec)))
    assert "eval_type" not in payload
    assert "eval_id" not in payload
    assert "evaluation" not in payload["requested_spec"]
    first = resolve_run_spec(spec, pairs_of(spec)).run_id
    second = resolve_run_spec(spec, pairs_of(spec)).run_id
    assert first == second


def test_explicit_default_evaluation_aliases_legacy(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    legacy = make_spec(dataset)
    explicit = make_spec(dataset, evaluation=EvalSpec())
    assert spec_hash(legacy) == spec_hash(explicit)
    assert (
        resolve_run_spec(legacy, pairs_of(legacy)).run_id
        == resolve_run_spec(explicit, pairs_of(explicit)).run_id
    )
    assert is_default_evaluation_dump({"type": "bundled", "id": None, "revision": None})
    assert not is_default_evaluation_dump({"type": "external", "id": "x"})


def test_unknown_bundled_eval_rejected(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    with pytest.raises(ValueError, match="unknown bundled eval"):
        make_spec(dataset, evaluation=EvalSpec(id="nope"))


def test_non_bundled_eval_requires_id(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    with pytest.raises(ValueError, match="evaluation.id is required"):
        make_spec(dataset, evaluation=EvalSpec(type="external"))


def test_unknown_bundled_eval_rejected_at_load(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(
        "schema_version = 2\n"
        'name = "bad-eval"\n'
        f'pi_version = "{PINNED}"\n'
        "[tasks]\n"
        f'path = "{dataset}"\n'
        "[evaluation]\n"
        'id = "nope"\n'
        "[[variants]]\n"
        'id = "a"\n'
    )
    with pytest.raises(SpecError, match="unknown bundled eval"):
        load_experiment(spec_path)


def write_eval_toml(
    task_root: Path, eval_id: str, revision: str, *, threshold: float = 0.7
) -> Path:
    path = task_root / "eval.toml"
    path.write_text(
        f'id = "{eval_id}"\nrevision = "{revision}"\n'
        f"[scoring]\npass_threshold = {threshold}\n"
    )
    return path


def test_generated_eval_freezes_descriptor(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    eval_path = write_eval_toml(dataset, "my-eval", "2026-09-01")
    spec = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    frozen = resolve_eval(spec, dataset)
    assert frozen is not None
    assert (frozen.type, frozen.id, frozen.revision) == (
        "generated",
        "my-eval",
        "2026-09-01",
    )
    assert frozen.eval_hash == hashlib.sha256(eval_path.read_bytes()).hexdigest()
    resolved = resolve_run_spec(spec, pairs_of(spec), eval=frozen)
    assert resolved.eval_id == "my-eval"
    assert resolved.eval_hash == frozen.eval_hash
    assert (
        resolve_run_spec(spec, pairs_of(spec), eval=frozen).run_id == resolved.run_id
    )


def test_generated_eval_descriptor_change_forks_run_id(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_eval_toml(dataset, "my-eval", "2026-09-01")
    spec = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    first = resolve_run_spec(spec, pairs_of(spec), eval=resolve_eval(spec, dataset))
    write_eval_toml(dataset, "my-eval", "2026-09-02")
    second = resolve_run_spec(spec, pairs_of(spec), eval=resolve_eval(spec, dataset))
    assert first.run_id != second.run_id


def test_generated_eval_requires_descriptor(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    with pytest.raises(SpecError, match="needs an eval descriptor"):
        resolve_eval(spec, dataset)


def test_generated_eval_rejects_id_mismatch(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_eval_toml(dataset, "other-eval", "2026-09-01")
    spec = make_spec(dataset, evaluation=EvalSpec(type="generated", id="my-eval"))
    with pytest.raises(SpecError, match="describes id"):
        resolve_eval(spec, dataset)


def test_generated_eval_rejects_revision_conflict(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    write_eval_toml(dataset, "my-eval", "2026-09-01")
    spec = make_spec(
        dataset,
        evaluation=EvalSpec(type="generated", id="my-eval", revision="2026-09-02"),
    )
    with pytest.raises(SpecError, match="conflicts with"):
        resolve_eval(spec, dataset)


def test_external_eval_without_descriptor(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset, evaluation=EvalSpec(type="external", id="mine"))
    frozen = resolve_eval(spec, dataset)
    assert frozen is not None
    assert frozen.eval_hash is None
    legacy = make_spec(dataset)
    assert (
        resolve_run_spec(spec, pairs_of(spec), eval=frozen).run_id
        != resolve_run_spec(legacy, pairs_of(legacy)).run_id
    )


def test_cohort_key_legacy_stable_and_eval_scoped(tmp_path: Path):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    assert cohort_eval_id(spec) is None
    default = make_spec(dataset, evaluation=EvalSpec())
    assert cohort_eval_id(default) is None
    key_legacy = control_cohort_key(
        "ch", spec.model, "high", "th", agent="pi", agent_version=PINNED
    )
    key_default = control_cohort_key(
        "ch", spec.model, "high", "th", agent="pi", agent_version=PINNED, eval_id=None
    )
    assert key_legacy == key_default
    custom = make_spec(dataset, evaluation=EvalSpec(type="external", id="mine"))
    scoped = cohort_eval_id(custom)
    assert scoped == "external:mine:None"
    key_scoped = control_cohort_key(
        "ch", spec.model, "high", "th", agent="pi", agent_version=PINNED, eval_id=scoped
    )
    assert key_scoped != key_legacy


def test_legacy_resolved_envelope_still_loads():
    old = {
        "resolved_schema_version": 1,
        "experiment_name": "old",
        "requested_spec": {"name": "old"},
        "requested_agent_versions": {"pi": "latest"},
        "resolved_agent_versions": {"pi": PINNED},
        "tasks": [["t1", "abc"]],
        "repetitions": 1,
        "run_id": "old-12345678",
    }
    resolved = ResolvedRunSpec.model_validate(old)
    assert resolved.eval_id is None
    payload = identity_payload(resolved)
    assert "eval_type" not in payload
