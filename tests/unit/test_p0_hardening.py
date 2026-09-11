"""P0 hardening: strict specs, exits, identity, paths, symlinks, env, flags."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from roast_my_harness.errors import HomeBuildError, PierError, SpecError
from roast_my_harness.homes.builder import build_home
from roast_my_harness.homes.sources import copy_source_tree, source_tree_hash
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import (
    ExperimentSpec,
    LocalExtension,
    SkillSpec,
    TaskSelection,
    VariantSpec,
)
from roast_my_harness.spec.resolved import resolve_run_spec


def base_spec(tmp_path: Path, **variant_kwargs) -> ExperimentSpec:
    return ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=tmp_path),
        variants=[VariantSpec(id="a", **variant_kwargs)],
        pi_version="0.84.3",
    )





def test_unknown_top_level_field_rejected(tmp_path: Path):
    path = tmp_path / "exp.toml"
    path.write_text(
        'schema_version = 3\nname = "x"\nunknown_field = 1\n'
        "[tasks]\npath = '/tmp'\n[[variants]]\nid = 'a'\n"
    )
    with pytest.raises(SpecError, match="extra_forbidden|unknown_field"):
        load_experiment(path)


def test_unknown_concurrency_field_rejected(tmp_path: Path):
    path = tmp_path / "exp.toml"
    path.write_text(
        'schema_version = 3\nname = "x"\n[tasks]\npath = "/tmp"\n'
        '[concurrency]\nper_variant = 2\nglobal_max = 6\n[[variants]]\nid = "a"\n'
    )
    with pytest.raises(SpecError, match="extra_forbidden|global_max"):
        load_experiment(path)


def test_unknown_model_field_rejected(tmp_path: Path):
    path = tmp_path / "exp.toml"
    path.write_text(
        'schema_version = 3\nname = "x"\n[tasks]\npath = "/tmp"\n'
        '[model]\nprovider = "openai-codex"\nauth = "codex"\n[[variants]]\nid = "a"\n'
    )
    with pytest.raises(SpecError, match="extra_forbidden|auth"):
        load_experiment(path)


def test_stale_example_config_validates(tmp_path: Path):
    path = tmp_path / "my-comparison.toml"
    path.write_text(
        'schema_version = 3\nname = "my-comparison"\n'
        '[tasks]\npath = "/tmp"\n[[variants]]\nid = "a"\n'
    )
    spec = load_experiment(path)
    assert spec.name == "my-comparison"








def _task(tmp_path: Path, task_id: str, body: str = "do it\n") -> Path:
    task = tmp_path / task_id
    task.mkdir(parents=True, exist_ok=True)
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    (task / "instruction.md").write_text(body)
    return task


def test_resolved_identity_binds_task_content(tmp_path: Path):
    _task(tmp_path, "t1", "one")
    spec = base_spec(tmp_path)
    from roast_my_harness.tasks.discover import discover_tasks
    from roast_my_harness.tasks.hashes import task_hash

    tasks = discover_tasks(tmp_path, ["*"], [])
    pairs = [(t.task_id, task_hash(t.path)) for t in tasks]
    run1 = resolve_run_spec(spec, pairs).run_id
    _task(tmp_path, "t1", "changed")
    tasks = discover_tasks(tmp_path, ["*"], [])
    pairs2 = [(t.task_id, task_hash(t.path)) for t in tasks]
    assert resolve_run_spec(spec, pairs2).run_id != run1


def test_resume_refuses_changed_task_content(tmp_path: Path):
    os.environ["ROAST_MY_HARNESS_RUNS_DIR"] = str(tmp_path / "runs")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    _task(tmp_path, "t1", "one")
    from roast_my_harness.runner.controller import ExperimentController
    from roast_my_harness.store.repository import Repository

    spec = base_spec(tmp_path)
    from roast_my_harness.tasks.discover import discover_tasks
    from roast_my_harness.tasks.hashes import task_hash

    tasks = discover_tasks(tmp_path, ["*"], [])
    pairs = [(t.task_id, task_hash(t.path)) for t in tasks]
    exp_id = resolve_run_spec(spec, pairs).run_id
    repo = Repository(tmp_path / "db.sqlite")
    controller = ExperimentController(spec, exp_id, tmp_path / "run", repo, None)
    controller.prepare()

    _task(tmp_path, "t1", "changed")
    resumed = ExperimentController(spec, exp_id, tmp_path / "run", repo, None)
    with pytest.raises(PierError, match="task content changed"):
        resumed.prepare()
    repo.close()





@pytest.mark.parametrize(
    "entry", ["../escape.ts", "/abs/index.ts", "a/../../b.ts", "./x.ts"]
)
def test_unsafe_extension_entry_rejected(tmp_path: Path, entry: str):
    with pytest.raises(ValueError, match="entry"):
        LocalExtension(kind="local", path=tmp_path, entry=entry)


@pytest.mark.parametrize("name", ["..", "a/b", "/abs"])
def test_unsafe_extension_name_rejected(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="name"):
        LocalExtension(kind="local", path=tmp_path, entry="i.ts", name=name)


@pytest.mark.parametrize("name", ["..", "a/b", "/abs"])
def test_unsafe_skill_name_rejected(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="name"):
        SkillSpec(path=tmp_path, name=name)


@pytest.mark.parametrize("pkg", ["../escape", "/abs/pkg", "a/../b"])
def test_unsafe_runtime_package_rejected(tmp_path: Path, pkg: str):
    with pytest.raises(ValueError, match="runtime_package"):
        LocalExtension(
            kind="local", path=tmp_path, entry="i.ts", runtime_packages=[pkg]
        )


def test_builder_rejects_directory_name_escape(tmp_path: Path):
    source = tmp_path / "..parent"
    (source / "src").mkdir(parents=True)
    (source / "src" / "index.ts").write_text("1")
    spec = base_spec(
        tmp_path,
        extensions=[LocalExtension(kind="local", path=source, entry="src/index.ts")],
    )
    with pytest.raises(HomeBuildError, match="name"):
        build_home(spec.variants[0], spec, tmp_path / "homes")


def test_source_symlink_rejected_in_hash_and_copy(tmp_path: Path):
    src = tmp_path / "ext"
    (src / "real").mkdir(parents=True)
    (src / "real" / "index.ts").write_text("1")
    os.symlink(src / "real", src / "linked")
    with pytest.raises(ValueError, match="symlink"):
        source_tree_hash(src)
    with pytest.raises(ValueError, match="symlink"):
        copy_source_tree(src, tmp_path / "dst")


def test_runtime_package_symlink_rejected(tmp_path: Path):
    src = tmp_path / "ext"
    (src / "src").mkdir(parents=True)
    (src / "src" / "index.ts").write_text("1")
    nm = src / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("1")
    os.symlink(nm / "index.js", nm / "bad.js")
    from roast_my_harness.homes.sources import copy_runtime_packages

    with pytest.raises(ValueError, match="symlink"):
        copy_runtime_packages(src, tmp_path / "dst", ["dep"])





def test_secret_like_env_key_rejected():
    with pytest.raises(ValueError, match="credential"):
        VariantSpec(id="a", env={"MY_API_KEY": "x"})


def test_secret_like_env_value_rejected():
    with pytest.raises(ValueError, match="credential"):
        VariantSpec(id="a", env={"DATA": "bearer abc123secret"})


def test_invalid_env_name_rejected():
    with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
        VariantSpec(id="a", env={"bad-name": "x"})


def test_env_from_host_supported():
    variant = VariantSpec(id="a", env_from_host=["MY_TOKEN"])
    assert variant.env_from_host == ["MY_TOKEN"]
    with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
        VariantSpec(id="a", env_from_host=["not valid"])


def test_env_values_never_in_cached_home_or_hash(tmp_path: Path):
    src = tmp_path / "ext"
    (src / "src").mkdir(parents=True)
    (src / "src" / "index.ts").write_text("1")
    literal = "VALUE-NEVER-CACHED-7f3a"
    variant = VariantSpec(
        id="a",
        env={"AIRHEAD_KEEP": literal},
        extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ],
    )
    spec = base_spec(tmp_path)
    home = build_home(variant, spec, tmp_path / "homes")
    variant_json = (home.path / "variant.json").read_text()
    assert literal not in variant_json
    assert "AIRHEAD_KEEP" not in variant_json
    manifest = json.loads(variant_json)
    assert manifest["env"] == {}

    other = variant.model_copy(update={"env": {"AIRHEAD_KEEP": "other-value"}})
    assert variant_hash(other, "0.84.3") == variant_hash(variant, "0.84.3")





@pytest.mark.parametrize(
    "flag",
    [
        "--model",
        "--thinking",
        "--skill",
        "--session-dir",
        "--mode",
        "-nc",
        "--no-skills",
        "--extension",
    ],
)
def test_reserved_pi_flags_rejected(flag: str):
    with pytest.raises(ValueError, match="pi_flags entry"):
        VariantSpec(id="a", pi_flags=[flag])


def test_pi_flags_with_embedded_value_rejected():
    with pytest.raises(ValueError, match="harness-controlled"):
        VariantSpec(id="a", pi_flags=["--model=some/model"])


def test_non_allowlisted_flag_rejected():
    with pytest.raises(ValueError, match="not allowlisted"):
        VariantSpec(id="a", pi_flags=["--verbose"])


def test_allowed_pi_flags_accepted():
    variant = VariantSpec(id="a", pi_flags=["--no-builtin-tools", "--tools=fs"])
    assert variant.pi_flags == ["--no-builtin-tools", "--tools=fs"]
