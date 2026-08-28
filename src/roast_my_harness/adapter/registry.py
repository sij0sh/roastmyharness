"""Agent registry: which coding agents RoastMyHarness can launch.

Host-side only. Adapters (adapter/pi_agent.py and friends) run inside
pier's venv and must never import this module; the registry names their
import paths instead. Everything here is stdlib-only so spec loading and
the runner can use it anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS


@dataclass(frozen=True)
class AgentDef:
    """Everything the spec, runner, and home builder need to launch one agent.

    family groups agents that share the pi home layout, run command shape,
    and event-stream format ("pi" family, e.g. forks); other families need
    their own adapter and home builder.
    """

    id: str
    family: str
    import_path: str
    npm_package: str
    binary: str
    home_env: str
    version_field: str
    fairness_flags: str
    default_version: str


AGENTS: dict[str, AgentDef] = {
    "pi": AgentDef(
        id="pi",
        family="pi",
        import_path="roast_my_harness.adapter.pi_agent:PiAgent",
        npm_package="@earendil-works/pi-coding-agent",
        binary="pi",
        home_env="PI_CODING_AGENT_DIR",
        version_field="pi_version",
        fairness_flags=FAIRNESS_FLAGS,
        default_version=DEFAULT_PI_VERSION,
    ),
}


def get_agent(agent_id: str) -> AgentDef:
    """The registered agent, or a ValueError naming the known set."""
    agent = AGENTS.get(agent_id)
    if agent is None:
        known = ", ".join(sorted(AGENTS))
        raise ValueError(f"unknown agent {agent_id!r}; known agents: {known}")
    return agent
