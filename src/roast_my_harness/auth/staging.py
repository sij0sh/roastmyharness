"""Per-job credential staging. Cached homes stay secret-free.

The runner copies the immutable cached home into <run>/staging/<variant>,
drops in only the selected credential (mode 0600) and, for custom
providers, models.json with $VAR references left unresolved.

Resolve-then-render: provider branches resolve an opaque models-text
payload, one family-keyed renderer stages it. Owner: auth. Decision:
opaque per-kind payload, no unified credential model. Valid subset:
codex stages auth.json only and ignores the agent format (codex x omp
gap stays out of scope); custom/host render pi ($VAR models.json) or
bare-env (models.yml plus model-env.json) via registry credential_format.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.auth import service as auth_service
from roast_my_harness.auth.service import (
    CODEX_PROVIDER,
    codex_credential,
    host_provider_block,
    provider_credential,
)
from roast_my_harness.errors import AuthError
from roast_my_harness.files import atomic_write_text
from roast_my_harness.observability import contains_secret
from roast_my_harness.spec.models import ExperimentSpec


def stage_home(
    cached_home: Path, dest: Path, spec: ExperimentSpec, agent_id: str = "pi"
) -> Path:
    """Copy cached home into a writable staging dir and add credentials."""
    if dest.exists():
        force_remove(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cached_home, dest)
    _make_writable(dest)

    _stage_model(spec, dest, agent_id)
    return dest


def _strip_env_refs(value: str) -> tuple[str, str | None]:
    """pi-style ``$VAR`` ref to omp's bare-name form; returns (value, name)."""
    if value.startswith("$") and len(value) > 1:
        return value[1:], value[1:]
    return value, None


def _stage_model(spec: ExperimentSpec, dest: Path, agent_id: str = "pi") -> None:
    """Stage the model credential/config the spec's provider needs.

    The provider name drives staging, not the auth literal. The renderer
    below is the single agent-format dispatch point.
    """
    model = spec.model
    if model.provider == CODEX_PROVIDER:
        entry = codex_credential()
        if entry is None:
            raise AuthError(
                "no openai-codex credential in pi auth file; run pi /login codex"
            )
        _write_auth_entry(dest, CODEX_PROVIDER, entry)
        return
    provider, models_text = _resolve_models_text(spec)
    _render_models_text(dest, agent_id, models_text)
    entry = provider_credential(provider)
    if entry is not None:
        _write_auth_entry(dest, provider, entry)


def _resolve_models_text(spec: ExperimentSpec) -> tuple[str, str]:
    """Opaque provider payload: (provider, models JSON text)."""
    model = spec.model
    if model.provider == "custom":
        if model.models_json is None:
            raise AuthError("provider 'custom' requires models_json")
        if not model.models_json.is_file():
            raise AuthError(f"models.json missing: {model.models_json}")
        return ("custom", model.models_json.read_text())
    block = host_provider_block(model.provider)
    if block is None:
        raise AuthError(
            f"provider '{model.provider}' not in host pi models.json "
            f"({auth_service.pi_models_file()})"
        )
    resolved = model.resolved_model
    if resolved is not None and resolved.provider == model.provider:
        actual_hash = auth_service.provider_block_hash(block)
        if actual_hash != resolved.provider_block_sha256:
            raise AuthError(
                f"host provider '{model.provider}' changed since the spec was loaded; "
                "reload the experiment before running or resuming"
            )
    return (model.provider, json.dumps({"providers": {model.provider: block}}, indent=2) + "\n")


def _render_models_text(dest: Path, agent_id: str, models_json_text: str) -> None:
    """Single agent-format dispatch point for custom/host payloads."""
    if get_agent(agent_id).credential_format == "bare-env":
        _stage_bare_env_models(dest, models_json_text)
        return
    models_path = dest / "models.json"
    atomic_write_text(models_path, models_json_text, mode=0o600)


def _stage_bare_env_models(dest: Path, models_json_text: str) -> None:
    """Stage models.yml + model-env.json for omp-family arms.

    JSON text is valid YAML, so the staged file keeps the parsed shape.
    ``$VAR`` apiKey/headers refs become bare ``VAR`` names (omp resolves
    those from the environment); the names land in model-env.json for the
    adapter to resolve into the run environment. Values are never staged.
    """
    config = json.loads(models_json_text)
    env_names: list[str] = []
    for provider in (config.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        api_key = provider.get("apiKey")
        if isinstance(api_key, str):
            stripped, name = _strip_env_refs(api_key)
            provider["apiKey"] = stripped
            if name and name not in env_names:
                env_names.append(name)
        headers = provider.get("headers")
        if isinstance(headers, dict):
            for key, value in headers.items():
                if isinstance(value, str):
                    stripped, name = _strip_env_refs(value)
                    headers[key] = stripped
                    if name and name not in env_names:
                        env_names.append(name)
    atomic_write_text(
        dest / "models.yml",
        json.dumps(config, indent=2) + "\n",
        mode=0o600,
    )
    atomic_write_text(
        dest / "model-env.json",
        json.dumps(env_names) + "\n",
        mode=0o600,
    )


def _write_auth_entry(dest: Path, provider: str, entry: dict) -> None:
    auth_path = dest / "auth.json"
    existing: dict = {}
    if auth_path.is_file():
        try:
            existing = json.loads(auth_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing[provider] = entry
    atomic_write_text(
        auth_path,
        json.dumps(existing) + "\n",
        mode=0o600,
    )


def force_remove(path: Path) -> None:
    def _onexc(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(target)
        except OSError:
            pass

    if path.is_dir():
        shutil.rmtree(path, onexc=_onexc)
    elif path.exists():
        path.unlink()


def _make_writable(root: Path) -> None:
    os.chmod(root, 0o755)
    for path in root.rglob("*"):
        os.chmod(path, 0o644 if path.is_file() else 0o755)


def sweep_stale_staging(run_dir: Path) -> list[str]:
    """Scan leftover staging creds from a crashed run, then delete them.

    No handler can run on SIGKILL/power loss, so the next startup must not
    silently delete residue via force_remove. Scan first (persisted by the
    caller), then remove. Returns secret-scan hits (usually paths)."""
    staging_dir = run_dir / "staging"
    if not staging_dir.exists():
        return []
    hits = scan_for_secrets(staging_dir)
    force_remove(staging_dir)
    return hits


def scan_for_secrets(run_dir: Path) -> list[str]:
    """Scan every regular run artifact for known credential prefixes."""
    hits: list[str] = []
    if not run_dir.is_dir():
        return hits
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if 0 in data[:4096]:
            continue
        text = data.decode(errors="ignore")
        if contains_secret(text):
            hits.append(str(path))
    return hits

