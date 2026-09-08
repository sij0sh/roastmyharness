"""Immutable Pi-home builder, cached by variant hash.

Pipeline (plan section 9): normalize -> hash -> cache check -> build in
tmp sibling -> settings.json -> copy sources in order -> sanitizer ->
runtime packages -> variant.json + build-manifest.json -> assert entries
-> assert no instruction leaks -> atomic rename -> read-only.
"""

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
    ManifestContextFile,
    ManifestExtension,
    ManifestSetupStep,
    ManifestSkill,
    VariantManifest,
)
from roast_my_harness.homes.sanitize import INSTRUCTION_FILES
from roast_my_harness.homes.sources import (
    copy_runtime_packages,
    copy_source_tree,
    source_file_hash,
    source_tree_hash,
)
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.models import (
    ContextFileSpec,
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


def _source_name(path: Path, fallback: str) -> str:
    name = path.name
    if name.startswith(".") and not name.startswith(".."):
        name = name[1:]
    return name or fallback


def _extension_name(ext: LocalExtension, index: int) -> str:
    name = ext.name or _source_name(ext.path, f"extension-{index + 1}")
    return _checked_component(name, "extension name")


def _skill_name(skill: SkillSpec) -> str:
    name = skill.name or _source_name(skill.path, "skill")
    return _checked_component(name, "skill name")


def _context_file_name(ctx: ContextFileSpec) -> str:
    name = ctx.name or _source_name(ctx.path, "context-file")
    return _checked_component(name, "context file name")


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
    for ctx in variant.context_files:
        name = _context_file_name(ctx)
        hashes[f"ctx:{name}"] = source_file_hash(ctx.path)
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


def resolve_arm_agent(
    spec: ExperimentSpec,
    variant: VariantSpec,
    *,
    agent_version: str | None = None,
) -> tuple[str, str]:
    """Arm agent plus the exact version to install.

    agent_version pins the answer (the prepare-time frozen value); when
    None the pin resolves live, which callers must only use outside a
    frozen run (tests, one-off builds).
    """
    agent_id = variant.agent or spec.agent
    if agent_version is not None:
        return agent_id, agent_version
    return agent_id, spec.resolved_version_for(agent_id)


def build_home(
    variant: VariantSpec,
    spec: ExperimentSpec,
    homes_root: Path,
    *,
    agent_version: str | None = None,
) -> HomeBuild:
    """Build (or return cached) home for one variant arm."""
    agent_id, agent_version = resolve_arm_agent(
        spec, variant, agent_version=agent_version
    )
    # Files (unlike trees) must exist before hashing: a missing file has
    # no content to bind, so fail here with a domain error instead of an
    # OSError from the hash step.
    for ctx in variant.context_files:
        if not ctx.path.is_file():
            raise HomeBuildError(
                f"context file {_context_file_name(ctx)!r} is not a file "
                f"at {ctx.path}"
            )
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

        context_files: list[ManifestContextFile] = []
        for ctx in variant.context_files:
            name = _context_file_name(ctx)
            dst = tmp / "context-files" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(ctx.path.read_bytes())
            rel = f"context-files/{name}"
            context_files.append(
                ManifestContextFile(name=name, path=rel, kind=ctx.kind)
            )

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
            context_files=context_files,
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
            # A concurrent same-hash builder published first; content
            # addressing makes its home equivalent. Cache hit, never clobber.
            _discard_tree(tmp)
            return _published_home(home, v_hash)
        try:
            os.replace(tmp, home)
        except OSError as err:
            if err.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
            # Lost the replace race; the winner's home is equivalent.
            _discard_tree(tmp)
            return _published_home(home, v_hash)
    except BaseException:
        _discard_tree(tmp)
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
    paths += [c.path.parent for c in variant.context_files]
    for path in paths:
        if path.exists() and (path.stat().st_mode & 0o002):
            raise HomeBuildError(
                f"source directory is world-writable: {path} "
                "(chmod o-w the source to proceed)"
            )


def _assert_no_instruction_leaks(home: Path) -> None:
    # Explicitly declared context files live under context-files/ (hashed,
    # manifest-recorded); only implicit copies elsewhere are leaks.
    staged = home / "context-files"
    leaked = [
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file()
        and p.name in INSTRUCTION_FILES
        and staged not in p.parents
    ]
    if leaked:
        raise HomeBuildError(f"instruction files leaked into home: {leaked}")


def _published_home(home: Path, v_hash: str) -> HomeBuild:
    """HomeBuild for an existing same-hash cache home; trusted on existence."""
    manifest = VariantManifest.model_validate(
        json.loads((home / "variant.json").read_text())
    )
    return HomeBuild(path=home, manifest=manifest, variant_hash=v_hash)


def _discard_tree(root: Path) -> None:
    """Best-effort removal of a possibly read-only tree."""
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
