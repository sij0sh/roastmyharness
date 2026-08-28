"""Agent registry, per-arm agent resolution, and pier argv plumbing."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from roast_my_harness.adapter import registry as agent_registry
from roast_my_harness.adapter.pi_agent import load_variant_manifest
from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS
from roast_my_harness.homes.builder import build_home
from roast_my_harness.runner import pier
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import (
    ControlSpec,
    ExperimentSpec,
    LocalExtension,
    NpmPiInstall,
    TaskSelection,
    VariantSpec,
)


def spec_toml(top: str = "", control: str = "", variants: str = 'id = "a"') -> str:
    """A valid experiment TOML with optional top-level, control, and variant keys.

    Top-level keys precede every table header, as TOML requires.
    """
    parts = ["schema_version = 1", 'name = "agents"']
    if top:
        parts.append(top)
    parts += ["[tasks]", 'path = "/tmp/does-not-need-to-exist"']
    if control:
        parts += ["[control]", control]
    if variants:
        parts += ["[[variants]]", variants]
    return "\n".join(parts) + "\n"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "experiment.toml"
    path.write_text(text)
    return path


def direct_spec(**kwargs) -> ExperimentSpec:
    return ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=Path("/tmp/x")),
        **kwargs,
    )


def test_pi_registry_entry():
    agent = get_agent("pi")
    assert agent.family == "pi"
    assert agent.import_path == "roast_my_harness.adapter.pi_agent:PiAgent"
    assert agent.npm_package == "@earendil-works/pi-coding-agent"
    assert agent.binary == "pi"
    assert agent.home_env == "PI_CODING_AGENT_DIR"
    assert agent.version_field == "pi_version"
    assert agent.fairness_flags == FAIRNESS_FLAGS
    assert agent.default_version == DEFAULT_PI_VERSION


def test_get_agent_unknown_lists_known():
    with pytest.raises(ValueError, match="known agents: pi"):
        get_agent("nope")


def test_registry_import_paths_resolve():
    """Every registered import path names a real adapter class."""
    for agent in agent_registry.AGENTS.values():
        module_name, _, class_name = agent.import_path.partition(":")
        cls = getattr(importlib.import_module(module_name), class_name)
        assert cls.name() == agent.id


def test_default_agent_is_pi(tmp_path: Path):
    spec = load_experiment(write(tmp_path, spec_toml(control="enabled = true")))
    assert spec.agent == "pi"
    assert spec.resolved_agents() == {"control": "pi", "a": "pi"}
    assert spec.agent_version_for("pi") == DEFAULT_PI_VERSION


def test_variant_agent_override(tmp_path: Path):
    spec = load_experiment(write(tmp_path, spec_toml(variants='id = "b"\nagent = "pi"')))
    assert spec.resolved_agents() == {"b": "pi"}


def test_unknown_agent_on_spec_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="unknown agent"):
        load_experiment(write(tmp_path, spec_toml(top='agent = "claude"')))


def test_unknown_agent_on_variant_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="unknown agent"):
        load_experiment(write(tmp_path, spec_toml(variants='id = "b"\nagent = "omp"')))


def test_unknown_agent_on_control_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="unknown agent"):
        load_experiment(write(tmp_path, spec_toml(control='enabled = true\nagent = "codex"')))


def test_control_arm_inherits_spec_agent():
    spec = direct_spec(control=ControlSpec(), variants=[VariantSpec(id="a")])
    assert spec.resolved_agents() == {"control": "pi", "a": "pi"}
    assert [arm.agent for arm in spec.arms()] == ["pi", "pi"]


def test_disabled_control_not_resolved():
    spec = direct_spec(control=ControlSpec(enabled=False), variants=[VariantSpec(id="a")])
    assert spec.resolved_agents() == {"a": "pi"}


def test_agent_version_must_be_exact_pin(tmp_path: Path):
    with pytest.raises(Exception, match="agent_version"):
        load_experiment(write(tmp_path, spec_toml(top='agent_version = "1.2; rm -rf"')))


def test_agent_version_conflicts_with_pi_version(tmp_path: Path):
    with pytest.raises(Exception, match="conflicts"):
        load_experiment(write(tmp_path, spec_toml(top='agent_version = "0.99.0"')))


def test_agent_version_matching_pi_version_allowed(tmp_path: Path):
    spec = load_experiment(
        write(
            tmp_path,
            spec_toml(top='agent_version = "0.84.3"\npi_version = "0.84.3"'),
        )
    )
    assert spec.agent_version_for("pi") == "0.84.3"


def test_agent_version_defaults_to_pi_version():
    spec = direct_spec(variants=[VariantSpec(id="a")])
    assert spec.agent_version_for("pi") == spec.pi_version


@pytest.fixture
def fake_independent_agent(monkeypatch):
    fake = agent_registry.AgentDef(
        id="fake",
        family="independent",
        import_path="roast_my_harness.adapter.pi_agent:PiAgent",
        npm_package="fake/fake",
        binary="fake",
        home_env="FAKE_HOME",
        version_field="agent_version",
        fairness_flags="",
        default_version="1.0.0",
    )
    monkeypatch.setattr(agent_registry, "AGENTS", {**agent_registry.AGENTS, "fake": fake})
    return fake


def test_non_pi_family_rejects_pi_features(fake_independent_agent):
    variant = VariantSpec(
        id="f",
        agent="fake",
        extensions=[LocalExtension(kind="local", path=Path("/tmp/fake-ext"), entry="index.ts")],
    )
    with pytest.raises(Exception, match="pi-only features: extensions"):
        direct_spec(variants=[variant])


def test_non_pi_family_rejects_skills_and_flags(fake_independent_agent):
    variant = VariantSpec(id="f", agent="fake", pi_flags=["--append-system-prompt=hi"])
    with pytest.raises(Exception, match="pi-only features: pi_flags"):
        direct_spec(variants=[variant])


def test_non_pi_family_rejects_npm_pi_install(fake_independent_agent):
    variant = VariantSpec(
        id="f",
        agent="fake",
        setup=[NpmPiInstall(handler="npm_pi_install", package="left-pad@1.3.0")],
    )
    with pytest.raises(Exception, match="npm_pi_install setup"):
        direct_spec(variants=[variant])


def test_pi_family_allows_pi_features(tmp_path):
    src = tmp_path / "ext-src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "index.ts").write_text("1")
    spec = direct_spec(
        variants=[
            VariantSpec(
                id="p",
                extensions=[LocalExtension(kind="local", path=src, entry="src/index.ts")],
            )
        ]
    )
    assert spec.resolved_agents() == {"p": "pi"}


def test_build_run_args_default_matches_pi_registry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pier, "pier_executable", lambda: "pier")

    def run(**kwargs):
        return pier.build_run_args(
            task_root=tmp_path / "tasks",
            jobs_dir=tmp_path / "jobs",
            job_name="exp-a",
            manifest_path=tmp_path / "m.json",
            model_id="openai-codex/gpt-5.6-luna",
            thinking="high",
            pi_version="0.84.3",
            n_concurrent=2,
            include_tasks=["t1"],
            **kwargs,
        )

    assert run() == run(agent="pi")
    args = run()
    assert args[args.index("--agent-import-path") + 1] == get_agent("pi").import_path
    assert "pi_version=0.84.3" in args
    assert "agent_version=0.84.3" not in args


def test_build_run_args_unknown_agent_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pier, "pier_executable", lambda: "pier")
    with pytest.raises(ValueError, match="unknown agent"):
        pier.build_run_args(
            task_root=tmp_path,
            jobs_dir=tmp_path,
            job_name="j",
            manifest_path=tmp_path / "m.json",
            model_id="p/m",
            thinking="high",
            pi_version="1.0.0",
            n_concurrent=1,
            agent="nope",
        )


def test_variant_hash_distinguishes_agent_identity():
    variant = VariantSpec(id="a")
    base = variant_hash(variant, "0.84.3")
    assert base == variant_hash(variant, "0.84.3")
    assert base != variant_hash(variant, "0.84.3", agent="omp", agent_version="1.0.0")
    assert base != variant_hash(variant, "0.84.3", agent="pi", agent_version="0.90.0")


def test_build_home_manifest_records_agent(tmp_path: Path):
    spec = direct_spec(variants=[VariantSpec(id="bareish")])
    home = build_home(VariantSpec(id="bareish"), spec, tmp_path / "homes")
    manifest = json.loads((home.path / "variant.json").read_text())
    assert manifest["agent"] == "pi"
    assert manifest["agent_version"] == spec.pi_version
    build_manifest = json.loads((home.path / "build-manifest.json").read_text())
    assert build_manifest["agent"] == "pi"
    assert build_manifest["agent_version"] == spec.pi_version


def test_adapter_manifest_requires_agent_keys(tmp_path: Path):
    base = {
        "variant_id": "a",
        "variant_hash": "h",
        "pi_version": "0.84.3",
        "model_id": "p/m",
    }
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="agent"):
        load_variant_manifest(str(path))
    base.update({"agent": "pi", "agent_version": "0.84.3"})
    path.write_text(json.dumps(base))
    manifest = load_variant_manifest(str(path))
    assert manifest["agent"] == "pi"
