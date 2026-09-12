"""Home builder: bare, extension, skill, cache, and leak rejection. Pi-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.errors import HomeBuildError
from roast_my_harness.homes.builder import build_home
from roast_my_harness.spec.models import (
    ExperimentSpec,
    LocalExtension,
    SkillSpec,
    TaskSelection,
    VariantSpec,
)


def spec_for(tmp_path: Path, variants: list[VariantSpec]) -> ExperimentSpec:
    return ExperimentSpec(name="t", tasks=TaskSelection(path=tmp_path), variants=variants,
                          pi_version="0.84.3")


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
    assert manifest["agent"] == "pi"
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings == {"extensions": []}


def test_local_extension_home(tmp_path: Path):
    src = make_ext(tmp_path)
    ext = LocalExtension(path=src, entry="src/index.ts")
    spec = spec_for(tmp_path, [VariantSpec(id="a", extensions=[ext])])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "extensions" / "myext" / "src" / "index.ts").is_file()
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings["extensions"] == ["extensions/myext/src/index.ts"]


def test_agents_md_placed_at_root(tmp_path: Path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# instructions\n")
    spec = spec_for(tmp_path, [VariantSpec(id="a", agents_md=agents)])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "AGENTS.md").read_text() == "# instructions\n"


def test_settings_file_merged(tmp_path: Path):
    custom = tmp_path / "settings.json"
    custom.write_text('{"theme": "dark"}')
    spec = spec_for(tmp_path, [VariantSpec(id="a", settings=custom)])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings["theme"] == "dark"
    assert settings["extensions"] == []


def test_missing_entry_fails(tmp_path: Path):
    src = make_ext(tmp_path)
    ext = LocalExtension(path=src, entry="src/nope.ts")
    spec = spec_for(tmp_path, [VariantSpec(id="a", extensions=[ext])])
    with pytest.raises(HomeBuildError, match="entry missing"):
        build_home(spec.variants[0], spec, tmp_path / "homes")


def test_skill_home(tmp_path: Path):
    skill = tmp_path / "sources" / "sk"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n")
    spec = spec_for(tmp_path, [VariantSpec(id="a", skills=[SkillSpec(path=skill)])])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "skills" / "sk" / "SKILL.md").is_file()


def test_cache_hit_on_rebuild(tmp_path: Path):
    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    homes = tmp_path / "homes"
    first = build_home(spec.variants[0], spec, homes)
    second = build_home(spec.variants[0], spec, homes)
    assert first.path == second.path


def test_instruction_leak_rejected(tmp_path: Path):
    src = make_ext(tmp_path)
    (src / "AGENTS.md").write_text("leak")
    ext = LocalExtension(path=src, entry="src/index.ts")
    spec = spec_for(tmp_path, [VariantSpec(id="a", extensions=[ext])])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "AGENTS.md").is_file() is False
    assert not (home.path / "extensions" / "myext" / "AGENTS.md").exists()


def test_control_arm_builds_like_bare(tmp_path: Path):
    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    home = build_home(spec.arms()[0], spec, tmp_path / "homes")
    assert home.manifest.variant_id == "control"
