"""Shared hermetic fixtures for the test suite.

Controller.prepare() stages the openai-codex credential from the host pi
auth file. Tests must not depend on the developer's real ~/.pi/agent
auth file or fail on clean CI runners. This autouse fixture points
PI_CODING_AGENT_DIR at a per-test fake home containing a valid codex
entry. Tests that need their own host home (e.g. host_pi) override the
env var with their own fixture, which runs after this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_pi_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    auth_dir = tmp_path / "fake-pi-home"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "auth.json").write_text(
        json.dumps(
            {
                "openai-codex": {
                    "type": "oauth",
                    "access": "test-token",
                    "refresh": "r",
                    "expires": 4102444800,
                    "accountId": "acc",
                }
            }
        )
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(auth_dir))
