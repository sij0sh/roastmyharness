"""Pi-only registry and home/pier plumbing."""

from __future__ import annotations

import pytest

from roast_my_harness.adapter.registry import PI_AGENT, get_agent
from roast_my_harness.constants import FAIRNESS_FLAGS
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def test_registry_is_pi_only():
    assert get_agent("pi") is PI_AGENT
    assert PI_AGENT.binary == "pi"
    assert "pi-coding-agent" in PI_AGENT.npm_package
    with pytest.raises(ValueError, match="Pi-only"):
        get_agent("omp")
    with pytest.raises(ValueError, match="Pi-only"):
        get_agent("claude")


def test_fairness_has_no_implicit_context_flag():
    assert "-nc" not in FAIRNESS_FLAGS.split()


def test_spec_arms_are_pi_only(tmp_path):
    spec = ExperimentSpec(name="x", tasks=TaskSelection(path=tmp_path), variants=[VariantSpec(id="a")])
    assert spec.resolved_agents() == {"control": "pi", "a": "pi"}
    assert spec.agent_version_for("pi") == "latest"
