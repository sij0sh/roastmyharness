"""Pi command construction. Stdlib-only: importable inside pier's venv."""

from __future__ import annotations

import shlex

from roast_my_harness.constants import FAIRNESS_FLAGS

REMOTE_HOME = "/opt/pi-home"
EVENTS_FILENAME = "pi-events.jsonl"
EVENT_TIMES_FILENAME = "pi-event-times.log"
SESSIONS_DIR = "pi-sessions"
# Container-local session storage (NOT under /logs): pier relocates
# /logs/agent per step in staged trials, which would move the session away
# before the next step's resume. /tmp persists across steps of the same
# trial container and is invisible to log collection and model.patch.
SESSION_BASE_DIR = "/tmp"
STDERR_FILENAME = "pi-stderr.log"
TRAJECTORY_FILENAME = "trajectory.json"





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
    fairness_flags: str = FAIRNESS_FLAGS,
    binary: str = "pi",
    resume: bool = False,
) -> str:
    """The in-container agent invocation, streamed through the event stamper.

    resume appends --continue so a staged follow-up step rejoins the same
    pi session instead of starting a fresh conversation (context
    accumulates across steps; the worktree is already shared).
    """
    parts = [
        f"export PI_CODING_AGENT_DIR={shlex.quote(REMOTE_HOME)};",
        f"{binary} --mode json",
        f"--model {shlex.quote(model)}",
    ]
    if thinking:
        parts.append(f"--thinking {shlex.quote(thinking)}")
    if resume:
        parts.append("--continue")
    parts.append(fairness_flags)
    flags = skill_flags(skill_paths)
    if flags:
        parts.append(flags)
    parts.append(f"--session-dir {SESSION_BASE_DIR}/{SESSIONS_DIR}")
    for flag in extra_flags:
        parts.append(shlex.quote(flag))
    parts.append(shlex.quote(instruction))
    parts.append("</dev/null")
    parts.append(f"2>/logs/agent/{STDERR_FILENAME}")
    parts.append(f"| tee /logs/agent/{EVENTS_FILENAME}")
    parts.append(f"| {EVENT_STAMPER}")
    parts.append(f"> /logs/agent/{EVENT_TIMES_FILENAME}")
    return " ".join(parts)

