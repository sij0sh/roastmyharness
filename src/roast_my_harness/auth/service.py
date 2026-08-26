"""Credential inspection. Never prints or stores token values."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC
from pathlib import Path
from typing import Any

from roast_my_harness.errors import AuthError

CODEX_PROVIDER = "openai-codex"


def pi_auth_dir() -> Path:
    override = os.environ.get("PI_CODING_AGENT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pi" / "agent"


def pi_auth_file() -> Path:
    return pi_auth_dir() / "auth.json"


def load_auth_file() -> dict[str, Any]:
    path = pi_auth_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise AuthError(f"cannot read pi auth file {path}: {e}") from e
    if not isinstance(data, dict):
        raise AuthError(f"pi auth file {path} is not a JSON object")
    return data


def codex_credential() -> dict[str, Any] | None:
    """The openai-codex entry, shape-validated, or None."""
    entry = load_auth_file().get(CODEX_PROVIDER)
    if not isinstance(entry, dict):
        return None
    if not entry.get("access") or not entry.get("type"):
        return None
    return entry


def _expiry_epoch_seconds(expires: Any) -> float | None:
    """Normalize pi's epoch timestamps; pi stores milliseconds."""
    if not isinstance(expires, (int, float)) or expires <= 0:
        return None
    return expires / 1000.0 if expires > 1e12 else float(expires)


def credential_expiry(entry: dict[str, Any]) -> str:
    """Human expiry line without token values."""
    from datetime import datetime

    seconds = _expiry_epoch_seconds(entry.get("expires"))
    if seconds is not None:
        when = datetime.fromtimestamp(seconds, tz=UTC)
        return f", expires {when.isoformat(timespec='seconds')}"
    return ", expiry unknown"


def refresh_hint(entry: dict[str, Any]) -> bool:
    """True when the credential looks expired (host should re-login)."""
    from datetime import datetime

    seconds = _expiry_epoch_seconds(entry.get("expires"))
    if seconds is not None:
        return datetime.now(UTC).timestamp() >= seconds
    return False


def pi_models_file() -> Path:
    return pi_auth_dir() / "models.json"


def load_host_models() -> dict[str, Any]:
    """The host pi models.json providers, or {} when absent."""
    path = pi_models_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise AuthError(f"cannot read pi models file {path}: {e}") from e
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise AuthError(f"pi models file {path} has no providers object")
    return providers


def host_provider_block(provider: str) -> dict[str, Any] | None:
    """A provider block from the host models.json, or None."""
    block = load_host_models().get(provider)
    return block if isinstance(block, dict) else None


def host_model_ids(provider: str) -> list[str]:
    """Model ids declared for a host provider (may be empty)."""
    block = host_provider_block(provider)
    if block is None:
        return []
    models = block.get("models", [])
    if not isinstance(models, list):
        return []
    return [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]


def provider_credential(provider: str) -> dict[str, Any] | None:
    """Any auth.json entry for a provider, shape-checked, or None."""
    entry = load_auth_file().get(provider)
    if not isinstance(entry, dict):
        return None
    if not entry.get("type"):
        return None
    return entry


def has_command_keys(block: dict[str, Any]) -> bool:
    """True when any apiKey/headers value runs a host command (! prefix)."""
    candidates: list[Any] = [block.get("apiKey")]
    headers = block.get("headers")
    if isinstance(headers, dict):
        candidates.extend(headers.values())
    model_overrides = block.get("modelOverrides")
    if isinstance(model_overrides, dict):
        for override in model_overrides.values():
            if isinstance(override, dict):
                candidates.append(override.get("apiKey"))
    for value in candidates:
        if isinstance(value, str) and value.startswith("!"):
            return True
    return False


def missing_env_vars(models_json: Path) -> list[str]:
    """Env var names referenced as $VAR / ${VAR} in models.json that are unset.

    Names only; values are never read for reporting.
    """
    try:
        text = models_json.read_text()
    except OSError as e:
        raise AuthError(f"cannot read models.json {models_json}: {e}") from e
    names = sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", text)))
    return [n for n in names if not os.environ.get(n)]
