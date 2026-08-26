"""Home builder: bare, extension, skill, cache, and leak rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.errors import HomeBuildError
from roast_my_harness.homes.builder import build_home
from roast_my_harness.spec.models import (
    ControlSpec,
    ExperimentSpec,
    LocalExtension,
    SkillSpec,
    TaskSelection,
    VariantSpec,
)


def spec_for(tmp_path: Path, variants: list[VariantSpec]) -> ExperimentSpec:
    return ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=tmp_path),
        control=None,
        variants=variants,
    )


def make_ext(tmp_path: Path, name="myext", body="1") -> Path:
    src = tmp_path / "sources" / name
    (src / "src").mkdir(parents=True)
    (src / "src" / "index.ts").write_text(body)
    return src


def test_bare_home(tmp_path: Path):
    spec = spec_for(tmp_path, [VariantSpec(id="bareish")])
    home = build_home(VariantSpec(id="bareish"), spec, tmp_path / "homes")
    assert (home.path / "variant.json").is_file()
    manifest = json.loads((home.path / "variant.json").read_text())
    assert manifest["variant_id"] == "bareish"
    assert manifest["model_id"] == "openai-codex/gpt-5.6-luna"
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings == {"extensions": []}


def test_local_extension_home(tmp_path: Path):
    src = make_ext(tmp_path)
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ])],
    )
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    entry = home.path / "extensions" / "myext" / "src" / "index.ts"
    assert entry.is_file()
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings["extensions"] == ["extensions/myext/src/index.ts"]
    assert (home.path / "extensions" / "myext" / "src" / "index.ts").read_text() == "1"


def test_missing_entry_fails(tmp_path: Path):
    src = make_ext(tmp_path)
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/nope.ts")
        ])],
    )
    with pytest.raises(HomeBuildError, match="entry point missing"):
        build_home(spec.variants[0], spec, tmp_path / "homes")


def test_skill_requires_skill_md(tmp_path: Path):
    skill = tmp_path / "sources" / "sk"
    skill.mkdir(parents=True)
    spec = spec_for(
        tmp_path, [VariantSpec(id="a", skills=[SkillSpec(path=skill)])]
    )
    with pytest.raises(HomeBuildError, match="SKILL.md"):
        build_home(spec.variants[0], spec, tmp_path / "homes")


def test_skill_home(tmp_path: Path):
    skill = tmp_path / "sources" / "sk"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n")
    spec = spec_for(
        tmp_path, [VariantSpec(id="a", skills=[SkillSpec(path=skill)])]
    )
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "skills" / "sk" / "SKILL.md").is_file()
    manifest = json.loads((home.path / "variant.json").read_text())
    assert manifest["skills"][0]["path"] == "skills/sk"


def test_cache_hit_on_rebuild(tmp_path: Path):
    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    homes = tmp_path / "homes"
    first = build_home(spec.variants[0], spec, homes)
    assert not first.cached
    second = build_home(spec.variants[0], spec, homes)
    assert second.cached
    assert first.variant_hash == second.variant_hash


def test_cache_invalidated_by_source_change(tmp_path: Path):
    src = make_ext(tmp_path)
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ])],
    )
    homes = tmp_path / "homes"
    first = build_home(spec.variants[0], spec, homes)
    (src / "src" / "index.ts").write_text("changed")
    second = build_home(spec.variants[0], spec, homes)
    assert second.variant_hash != first.variant_hash
    assert not second.cached


def test_instruction_file_leak_rejected(tmp_path: Path):
    src = make_ext(tmp_path)
    (src / "AGENTS.md").write_text("leak")  # sanitizer should drop it anyway
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ])],
    )
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert not list(home.path.rglob("AGENTS.md"))


def test_world_writable_source_rejected(tmp_path: Path):
    src = make_ext(tmp_path)
    src.chmod(0o777)
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ])],
    )
    with pytest.raises(HomeBuildError, match="world-writable"):
        build_home(spec.variants[0], spec, tmp_path / "homes")
    assert ControlSpec().enabled


def test_control_arm_builds_like_bare(tmp_path: Path):
    spec = ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=tmp_path),
        control=ControlSpec(enabled=True),
        variants=[],
    )
    home = build_home(spec.arms()[0], spec, tmp_path / "homes")
    assert home.manifest.variant_id == "control"
