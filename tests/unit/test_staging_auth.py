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


def test_write_auth_file_is_atomic_and_private(tmp_path: Path, monkeypatch):
    auth_file = tmp_path / "agent" / "auth.json"
    monkeypatch.setattr(auth_service, "pi_auth_file", lambda: auth_file)
    auth_service.write_auth_file({"openai-codex": {"access": "secret"}})
    assert json.loads(auth_file.read_text())["openai-codex"]["access"] == "secret"
    assert (os.stat(auth_file).st_mode & 0o777) == 0o600
    assert not list(auth_file.parent.glob(".auth.json.*"))


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


def test_scan_for_secrets_covers_non_log_artifacts(tmp_path: Path):
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "a.log").write_text("all good\n")
    (logs / "b.log").write_text("authorization: bearer abc123\n")
    (run_dir / "summary.json").write_text('{"key": "sk-secret"}\n')
    hits = staging.scan_for_secrets(run_dir)
    assert hits == [str(logs / "b.log"), str(run_dir / "summary.json")]
