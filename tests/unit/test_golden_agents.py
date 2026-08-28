"""Golden tests: per-agent variant.json, build-manifest.json, pier argv.

These pin the host/adapter contract for every registered agent. A diff
here means the staging contract or the pier argv changed on purpose;
regenerate with ROAST_GOLDEN_UPDATE=1 and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from roast_my_harness.homes.builder import build_home
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.spec.load import load_experiment

GOLDENS = Path(__file__).resolve().parent.parent / "golden" / "agents"

SPEC_TOML = """\
schema_version = 1
name = "golden-agents"
pi_version = "0.84.3"

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[tasks]
path = "/tmp/golden-does-not-exist"
include = []
exclude = []

[control]
enabled = true

[[variants]]
id = "omp"
agent = "omp"
"""


@pytest.fixture(scope="module")
def homes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """Build both arms of the fixed spec; keyed by agent id."""
    root = tmp_path_factory.mktemp("golden-homes")
    spec_path = root / "spec.toml"
    spec_path.write_text(SPEC_TOML)
    spec = load_experiment(spec_path)
    out: dict[str, dict] = {}
    for arm in spec.arms():
        build = build_home(arm, spec, root / "homes")
        agent = build.manifest.agent
        assert agent not in out
        out[agent] = {
            "variant": json.loads((build.path / "variant.json").read_text()),
            "build-manifest": json.loads((build.path / "build-manifest.json").read_text()),
        }
    return out


def _golden_path(name: str) -> Path:
    return GOLDENS / name


def _load_or_update(name: str, payload) -> None:
    path = _golden_path(name)
    if os.environ.get("ROAST_GOLDEN_UPDATE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, indent=2) + "\n")
        else:
            path.write_text(payload)
    if isinstance(payload, (dict, list)):
        assert json.loads(path.read_text()) == payload, name
    else:
        assert path.read_text() == payload, name


@pytest.mark.parametrize("agent", ["pi", "omp"])
def test_golden_variant_manifest(homes, agent: str) -> None:
    _load_or_update(f"{agent}.variant.json", homes[agent]["variant"])


@pytest.mark.parametrize("agent", ["pi", "omp"])
def test_golden_build_manifest(homes, agent: str) -> None:
    _load_or_update(f"{agent}.build-manifest.json", homes[agent]["build-manifest"])


@pytest.mark.parametrize("agent,version", [("pi", "0.84.3"), ("omp", "18.0.9")])
def test_golden_pier_argv(agent: str, version: str) -> None:
    argv = pier_mod.build_run_args(
        task_root=Path("/tasks"),
        jobs_dir=Path("/jobs"),
        job_name=f"golden-{agent}",
        manifest_path=Path("/staging/variant.json"),
        model_id="openai-codex/gpt-5.6-luna",
        thinking="high",
        pi_version=version,
        n_concurrent=2,
        agent=agent,
    )
    # The pier executable path is machine-specific; pin only its position.
    argv[0] = "<pier>"
    _load_or_update(f"{agent}.argv.txt", "\n".join(argv) + "\n")
