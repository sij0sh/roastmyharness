"""Frozen run identity: latest resolves once at prepare, never after."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.agent.service import plan_bindings
from roast_my_harness.errors import PierError
from roast_my_harness.runner.controller import ExperimentController
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec
from roast_my_harness.spec.resolved import resolve_run_spec
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

PINNED = "0.84.3"


def make_spec(tasks_path: Path, *, pi_version: str = "latest") -> ExperimentSpec:
    return ExperimentSpec(
        name="freeze",
        tasks=TaskSelection(path=tasks_path),
        variants=[VariantSpec(id="a")],
        pi_version=pi_version,
    )


def make_dataset(root: Path) -> Path:
    dataset = root / "dataset"
    task = dataset / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    (task / "instruction.md").write_text("do it\n")
    return dataset


def pairs_of(spec: ExperimentSpec) -> list[tuple[str, str]]:
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    return [(t.task_id, task_hash(t.path)) for t in tasks]


def freeze(monkeypatch, version: str):
    from roast_my_harness.spec import models as spec_models

    monkeypatch.setattr(
        spec_models, "resolve_package_version", lambda package, pin: version
    )


def test_moved_latest_yields_new_run_id(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    pairs = pairs_of(spec)
    freeze(monkeypatch, "0.99.1")
    first = resolve_run_spec(spec, pairs)
    assert first.requested_pi_versions["pi"] == "latest"
    assert first.resolved_pi_versions["pi"] == "0.99.1"
    freeze(monkeypatch, "0.99.2")
    second = resolve_run_spec(spec, pairs)
    assert second.run_id != first.run_id


def test_identical_resolution_reproduces_run_id(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    pairs = pairs_of(spec)
    freeze(monkeypatch, "0.99.1")
    assert resolve_run_spec(spec, pairs).run_id == resolve_run_spec(spec, pairs).run_id


def test_exact_pin_needs_no_registry(tmp_path: Path, monkeypatch):
    from roast_my_harness.spec import models as spec_models

    def boom(package, pin):
        raise AssertionError("must not touch the network for exact pins")

    monkeypatch.setattr(spec_models, "resolve_package_version", boom)
    spec = make_spec(make_dataset(tmp_path), pi_version=PINNED)
    resolved = resolve_run_spec(spec, pairs_of(spec))
    assert resolved.resolved_pi_versions["pi"] == PINNED
    assert resolved.requested_pi_versions["pi"] == PINNED


def _controller(tmp_path: Path, spec: ExperimentSpec, run_id: str) -> ExperimentController:
    import os

    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    repo = Repository(tmp_path / "db.sqlite")
    return ExperimentController(spec, run_id, tmp_path / "run", repo, None)


def test_prepare_persists_frozen_identity(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    freeze(monkeypatch, "0.99.1")
    resolved = resolve_run_spec(spec, pairs_of(spec))
    controller = _controller(tmp_path, spec, resolved.run_id)
    controller.prepare(resolved=resolved)
    assert controller.state == "READY"
    assert controller.resolved_pi_version() == "0.99.1"
    persisted = json.loads((tmp_path / "run" / "resolved.json").read_text())
    assert persisted["resolved_pi_versions"]["pi"] == "0.99.1"
    assert persisted["requested_pi_versions"]["pi"] == "latest"
    assert persisted["run_id"] == resolved.run_id
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["pi_version"] == "0.99.1"
    assert manifest["requested_pi_version"] == "latest"
    assert manifest["resolved_pi_versions"]["pi"] == "0.99.1"
    controller.store.close()


def test_resume_uses_frozen_versions_without_registry(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    freeze(monkeypatch, "0.99.1")
    resolved = resolve_run_spec(spec, pairs_of(spec))
    controller = _controller(tmp_path, spec, resolved.run_id)
    controller.prepare(resolved=resolved)
    controller.store.close()

    from roast_my_harness.spec import models as spec_models

    def boom(package, pin):
        raise AssertionError("resume must not re-resolve versions")

    monkeypatch.setattr(spec_models, "resolve_package_version", boom)
    resumed = _controller(tmp_path, spec, resolved.run_id)
    resumed.prepare()
    assert resumed.state == "READY"
    assert resumed.resolved_pi_version() == "0.99.1"
    resumed.store.close()


def test_prepare_rejects_mismatched_run_id(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec = make_spec(dataset)
    freeze(monkeypatch, "0.99.1")
    resolved = resolve_run_spec(spec, pairs_of(spec))
    controller = _controller(tmp_path, spec, "freeze-deadbeef")
    with pytest.raises(PierError, match="does not match experiment"):
        controller.prepare(resolved=resolved)
    controller.store.close()


def test_plan_bindings_freeze_versions(tmp_path: Path, monkeypatch):
    dataset = make_dataset(tmp_path)
    spec_path = tmp_path / "exp.toml"
    spec_path.write_text(
        'schema_version = 3\nname = "freeze"\npi_version = "latest"\n'
        f"[tasks]\npath = {str(dataset)!r}\n[[variants]]\nid = \"a\"\n"
    )
    spec = load_experiment(spec_path)
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    freeze(monkeypatch, "0.99.1")
    first = plan_bindings(spec, tasks)
    freeze(monkeypatch, "0.99.2")
    second = plan_bindings(spec, tasks)
    assert first["resolved"]["resolved_pi_versions"]["pi"] == "0.99.1"
    assert first["versions"]["resolved_pi_version"] == "0.99.1"
    assert second["resolved"]["run_id"] != first["resolved"]["run_id"]
    assert second != first
