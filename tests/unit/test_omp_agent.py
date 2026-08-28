"""oh-my-pi (omp): registry entry, adapter identity, staging, and argv."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from roast_my_harness.adapter import command as cmd
from roast_my_harness.adapter.omp_agent import CONFIG_YML, OmpAgent
from roast_my_harness.adapter.pi_agent import PiAgent
from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.auth import staging
from roast_my_harness.runner import pier
from roast_my_harness.spec.hashes import variant_hash
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def test_omp_registry_entry():
    agent = get_agent("omp")
    assert agent.family == "pi"
    assert agent.import_path == "roast_my_harness.adapter.omp_agent:OmpAgent"
    assert agent.npm_package == "@oh-my-pi/pi-coding-agent"
    assert agent.binary == "omp"
    assert agent.home_env == "PI_CODING_AGENT_DIR"
    assert agent.version_field == "agent_version"
    assert agent.fairness_flags == "--no-skills"
    assert agent.default_version == "18.0.9"


def test_omp_spec_resolution(tmp_path):
    toml = "\n".join(
        [
            "schema_version = 1",
            'name = "omp"',
            "[tasks]",
            'path = "/tmp/does-not-need-to-exist"',
            "[control]",
            "enabled = true",
            "[[variants]]",
            'id = "omp-arm"',
            'agent = "omp"',
        ]
    ) + "\n"
    path = tmp_path / "experiment.toml"
    path.write_text(toml)
    from roast_my_harness.spec.load import load_experiment

    spec = load_experiment(path)
    assert spec.resolved_agents() == {"control": "pi", "omp-arm": "omp"}
    assert spec.agent_version_for("omp") == "18.0.9"
    assert spec.agent_version_for("pi") == spec.pi_version
    assert [(a.id, a.agent) for a in spec.arms()] == [("control", "pi"), ("omp-arm", "omp")]


def test_omp_adapter_identity():
    assert OmpAgent.name() == "omp"
    assert OmpAgent.PACKAGE == "@oh-my-pi/pi-coding-agent"
    assert OmpAgent.BINARY == "omp"
    assert OmpAgent.FAIRNESS == "--no-skills"
    assert PiAgent.BINARY == "pi"


def _manifest(home: Path, **extra) -> Path:
    manifest = {
        "variant_id": "omp-arm",
        "variant_hash": "h",
        "pi_version": "0.84.3",
        "agent": "omp",
        "agent_version": "18.0.9",
        "model_id": "openai-codex/gpt-5.6-luna",
    }
    manifest.update(extra)
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"openai-codex": {"type": "oauth"}}))
    path = home / "variant.json"
    path.write_text(json.dumps(manifest))
    return path


def test_omp_install_spec_installs_bun_first(tmp_path: Path):
    agent = OmpAgent(
        logs_dir=tmp_path, variant_manifest=str(_manifest(tmp_path)),
        thinking="high", model_name="openai-codex/gpt-5.6-luna",
    )
    spec = agent.install_spec()
    runs = [step.run for step in spec.steps]
    assert any("npm install -g bun" in run for run in runs)

    assert any("npm_config_omit=" in run for run in runs)
    assert any("bun@1.4.0" in run for run in runs)
    assert any('bun --version)" = "1.4.0"' in run for run in runs)
    assert any("@oh-my-pi/pi-coding-agent@18.0.9" in run for run in runs)
    assert runs[-1].endswith("&& omp --version")
    assert spec.verification_command == "omp --version"
    bun_index = next(i for i, run in enumerate(runs) if "bun" in run)
    omp_index = next(i for i, run in enumerate(runs) if "omp" in run)
    assert bun_index < omp_index


def test_omp_version_pin_sources(tmp_path: Path):
    agent = OmpAgent(
        logs_dir=tmp_path,
        variant_manifest=str(_manifest(tmp_path / "home1", agent_version="18.1.0")),
        thinking="high", model_name="openai-codex/gpt-5.6-luna",
    )
    assert agent._pi_version == "18.1.0"
    agent = OmpAgent(
        logs_dir=tmp_path,
        variant_manifest=str(_manifest(tmp_path / "home2")),
        thinking="high", model_name="openai-codex/gpt-5.6-luna",
    )
    assert agent._pi_version == "18.0.9"


def test_omp_version_pin_required(tmp_path: Path):
    manifest = {
        "variant_id": "a", "variant_hash": "h", "pi_version": "0.84.3",
        "agent": "omp", "model_id": "p/m",
    }
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="agent_version"):
        OmpAgent(
            logs_dir=tmp_path, variant_manifest=str(path),
            thinking="high", model_name="openai-codex/gpt-5.6-luna",
        )


def test_omp_run_command_uses_omp_fairness(tmp_path: Path):
    command = cmd.build_run_command(
        model="p/m",
        instruction="do it",
        thinking="high",
        skill_paths=[],
        extra_flags=[],
        fairness_flags=OmpAgent.FAIRNESS,
        binary=OmpAgent.BINARY,
    )
    assert command.startswith("export PI_CODING_AGENT_DIR=")
    assert "omp --mode json" in command
    assert "--no-skills" in command
    assert "--no-prompt-templates" not in command
    assert "-nc " not in command and " -nc" not in command


def test_build_run_args_omp(tmp_path, monkeypatch):
    monkeypatch.setattr(pier, "pier_executable", lambda: "pier")
    args = pier.build_run_args(
        task_root=tmp_path, jobs_dir=tmp_path, job_name="j",
        manifest_path=tmp_path / "m.json", model_id="p/m",
        thinking="high", pi_version="18.0.9", n_concurrent=1,
        include_tasks=["t1"], agent="omp",
    )
    assert args[args.index("--agent-import-path") + 1] == get_agent("omp").import_path
    assert "agent_version=18.0.9" in args
    assert "pi_version=18.0.9" not in args


def test_stage_model_omp_writes_bare_env_yml(tmp_path, monkeypatch):
    block = {
        "api": "openai-completions",
        "baseUrl": "https://example/v1",
        "apiKey": "$AZURE_KEY",
        "headers": {"X-Custom": "$CUSTOM_VALUE"},
        "models": [{"id": "m1"}],
    }
    monkeypatch.setattr(
        "roast_my_harness.auth.staging.host_provider_block", lambda provider: dict(block)
    )
    monkeypatch.setattr(
        "roast_my_harness.auth.staging.provider_credential", lambda provider: None
    )
    spec = ExperimentSpec(
        name="t", tasks=TaskSelection(path=tmp_path), variants=[VariantSpec(id="a")],
        model={"provider": "p", "id": "m1"},
    )
    staging._stage_model(spec, tmp_path, agent_id="omp")
    models = json.loads((tmp_path / "models.yml").read_text())
    providers = models["providers"]
    assert list(providers) == ["p"]
    assert providers["p"]["apiKey"] == "AZURE_KEY"
    assert providers["p"]["headers"]["X-Custom"] == "CUSTOM_VALUE"
    env_names = json.loads((tmp_path / "model-env.json").read_text())
    assert env_names == ["AZURE_KEY", "CUSTOM_VALUE"]
    assert not (tmp_path / "models.json").exists()


def test_stage_model_pi_keeps_dollar_refs(tmp_path, monkeypatch):
    block = {
        "api": "openai-completions",
        "baseUrl": "https://example/v1",
        "apiKey": "$AZURE_KEY",
        "models": [{"id": "m1"}],
    }
    monkeypatch.setattr(
        "roast_my_harness.auth.staging.host_provider_block", lambda provider: dict(block)
    )
    monkeypatch.setattr(
        "roast_my_harness.auth.staging.provider_credential", lambda provider: None
    )
    spec = ExperimentSpec(
        name="t", tasks=TaskSelection(path=tmp_path), variants=[VariantSpec(id="a")],
        model={"provider": "p", "id": "m1"},
    )
    staging._stage_model(spec, tmp_path, agent_id="pi")
    models = json.loads((tmp_path / "models.json").read_text())
    assert models["providers"]["p"]["apiKey"] == "$AZURE_KEY"
    assert not (tmp_path / "models.yml").exists()
    assert not (tmp_path / "model-env.json").exists()


def test_stage_model_omp_custom_provider(tmp_path):
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps(
        {"providers": {"prov": {"baseUrl": "https://x/v1", "apiKey": "$MY_KEY",
                                 "models": [{"id": "m"}]}}}
    ))
    spec = ExperimentSpec(
        name="t", tasks=TaskSelection(path=tmp_path), variants=[VariantSpec(id="a")],
        model={"provider": "custom", "provider_id": "prov", "models_json": models_json},
    )
    staging._stage_model(spec, tmp_path, agent_id="omp")
    models = json.loads((tmp_path / "models.yml").read_text())
    assert models["providers"]["prov"]["apiKey"] == "MY_KEY"
    assert json.loads((tmp_path / "model-env.json").read_text()) == ["MY_KEY"]


def test_omp_config_yml_disables_context_providers():
    parsed = {}
    in_list = False
    for line in CONFIG_YML.splitlines():
        if line.startswith("disabledProviders:"):
            in_list = True
            parsed["disabledProviders"] = []
            continue
        if in_list and line.strip().startswith("- "):
            parsed["disabledProviders"].append(line.strip()[2:])
    assert "agents-md" in parsed["disabledProviders"]
    assert "claude" in parsed["disabledProviders"]


def test_omp_env_resolution_from_model_env(tmp_path: Path, monkeypatch):
    (tmp_path / "model-env.json").write_text(json.dumps(["MY_KEY"]))

    def load_config():
        return None

    agent = types.SimpleNamespace(
        _home_dir=tmp_path,
        _load_models_config=load_config,
        _get_env=lambda name: {"MY_KEY": "secret-value"}.get(name),
    )
    resolved = OmpAgent._referenced_env_vars(agent)
    assert resolved == {"MY_KEY": "secret-value"}


def test_omp_hash_differs_from_pi_same_variant():
    variant = VariantSpec(id="a")
    omp_hash = variant_hash(variant, "0.84.3", agent="omp", agent_version="18.0.9")
    pi_hash = variant_hash(variant, "0.84.3", agent="pi", agent_version="0.84.3")
    assert omp_hash != pi_hash


def test_omp_model_validation_reads_models_yml(tmp_path: Path):
    """Regression: omp stages models.yml; model validation must find it."""
    (tmp_path / "models.yml").write_text(json.dumps(
        {"providers": {"p": {"baseUrl": "https://x/v1", "models": [{"id": "m1"}]}}}
    ))
    obj = types.SimpleNamespace(
        _home_dir=tmp_path,
        model_name="p/m1",
        _load_models_config=lambda: json.loads((tmp_path / "models.yml").read_text()),
        _staged_providers=lambda: {"p"},
    )
    OmpAgent._validate_model(obj)
    path = OmpAgent._models_config_path(obj)
    assert path == tmp_path / "models.yml"


def test_omp_model_validation_still_tolerates_models_json(tmp_path: Path):
    (tmp_path / "models.json").write_text(json.dumps(
        {"providers": {"p": {"baseUrl": "https://x/v1", "models": [{"id": "m1"}]}}}
    ))
    obj = types.SimpleNamespace(
        _home_dir=tmp_path,
        model_name="p/m1",
        _load_models_config=lambda: json.loads((tmp_path / "models.json").read_text()),
        _staged_providers=lambda: {"p"},
    )
    assert OmpAgent._models_config_path(obj) == tmp_path / "models.json"
    OmpAgent._validate_model(obj)
