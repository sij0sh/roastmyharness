"""Pi command construction. Stdlib-only: importable inside pier's venv."""

from __future__ import annotations

import shlex

from roast_my_harness.constants import FAIRNESS_FLAGS

REMOTE_HOME = "/opt/pi-home"
EVENTS_FILENAME = "pi-events.jsonl"
EVENT_TIMES_FILENAME = "pi-event-times.log"
SESSIONS_DIR = "pi-sessions"
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
) -> str:
    """The in-container agent invocation, streamed through the event stamper.

    binary and fairness_flags let pi-family forks (omp) reuse the command
    shape while pinning their own fairness contract.
    """
    parts = [
        f"export PI_CODING_AGENT_DIR={shlex.quote(REMOTE_HOME)};",
        f"{binary} --mode json",
        f"--model {shlex.quote(model)}",
    ]
    if thinking:
        parts.append(f"--thinking {shlex.quote(thinking)}")
    parts.append(fairness_flags)
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

