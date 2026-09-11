"""Spec loading and validation rules. Pi-only, schema v3."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import ExperimentSpec, NpmExtension, VariantSpec

MINIMAL = """
schema_version = 3
name = "demo"
[tasks]
path = "/tmp/does-not-need-to-exist"
[[variants]]
id = "bareish"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "experiment.toml"
    path.write_text(text)
    return path


def test_minimal_spec_defaults(tmp_path: Path):
    spec = load_experiment(write(tmp_path, MINIMAL))
    assert spec.name == "demo"
    assert spec.thinking == "high"
    assert spec.pi_version == "latest"
    assert spec.model.full_id() == "openai-codex/gpt-5.6-luna"
    assert [v.id for v in spec.arms()] == ["control", "bareish"]


def test_control_is_implicit(tmp_path: Path):
    spec = load_experiment(write(tmp_path, MINIMAL))
    assert spec.arms()[0].id == "control"


def test_control_block_rejected(tmp_path: Path):
    with pytest.raises(SpecError, match="valid boolean"):
        load_experiment(write(tmp_path, MINIMAL + "\n[control]\nenabled = true\n"))


def test_validation_error_is_single_line(tmp_path: Path):
    path = write(tmp_path, MINIMAL + "\nbogus_field = true\n")
    with pytest.raises(SpecError) as excinfo:
        load_experiment(path)
    message = str(excinfo.value)
    assert "\n" not in message
    assert "unknown field" in message


def test_reserved_control_id_rejected(tmp_path: Path):
    with pytest.raises(SpecError, match="reserved"):
        load_experiment(write(tmp_path, 'schema_version = 3\nname = "x"\n[tasks]\npath = "."\n[[variants]]\nid = "control"\n'))


def test_duplicate_variant_ids_rejected(tmp_path: Path):
    with pytest.raises(SpecError, match="duplicate"):
        load_experiment(write(tmp_path, 'schema_version = 3\nname = "x"\n[tasks]\npath = "."\n[[variants]]\nid = "a"\n[[variants]]\nid = "a"\n'))


def test_unsafe_variant_id_rejected(tmp_path: Path):
    with pytest.raises(SpecError):
        load_experiment(write(tmp_path, 'schema_version = 3\nname = "x"\n[tasks]\npath = "."\n[[variants]]\nid = "Bad Id!"\n'))


def test_pi_version_pins(tmp_path: Path):
    spec = load_experiment(write(tmp_path, 'pi_version = "latest"\n' + MINIMAL))
    assert spec.pi_version == "latest"
    assert spec.pi_version_for(None) == "latest"
    spec = load_experiment(write(tmp_path, 'pi_version = "0.85.1"\n' + MINIMAL))
    assert spec.resolved_pi_version_for(None) == "0.85.1"


def test_variant_pi_version_override(tmp_path: Path):
    spec = load_experiment(write(tmp_path, MINIMAL + '\n[variants.pi_version]\n' if False else MINIMAL))
    assert spec.pi_version_for(spec.variants[0]) == "latest"


def test_every_fairness_flag_is_reserved():
    from roast_my_harness.adapter.command import FAIRNESS_FLAGS
    from roast_my_harness.spec.models import RESERVED_PI_FLAGS
    fairness = frozenset(FAIRNESS_FLAGS.split())
    assert fairness
    assert fairness <= RESERVED_PI_FLAGS


def test_needs_at_least_one_variant(tmp_path: Path):
    with pytest.raises(SpecError, match="at least one"):
        load_experiment(write(tmp_path, 'schema_version = 3\nname = "x"\n[tasks]\npath = "."\n'))


def test_npm_requires_exact_pin():
    assert NpmExtension(package="context-mode@1.0.169").package
    with pytest.raises(ValueError):
        NpmExtension(package="context-mode")
    with pytest.raises(ValueError):
        NpmExtension(package="context-mode@1.0.0; echo leaked")


def test_extension_kind_inferred(tmp_path: Path):
    spec = load_experiment(write(tmp_path, """
schema_version = 3
name = "x"
[tasks]
path = "."
[[variants]]
id = "a"
[[variants.extensions]]
package = "context-mode@1.0.169"
"""))
    assert spec.variants[0].extensions[0].kind == "npm"


def test_model_string_shorthand(tmp_path: Path):
    spec = load_experiment(write(tmp_path, """
schema_version = 3
name = "x"
model = "openai-codex/gpt-5.6-luna"
[tasks]
path = "."
[[variants]]
id = "a"
"""))
    assert spec.model.full_id() == "openai-codex/gpt-5.6-luna"


def test_paths_resolve_against_spec_dir(tmp_path: Path):
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "index.ts").write_text("x")
    spec = load_experiment(write(tmp_path, """
schema_version = 3
name = "x"
[tasks]
path = "."
[[variants]]
id = "a"
[[variants.extensions]]
path = "./ext"
entry = "index.ts"
"""))
    assert spec.variants[0].extensions[0].path == (tmp_path / "ext").resolve()


def test_schema_version_must_be_3():
    with pytest.raises(ValueError, match="schema_version = 3"):
        ExperimentSpec.model_validate({"schema_version": 2, "name": "x", "tasks": {"path": "/tmp"}, "variants": [{"id": "a"}]})
    spec = ExperimentSpec.model_validate({"schema_version": 3, "name": "x", "tasks": {"path": "/tmp"}, "variants": [{"id": "a"}]})
    assert spec.schema_version == 3


def test_variant_spec_id_rules():
    assert VariantSpec(id="a-1").id == "a-1"
    with pytest.raises(ValueError):
        VariantSpec(id="-leading")


def test_unknown_variant_field_rejected():
    with pytest.raises(ValueError, match="Extra inputs"):
        VariantSpec(id="a", context_files=[{"path": "other.md"}])  # type: ignore[call-arg]
