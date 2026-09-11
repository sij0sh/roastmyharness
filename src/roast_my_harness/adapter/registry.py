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

    credential_format selects staged credential rendering: "pi" keeps
    models.json with $VAR refs, "bare-env" stages models.yml plus
    model-env.json with bare names. supports_pi_features gates pi-only
    variant features; supports_context_files gates explicit context-file
    delivery. Owner: adapter/registry. Decision: host-side strategy
    lives here, pier-side behavior lives in the adapter module named by
    import_path; consumers delegate instead of branching on agent_id.
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
    credential_format: str = "pi"
    supports_pi_features: bool = True
    supports_context_files: bool = True


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
    "omp": AgentDef(
        id="omp",
        family="pi",
        import_path="roast_my_harness.adapter.omp_agent:OmpAgent",
        npm_package="@oh-my-pi/pi-coding-agent",
        binary="omp",
        home_env="PI_CODING_AGENT_DIR",
        version_field="agent_version",
        fairness_flags="--no-skills",
        default_version="18.0.9",
        credential_format="bare-env",
        supports_pi_features=True,
    ),
    "claude": AgentDef(
        id="claude",
        family="claude-code",
        import_path="roast_my_harness.adapter.claude_agent:RobmyClaude",
        npm_package="@anthropic-ai/claude-code",
        binary="claude",
        home_env="CLAUDE_CONFIG_DIR",
        version_field="agent_version",
        fairness_flags="--strict-mcp-config",
        default_version="2.1.266",
        supports_pi_features=False,
        supports_context_files=False,
    ),
}


def get_agent(agent_id: str) -> AgentDef:
    """The registered agent, or a ValueError naming the known set."""
    agent = AGENTS.get(agent_id)
    if agent is None:
        known = ", ".join(sorted(AGENTS))
        raise ValueError(f"unknown agent {agent_id!r}; known agents: {known}")
    return agent
