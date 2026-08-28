"""Immutable Pi-home builder, cached by variant hash.

Pipeline (plan section 9): normalize -> hash -> cache check -> build in
tmp sibling -> settings.json -> copy sources in order -> sanitizer ->
runtime packages -> variant.json + build-manifest.json -> assert entries
-> assert no instruction leaks -> atomic rename -> read-only.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from roast_my_harness.errors import HomeBuildError
from roast_my_harness.homes.manifest import (
    ManifestExtension,
    ManifestSetupStep,
    ManifestSkill,
    VariantManifest,
)
from roast_my_harness.homes.sanitize import INSTRUCTION_FILES
from roast_my_harness.homes.sources import copy_runtime_packages, copy_source_tree, source_tree_hash
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.models import (
    ExperimentSpec,
    LocalExtension,
    SkillSpec,
    VariantSpec,
    _safe_relative_component,
)


@dataclass(frozen=True)
class HomeBuild:
    path: Path  # cached, immutable home directory
    manifest: VariantManifest
    variant_hash: str


def _extension_name(ext: LocalExtension, index: int) -> str:
    name = ext.name or ext.path.name.lstrip(".") or f"extension-{index + 1}"
    return _checked_component(name, "extension name")


def _skill_name(skill: SkillSpec) -> str:
    name = skill.name or skill.path.name.lstrip(".") or "skill"
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
    return hashes


def compute_variant_hash(
    variant: VariantSpec,
    pi_version: str,
    *,
    agent: str = "pi",
    agent_version: str | None = None,
) -> str:
    return variant_hash(
        variant,
        pi_version,
        compute_source_hashes(variant),
        agent=agent,
        agent_version=agent_version,
    )


def resolve_arm_agent(spec: ExperimentSpec, variant: VariantSpec) -> tuple[str, str]:
    """The (agent id, agent version) pin one arm runs."""
    agent_id = variant.agent or spec.agent
    return agent_id, spec.agent_version_for(agent_id)


def build_home(
    variant: VariantSpec,
    spec: ExperimentSpec,
    homes_root: Path,
) -> HomeBuild:
    """Build (or return cached) home for one variant arm."""
    agent_id, agent_version = resolve_arm_agent(spec, variant)
    v_hash = compute_variant_hash(
        variant, spec.pi_version, agent=agent_id, agent_version=agent_version
    )
    home = homes_root / v_hash[:16]
    manifest_path = home / "build-manifest.json"

    if home.is_dir() and (home / "variant.json").is_file() and manifest_path.is_file():
        try:
            recorded = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            recorded = None
        if recorded and recorded.get("variant_hash") == v_hash:
            manifest = VariantManifest.model_validate(
                json.loads((home / "variant.json").read_text())
            )
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
                copy_runtime_packages(ext.path, dst, ext.runtime_packages)
                entry = f"extensions/{name}/{ext.entry}"
                if not (tmp / entry).is_file():
                    raise HomeBuildError(
                        f"extension {name!r} entry point missing: {tmp / entry}"
                    )
                entries.append(entry)
                manifest_exts.append(
                    ManifestExtension(name=name, entry=entry)
                )
            else:  # npm: installed in-container during adapter setup
                manifest_exts.append(
                    ManifestExtension(name=ext.package, entry="")
                )

        skills: list[ManifestSkill] = []
        for skill in variant.skills:
            name = _skill_name(skill)
            if not (skill.path / "SKILL.md").is_file():
                raise HomeBuildError(f"skill {name!r} missing SKILL.md at {skill.path}")
            dst = tmp / "skills" / name
            dst.mkdir(parents=True, exist_ok=True)
            copy_source_tree(skill.path, dst)
            rel = f"skills/{name}"
            skills.append(ManifestSkill(name=name, path=rel))

        (tmp / "settings.json").write_text(
            json.dumps({"extensions": entries}, indent=2) + "\n"
        )

        setup = [
            ManifestSetupStep(handler=step.handler, args=_setup_args(step))
            for step in variant.setup
        ]
        npm_packages = [
            ext.package for ext in variant.extensions if ext.kind == "npm"
        ]
        manifest = VariantManifest(
            variant_id=variant.id,
            variant_hash=v_hash,
            pi_version=spec.pi_version,
            agent=agent_id,
            agent_version=agent_version,
            model_id=spec.model.full_id(),
            extensions=manifest_exts,
            skills=skills,
            npm_packages=npm_packages,
            env={},
            env_from_host=list(variant.env_from_host),
            setup=setup,
            egress_urls=list(variant.egress_urls),
            pi_flags=list(variant.pi_flags),
        )
        (tmp / "variant.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
        )
        (tmp / "build-manifest.json").write_text(
            json.dumps(
                {
                    "variant_hash": v_hash,
                    "variant_id": variant.id,
                    "pi_version": spec.pi_version,
                    "agent": agent_id,
                    "agent_version": agent_version,
                    "source_hashes": source_hashes,
                },
                indent=2,
            )
            + "\n"
        )

        _assert_no_instruction_leaks(tmp)
        _mark_readonly(tmp)

        if home.exists():
            shutil.rmtree(home)
        os.replace(tmp, home)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return HomeBuild(path=home, manifest=manifest, variant_hash=v_hash)


def _setup_args(step) -> dict[str, str]:
    if step.handler == "npm_pi_install":
        return {"package": step.package}
    if step.handler == "install_binary":
        args = {"source": str(step.source), "destination": step.destination}
        if step.verify:
            args["verify"] = step.verify
        return args
    if step.handler == "codegraph_index":
        return {"bundle": str(step.bundle)}
    return {}


def _validate_sources(variant: VariantSpec) -> None:
    paths: list[Path] = [e.path for e in variant.extensions if e.kind == "local"]
    paths += [s.path for s in variant.skills]
    for path in paths:
        if path.exists() and (path.stat().st_mode & 0o002):
            raise HomeBuildError(
                f"source directory is world-writable: {path} "
                "(chmod o-w the source to proceed)"
            )


def _assert_no_instruction_leaks(home: Path) -> None:
    leaked = [
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file() and p.name in INSTRUCTION_FILES
    ]
    if leaked:
        raise HomeBuildError(f"instruction files leaked into home: {leaked}")


def _mark_readonly(root: Path) -> None:
    os.chmod(root, 0o555)
    for path in root.rglob("*"):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
