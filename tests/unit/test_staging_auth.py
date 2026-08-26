"""Credential staging: secret-free cached homes, 0600 auth, secret scan."""

from __future__ import annotations

import json
import os
from pathlib import Path

from roast_my_harness.auth import service as auth_service
from roast_my_harness.auth import staging
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def test_codex_credential_shape(monkeypatch, tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({
        "openai-codex": {"type": "oauth", "access": "tok", "refresh": "r",
                          "expires": 4102444800, "accountId": "acc"}
    }))
    monkeypatch.setattr(auth_service, "pi_auth_file", lambda: auth_file)
    cred = auth_service.codex_credential()
    assert cred is not None and cred["access"] == "tok"
    assert not auth_service.refresh_hint(cred)
    assert "expires" in auth_service.credential_expiry(cred)


def test_stage_home_copies_credential(tmp_path: Path, monkeypatch):
    home = tmp_path / "cached"
    (home / "extensions").mkdir(parents=True)
    (home / "variant.json").write_text("{}")
    monkeypatch.setattr(
        staging, "codex_credential",
        lambda: {"type": "oauth", "access": "t", "expires": 1},
    )
    spec = ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=tmp_path),
        variants=[VariantSpec(id="a")],
    )
    dest = staging.stage_home(home, tmp_path / "staged", spec)
    staged_auth = dest / "auth.json"
    assert staged_auth.is_file()
    assert (os.stat(staged_auth).st_mode & 0o777) == 0o600
    assert json.loads(staged_auth.read_text())["openai-codex"]["access"] == "t"
    # cached home stays secret-free
    assert not (home / "auth.json").exists()


def test_scan_for_secrets(tmp_path: Path):
    log = tmp_path / "logs"
    log.mkdir()
    (log / "a.log").write_text("all good\n")
    (log / "b.log").write_text("Authorization: Bearer abc123\n")
    hits = staging.scan_for_secrets(log)
    assert hits == [str(log / "b.log")]
