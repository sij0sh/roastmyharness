"""Native AGENTS.md semantics: staged file discovered by Pi, ambient sanitized."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.homes.builder import build_home
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def make_spec(tmp_path: Path, variants: list[VariantSpec]) -> ExperimentSpec:
    return ExperimentSpec(name="ctx", tasks=TaskSelection(path=tmp_path), variants=variants,
                          pi_version="0.84.3")


def test_agents_md_placed_at_home_root(tmp_path: Path):
    src = tmp_path / "AGENTS.md"
    src.write_text("Be brief.\n")
    spec = make_spec(tmp_path, [VariantSpec(id="a", agents_md=src)])
    home = build_home(spec.variants[0], spec, tmp_path / "homes")
    assert (home.path / "AGENTS.md").read_text() == "Be brief.\n"
    manifest = json.loads((home.path / "variant.json").read_text())
    assert manifest["has_agents_md"] is True


def test_adapter_does_not_prepend_context(tmp_path: Path):
    import inspect

    from roast_my_harness.adapter import pi_agent
    src = inspect.getsource(pi_agent.PiAgent.run)
    assert "with_context_files" not in src
