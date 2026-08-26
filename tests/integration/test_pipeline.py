"""Integration: spec -> tasks -> homes -> manifests end to end."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.homes.builder import build_home
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.tasks.discover import discover_tasks
from roast_my_harness.tasks.hashes import task_hash

SPEC = """
schema_version = 1
name = "integration"
pi_version = "0.84.3"
thinking = "high"

[tasks]
path = "./dataset"

[control]
enabled = true

[[variants]]
id = "airhead2"
[[variants.extensions]]
kind = "local"
path = "./ext-src"
entry = "src/index.ts"
[variants.env]
AIRHEAD_KEEP = "2"
"""


def setup_tree(tmp_path: Path) -> Path:
    for task_id in ("t1", "t2"):
        task = tmp_path / "dataset" / task_id
        task.mkdir(parents=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"task {task_id}\n")
    ext = tmp_path / "ext-src"
    (ext / "src").mkdir(parents=True)
    (ext / "src" / "index.ts").write_text("export default 1\n")
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(SPEC)
    return spec_path


def test_pipeline(tmp_path: Path):
    spec = load_experiment(setup_tree(tmp_path))
    tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
    assert [t.task_id for t in tasks] == ["t1", "t2"]
    hashes = {t.task_id: task_hash(t.path) for t in tasks}
    assert hashes["t1"] != hashes["t2"]

    homes = tmp_path / "homes"
    manifests = {}
    for variant in spec.arms():
        build = build_home(variant, spec, homes)
        manifests[variant.id] = json.loads(
            (build.path / "variant.json").read_text()
        )
    assert set(manifests) == {"control", "airhead2"}
    assert manifests["control"]["extensions"] == []
    assert manifests["airhead2"]["extensions"][0]["entry"].endswith(
        "src/index.ts"
    )
    assert manifests["airhead2"]["env"] == {"AIRHEAD_KEEP": "2"}
    assert manifests["airhead2"]["variant_hash"] != manifests["control"]["variant_hash"]
