"""Pi command construction from the manifest."""

from __future__ import annotations

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
    assert "--no-skills --no-prompt-templates --no-themes" in command
    assert "--skill /opt/pi-home/skills/s" in command
    assert "--flag-a" in command
    assert "'fix the bug; now'" in command
    assert "2>/logs/agent/pi-stderr.log" in command
    assert "> /logs/agent/pi-event-times.log" in command


def test_run_command_quotes_extra_flags():
    command = build_run_command(
        model="openai-codex/model",
        instruction="work",
        thinking=None,
        skill_paths=[],
        extra_flags=["--name=one; echo leaked"],
    )
    assert "'--name=one; echo leaked'" in command




def _base_kwargs(**over):
    kw = dict(
        model="openai-codex/model",
        instruction="work",
        thinking=None,
        skill_paths=[],
        extra_flags=[],
    )
    kw.update(over)
    return kw


def test_run_command_fresh_omits_continue():
    command = build_run_command(**_base_kwargs())
    assert "--continue" not in command


def test_run_command_resume_appends_continue():
    command = build_run_command(**_base_kwargs(resume=True))
    assert " --continue " in command


def test_run_command_session_dir_is_container_local():
    # Staged trials relocate /logs/agent per step; the session must live
    # somewhere the relocate cannot move it.
    command = build_run_command(**_base_kwargs())
    assert "--session-dir /tmp/pi-sessions" in command
    assert "/logs/agent/pi-sessions" not in command


def test_bash_only_flags_follow_fairness():
    command = build_run_command(
        **_base_kwargs(extra_flags=["--no-builtin-tools", "--tools=bash"])
    )
    fairness_at = command.index("--no-skills --no-prompt-templates --no-themes")
    assert command.index("--no-builtin-tools") > fairness_at
    assert command.index("--tools=bash") > fairness_at
