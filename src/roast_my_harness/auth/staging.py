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
from roast_my_harness.auth.service import CODEX_PROVIDER, codex_credential
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

    if spec.model.auth == "codex":
        entry = codex_credential()
        if entry is None:
            raise AuthError(
                "no openai-codex credential in pi auth file; run pi /login codex"
            )
        (dest / "auth.json").write_text(
            json.dumps({CODEX_PROVIDER: entry}) + "\n"
        )
        os.chmod(dest / "auth.json", 0o600)
    elif spec.model.models_json is not None:
        if not spec.model.models_json.is_file():
            raise AuthError(f"models.json missing: {spec.model.models_json}")
        shutil.copy2(spec.model.models_json, dest / "models.json")
    return dest


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
