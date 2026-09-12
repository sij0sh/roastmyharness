"""Immutable Pi-home builder, cached by variant hash. Pi-only."""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from roast_my_harness.errors import HomeBuildError
from roast_my_harness.homes.manifest import (
    ManifestExtension,
    ManifestSkill,
    VariantManifest,
)
from roast_my_harness.homes.sanitize import INSTRUCTION_FILES
from roast_my_harness.homes.sources import (
    copy_source_tree,
    source_file_hash,
    source_tree_hash,
)
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.models import (
    SkillSpec,
    VariantSpec,
    _safe_relative_component,
)


@dataclass(frozen=True)
class HomeBuild:
    path: Path
    manifest: VariantManifest
    variant_hash: str


def _source_name(path: Path, fallback: str) -> str:
    name = path.name
    if name.startswith(".") and not name.startswith(".."):
        name = name[1:]
    return name or fallback


def _extension_name(ext, index: int) -> str:
    name = ext.name or _source_name(ext.path, f"extension-{index + 1}")
    return _checked_component(name, "extension name")


def _skill_name(skill: SkillSpec) -> str:
    name = skill.name or _source_name(skill.path, "skill")
    return _checked_component(name, "skill name")


def _checked_component(value: str, what: str) -> str:
    try:
        return _safe_relative_component(value, what)
    except ValueError as e:
        raise HomeBuildError(str(e)) from e


def compute_source_hashes(variant: VariantSpec) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for i, ext in enumerate(variant.extensions):
        if ext.kind == "local":
            name = _extension_name(ext, i)
            hashes[f"ext:{name}"] = source_tree_hash(ext.path, ext.exclude)
    for skill in variant.skills:
        name = _skill_name(skill)
        hashes[f"skill:{name}"] = source_tree_hash(skill.path)
    if variant.agents_md is not None:
        hashes["agents_md"] = source_file_hash(variant.agents_md)
    if variant.settings is not None:
        hashes["settings"] = source_file_hash(variant.settings)
    return hashes


def compute_variant_hash(variant, pi_version, *, resolved_pi_version=None) -> str:
    return variant_hash(variant, pi_version, compute_source_hashes(variant),
                        resolved_pi_version=resolved_pi_version)


def resolve_arm_pi_version(spec, variant, *, pi_version=None):
    if pi_version is not None:
        return pi_version
    return spec.resolved_pi_version_for(variant if variant.id != "control" else None)


def build_home(variant, spec, homes_root, *, pi_version=None,
               runtime_agent_install=False) -> HomeBuild:
    resolved_version = resolve_arm_pi_version(spec, variant, pi_version=pi_version)
    if variant.agents_md is not None and not variant.agents_md.is_file():
        raise HomeBuildError(f"agents_md missing at {variant.agents_md}")
    if variant.settings is not None and not variant.settings.is_file():
        raise HomeBuildError(f"settings file missing at {variant.settings}")
    effective_pin = spec.pi_version_for(variant if variant.id != "control" else None)
    v_hash = compute_variant_hash(variant, effective_pin, resolved_pi_version=resolved_version)
    home = homes_root / v_hash[:16]
    manifest_path = home / "build-manifest.json"
    if home.is_dir() and (home / "variant.json").is_file() and manifest_path.is_file():
        try:
            recorded = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            recorded = None
        if recorded and recorded.get("variant_hash") == v_hash:
            raw = (home / "variant.json").read_text()
            manifest = VariantManifest.model_validate(json.loads(raw))
            return HomeBuild(path=home, manifest=manifest, variant_hash=v_hash)
        shutil.rmtree(home, ignore_errors=True)
    _validate_sources(variant)
    homes_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".build-{v_hash[:8]}-", dir=homes_root))
    try:
        entries: list[str] = []
        manifest_exts: list[ManifestExtension] = []
        source_hashes = compute_source_hashes(variant)
        for i, ext in enumerate(variant.extensions):
            if ext.kind == "local":
                name = _extension_name(ext, i)
                dst = tmp / "extensions" / name
                dst.mkdir(parents=True, exist_ok=True)
                copy_source_tree(ext.path, dst, ext.exclude)
                entry = f"extensions/{name}/{ext.entry}"
                if not (tmp / entry).is_file():
                    raise HomeBuildError(f"extension {name!r} entry missing: {tmp / entry}")
                entries.append(entry)
                manifest_exts.append(ManifestExtension(name=name, entry=entry))
            else:
                manifest_exts.append(ManifestExtension(name=ext.package, entry=""))
        skills: list[ManifestSkill] = []
        for skill in variant.skills:
            name = _skill_name(skill)
            if not (skill.path / "SKILL.md").is_file():
                raise HomeBuildError(f"skill {name!r} missing SKILL.md at {skill.path}")
            dst = tmp / "skills" / name
            dst.mkdir(parents=True, exist_ok=True)
            copy_source_tree(skill.path, dst)
            skills.append(ManifestSkill(name=name, path=f"skills/{name}"))
        has_agents_md = variant.agents_md is not None
        if has_agents_md:
            assert variant.agents_md is not None
            (tmp / "AGENTS.md").write_bytes(Path(variant.agents_md).read_bytes())
        payload = json.dumps(_settings_payload(variant, entries), indent=2) + "\n"
        (tmp / "settings.json").write_text(payload)
        npm_packages = [ext.package for ext in variant.extensions if ext.kind == "npm"]
        manifest = VariantManifest(
            variant_id=variant.id,
            variant_hash=v_hash,
            pi_version=effective_pin,
            agent="pi",
            agent_version=resolved_version,
            model_id=spec.model.full_id(),
            extensions=manifest_exts,
            skills=skills,
            has_agents_md=has_agents_md,
            npm_packages=npm_packages,
            env={},
            env_from_host=list(variant.env_from_host),
            egress_urls=list(variant.egress_urls),
            pi_flags=list(variant.pi_flags),
            runtime_agent_install=runtime_agent_install,
        )
        text = json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
        (tmp / "variant.json").write_text(text)
        manifest_json = {"variant_hash": v_hash, "variant_id": variant.id,
                         "pi_version": effective_pin, "agent": "pi",
                         "agent_version": resolved_version, "source_hashes": source_hashes}
        (tmp / "build-manifest.json").write_text(json.dumps(manifest_json, indent=2) + "\n")
        _assert_no_instruction_leaks(tmp)
        _mark_readonly(tmp)
        if home.exists():
            _discard_tree(tmp)
            return _published_home(home, v_hash)
        try:
            os.replace(tmp, home)
        except OSError as err:
            if err.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
            _discard_tree(tmp)
            return _published_home(home, v_hash)
    except BaseException:
        _discard_tree(tmp)
        raise
    return HomeBuild(path=home, manifest=manifest, variant_hash=v_hash)


def _settings_payload(variant, entries: list[str]) -> dict:
    base: dict = {}
    if variant.settings is not None:
        try:
            base = json.loads(Path(variant.settings).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise HomeBuildError(f"settings file unreadable {variant.settings}: {e}") from e
        if not isinstance(base, dict):
            raise HomeBuildError(f"settings file must be a JSON object: {variant.settings}")
    base = dict(base)
    base["extensions"] = entries
    return base


def _validate_sources(variant) -> None:
    paths: list[Path] = [e.path for e in variant.extensions if e.kind == "local"]
    paths += [s.path for s in variant.skills]
    if variant.agents_md is not None:
        paths.append(variant.agents_md.parent)
    if variant.settings is not None:
        paths.append(variant.settings.parent)
    for path in paths:
        if path.exists() and (path.stat().st_mode & 0o002):
            raise HomeBuildError(f"source directory is world-writable: {path}")


def _assert_no_instruction_leaks(home: Path) -> None:
    allowed = {home / "AGENTS.md"}
    leaked = [str(p.relative_to(home)) for p in home.rglob("*")
              if p.is_file() and p.name in INSTRUCTION_FILES and p not in allowed]
    if leaked:
        raise HomeBuildError(f"instruction files leaked into home: {leaked}")


def _published_home(home: Path, v_hash: str) -> HomeBuild:
    manifest = VariantManifest.model_validate(json.loads((home / "variant.json").read_text()))
    return HomeBuild(path=home, manifest=manifest, variant_hash=v_hash)


def _discard_tree(root: Path) -> None:
    try:
        os.chmod(root, 0o700)
        for path in root.rglob("*"):
            try:
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=True)


def _mark_readonly(root: Path) -> None:
    os.chmod(root, 0o555)
    for path in root.rglob("*"):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
