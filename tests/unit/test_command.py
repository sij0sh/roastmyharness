"""Pi command construction from the manifest."""

from __future__ import annotations

import pytest

from roast_my_harness.adapter.command import (
    build_run_command,
    skill_flags,
)


def test_skill_flags_quote_paths():
    flags = skill_flags(["skills/codegraph-cli"])
    assert flags == "--skill /opt/pi-home/skills/codegraph-cli"


def test_run_command_shape():
    command = build_run_command(
        model="openai-codex/gpt-5.6-luna",
        instruction="fix the bug; now",
        thinking="high",
        skill_paths=["skills/s"],
        extra_flags=["--flag-a"],
    )
    assert "pi --mode json" in command
    assert "--model openai-codex/gpt-5.6-luna" in command
    assert "--thinking high" in command
    assert "--no-skills --no-prompt-templates --no-themes -nc" in command
    assert "--skill /opt/pi-home/skills/s" in command
    assert "--flag-a" in command
    assert "'fix the bug; now'" in command
    assert "2>/logs/agent/pi-stderr.log" in command
    assert "> /logs/agent/pi-event-times.log" in command


