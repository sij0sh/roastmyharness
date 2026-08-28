"""Spec loading and validation rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import (
    ExperimentSpec,
    NpmExtension,
    NpmPiInstall,
    VariantSpec,
)

MINIMAL = """
schema_version = 1
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
    assert spec.pi_version == "0.84.3"
    assert spec.model.full_id() == "openai-codex/gpt-5.6-luna"
    assert [v.id for v in spec.arms()] == ["bareish"]


def test_control_arm_added(tmp_path: Path):
    spec = load_experiment(write(tmp_path, MINIMAL + "\n[control]\nenabled = true\n"))
    assert [v.id for v in spec.arms()] == ["control", "bareish"]


def test_validation_error_is_single_line(tmp_path: Path):
    path = write(tmp_path, MINIMAL + "\nbogus_field = true\n")
    with pytest.raises(SpecError) as excinfo:
        load_experiment(path)
    message = str(excinfo.value)
    assert "\n" not in message
    assert "unknown field" in message


def test_reserved_control_id_rejected(tmp_path: Path):
    with pytest.raises(SpecError, match="reserved"):
        load_experiment(write(tmp_path, """
schema_version = 1
name = "x"
[[variants]]
id = "control"
"""))


def test_duplicate_variant_ids_rejected(tmp_path: Path):
    with pytest.raises(SpecError, match="duplicate"):
        load_experiment(write(tmp_path, """
schema_version = 1
name = "x"
[[variants]]
id = "a"
[[variants]]
id = "a"
"""))


def test_unsafe_variant_id_rejected(tmp_path: Path):
    with pytest.raises(SpecError):
        load_experiment(write(tmp_path, """
schema_version = 1
name = "x"
[[variants]]
id = "Bad Id!"
"""))


def test_pi_version_rejects_shell_syntax(tmp_path: Path):
    with pytest.raises(SpecError):
        load_experiment(
            write(
                tmp_path,
                """
schema_version = 1
name = "x"
pi_version = "0.84.3; echo leaked"
[tasks]
path = "."
[[variants]]
id = "a"
""",
            )
        )


def test_every_fairness_flag_is_reserved():
    """Fairness flags must stay reserved, or arms could diverge on them."""
    from roast_my_harness.adapter.command import FAIRNESS_FLAGS
    from roast_my_harness.spec.models import RESERVED_PI_FLAGS

    fairness = frozenset(FAIRNESS_FLAGS.split())
    assert fairness
    assert fairness <= RESERVED_PI_FLAGS


def test_needs_at_least_one_arm(tmp_path: Path):
    with pytest.raises(SpecError, match="at least one"):
        load_experiment(write(tmp_path, """
schema_version = 1
name = "x"
[tasks]
path = "."
"""))


def test_npm_requires_exact_pin():
    assert NpmExtension(kind="npm", package="context-mode@1.0.169").package
    with pytest.raises(ValueError):
        NpmExtension(kind="npm", package="context-mode")
    with pytest.raises(ValueError):
        NpmExtension(kind="npm", package="context-mode@^1.0.0")
    with pytest.raises(ValueError):
        NpmExtension(kind="npm", package="context-mode@1.0.0; echo leaked")


def test_npm_setup_requires_exact_pin():
    assert NpmPiInstall(
        handler="npm_pi_install", package="@scope/package@1.2.3"
    ).package
    with pytest.raises(ValueError):
        NpmPiInstall(handler="npm_pi_install", package="package@latest")


def test_paths_resolve_against_spec_dir(tmp_path: Path):
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "index.ts").write_text("x")
    spec = load_experiment(write(tmp_path, """
schema_version = 1
name = "x"
[tasks]
path = "."
[[variants]]
id = "a"
[[variants.extensions]]
kind = "local"
path = "./ext"
entry = "index.ts"
"""))
    assert spec.variants[0].extensions[0].path == (tmp_path / "ext").resolve()


def test_schema_version_must_be_1():
    with pytest.raises(ValueError):
        ExperimentSpec.model_validate(
            {"schema_version": 2, "name": "x", "tasks": {"path": "/tmp"},
             "variants": [{"id": "a"}]}
        )


def test_variant_spec_id_rules():
    assert VariantSpec(id="a-1").id == "a-1"
    with pytest.raises(ValueError):
        VariantSpec(id="-leading")
