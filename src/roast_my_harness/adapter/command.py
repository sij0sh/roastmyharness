"""Pi command construction. Stdlib-only: importable inside pier's venv."""

from __future__ import annotations

import shlex

REMOTE_HOME = "/opt/pi-home"
EVENTS_FILENAME = "pi-events.jsonl"
EVENT_TIMES_FILENAME = "pi-event-times.log"
SESSIONS_DIR = "pi-sessions"
STDERR_FILENAME = "pi-stderr.log"
TRAJECTORY_FILENAME = "trajectory.json"

# Fairness flags kept identical for every arm so repo/global context files
# and per-variant cosmetics cannot differ.
FAIRNESS_FLAGS = "--no-skills --no-prompt-templates --no-themes -nc"

EVENT_STAMPER = (
    "node -e \"require('readline').createInterface({input:process.stdin})"
    ".on('line',l=>process.stdout.write(Date.now()+' '+l+'\\n'))\""
)


def skill_flags(skills: list[str]) -> str:
    """One --skill flag per declared skill; never load implicit skills."""
    return " ".join(
        f"--skill {shlex.quote(f'{REMOTE_HOME}/{path}')}" for path in skills
    )


def build_run_command(
    *,
    model: str,
    instruction: str,
    thinking: str | None,
    skill_paths: list[str],
    extra_flags: list[str],
) -> str:
    """The in-container pi invocation, streamed through the event stamper."""
    parts = [
        f"export PI_CODING_AGENT_DIR={shlex.quote(REMOTE_HOME)};",
        "pi --mode json",
        f"--model {shlex.quote(model)}",
    ]
    if thinking:
        parts.append(f"--thinking {shlex.quote(thinking)}")
    parts.append(FAIRNESS_FLAGS)
    flags = skill_flags(skill_paths)
    if flags:
        parts.append(flags)
    parts.append(f"--session-dir /logs/agent/{SESSIONS_DIR}")
    for flag in extra_flags:
        parts.append(shlex.quote(flag))
    parts.append(shlex.quote(instruction))
    parts.append("</dev/null")
    parts.append(f"2>/logs/agent/{STDERR_FILENAME}")
    parts.append(f"| tee /logs/agent/{EVENTS_FILENAME}")
    parts.append(f"| {EVENT_STAMPER}")
    parts.append(f"> /logs/agent/{EVENT_TIMES_FILENAME}")
    return " ".join(parts)

