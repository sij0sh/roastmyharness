"""Adapter checks that run on the host (pier is a dev dependency here)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.adapter.pi_agent import PiAgent

pier_network = pytest.importorskip(
    "pier.models.agent.network"
)


def _agent_obj(home_dir: Path, manifest: dict, model_name: str):
    """A PiAgent-shaped namespace without pier's full constructor."""
    import types

    def load_config():
        path = home_dir / "models.json"
        return json.loads(path.read_text()) if path.is_file() else None

    return types.SimpleNamespace(
        _manifest=manifest,
        _home_dir=home_dir,
        model_name=model_name,
        _load_models_config=load_config,
    )


def test_network_allowlist_default_codex(tmp_path: Path):
    """Regression: default openai-codex path must not raise (RP1)."""
    obj = _agent_obj(tmp_path, {"egress_urls": []}, "openai-codex/gpt-5.6-luna")
    allow = PiAgent.network_allowlist(obj)
    assert isinstance(allow, pier_network.NetworkAllowlist)


def test_network_allowlist_merges_staged_and_manifest_urls(tmp_path: Path):
    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "custom-prov": {
                        "baseUrl": "https://api.example/v1",
                        "models": [{"id": "m1"}],
                    }
                }
            }
        )
    )
    obj = _agent_obj(
        tmp_path,
        {"egress_urls": ["https://github.com"]},
        "custom-prov/m1",
    )
    allow = PiAgent.network_allowlist(obj)
    assert allow is not None


def _pi_manifest(home, **extra):
    manifest = {
        "variant_id": "v",
        "variant_hash": "h",
        "pi_version": "latest",
        "agent": "pi",
        "agent_version": "latest",
        "model_id": "openai-codex/gpt-5.6-luna",
    }
    manifest.update(extra)
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"openai-codex": {"type": "oauth"}}))
    path = home / "variant.json"
    path.write_text(json.dumps(manifest))
    return path


def _pi_agent(tmp_path, name, pi_version=None, **extra):
    args = {}
    if pi_version is not None:
        args["pi_version"] = pi_version
    return PiAgent(
        logs_dir=tmp_path,
        variant_manifest=str(_pi_manifest(tmp_path / name, **extra)),
        thinking="high",
        model_name="openai-codex/gpt-5.6-luna",
        **args,
    )


def test_install_spec_pins_exact_version(tmp_path: Path):
    agent = _pi_agent(tmp_path, "pinned", pi_version="0.85.1")
    runs = [step.run for step in agent.install_spec().steps]
    assert any("@earendil-works/pi-coding-agent@0.85.1" in run for run in runs)


def test_install_spec_latest_installs_unpinned(tmp_path: Path):
    agent = _pi_agent(tmp_path, "floated")
    assert agent._pi_version == "latest"
    runs = [step.run for step in agent.install_spec().steps]
    assert any("npm install -g @earendil-works/pi-coding-agent " in run for run in runs)
    assert not any("@earendil-works/pi-coding-agent@" in run for run in runs)


def test_git_identity_command_deterministic():
    from roast_my_harness.adapter.pi_agent import git_identity_command

    first, second = git_identity_command(), git_identity_command()
    assert first == second
    assert "git config --global user.name roastmyharness" in first
    assert "git config --global user.email roastmyharness@local" in first
    assert "git config --global --add safe.directory /app" in first


async def test_setup_git_identity_runs_as_agent_user(tmp_path: Path):
    """Regression: agent commits failed for missing identity, emptying patches.

    The identity step must run as the agent user (the committing user), not
    root, or the config lands in the wrong home.
    """
    agent = _pi_agent(tmp_path, "gitid")
    calls: list[tuple[str, dict]] = []

    async def fake_exec_as_agent(environment, command, **kwargs):
        calls.append((command, kwargs))
        return None

    agent.exec_as_agent = fake_exec_as_agent  # type: ignore[method-assign]
    sentinel = object()
    await agent._ensure_git_identity(sentinel)
    assert len(calls) == 1
    command, _ = calls[0]
    assert "user.name roastmyharness" in command
    assert "user.email roastmyharness@local" in command
