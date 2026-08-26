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
