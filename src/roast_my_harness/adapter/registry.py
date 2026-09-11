"""Pi agent definition: the only runtime RoastMyHarness launches.

Pi-native experiment extension. There is exactly one adapter
(adapter/pi_agent.py). This module keeps the host-side Pi package facts
in one place so spec loading and the runner never hardcode them.
"""

from __future__ import annotations

from dataclasses import dataclass

from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS


@dataclass(frozen=True)
class AgentDef:
    """Host-side facts needed to launch Pi."""

    id: str
    import_path: str
    npm_package: str
    binary: str
    home_env: str
    version_field: str
    fairness_flags: str
    default_version: str


PI_AGENT = AgentDef(
    id="pi",
    import_path="roast_my_harness.adapter.pi_agent:PiAgent",
    npm_package="@earendil-works/pi-coding-agent",
    binary="pi",
    home_env="PI_CODING_AGENT_DIR",
    version_field="pi_version",
    fairness_flags=FAIRNESS_FLAGS,
    default_version=DEFAULT_PI_VERSION,
)

AGENTS: dict[str, AgentDef] = {"pi": PI_AGENT}


def get_agent(agent_id: str = "pi") -> AgentDef:
    """The Pi agent definition. Only 'pi' is supported."""
    if agent_id != "pi":
        raise ValueError(f"unknown agent {agent_id!r}; RoastMyHarness is Pi-only")
    return PI_AGENT
