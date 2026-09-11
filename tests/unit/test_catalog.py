"""Benchmark catalog: metadata outside task dirs, presets, smoke probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.runner.probe import select_probe_task
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.resolved import resolve_run_spec
from roast_my_harness.tasks.catalog import catalog_info, load_catalog
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

CATALOG = """
catalog_version = 1
benchmark = "mini"
benchmark_revision = "2026-09"
source = "test"

[presets.quick]
label = "Quick"
tasks = ["t1", "t2"]

[presets.full]
label = "Full"
tasks = ["t1", "t2", "t3"]

[tasks.t1]
duration = "fast"
difficulty = "easy"
smoke = true
estimated_minutes = 8
basis_revision = "mini-2026-09"
basis_samples = 10
basis_date = "2026-09-01"

[tasks.t2]
duration = "long"
difficulty = "hard"
smoke = true
estimated_minutes = 35
basis_revision = "mini-2026-09"
basis_samples = 10
basis_date = "2026-09-01"
"""


class FakeTask:
    def __init__(self, task_id: str):
        self.task_id = task_id


def make_benchmark(root: Path, tasks: tuple[str, ...] = ("t1", "t2", "t3")) -> Path:
    for task_id in tasks:
        task = root / task_id
        task.mkdir(parents=True, exist_ok=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"{task_id}\n")
    (root / "catalog.toml").write_text(CATALOG)
    return root


def test_load_catalog_parses_presets_and_labels(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    catalog = load_catalog(root)
    assert catalog is not None
    assert catalog.benchmark == "mini"
    assert catalog.revision == "2026-09"
    assert catalog.presets["quick"].tasks == ("t1", "t2")
    assert catalog.tasks["t1"].duration == "fast"
    assert catalog.tasks["t1"].smoke is True
    assert catalog.tasks["t2"].difficulty == "hard"
    assert "t3" not in catalog.tasks


def test_load_catalog_absent_returns_none(tmp_path: Path):
    assert load_catalog(tmp_path) is None
    assert catalog_info(tmp_path) == (None, None)


def test_catalog_hash_stable_and_content_bound(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    first = load_catalog(root)
    second = load_catalog(root)
    assert first is not None and second is not None
    assert first.sha256 == second.sha256
    (root / "catalog.toml").write_text(
        (root / "catalog.toml").read_text().replace("2026-09", "2026-10", 1)
    )
    assert load_catalog(root).sha256 != first.sha256  # type: ignore[union-attr]


def test_catalog_rejects_labels_without_basis(tmp_path: Path):
    root = tmp_path / "bench"
    root.mkdir()
    (root / "catalog.toml").write_text(
        'catalog_version = 1\n[tasks.t1]\nduration = "fast"\n'
    )
    with pytest.raises(SpecError, match="basis_revision"):
        load_catalog(root)


def test_catalog_rejects_bad_enum_and_version(tmp_path: Path):
    root = tmp_path / "bench"
    root.mkdir()
    (root / "catalog.toml").write_text(
        'catalog_version = 1\n[tasks.t1]\nduration = "medium"\n'
        'basis_revision = "x"\n'
    )
    with pytest.raises(SpecError, match="duration"):
        load_catalog(root)
    (root / "catalog.toml").write_text("catalog_version = 99\n")
    with pytest.raises(SpecError, match="catalog_version"):
        load_catalog(root)


def test_shipped_deepswe_catalog():
    root = Path(__file__).resolve().parents[2] / "tasks" / "deepswe" / "tasks"
    catalog = load_catalog(root)
    assert catalog is not None
    assert catalog.revision == "2026-09"
    assert set(catalog.presets) == {
        "luna-signal", "luna-confirmation", "glm-signal", "glm-confirmation",
    }
    for preset in catalog.presets.values():
        assert len(preset.tasks) == 30
    from roast_my_harness.tasks.discover import is_task_dir

    for name, preset in catalog.presets.items():
        missing = [t for t in preset.tasks if not is_task_dir(root / t)]
        assert not missing, f"preset {name} lists missing tasks: {missing}"
    revision, digest = catalog_info(root)
    assert revision == "2026-09" and digest == catalog.sha256


def write_spec(tmp_path: Path, tasks_path: Path, extra: str = "") -> Path:
    spec_path = tmp_path / "exp.toml"
    spec_path.write_text(
        "schema_version = 3\nname = \"cat\"\npi_version = \"0.84.3\"\n"
        f"[tasks]\npath = {str(tasks_path)!r}\n{extra}"
        "[[variants]]\nid = \"a\"\n"
    )
    return spec_path


def test_preset_scopes_discovery(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    spec = load_experiment(write_spec(tmp_path, root, 'preset = "quick"\n'))
    assert spec.tasks.include == ["t1", "t2"]
    found = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    assert [t.task_id for t in found] == ["t1", "t2"]


def test_preset_composes_with_include_exclude(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    spec = load_experiment(
        write_spec(tmp_path, root, 'preset = "full"\ninclude = ["t1", "t2"]\n')
    )
    found = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    assert [t.task_id for t in found] == ["t1", "t2"]


def test_preset_errors(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    with pytest.raises(SpecError, match="unknown tasks.preset"):
        load_experiment(write_spec(tmp_path, root, 'preset = "nope"\n'))
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(SpecError, match="needs catalog"):
        load_experiment(write_spec(tmp_path, bare, 'preset = "quick"\n'))
    (root / "t3" / "task.toml").unlink()
    (root / "t3" / "instruction.md").unlink()
    (root / "t3").rmdir()
    with pytest.raises(SpecError, match="missing tasks"):
        load_experiment(write_spec(tmp_path, root, 'preset = "full"\n'))


def test_catalog_revision_enters_run_identity(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    spec = load_experiment(write_spec(tmp_path, root))
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    pairs = [(t.task_id, task_hash(t.path)) for t in tasks]
    revision, digest = catalog_info(root)
    assert revision == "2026-09" and digest
    first = resolve_run_spec(spec, pairs)
    assert first.catalog_revision is None
    second = resolve_run_spec(
        spec, pairs, catalog_revision=revision, catalog_hash=digest
    )
    assert second.catalog_revision == "2026-09"
    assert second.catalog_hash == digest
    assert second.run_id != first.run_id


def test_task_hash_ignores_catalog_file(tmp_path: Path):
    task = tmp_path / "t1"
    task.mkdir()
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    before = task_hash(task)
    (task / "catalog.toml").write_text('catalog_version = 1\n')
    assert task_hash(task) == before


def test_select_probe_task_prefers_fast_easy_smoke(tmp_path: Path):
    catalog = load_catalog(make_benchmark(tmp_path / "bench"))
    assert catalog is not None
    tasks = [FakeTask("t3"), FakeTask("t2"), FakeTask("t1")]
    assert select_probe_task(tasks, catalog) == "t1"


def test_select_probe_task_ignores_unknown_smoke_ids(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    (root / "catalog.toml").write_text(
        'catalog_version = 1\n[tasks.ghost]\nsmoke = true\nbasis_revision = "x"\n'
    )
    catalog = load_catalog(root)
    tasks = [FakeTask("t2"), FakeTask("t1")]
    assert select_probe_task(tasks, catalog) == "t2"
    assert select_probe_task(tasks, None) == "t2"
