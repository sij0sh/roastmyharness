"""TOML loading with path resolution."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from pydantic import ValidationError

from roast_my_harness.errors import SpecError
from roast_my_harness.spec.models import ExperimentSpec, ResolvedModelSpec
from roast_my_harness.spec.normalize import absolute


def _resolve_model(spec: ExperimentSpec) -> ExperimentSpec:
    """Materialize host model config into the spec.

    For a provider defined in the host pi models.json, record the
    provider block's hash and its env-var references so spec_hash
    covers host configuration drift. Codex and custom providers are
    already fully explicit in the spec and need no resolution.
    """
    model = spec.model
    if model.provider in ("openai-codex", "custom"):
        return spec
    try:
        from roast_my_harness.auth import service as auth_service

        block = auth_service.host_provider_block(model.provider)
    except Exception:
        # Host config unreadable here; preflight reports it with detail.
        return spec
    if block is None:
        return spec
    block_json = json.dumps(block, sort_keys=True, separators=(",", ":"))
    sha = auth_service.provider_block_hash(block)
    names = sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", block_json)))
    return spec.model_copy(
        update={
            "model": model.model_copy(
                update={
                    "resolved_model": ResolvedModelSpec(
                        provider=model.provider,
                        provider_block_sha256=sha,
                        env_vars=names,
                    )
                }
            )
        }
    )


def _resolve(spec: ExperimentSpec, base_dir: Path) -> ExperimentSpec:
    updates: dict = {}
    updates["tasks"] = spec.tasks.model_copy(
        update={"path": absolute(spec.tasks.path, base_dir)}
    )
    updates["model"] = spec.model.model_copy(
        update={
            "models_json": (
                absolute(spec.model.models_json, base_dir)
                if spec.model.models_json
                else None
            )
        }
    )
    variants = []
    for variant in spec.variants:
        v_updates: dict = {}
        exts = []
        for ext in variant.extensions:
            if ext.kind == "local":
                exts.append(ext.model_copy(update={"path": absolute(ext.path, base_dir)}))
            else:
                exts.append(ext)
        skills = [
            s.model_copy(update={"path": absolute(s.path, base_dir)})
            for s in variant.skills
        ]
        setups = []
        for setup in variant.setup:
            if setup.handler in ("install_binary", "codegraph_index"):
                field = "source" if setup.handler == "install_binary" else "bundle"
                setup = setup.model_copy(
                    update={field: absolute(getattr(setup, field), base_dir)}
                )
            setups.append(setup)
        v_updates.update(extensions=exts, skills=skills, setup=setups)
        variants.append(variant.model_copy(update=v_updates))
    updates["variants"] = variants
    resolved = spec.model_copy(update=updates)
    return _resolve_model(resolved)


def validation_summary(error: ValidationError) -> str:
    """Compact, single-line pydantic error summary for agent-facing messages."""
    parts: list[str] = []
    for item in error.errors():
        loc = ".".join(str(piece) for piece in item.get("loc", ())) or "spec"
        message = str(item.get("msg", "invalid value"))
        kind = item.get("type", "")
        if kind == "extra_forbidden":
            value = item.get("input")
            shown = str(value) if not isinstance(value, str) else compact_str(value, 120)
            message = f"unknown field: {shown}"
        parts.append(f"{loc}: {message}")
    return compact_str("; ".join(parts), 480)


def compact_str(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def load_experiment(path: Path) -> ExperimentSpec:
    """Load and validate a TOML experiment. Resolve all paths."""
    path = path.expanduser().resolve()
    try:
        text = path.read_text()
        raw = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read experiment file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"invalid experiment spec in {path}: expected a mapping")
    try:
        spec = ExperimentSpec.model_validate(raw)
    except ValidationError as e:
        raise SpecError(
            f"invalid experiment spec in {path}: {validation_summary(e)}"
        ) from e
    return _resolve(spec, path.parent)
