"""Golden test: Pi variant.json, build-manifest.json, pier argv."""

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
schema_version = 3
name = "golden-agents"
pi_version = "0.84.3"
model = "openai-codex/gpt-5.6-luna"

[tasks]
path = "/tmp/golden-does-not-exist"
include = []
exclude = []

[[variants]]
id = "my-ext"
"""


@pytest.fixture(scope="module")
def home_payload(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("golden-homes")
    spec_path = root / "spec.toml"
    spec_path.write_text(SPEC_TOML)
    spec = load_experiment(spec_path)
    arm = [a for a in spec.arms() if a.id == "my-ext"][0]
    build = build_home(arm, spec, root / "homes")
    return {
        "variant": json.loads((build.path / "variant.json").read_text()),
        "build-manifest": json.loads((build.path / "build-manifest.json").read_text()),
    }


def _load_or_update(name: str, payload) -> None:
    path = GOLDENS / name
    if os.environ.get("ROAST_GOLDEN_UPDATE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        structured = isinstance(payload, (dict, list))
        text = json.dumps(payload, indent=2) + "\n" if structured else payload
        path.write_text(text)
    if isinstance(payload, (dict, list)):
        assert json.loads(path.read_text()) == payload, name
    else:
        assert path.read_text() == payload, name


def test_golden_variant_manifest(home_payload) -> None:
    _load_or_update("pi.variant.json", home_payload["variant"])


def test_golden_build_manifest(home_payload) -> None:
    _load_or_update("pi.build-manifest.json", home_payload["build-manifest"])


def test_golden_pier_argv() -> None:
    argv = pier_mod.build_run_args(
        task_root=Path("/tasks"), jobs_dir=Path("/jobs"), job_name="golden-pi",
        manifest_path=Path("/staging/variant.json"), model_id="openai-codex/gpt-5.6-luna",
        thinking="high", pi_version="0.84.3", n_concurrent=2,
    )
    argv[0] = "<pier>"
    _load_or_update("pi.argv.txt", "\n".join(argv) + "\n")
