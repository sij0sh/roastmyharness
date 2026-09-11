"""Pi-only registry and home/pier plumbing."""

from __future__ import annotations

from roast_my_harness.adapter import registry
from roast_my_harness.constants import FAIRNESS_FLAGS
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def test_registry_is_pi_constants():
    assert registry.PI_BINARY == "pi"
    assert "pi-coding-agent" in registry.PI_NPM_PACKAGE
    assert registry.PI_IMPORT_PATH.endswith("pi_agent:PiAgent")
    assert registry.PI_VERSION_FIELD == "pi_version"


def test_fairness_has_no_implicit_context_flag():
    assert "-nc" not in FAIRNESS_FLAGS.split()


def test_spec_arms_are_pi_only(tmp_path):
    spec = ExperimentSpec(name="x", tasks=TaskSelection(path=tmp_path), variants=[VariantSpec(id="a")])
    assert [a.id for a in spec.arms()] == ["control", "a"]
    assert spec.pi_version_for(None) == "latest"
