"""Per-job credential staging. Cached homes stay secret-free.

The runner copies the immutable cached home into <run>/staging/<variant>,
drops in only the selected credential (mode 0600) and, for custom
providers, models.json with $VAR references left unresolved.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from roast_my_harness import __version__
from roast_my_harness.auth import service as auth_service
from roast_my_harness.auth.service import CODEX_PROVIDER, codex_credential, host_provider_block, provider_credential
from roast_my_harness.errors import AuthError
from roast_my_harness.spec.models import ExperimentSpec

SECRET_PREFIXES = ("sk-", "Bearer ")


def stage_home(
    cached_home: Path, dest: Path, spec: ExperimentSpec
) -> Path:
    """Copy cached home into a writable staging dir and add credentials."""
    if dest.exists():
        force_remove(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cached_home, dest)
    _make_writable(dest)

    _stage_model(spec, dest)
    return dest


def _stage_model(spec: ExperimentSpec, dest: Path) -> None:
    """Stage the model credential/config the spec's provider needs.

    The provider name drives staging, not the auth literal.
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
    if model.provider == "custom":
        if model.models_json is None:
            raise AuthError("provider 'custom' requires models_json")
        if not model.models_json.is_file():
            raise AuthError(f"models.json missing: {model.models_json}")
        shutil.copy2(model.models_json, dest / "models.json")
        return
    # Host-configured provider: slice its block from the host models.json
    # and its auth entry when one exists.
    block = host_provider_block(model.provider)
    if block is None:
        raise AuthError(
            f"provider '{model.provider}' not in host pi models.json "
            f"({auth_service.pi_models_file()})"
        )
    (dest / "models.json").write_text(
        json.dumps({"providers": {model.provider: block}}, indent=2) + "\n"
    )
    entry = provider_credential(model.provider)
    if entry is not None:
        _write_auth_entry(dest, model.provider, entry)


def _write_auth_entry(dest: Path, provider: str, entry: dict) -> None:
    auth_path = dest / "auth.json"
    existing: dict = {}
    if auth_path.is_file():
        try:
            existing = json.loads(auth_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing[provider] = entry
    auth_path.write_text(json.dumps(existing) + "\n")
    os.chmod(auth_path, 0o600)


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


def scan_for_secrets(run_dir: Path) -> list[str]:
    """Scan run artifacts for known credential prefixes. Report paths only."""
    hits: list[str] = []
    if not run_dir.is_dir():
        return hits
    for path in run_dir.rglob("*.log"):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if any(prefix in text for prefix in SECRET_PREFIXES):
            hits.append(str(path))
    return hits


def staging_note() -> str:
    return f"roast-my-harness {__version__}: staging is per-job; discard after run"
