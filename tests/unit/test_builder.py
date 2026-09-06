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
        pi_version="0.84.3",
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


def test_dot_dir_extension_home(tmp_path: Path):
    src = make_ext(tmp_path, name=".pi-git-suite")
    spec = spec_for(
        tmp_path,
        [VariantSpec(id="a", extensions=[
            LocalExtension(kind="local", path=src, entry="src/index.ts")
        ])],
    )
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    settings = json.loads((home.path / "settings.json").read_text())
    assert settings["extensions"] == ["extensions/pi-git-suite/src/index.ts"]
    assert (home.path / "extensions" / "pi-git-suite" / "src" / "index.ts").is_file()


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
    second = build_home(spec.variants[0], spec, homes)
    assert first.path == second.path
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
    assert second.path != first.path


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


# --- Fix D: idempotent home cache publish (probe-home-cache-publish-race.py)


def test_concurrent_same_hash_publish_is_a_cache_hit(tmp_path, monkeypatch):
    """Loser of a same-hash publish race gets the winner's home, not EACCES."""
    import threading
    import time

    from roast_my_harness.homes import builder as builder_mod

    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    homes = tmp_path / "homes"
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    gate = [True]
    orig = builder_mod._mark_readonly

    def gated_readonly(root):
        orig(root)
        if gate[0]:
            ready.write_text("loser at the publish gate")
            deadline = time.monotonic() + 30
            while not go.exists() and time.monotonic() < deadline:
                time.sleep(0.01)

    monkeypatch.setattr(builder_mod, "_mark_readonly", gated_readonly)
    loser_out: list = []

    def loser():
        try:
            loser_out.append(build_home(spec.variants[0], spec, homes))
        except BaseException as err:
            loser_out.append(err)

    thread = threading.Thread(target=loser)
    thread.start()
    deadline = time.monotonic() + 30
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "loser never reached the publish gate"
    gate[0] = False
    winner = build_home(spec.variants[0], spec, homes)
    go.write_text("go")
    thread.join(timeout=60)

    assert loser_out and isinstance(loser_out[0], object)
    lost = loser_out[0]
    assert not isinstance(lost, BaseException), f"loser failed: {lost!r}"
    assert lost.path == winner.path
    assert lost.variant_hash == winner.variant_hash
    assert lost.manifest.variant_id == "a"
    assert not [p for p in homes.iterdir() if p.name.startswith(".build-")]
    assert [p for p in homes.iterdir() if p.is_dir()] == [winner.path]


def test_replace_race_loss_treated_as_cache_hit(tmp_path, monkeypatch):
    """Losing os.replace to a concurrent publish is a hit, not an error."""
    import errno

    from roast_my_harness.homes import builder as builder_mod

    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    homes = tmp_path / "homes"
    first = build_home(spec.variants[0], spec, homes)
    staging = homes / f".{first.path.name}.away"
    first.path.rename(staging)  # a builder that missed the cache

    def losing_replace(src, dst, *args, **kwargs):
        staging.rename(dst)  # the concurrent winner publishes first
        raise OSError(errno.ENOTEMPTY, "simulated publish race loss")

    monkeypatch.setattr(builder_mod.os, "replace", losing_replace)
    rebuilt = build_home(spec.variants[0], spec, homes)
    assert rebuilt.path == first.path
    assert rebuilt.variant_hash == first.variant_hash
    assert rebuilt.manifest.variant_id == "a"
    assert not [p for p in homes.iterdir() if p.name.startswith(".build-")]


def test_failed_publish_leaves_no_tmp_dir(tmp_path, monkeypatch):
    """A read-only tmp tree must not leak when the build fails late."""
    import errno

    from roast_my_harness.homes import builder as builder_mod

    spec = spec_for(tmp_path, [VariantSpec(id="a")])
    homes = tmp_path / "homes"

    def failing_replace(src, dst, *args, **kwargs):
        raise OSError(errno.EACCES, "simulated publish failure")

    monkeypatch.setattr(builder_mod.os, "replace", failing_replace)
    with pytest.raises(OSError):
        build_home(spec.variants[0], spec, homes)
    assert not [p for p in homes.iterdir() if p.name.startswith(".build-")]
