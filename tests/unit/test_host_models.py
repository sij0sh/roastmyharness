"""Host-configured model acceptance, slicing, materialization, guards."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from roast_my_harness.auth import service as auth_service
from roast_my_harness.auth import staging
from roast_my_harness.errors import AuthError
from roast_my_harness.runner import preflight
from roast_my_harness.spec.hashes import spec_hash
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import (
    ExperimentSpec,
    ModelSpec,
    ResolvedModelSpec,
    TaskSelection,
    VariantSpec,
)

HOST_MODELS = {
    "providers": {
        "z-ai-openai": {
            "baseUrl": "https://api.example/v1",
            "api": "openai-completions",
            "apiKey": "$ZAI_TEST_KEY",
            "models": [{"id": "glm-5.3"}, {"id": "glm-5.2"}],
        },
        "cmd-prov": {
            "baseUrl": "https://api.example/v2",
            "api": "openai-completions",
            "apiKey": "!op read vault-item",
            "models": [{"id": "m1"}],
        },
    }
}

HOST_AUTH = {
    "openai-codex": {
        "type": "oauth",
        "access": "codex-tok",
        "refresh": "r",
        "expires": 4102444800,
        "accountId": "acc",
    },
    "z-ai-openai": {"type": "api_key", "key": "sk-zai"},
}


@pytest.fixture
def host_pi(tmp_path: Path, monkeypatch):
    """A fake host pi home with models.json and auth.json."""
    home = tmp_path / "pi-home"
    home.mkdir()
    (home / "models.json").write_text(json.dumps(HOST_MODELS))
    (home / "auth.json").write_text(json.dumps(HOST_AUTH))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home))
    return home


def _spec(model: ModelSpec) -> ExperimentSpec:
    return ExperimentSpec(
        name="t",
        model=model,
        tasks=TaskSelection(path=Path("/tmp")),
        variants=[VariantSpec(id="a")],
    )


# ------------------------------------------------------------- spec --


def test_codex_default_unchanged():
    m = ModelSpec()
    assert m.provider == "openai-codex"
    assert m.id == "gpt-5.6-luna"
    assert m.full_id() == "openai-codex/gpt-5.6-luna"


def test_host_provider_full_id():
    m = ModelSpec(id="glm-5.3", provider="z-ai-openai")
    assert m.full_id() == "z-ai-openai/glm-5.3"


def test_custom_provider_still_works(tmp_path: Path):
    m = ModelSpec(
        id="my-model",
        provider="custom",
        provider_id="my-prov",
        models_json=tmp_path / "m.json",
    )
    assert m.full_id() == "my-prov/my-model"


def test_custom_still_requires_fields():
    with pytest.raises(ValueError):
        ModelSpec(provider="custom")


# ------------------------------------------------------------- service --


def test_host_provider_block_lookup(host_pi):
    block = auth_service.host_provider_block("z-ai-openai")
    assert block is not None and block["apiKey"] == "$ZAI_TEST_KEY"


def test_host_model_ids(host_pi):
    assert auth_service.host_model_ids("z-ai-openai") == ["glm-5.3", "glm-5.2"]
    assert auth_service.host_model_ids("no-such") == []


def test_provider_credential(host_pi):
    cred = auth_service.provider_credential("z-ai-openai")
    assert cred is not None and cred["key"] == "sk-zai"
    assert auth_service.provider_credential("absent") is None


def test_command_key_detection():
    assert auth_service.has_command_keys({"apiKey": "!run-me"})
    assert not auth_service.has_command_keys({"apiKey": "$VAR"})
    assert not auth_service.has_command_keys({"apiKey": "literal"})
    assert auth_service.has_command_keys(
        {"headers": {"Authorization": "!token.sh"}}
    )


# ------------------------------------------------------------ staging --


def test_stage_home_slices_host_provider(host_pi, tmp_path: Path):
    cached = tmp_path / "cached"
    (cached / "extensions").mkdir(parents=True)
    (cached / "variant.json").write_text("{}")
    spec = _spec(
        ModelSpec(id="glm-5.3", provider="z-ai-openai")
    )
    dest = staging.stage_home(cached, tmp_path / "staged", spec)

    staged = json.loads((dest / "models.json").read_text())
    assert list(staged["providers"]) == ["z-ai-openai"]
    assert staged["providers"]["z-ai-openai"]["apiKey"] == "$ZAI_TEST_KEY"
    assert (os.stat(dest / "models.json").st_mode & 0o777) == 0o600

    staged_auth = json.loads((dest / "auth.json").read_text())
    assert "z-ai-openai" in staged_auth
    assert (os.stat(dest / "auth.json").st_mode & 0o777) == 0o600

    assert not (cached / "models.json").exists()
    assert not (cached / "auth.json").exists()


def test_stage_home_rejects_host_provider_drift(host_pi, tmp_path: Path):
    toml = f"""
schema_version = 1
name = "drift"
[model]
id = "glm-5.3"
provider = "z-ai-openai"
[tasks]
path = "{tmp_path}"
[[variants]]
id = "a"
"""
    spec_path = tmp_path / "drift.toml"
    spec_path.write_text(toml)
    spec = load_experiment(spec_path)
    changed = dict(HOST_MODELS)
    changed["providers"] = dict(HOST_MODELS["providers"])
    changed["providers"]["z-ai-openai"] = {
        **HOST_MODELS["providers"]["z-ai-openai"],
        "models": [{"id": "glm-5.4"}],
    }
    (host_pi / "models.json").write_text(json.dumps(changed))
    cached = tmp_path / "cached"
    (cached / "extensions").mkdir(parents=True)
    (cached / "variant.json").write_text("{}")
    with pytest.raises(AuthError, match="changed since the spec was loaded"):
        staging.stage_home(cached, tmp_path / "staged", spec)


def test_stage_home_host_provider_without_auth_entry(
    host_pi, tmp_path: Path, monkeypatch
):
    auth_file = host_pi / "auth.json"
    auth_file.write_text(json.dumps({"openai-codex": HOST_AUTH["openai-codex"]}))
    cached = tmp_path / "cached"
    (cached / "extensions").mkdir(parents=True)
    (cached / "variant.json").write_text("{}")
    spec = _spec(
        ModelSpec(id="glm-5.3", provider="z-ai-openai")
    )
    dest = staging.stage_home(cached, tmp_path / "staged", spec)
    assert (dest / "models.json").is_file()
    assert (os.stat(dest / "models.json").st_mode & 0o777) == 0o600
    assert not (dest / "auth.json").exists()


def test_stage_home_unknown_provider_fails(host_pi, tmp_path: Path):
    cached = tmp_path / "cached"
    (cached / "extensions").mkdir(parents=True)
    (cached / "variant.json").write_text("{}")
    spec = _spec(
        ModelSpec(id="x", provider="mystery")
    )
    with pytest.raises(Exception, match="not in host pi models.json"):
        staging.stage_home(cached, tmp_path / "staged", spec)


def test_stage_home_codex_path_unchanged(tmp_path: Path, monkeypatch):
    cached = tmp_path / "cached"
    (cached / "extensions").mkdir(parents=True)
    (cached / "variant.json").write_text("{}")
    monkeypatch.setattr(
        staging,
        "codex_credential",
        lambda: {"type": "oauth", "access": "t", "expires": 1},
    )
    spec = _spec(ModelSpec())
    dest = staging.stage_home(cached, tmp_path / "staged", spec)
    staged_auth = json.loads((dest / "auth.json").read_text())
    assert list(staged_auth) == ["openai-codex"]
    assert not (dest / "models.json").exists()


# ------------------------------------------------------ materialize --


def test_load_materializes_host_model(host_pi, tmp_path: Path):
    toml = f"""
schema_version = 1
name = "mat"
pi_version = "0.84.3"
thinking = "high"

[model]
id = "glm-5.3"
provider = "z-ai-openai"
auth = "api_key"

[tasks]
path = "{tmp_path}"

[[variants]]
id = "a"
"""
    p = tmp_path / "exp.toml"
    p.write_text(toml)
    spec = load_experiment(p)
    rm = spec.model.resolved_model
    assert rm is not None
    assert rm.provider == "z-ai-openai"
    assert rm.env_vars == ["ZAI_TEST_KEY"]
    assert len(rm.provider_block_sha256) == 64


def test_load_leaves_codex_unresolved(host_pi, tmp_path: Path):
    toml = f"""
schema_version = 1
name = "mat"
pi_version = "0.84.3"
thinking = "high"

[tasks]
path = "{tmp_path}"

[[variants]]
id = "a"
"""
    p = tmp_path / "exp.toml"
    p.write_text(toml)
    spec = load_experiment(p)
    assert spec.model.resolved_model is None


def test_spec_hash_covers_host_drift(host_pi, tmp_path: Path):
    spec = _spec(
        ModelSpec(id="glm-5.3", provider="z-ai-openai")
    )
    drifted = spec.model_copy(
        update={
            "model": spec.model.model_copy(
                update={
                    "resolved_model": ResolvedModelSpec(
                        provider="z-ai-openai",
                        provider_block_sha256="0" * 64,
                        env_vars=["ZAI_TEST_KEY"],
                    )
                }
            )
        }
    )
    assert spec_hash(spec) != spec_hash(drifted)


# ---------------------------------------------------------- preflight --


def test_preflight_host_provider_passes(host_pi, monkeypatch):
    monkeypatch.setenv("ZAI_TEST_KEY", "value")
    spec = _spec(
        ModelSpec(id="glm-5.3", provider="z-ai-openai")
    )
    (r,) = preflight._auth(spec)
    assert r.status == "pass", r.detail


def test_preflight_unknown_provider_fails(host_pi):
    spec = _spec(ModelSpec(id="x", provider="mystery"))
    (r,) = preflight._auth(spec)
    assert r.status == "fail"
    assert "not in host pi models.json" in r.detail


def test_preflight_unknown_model_id_fails(host_pi):
    spec = _spec(
        ModelSpec(id="bogus", provider="z-ai-openai")
    )
    (r,) = preflight._auth(spec)
    assert r.status == "fail"
    assert "not defined for host provider" in r.detail


def test_preflight_command_keys_rejected(host_pi, monkeypatch):
    monkeypatch.setenv("ZAI_TEST_KEY", "value")
    spec = _spec(ModelSpec(id="m1", provider="cmd-prov"))
    (r,) = preflight._auth(spec)
    assert r.status == "fail"
    assert "!command" in r.detail


def test_preflight_unset_env_fails(host_pi, monkeypatch):
    monkeypatch.delenv("ZAI_TEST_KEY", raising=False)
    spec = _spec(
        ModelSpec(id="glm-5.3", provider="z-ai-openai")
    )
    (r,) = preflight._auth(spec)
    assert r.status == "fail"
    assert "unset env vars" in r.detail
