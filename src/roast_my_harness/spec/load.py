"""TOML loading and path resolution."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from roast_my_harness.errors import SpecError
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import absolute


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
    return spec.model_copy(update=updates)


def load_experiment(path: Path) -> ExperimentSpec:
    """Load and validate an experiment TOML file. All paths become absolute."""
    path = path.expanduser().resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read experiment file {path}: {e}") from e
    try:
        spec = ExperimentSpec.model_validate(raw)
    except ValidationError as e:
        raise SpecError(f"invalid experiment spec in {path}:\n{e}") from e
    return _resolve(spec, path.parent)
