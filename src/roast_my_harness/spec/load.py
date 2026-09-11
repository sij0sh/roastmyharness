"""TOML loading with path resolution."""

from __future__ import annotations

import json
import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from pydantic import ValidationError

from roast_my_harness.errors import SpecError
from roast_my_harness.spec.models import ExperimentSpec, ResolvedModelSpec, TaskSelection
from roast_my_harness.spec.normalize import absolute
from roast_my_harness.tasks.catalog import load_catalog
from roast_my_harness.tasks.discover import is_task_dir


def _resolve_model(spec: ExperimentSpec) -> ExperimentSpec:
    model = spec.model
    if model.provider == "openai-codex":
        return spec
    try:
        from roast_my_harness.auth import service as auth_service
        block = auth_service.host_provider_block(model.provider)
    except Exception:
        return spec
    if block is None:
        return spec
    block_json = json.dumps(block, sort_keys=True, separators=(",", ":"))
    sha = auth_service.provider_block_hash(block)
    names = sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", block_json)))
    return spec.model_copy(update={"model": model.model_copy(update={
        "resolved_model": ResolvedModelSpec(provider=model.provider,
                                            provider_block_sha256=sha, env_vars=names)})})


def _apply_preset(tasks: TaskSelection) -> TaskSelection:
    if tasks.preset is None:
        return tasks
    catalog = load_catalog(tasks.path)
    if catalog is None:
        raise SpecError(f"tasks.preset = {tasks.preset!r} needs catalog at {tasks.path / 'catalog.toml'}")
    preset = catalog.presets.get(tasks.preset)
    if preset is None:
        raise SpecError(f"unknown tasks.preset {tasks.preset!r}")
    missing = [t for t in preset.tasks if not is_task_dir(tasks.path / t)]
    if missing:
        raise SpecError(f"tasks.preset {tasks.preset!r} lists missing tasks: {', '.join(missing)}")
    effective = [t for t in preset.tasks if any(fnmatch(t, pat) for pat in tasks.include)]
    return tasks.model_copy(update={"include": effective})


def _resolve(spec: ExperimentSpec, base_dir: Path) -> ExperimentSpec:
    updates: dict = {}
    updates["tasks"] = _apply_preset(spec.tasks.model_copy(update={"path": absolute(spec.tasks.path, base_dir)}))
    variants = []
    for variant in spec.variants:
        exts = [e.model_copy(update={"path": absolute(e.path, base_dir)}) if e.kind == "local" else e
                for e in variant.extensions]
        skills = [s.model_copy(update={"path": absolute(s.path, base_dir)}) for s in variant.skills]
        ctx = [c.model_copy(update={"path": absolute(c.path, base_dir)}) for c in variant.context_files]
        v = variant.model_copy(update={"extensions": exts, "skills": skills, "context_files": ctx})
        if v.agents_md is not None:
            v = v.model_copy(update={"agents_md": absolute(v.agents_md, base_dir)})
        if v.settings is not None:
            v = v.model_copy(update={"settings": absolute(v.settings, base_dir)})
        variants.append(v)
    updates["variants"] = variants
    return _resolve_model(spec.model_copy(update=updates))


def validation_summary(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors():
        loc = ".".join(str(p) for p in item.get("loc", ())) or "spec"
        message = str(item.get("msg", "invalid value"))
        if item.get("type") == "extra_forbidden":
            value = item.get("input")
            shown = str(value) if not isinstance(value, str) else compact_str(value, 120)
            message = f"unknown field: {shown}"
        parts.append(f"{loc}: {message}")
    return compact_str("; ".join(parts), 480)


def compact_str(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def load_experiment(path: Path) -> ExperimentSpec:
    path = path.expanduser().resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read experiment file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"invalid experiment spec in {path}")
    try:
        spec = ExperimentSpec.model_validate(raw)
    except ValidationError as e:
        raise SpecError(f"invalid experiment spec in {path}: {validation_summary(e)}") from e
    return _resolve(spec, path.parent)
