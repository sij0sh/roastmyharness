"""Claude Code: registry entry, adapter identity, staging, and argv."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.adapter.claude_agent import (
    RobmyClaude,
    claude_config_dir,
)
from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.runner import pier


def test_claude_registry_entry():
    agent = get_agent("claude")
    assert agent.family == "claude-code"
    assert agent.import_path == "roast_my_harness.adapter.claude_agent:RobmyClaude"
    assert agent.npm_package == "@anthropic-ai/claude-code"
    assert agent.binary == "claude"
    assert agent.home_env == "CLAUDE_CONFIG_DIR"
    assert agent.version_field == "agent_version"
    assert agent.fairness_flags == "--strict-mcp-config"
    assert agent.default_version == "2.1.251"


def test_claude_spec_resolution(tmp_path):
    toml = "\n".join(
        [
            "schema_version = 1",
            'name = "claude-smoke"',
            "[tasks]",
            'path = "/tmp/does-not-need-to-exist"',
            "[control]",
            "enabled = true",
            "[[variants]]",
            'id = "claude-arm"',
            'agent = "claude"',
            "[variants.model]",
            'provider = "azure-anthropic-gateway"',
            'id = "claude-sonnet-5"',
        ]
    ) + "\n"
    path = tmp_path / "experiment.toml"
    path.write_text(toml)
    from roast_my_harness.spec.load import load_experiment

    spec = load_experiment(path)
    assert spec.resolved_agents() == {"control": "pi", "claude-arm": "claude"}
    assert spec.agent_version_for("claude") == "2.1.251"
    arm = spec.arms()[1]
    assert spec.model_for(arm).full_id() == "azure-anthropic-gateway/claude-sonnet-5"
    assert spec.model_for(spec.arms()[0]).full_id() == spec.model.full_id()


def test_claude_pier_args_strip_provider_prefix():
    argv = pier.build_run_args(
        task_root=Path("/tmp/tasks"),
        jobs_dir=Path("/tmp/jobs"),
        job_name="exp-claude",
        manifest_path=Path("/tmp/staging/variant.json"),
        model_id="azure-anthropic-gateway/claude-sonnet-5",
        thinking="high",
        pi_version="2.1.251",
        n_concurrent=1,
        agent="claude",
    )
    assert "--agent-import-path" in argv
    assert "roast_my_harness.adapter.claude_agent:RobmyClaude" in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert "agent_version=2.1.251" in argv
    argv_pi = pier.build_run_args(
        task_root=Path("/tmp/tasks"),
        jobs_dir=Path("/tmp/jobs"),
        job_name="exp-pi",
        manifest_path=Path("/tmp/staging/variant.json"),
        model_id="openai-codex/gpt-5.6-luna",
        thinking="high",
        pi_version="0.84.3",
        n_concurrent=1,
        agent="pi",
    )
    assert argv_pi[argv_pi.index("--model") + 1] == "openai-codex/gpt-5.6-luna"


def _manifest(home: Path, **extra) -> Path:
    manifest = {
        "variant_id": "claude-arm",
        "variant_hash": "h",
        "pi_version": "0.84.3",
        "agent": "claude",
        "agent_version": "2.1.251",
        "model_id": "azure-anthropic-gateway/claude-sonnet-5",
    }
    manifest.update(extra)
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(
        json.dumps({"model": "claude-sonnet-5", "permissions": {}})
    )
    path = home / "variant.json"
    path.write_text(json.dumps(manifest))
    return path


def _agent(tmp_path: Path, monkeypatch=None, **extra) -> RobmyClaude:
    if monkeypatch is not None:
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    manifest_path = _manifest(home, **extra.pop("manifest_extra", {}))
    (home / "env.json").write_text(
        json.dumps(
            {
                "ANTHROPIC_BASE_URL": "https://gw.example.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "tok",
            }
        )
    )
    return RobmyClaude(
        logs_dir=tmp_path / "logs",
        variant_manifest=str(manifest_path),
        thinking="high",
        model_name="claude-sonnet-5",
        **extra,
    )


def test_claude_adapter_identity():
    assert RobmyClaude.name() == "claude"
    assert RobmyClaude.FAIRNESS == "--strict-mcp-config"


def test_claude_requires_manifest(tmp_path: Path):
    with pytest.raises(ValueError, match="variant_manifest"):
        RobmyClaude(logs_dir=tmp_path, thinking="high")


def test_claude_rejects_wrong_agent_manifest(tmp_path: Path):
    home = tmp_path / "home"
    manifest_path = _manifest(home, agent="omp")
    with pytest.raises(ValueError, match="is not 'claude'"):
        RobmyClaude(
            logs_dir=tmp_path / "logs",
            variant_manifest=str(manifest_path),
            thinking="high",
            model_name="claude-sonnet-5",
        )


def test_claude_model_validation(tmp_path: Path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.model_name == "claude-sonnet-5"
    assert agent._extra_env["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert agent._extra_env["ANTHROPIC_BASE_URL"] == "https://gw.example.com/anthropic"


def test_claude_model_validation_settings_pin(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    manifest_path = _manifest(home)
    (home / "settings.json").write_text(json.dumps({"model": "claude-opus-5"}))
    (home / "env.json").write_text(json.dumps({"ANTHROPIC_AUTH_TOKEN": "tok"}))
    with pytest.raises(ValueError, match="pins model"):
        RobmyClaude(
            logs_dir=tmp_path / "logs",
            variant_manifest=str(manifest_path),
            thinking="high",
            model_name="claude-sonnet-5",
        )


def test_claude_model_validation_requires_credential(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    manifest_path = _manifest(home)
    with pytest.raises(ValueError, match="no Anthropic credential"):
        RobmyClaude(
            logs_dir=tmp_path / "logs",
            variant_manifest=str(manifest_path),
            thinking="high",
            model_name="claude-sonnet-5",
        )


def test_claude_install_spec_pins_npm(tmp_path: Path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    spec = agent.install_spec()
    runs = [step.run for step in spec.steps]
    assert any("nodejs npm" in run for run in runs)
    assert any(
        "npm install -g @anthropic-ai/claude-code@2.1.251" in run for run in runs
    )
    assert any("claude --version" in run for run in runs)
    assert spec.verification_command == (
        'export PATH="$HOME/.local/bin:$PATH"; claude --version'
    )


def test_claude_fairness_flag_prepended(tmp_path: Path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    flags = agent.build_cli_flags()
    assert flags.startswith("--strict-mcp-config")
    assert "--effort high" in flags
    assert "--thinking enabled" in flags


def test_claude_thinking_off_maps_to_disabled(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    manifest_path = _manifest(home)
    (home / "env.json").write_text(json.dumps({"ANTHROPIC_AUTH_TOKEN": "tok"}))
    agent = RobmyClaude(
        logs_dir=tmp_path / "logs",
        variant_manifest=str(manifest_path),
        thinking="off",
        model_name="claude-sonnet-5",
    )
    assert "--thinking disabled" in agent.build_cli_flags()


def test_claude_network_allowlist(tmp_path: Path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    allowlist = agent.network_allowlist()
    domains = getattr(allowlist, "domains", [])
    assert any("gw.example.com" in d for d in domains)

    assert any("npmjs" in d for d in domains)


def test_claude_config_dir_layout(tmp_path: Path):
    assert claude_config_dir(Path("/job/logs")) == Path("/job/logs/agent/sessions")


def test_claude_find_session_dir(tmp_path: Path):
    agent = _agent(tmp_path)
    config = claude_config_dir(tmp_path / "logs")
    project = config / "projects" / "-task"
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text("{}\n")
    assert agent._find_session_dir(config) == project
    assert agent._find_session_dir(tmp_path / "logs") is None
